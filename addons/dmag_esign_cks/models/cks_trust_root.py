import base64

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..tools import pades


class DmagCksTrustRoot(models.Model):
    """Root CA được tin cậy khi kiểm tra chữ ký.

    Ví dụ Root CA quốc gia, hoặc CA gốc của VNPT, Viettel, BKAV…
    """
    _name = 'dmag.cks.trust.root'
    _description = 'Root CA tin cậy'
    _order = 'name'

    name = fields.Char('Tên', required=True)
    active = fields.Boolean(default=True)
    cert_file = fields.Binary('Chứng thư (.cer/.crt/.pem)', required=True, attachment=False)
    subject = fields.Char('Subject', compute='_compute_info', store=True)
    valid_to = fields.Datetime('Hết hạn', compute='_compute_info', store=True)
    is_demo = fields.Boolean('Giả lập', help='Root CA giả lập chỉ dùng để demo, không có giá trị pháp lý.')
    demo_key_pem = fields.Text('Khóa Root giả lập', groups='base.group_system')

    @api.depends('cert_file')
    def _compute_info(self):
        for root in self:
            root.subject = False
            root.valid_to = False
            if root.cert_file:
                try:
                    identity = pades.cert_identity(base64.b64decode(root.cert_file))
                except Exception:  # noqa: BLE001 – báo lỗi ở constraint
                    continue
                root.subject = identity['subject']
                root.valid_to = identity['not_after'].replace(tzinfo=None)

    @api.constrains('cert_file')
    def _check_cert(self):
        for root in self:
            try:
                pades.load_cert(base64.b64decode(root.cert_file))
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(_('File không phải chứng thư X.509 hợp lệ (DER hoặc PEM).')) from exc

    @api.model
    def _get_der_list(self):
        return [pades.load_cert(base64.b64decode(root.cert_file)).dump() for root in self.sudo().search([])]
