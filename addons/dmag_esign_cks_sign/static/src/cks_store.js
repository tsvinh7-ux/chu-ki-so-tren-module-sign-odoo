/** @odoo-module **/
/* Trạng thái ký số dùng chung giữa hộp chọn chữ ký (NameAndSignature) và trang ký của module Sign. */
import { reactive } from "@odoo/owl";

export const dmagCks = reactive({
    config: null,       // từ <div id="dmag_cks_config">: chỉ có trên trang ký của Sign
    usb: false,         // người ký đang chọn tab USB token
    cert: null,         // chứng thư đã chọn (từ DMAG Ký số)
    certs: [],
    agent: "unknown",   // unknown | checking | ready | missing | not_allowed
    error: "",
    step: "",           // thông báo tiến trình khi ký
});

export function loadCksConfig() {
    if (dmagCks.config) {
        return dmagCks.config;
    }
    const el = document.getElementById("dmag_cks_config");
    if (el && el.dataset.config) {
        try {
            dmagCks.config = JSON.parse(el.dataset.config);
        } catch {
            dmagCks.config = null;
        }
    }
    return dmagCks.config;
}

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
    dmagCks.error = "";
    try {
        await agent("/v1/info").catch((err) => {
            // Phần mềm có chạy nhưng chưa cho phép trang này: gọi tiếp để máy tính hỏi "Cho phép?".
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

async function rpc(path, params) {
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

/**
 * Ký số bằng USB token rồi hoàn tất phần ký của người này trên module Sign (máy chủ làm cùng lúc).
 * values: {id ô ký: giá trị}. Nếu người khác vừa ký trước, tự chuẩn bị lại (token hỏi PIN thêm lần nữa).
 */
export async function cksSubmit(values) {
    const cfg = dmagCks.config;
    const base = `/dmag_cks/sign/${cfg.requestId}/${cfg.token}`;
    const cert = dmagCks.cert;
    for (let attempt = 0; attempt < 3; attempt++) {
        dmagCks.step = "Đang chuẩn bị tài liệu…";
        const prep = await rpc(base + "/prepare", { signature: values, cert: cert.der, chain: cert.chain || [] });
        dmagCks.step = "Mời xác nhận và nhập mã PIN USB token trên máy tính…";
        const signed = await agent("/v1/sign", {
            id: cert.id, hash: prep.hash, hash_alg: "sha256", document: prep.document,
        });
        dmagCks.step = "Đang ghi chữ ký số vào tài liệu…";
        const result = await rpc(base + "/finalize", { prep: prep.prep, signature: signed.signature });
        if (!result.conflict) {
            dmagCks.step = "";
            return result;
        }
        dmagCks.step = "Có người vừa ký trước bạn, đang ký lại trên bản mới nhất…";
    }
    dmagCks.step = "";
    throw new Error("Tài liệu đang có nhiều người ký cùng lúc, vui lòng thử lại.");
}
