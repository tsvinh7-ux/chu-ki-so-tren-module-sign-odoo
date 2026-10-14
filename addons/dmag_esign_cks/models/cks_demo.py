import base64
import io

from odoo import _, api, models
from odoo.exceptions import AccessError, UserError

from ..tools import demo_pki

DMAG_MST = '0110085510'


class DmagCksTrustRootDemo(models.Model):
    _inherit = 'dmag.cks.trust.root'

    @api.model
    def _dmag_cks_install_demo(self):
        """Dữ liệu demo: Root CA giả lập, chứng thư DMAG, ký từ xa giả lập, khách mẫu, hợp đồng mẫu."""
        root = demo_pki.make_root()
        trust = self.create({
            'name': 'Root CA GIẢ LẬP (demo)', 'is_demo': True,
            'cert_file': base64.b64encode(root['cert_der']), 'demo_key_pem': root['key_pem'].decode()})
        company = self.env.company
        if company.name == 'YourCompany':  # công ty mặc định của DB demo Odoo
            company.name = 'Công ty TNHH DMAG (demo)'
        if not company.vat:
            company.vat = DMAG_MST
        dmag = demo_pki.issue(root, company.name.upper(), company.name.upper(), 'MST:%s' % company.vat)
        Provider = self.env['dmag.cks.provider']
        Provider.create({
            'name': 'CKS tổ chức DMAG (file demo)', 'sequence': 1, 'provider_type': 'local_cert',
            'use_for_company': True, 'use_for_customer': False,
            'p12_file': base64.b64encode(demo_pki.to_pkcs12(dmag, b'demo')), 'p12_password': 'demo'})
        Provider.create({
            'name': 'Ký từ xa GIẢ LẬP', 'sequence': 5, 'provider_type': 'demo_cloud',
            'use_for_customer': True, 'customer_label': 'Ứng dụng ký số (GIẢ LẬP – SmartCA/MySign demo)',
            'customer_hint': 'Demo: nhập mã số thuế 0109999999 hoặc CCCD 001090012345',
            'demo_delay': 4, 'demo_root_id': trust.id})
        Partner = self.env['res.partner']
        clinic = Partner.create({'name': 'Công ty TNHH Nha khoa C&N (demo)', 'is_company': True,
                                 'vat': '0109999999', 'email': 'cn.dental@example.com', 'city': 'Hà Nội'})
        Partner.create({'name': 'Nguyễn Văn A', 'parent_id': clinic.id, 'function': 'Giám đốc',
                        'email': 'giamdoc.cn@example.com'})
        doctor = Partner.create({'name': 'BS. Trần Thị B (demo)', 'dmag_cks_personal_id': '001090012345',
                                 'email': 'bs.tranthib@example.com'})
        Envelope = self.env['dmag.cks.envelope']
        for partner, kind, title in ((clinic, 'organization', 'Hợp đồng dịch vụ 1708A-2026-DMAG-HĐDV (demo)'),
                                     (doctor, 'individual', 'Hợp đồng đào tạo DME Club (demo)')):
            Envelope.create({
                'document_name': title, 'partner_id': partner.id,
                'source_pdf': base64.b64encode(self._dmag_cks_sample_pdf(title, partner.name)),
                'source_filename': '%s.pdf' % title,
                'signer_ids': [(0, 0, self.env['dmag.cks.signer']._customer_vals(partner, kind))],
            })
        return True

    @api.model
    def _dmag_cks_sample_pdf(self, title, customer):
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        from ..tools.confirm_page import _font

        regular, bold, fix = _font()
        buf = io.BytesIO()
        pdf = canvas.Canvas(buf, pagesize=A4)
        width, height = A4
        pdf.setFont(bold, 13)
        pdf.drawCentredString(width / 2, height - 70, fix('CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM'))
        pdf.setFont(regular, 11)
        pdf.drawCentredString(width / 2, height - 88, fix('Độc lập – Tự do – Hạnh phúc'))
        pdf.setFont(bold, 15)
        pdf.drawCentredString(width / 2, height - 130, fix(title.upper()))
        lines = [
            'Bên A: %s' % self.env.company.name,
            'Bên B: %s' % customer,
            '',
            'Điều 1. Bên A cung cấp cho Bên B gói dịch vụ quản trị và tăng trưởng phòng khám.',
            'Điều 2. Giá trị hợp đồng: 324.000.000 đồng, thanh toán 3 đợt 40% / 40% / 20%.',
            'Điều 3. Các bên thống nhất giao kết hợp đồng bằng phương tiện điện tử và ký bằng',
            '        chữ ký số theo Luật Giao dịch điện tử 2023.',
            '',
            '(Tài liệu mẫu phục vụ chạy thử, không có giá trị pháp lý.)',
        ]
        y = height - 180
        pdf.setFont(regular, 11)
        for line in lines:
            pdf.drawString(60, y, fix(line))
            y -= 20
        pdf.showPage()
        pdf.save()
        return buf.getvalue()

    @api.model
    def _dmag_cks_demo_issue(self, common_name, organization, uid):
        """Cấp chứng thư GIẢ LẬP (dùng cho USB token giả lập khi chạy thử). Chỉ quản trị viên."""
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Chỉ quản trị viên được cấp chứng thư giả lập.'))
        root = self.sudo().search([('is_demo', '=', True), ('demo_key_pem', '!=', False)], limit=1)
        if not root:
            raise UserError(_('Không có Root CA giả lập.'))
        issued = demo_pki.issue({'key_pem': root.demo_key_pem.encode(),
                                 'cert_der': base64.b64decode(root.cert_file)},
                                common_name, organization or None, uid)
        return {'key_pem': issued['key_pem'].decode(), 'cert_pem': issued['cert_pem'].decode()}
