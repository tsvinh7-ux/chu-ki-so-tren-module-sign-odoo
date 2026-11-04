/** @odoo-module **/
/* Thêm tab "USB token" và lời nhắc ký số vào hộp chọn chữ ký (NameAndSignature) khi đang ở trang ký
 * của module Sign có cấu hình ký số. Ở nơi khác (ký báo giá trên cổng khách hàng…) không đổi gì. */
import { useState } from "@odoo/owl";
import { patch } from "@web/core/utils/patch";
import { NameAndSignature } from "@web/core/signature/name_and_signature";
import { dmagCks, loadCksConfig, loadCertificates } from "./cks_store";

const USB = "dmag_usb";

patch(NameAndSignature.prototype, {
    setup() {
        super.setup(...arguments);
        this.cks = useState(dmagCks);
        this.cksEnabled = !!loadCksConfig();
        if (this.cksEnabled) {
            // Mở sẵn tab USB token: khuyến khích ký số.
            this.state.signMode = USB;
            this.state.showSignatureArea = true;
        }
    },

    get cksRequired() {
        return this.cksEnabled && this.cks.config.required;
    },

    async setMode(mode, reset) {
        if (mode !== USB) {
            dmagCks.usb = false;
            return super.setMode(mode, reset);
        }
        this.state.signMode = USB;
        dmagCks.usb = true;
        this.jSignature("disable");
        this.jSignature("reset");
        this.props.signature.isSignatureEmpty = true;
        this.props.onSignatureChange(USB);
        if (dmagCks.agent !== "ready") {
            await loadCertificates();
        }
        if (dmagCks.cert) {
            this.cksDrawStamp(dmagCks.cert);
        }
    },

    onClickCksUsb() {
        this.setMode(USB);
    },

    onClickCksRetry() {
        loadCertificates().then(() => dmagCks.cert && this.cksDrawStamp(dmagCks.cert));
    },

    onSelectCksCert(cert) {
        if (cert.expired) {
            return;
        }
        dmagCks.cert = cert;
        this.cksDrawStamp(cert);
    },

    cksIssuer(cert) {
        return ((cert.issuer || "").match(/CN=([^,]+)/) || [])[1] || cert.issuer;
    },

    cksExpiry(cert) {
        return cert.not_after ? new Date(cert.not_after).toLocaleDateString("vi-VN") : "";
    },

    /** Hình hiển thị trong ô ký trên trang (bản PDF sẽ có ô chữ ký số do máy chủ vẽ). */
    cksDrawStamp(cert) {
        const canvas = document.createElement("canvas");
        canvas.width = 600;
        canvas.height = 200;
        const ctx = canvas.getContext("2d");
        ctx.strokeStyle = "#0b6e4f";
        ctx.lineWidth = 4;
        ctx.strokeRect(4, 4, 592, 192);
        ctx.fillStyle = "#0b6e4f";
        ctx.font = "bold 30px sans-serif";
        ctx.fillText("Ký số bằng USB token", 24, 52);
        ctx.fillStyle = "#1b2433";
        ctx.font = "26px sans-serif";
        ctx.fillText((cert.common_name || "").slice(0, 38), 24, 100);
        const id = (cert.subject || "").match(/(MST|CCCD|CMND)[:\s]*([0-9A-Z-]+)/i);
        if (id) {
            ctx.fillText(`${id[1].toUpperCase()}: ${id[2]}`, 24, 140);
        }
        ctx.font = "22px sans-serif";
        ctx.fillText(new Date().toLocaleString("vi-VN"), 24, 178);
        this.printImage(canvas.toDataURL("image/png"));
    },
});
