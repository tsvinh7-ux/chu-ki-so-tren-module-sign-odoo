/** @odoo-module **/
/* Bắt lúc trang ký gửi chữ ký lên máy chủ (theo đường dẫn trong cấu hình, mặc định /sign/sign/<id>/<mã>).
 * Nếu người ký đã chọn USB token / app ký số: ký số trước, rồi mới cho yêu cầu gốc đi tiếp, nên module
 * Sign vẫn xử lý và hiển thị như bình thường. Ký số lỗi: yêu cầu gốc vẫn đi kèm header X-DMAG-CKS-Error để
 * máy chủ từ chối và module Sign hiện thông báo lỗi của chính nó. */
import { dmagCks, runCks } from "./cks_store";

function shouldIntercept(url, method) {
    const cfg = dmagCks.config;
    if (!cfg || !dmagCks.method || (method && method.toUpperCase() !== "POST")) {
        return false;
    }
    if (dmagCks.method === "usb" && !dmagCks.cert) {
        return false;
    }
    try {
        return new RegExp(cfg.submitUrlRegex).test(new URL(url, window.location.href).pathname);
    } catch {
        return false;
    }
}

function extractValues(body) {
    try {
        const data = typeof body === "string" ? JSON.parse(body) : body;
        const params = (data && data.params) || data || {};
        return params[dmagCks.config.submitParam] || {};
    } catch {
        return {};
    }
}

// ------------------------------------------------------------------ XMLHttpRequest (rpc của Odoo 17)
const originalOpen = XMLHttpRequest.prototype.open;
const originalSend = XMLHttpRequest.prototype.send;

XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    this.__dmagCks = { method, url };
    return originalOpen.call(this, method, url, ...rest);
};

XMLHttpRequest.prototype.send = function (body) {
    const info = this.__dmagCks;
    if (!info || !shouldIntercept(info.url, info.method)) {
        return originalSend.call(this, body);
    }
    runCks(extractValues(body))
        .then(() => originalSend.call(this, body))
        .catch((err) => {
            this.setRequestHeader("X-DMAG-CKS-Error", encodeURIComponent(err.message || "Lỗi ký số"));
            originalSend.call(this, body);
        });
};

// ------------------------------------------------------------------ fetch
const originalFetch = window.fetch;
window.fetch = async function (input, init = {}) {
    const url = typeof input === "string" ? input : input.url;
    const method = init.method || (typeof input === "string" ? "GET" : input.method);
    if (!shouldIntercept(url, method)) {
        return originalFetch.call(this, input, init);
    }
    const headers = new Headers(init.headers || {});
    try {
        await runCks(extractValues(init.body));
    } catch (err) {
        headers.set("X-DMAG-CKS-Error", encodeURIComponent(err.message || "Lỗi ký số"));
    }
    return originalFetch.call(this, input, { ...init, headers });
};
