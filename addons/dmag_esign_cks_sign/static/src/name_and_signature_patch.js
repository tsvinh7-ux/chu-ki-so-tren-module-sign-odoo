/** @odoo-module **/
/* Thêm lời nhắc, tab "USB token" và tab "App ký số" vào hộp chọn chữ ký (NameAndSignature) khi đang ở
 * trang ký của module Sign đã cấu hình. Trang khác (ký báo giá trên cổng khách hàng…) không đổi gì. */
import { onWillStart, useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { NameAndSignature } from "@web/core/signature/name_and_signature";
import { checkCloudAccount, cksReady, dmagCks, loadCertificates } from "./cks_store";

const USB = "dmag_usb";
const CLOUD = "dmag_cloud";

patch(NameAndSignature.prototype, {
    setup() {
        super.setup(...arguments);
        this.cks = useState(dmagCks);
        onWillStart(async () => {
            await cksReady;
            if (dmagCks.config) {
                // Mở sẵn tab USB token: khuyến khích ký số.
                this.state.signMode = USB;
                this.state.showSignatureArea = true;
            }
        });
    },

    get cksEnabled() {
        return !!this.cks.config;
    },

    get cksRequired() {
        return this.cksEnabled && this.cks.config.required;
    },

    get cksHasCloud() {
        return this.cksEnabled && this.cks.config.cloudProviders.length > 0;
    },

    async setMode(mode, reset) {
        if (mode !== USB && mode !== CLOUD) {
            dmagCks.method = null;
            return super.setMode(mode, reset);
        }
        this.state.signMode = mode;
        this.jSignature("disable");
        this.jSignature("reset");
        this.props.signature.isSignatureEmpty = true;
        this.props.onSignatureChange(mode);
        if (mode === USB) {
            dmagCks.method = "usb";
            if (dmagCks.agent !== "ready") {
                await loadCertificates();
            }
            if (dmagCks.cert) {
                this.cksDrawStamp("Ký số bằng USB token", dmagCks.cert.common_name, this.cksIdFromSubject(dmagCks.cert.subject));
            }
        } else {
            dmagCks.method = "cloud";
            if (dmagCks.cloud.checked) {
                this.cksDrawCloudStamp();
            }
        }
    },

    onClickCksUsb() {
        this.setMode(USB);
    },

    onClickCksCloud() {
        this.setMode(CLOUD);
    },

    onClickCksRetry() {
        loadCertificates().then(() => dmagCks.cert && this.setMode(USB));
    },

    onSelectCksCert(cert) {
        if (cert.expired) {
            return;
        }
        dmagCks.cert = cert;
        this.cksDrawStamp("Ký số bằng USB token", cert.common_name, this.cksIdFromSubject(cert.subject));
    },

    onChangeCksProvider(ev) {
        dmagCks.cloud.providerId = Number(ev.target.value);
        dmagCks.cloud.checked = null;
    },

    onInputCksAccount(ev) {
        dmagCks.cloud.remoteUserId = ev.target.value;
        dmagCks.cloud.checked = null;
    },

    async onClickCksCheckAccount() {
        if (await checkCloudAccount()) {
            this.cksDrawCloudStamp();
        }
    },

    get cksProviderHint() {
        const provider = this.cks.config.cloudProviders.find((p) => p.id === this.cks.cloud.providerId);
        return provider ? provider.hint : "";
    },

    cksIssuer(cert) {
        return ((cert.issuer || "").match(/CN=([^,]+)/) || [])[1] || cert.issuer;
    },

    cksExpiry(cert) {
        return cert.not_after ? new Date(cert.not_after).toLocaleDateString("vi-VN") : "";
    },

    cksIdFromSubject(subject) {
        const match = (subject || "").match(/(MST|CCCD|CMND)[:\s]*([0-9A-Z-]+)/i);
        return match ? `${match[1].toUpperCase()}: ${match[2]}` : "";
    },

    cksDrawCloudStamp() {
        const checked = dmagCks.cloud.checked;
        const ids = Object.entries(checked.ids || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
        const provider = this.cks.config.cloudProviders.find((p) => p.id === dmagCks.cloud.providerId);
        this.cksDrawStamp("Ký số từ xa – " + (provider ? provider.label : ""), checked.common_name, ids);
    },

    /** Hình hiển thị trong ô ký trên trang; bản PDF sẽ có ô chữ ký số do máy chủ vẽ. */
    cksDrawStamp(title, name, idLine) {
        const canvas = document.createElement("canvas");
        canvas.width = 600;
        canvas.height = 200;
        const ctx = canvas.getContext("2d");
        ctx.strokeStyle = "#0b6e4f";
        ctx.lineWidth = 4;
        ctx.strokeRect(4, 4, 592, 192);
        ctx.fillStyle = "#0b6e4f";
        ctx.font = "bold 28px sans-serif";
        ctx.fillText(title.slice(0, 40), 24, 50);
        ctx.fillStyle = "#1b2433";
        ctx.font = "26px sans-serif";
        ctx.fillText((name || "").slice(0, 38), 24, 98);
        if (idLine) {
            ctx.fillText(idLine.slice(0, 40), 24, 138);
        }
        ctx.font = "22px sans-serif";
        ctx.fillText(new Date().toLocaleString("vi-VN"), 24, 178);
        this.printImage(canvas.toDataURL("image/png"));
    },
});
