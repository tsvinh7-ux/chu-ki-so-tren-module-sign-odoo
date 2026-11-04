/** @odoo-module **/
/* Trang ký GIẢ LẬP theo Odoo Sign 17: hiện PDF (pdf.js), các ô ký đặt theo tỉ lệ trên trang,
 * bấm ô chữ ký mở hộp chọn chữ ký dùng NameAndSignature của web (giống Odoo Sign),
 * nút "Ký và hoàn tất" gửi giá trị lên /sign/sign/<id>/<token>. */
import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { loadJS } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { NameAndSignature } from "@web/core/signature/name_and_signature";

export class SignatureModal extends Component {
    static template = "sign.SignatureModal";
    static components = { NameAndSignature };
    static props = ["defaultName", "signatureType", "onAdopt", "onClose"];

    setup() {
        this.signature = useState({ name: this.props.defaultName, isSignatureEmpty: true });
    }

    adopt() {
        // Như Odoo Sign: getSignatureImage() trả [mimetype, base64] → chuỗi data URL.
        const [mimetype, data] = this.signature.getSignatureImage();
        this.props.onAdopt(`data:${mimetype},${data}`, this.signature);
    }
}

export class SigningPage extends Component {
    static template = "sign.SigningPage";
    static components = { SignatureModal };
    static props = ["*"];

    setup() {
        this.rpc = useService("rpc");
        this.pagesRef = useRef("pages");
        this.state = useState({
            pages: [],
            values: Object.fromEntries(this.props.items.filter((i) => i.value).map((i) => [i.id, i.value])),
            modalItem: null,
            busy: false,
            error: "",
            done: this.props.itemState === "completed",
            requestState: this.props.requestState,
        });
        onMounted(() => this.renderPdf());
    }

    get myItems() {
        return this.props.items.filter((i) => i.mine);
    }

    get canSign() {
        return !this.state.done && this.props.isTurn && this.props.requestState === "sent";
    }

    async renderPdf() {
        await loadJS("/web/static/lib/pdfjs/build/pdf.js");
        const pdfjsLib = window["pdfjs-dist/build/pdf"] || window.pdfjsLib;
        pdfjsLib.GlobalWorkerOptions.workerSrc = "/web/static/lib/pdfjs/build/pdf.worker.js";
        const pdf = await pdfjsLib.getDocument(this.props.pdfUrl).promise;
        const pages = [];
        for (let n = 1; n <= pdf.numPages; n++) {
            const page = await pdf.getPage(n);
            const viewport = page.getViewport({ scale: 1.4 });
            const canvas = document.createElement("canvas");
            canvas.width = viewport.width;
            canvas.height = viewport.height;
            await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
            pages.push({ number: n, src: canvas.toDataURL("image/png"), width: viewport.width, height: viewport.height });
        }
        this.state.pages = pages;
    }

    itemsOnPage(number) {
        return this.props.items.filter((i) => i.page === number);
    }

    itemStyle(item) {
        return `left:${item.posX * 100}%;top:${item.posY * 100}%;width:${item.width * 100}%;height:${item.height * 100}%`;
    }

    openItem(item) {
        if (!item.mine || !this.canSign || item.type === "text") {
            return;
        }
        this.state.modalItem = item;
    }

    onAdopt(image) {
        // Như Odoo Sign: dùng cùng một chữ ký cho mọi ô chữ ký của người này.
        for (const item of this.myItems.filter((i) => i.type === this.state.modalItem.type)) {
            this.state.values[item.id] = image;
        }
        this.state.modalItem = null;
    }

    onText(item, ev) {
        this.state.values[item.id] = ev.target.value;
    }

    get missing() {
        return this.myItems.filter((i) => i.required && !this.state.values[i.id]);
    }

    collectValues() {
        return Object.fromEntries(this.myItems.filter((i) => this.state.values[i.id]).map((i) => [i.id, this.state.values[i.id]]));
    }

    async submit() {
        if (this.missing.length) {
            this.state.error = "Bạn chưa điền đủ các ô bắt buộc.";
            return;
        }
        this.state.busy = true;
        this.state.error = "";
        try {
            const result = await this.rpc(`/sign/sign/${this.props.requestId}/${this.props.token}`, {
                signature: this.collectValues(),
            });
            this.onSigned(result);
        } catch (error) {
            this.state.error = error.data?.message || error.message || "Không ký được.";
        } finally {
            this.state.busy = false;
        }
    }

    onSigned(result) {
        this.state.done = true;
        this.state.requestState = result.requestState || this.state.requestState;
    }
}

registry.category("public_components").add("sign.signing_page", SigningPage);
