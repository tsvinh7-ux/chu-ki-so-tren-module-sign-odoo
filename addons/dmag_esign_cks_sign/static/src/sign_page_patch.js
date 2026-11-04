/** @odoo-module **/
/* Bộ chuyển cho trang ký của module Sign: khi người ký chọn tab USB token, bước "Ký và hoàn tất"
 * đi qua ký số (chuẩn bị → USB token ký → ghi chữ ký và hoàn tất trên máy chủ).
 * Đây là phần duy nhất phụ thuộc mã nguồn trang ký: với bản Sign của Bizapps, đổi import và tên hàm
 * (Odoo Sign 17: lớp xử lý nút "Validate & Send"). */
import { patch } from "@web/core/utils/patch";
import { SigningPage } from "@sign/signing_page";
import { dmagCks, cksSubmit } from "./cks_store";

patch(SigningPage.prototype, {
    async submit() {
        if (!(dmagCks.usb && dmagCks.cert)) {
            return super.submit(...arguments);
        }
        if (this.missing.length) {
            this.state.error = "Bạn chưa điền đủ các ô bắt buộc.";
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            const result = await cksSubmit(this.collectValues());
            this.onSigned(result);
        } catch (error) {
            this.state.error = error.code === "user_cancelled" ? "Bạn đã hủy ký." : error.message;
        } finally {
            this.state.busy = false;
        }
    },
});
