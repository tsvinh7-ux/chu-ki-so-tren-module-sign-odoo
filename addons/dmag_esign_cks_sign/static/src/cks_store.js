/** @odoo-module **/
/* Trạng thái ký số dùng chung giữa hộp chọn chữ ký (NameAndSignature), bộ bắt lệnh gửi chữ ký và
 * lớp thông báo tiến trình. Không phụ thuộc mã nguồn của module Sign. */
import { reactive } from "@odoo/owl";

export const dmagCks = reactive({
    config: null,      // từ /dmag_cks/sign/config, chỉ có khi trang hiện tại là trang ký đã cấu hình
    method: null,      // "usb" | "cloud" | null (người ký đang chọn cách ký thường)
    cert: null,        // chứng thư USB token đã chọn
    certs: [],
    agent: "unknown",  // unknown | checking | ready | missing | not_allowed
    cloud: { providerId: null, remoteUserId: "", checked: null, error: "" },
});

async function jsonRpc(path, params) {
    const resp = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jsonrpc: "2.0", method: "call", params }),
    });
    const data = await resp.json();
    if (data.error) {
        throw new Error((data.error.data && data.error.data.message) || data.error.message);
    }
    return data.result;
}

async function initConfig() {
    // Chỉ hỏi máy chủ trên các trang có chữ "sign" trong đường dẫn.
    if (!/sign/i.test(window.location.pathname)) {
        return null;
    }
    try {
        const config = await jsonRpc("/dmag_cks/sign/config", { path: window.location.pathname });
        if (config && config.enabled) {
            dmagCks.config = config;
            dmagCks.cloud.providerId = config.cloudProviders.length ? config.cloudProviders[0].id : null;
            dmagCks.cloud.remoteUserId = config.defaultRemoteUser || "";
        }
    } catch {
        // Không lấy được cấu hình: trang ký chạy như cũ.
    }
    return dmagCks.config;
}

export const cksReady = initConfig();

// ------------------------------------------------------------------ DMAG Ký số (USB token)
async function agent(path, body) {
    const opts = body
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
        : { method: "GET" };
    const resp = await fetch(dmagCks.config.agentUrl + path, opts);
    const data = await resp.json();
    if (!resp.ok) {
        const err = new Error(data.message || "Phần mềm ký số báo lỗi.");
        err.code = data.error;
        throw err;
    }
    return data;
}

export async function loadCertificates() {
    dmagCks.agent = "checking";
    try {
        await agent("/v1/info").catch((err) => {
            if (err.code !== "origin_not_allowed") {
                throw err;
            }
        });
        const data = await agent("/v1/certificates");
        dmagCks.certs = data.certificates || [];
        dmagCks.agent = "ready";
        const valid = dmagCks.certs.filter((c) => !c.expired);
        if (valid.length === 1 && !dmagCks.cert) {
            dmagCks.cert = valid[0];
        }
    } catch (err) {
        dmagCks.agent = err.code === "origin_not_allowed" ? "not_allowed" : "missing";
    }
}

// ------------------------------------------------------------------ ký từ xa (app)
export async function checkCloudAccount() {
    const cfg = dmagCks.config;
    dmagCks.cloud.error = "";
    dmagCks.cloud.checked = null;
    try {
        dmagCks.cloud.checked = await jsonRpc("/dmag_cks/sign/cloud/check", {
            request_id: cfg.requestId, token: cfg.token,
            provider_id: dmagCks.cloud.providerId, remote_user_id: dmagCks.cloud.remoteUserId,
        });
    } catch (err) {
        dmagCks.cloud.error = err.message;
    }
    return dmagCks.cloud.checked;
}

// ------------------------------------------------------------------ lớp thông báo tiến trình
function overlay(message, opts = {}) {
    let el = document.getElementById("dmag_cks_overlay");
    if (!message) {
        el?.remove();
        return;
    }
    if (!el) {
        el = document.createElement("div");
        el.id = "dmag_cks_overlay";
        el.innerHTML = '<div class="dmag_cks_overlay_box"><span class="dmag_cks_spinner"></span>' +
            '<div class="dmag_cks_overlay_text"></div></div>';
        document.body.appendChild(el);
    }
    el.querySelector(".dmag_cks_spinner").hidden = !!opts.done;
    el.querySelector(".dmag_cks_overlay_text").textContent = message;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function usbOnce(values) {
    const cfg = dmagCks.config;
    const cert = dmagCks.cert;
    overlay("Đang chuẩn bị tài liệu để ký số…");
    const prep = await jsonRpc("/dmag_cks/sign/prepare", {
        request_id: cfg.requestId, token: cfg.token, signature: values, cert: cert.der, chain: cert.chain || [],
    });
    overlay("Mời xác nhận và nhập mã PIN USB token trên máy tính…");
    const signed = await agent("/v1/sign", { id: cert.id, hash: prep.hash, hash_alg: "sha256", document: prep.document });
    overlay("Đang ghi chữ ký số vào tài liệu…");
    return jsonRpc("/dmag_cks/sign/finalize", {
        request_id: cfg.requestId, token: cfg.token, prep: prep.prep, signature: signed.signature,
    });
}

async function cloudOnce(values) {
    const cfg = dmagCks.config;
    const provider = cfg.cloudProviders.find((p) => p.id === dmagCks.cloud.providerId);
    overlay("Đang gửi yêu cầu ký tới " + (provider ? provider.label : "nhà cung cấp") + "…");
    let res = await jsonRpc("/dmag_cks/sign/cloud/start", {
        request_id: cfg.requestId, token: cfg.token, signature: values,
        provider_id: dmagCks.cloud.providerId, remote_user_id: dmagCks.cloud.remoteUserId,
    });
    const prep = res.prep;
    while (res.state === "waiting") {
        overlay("Mời mở ứng dụng " + (provider ? provider.label : "ký số") + " trên điện thoại và bấm Xác nhận…");
        await sleep(3000);
        res = await jsonRpc("/dmag_cks/sign/cloud/status", { request_id: cfg.requestId, token: cfg.token, prep });
    }
    if (res.state === "conflict") {
        return { conflict: true };
    }
    if (res.state !== "signed") {
        throw new Error(res.message || "Ký số từ xa không thành công.");
    }
    return { conflict: false };
}

/** Ký số trước khi module Sign nhận chữ ký. Người khác vừa ký trước thì tự ký lại trên bản mới nhất. */
export async function runCks(values) {
    try {
        for (let attempt = 0; attempt < 3; attempt++) {
            const result = dmagCks.method === "usb" ? await usbOnce(values) : await cloudOnce(values);
            if (!result.conflict) {
                overlay("Đã ký số. Đang hoàn tất…");
                setTimeout(() => overlay(null), 1500);
                return;
            }
            overlay("Có người vừa ký trước bạn, đang ký lại trên bản mới nhất…");
        }
        throw new Error("Tài liệu đang có nhiều người ký cùng lúc, vui lòng thử lại.");
    } catch (err) {
        overlay(null);
        if (err.code === "user_cancelled") {
            err.message = "Bạn đã hủy ký.";
        }
        throw err;
    }
}
