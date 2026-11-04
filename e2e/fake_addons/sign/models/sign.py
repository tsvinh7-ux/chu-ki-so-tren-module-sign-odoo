"""Mô phỏng tối thiểu Odoo Sign 17: cùng tên model, tên trường và tên hàm chính.

- Ô ký: page tính từ 1; posX, posY, width, height là tỉ lệ 0..1, gốc tọa độ góc trên-trái trang.
- sign.request.item._sign(signature) nhận {sign_item_id: value}. Chữ ký/chữ viết tắt là ảnh base64
  (có hoặc không có tiền tố data:image/...). Khi mọi người đã ký: sign.request._sign() rồi
  _generate_completed_document() vẽ toàn bộ giá trị lên file gốc.
"""
import base64
import io
import uuid

from odoo import _, fields, models
from odoo.exceptions import UserError


class SignItemRole(models.Model):
    _name = 'sign.item.role'
    _description = 'Vai trò ký'

    name = fields.Char(required=True)


class SignItemType(models.Model):
    _name = 'sign.item.type'
    _description = 'Loại ô ký'

    name = fields.Char(required=True)
    item_type = fields.Selection([('signature', 'Chữ ký'), ('initial', 'Chữ ký tắt'), ('text', 'Chữ')],
                                 required=True, default='text')


class SignTemplate(models.Model):
    _name = 'sign.template'
    _description = 'Mẫu ký'

    name = fields.Char(required=True)
    attachment_id = fields.Many2one('ir.attachment', required=True)
    sign_item_ids = fields.One2many('sign.item', 'template_id')


class SignItem(models.Model):
    _name = 'sign.item'
    _description = 'Ô ký'
    _order = 'page, posY, posX, id'

    template_id = fields.Many2one('sign.template', required=True, ondelete='cascade')
    type_id = fields.Many2one('sign.item.type', required=True)
    responsible_id = fields.Many2one('sign.item.role')
    name = fields.Char()
    required = fields.Boolean(default=True)
    page = fields.Integer(default=1)
    posX = fields.Float(digits=(4, 3))
    posY = fields.Float(digits=(4, 3))
    width = fields.Float(digits=(4, 3))
    height = fields.Float(digits=(4, 3))


class SignRequest(models.Model):
    _name = 'sign.request'
    _inherit = ['mail.thread']
    _description = 'Yêu cầu ký'

    template_id = fields.Many2one('sign.template', required=True)
    reference = fields.Char(required=True)
    state = fields.Selection([('shared', 'Shared'), ('sent', 'To Sign'), ('signed', 'Fully Signed'),
                              ('refused', 'Refused'), ('canceled', 'Cancelled'), ('expired', 'Expired')],
                             default='sent', tracking=True)
    completed_document = fields.Binary(attachment=True, readonly=True)
    request_item_ids = fields.One2many('sign.request.item', 'sign_request_id')

    def _sign(self):
        self.ensure_one()
        self.state = 'signed'
        self._generate_completed_document()
        self.message_post(body=_('Mọi bên đã ký.'))

    def _generate_completed_document(self, password=''):
        """Vẽ mọi giá trị đã ký lên file gốc (giống Odoo Sign: tạo file MỚI ở cuối)."""
        from odoo.tools.pdf import OdooPdfFileReader, OdooPdfFileWriter
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas

        self.ensure_one()
        source = base64.b64decode(self.template_id.attachment_id.datas)
        reader = OdooPdfFileReader(io.BytesIO(source), strict=False)
        values = self.request_item_ids.sign_item_value_ids
        writer = OdooPdfFileWriter()
        for index in range(reader.getNumPages()):
            page = reader.getPage(index)
            width, height = float(page.mediaBox.getWidth()), float(page.mediaBox.getHeight())
            page_values = values.filtered(lambda v: v.sign_item_id.page == index + 1)
            if page_values:
                buf = io.BytesIO()
                pdf = canvas.Canvas(buf, pagesize=(width, height))
                for value in page_values:
                    item = value.sign_item_id
                    x, w = item.posX * width, item.width * width
                    h = item.height * height
                    y = height - item.posY * height - h
                    if item.type_id.item_type in ('signature', 'initial'):
                        data = value.value.split(',', 1)[-1]
                        pdf.drawImage(ImageReader(io.BytesIO(base64.b64decode(data))), x, y, w, h,
                                      mask='auto', preserveAspectRatio=True, anchor='c')
                    else:
                        pdf.setFont('Helvetica', min(11, h * 0.6))
                        pdf.drawString(x + 1, y + h * 0.25, value.value or '')
                pdf.showPage()
                pdf.save()
                page.mergePage(OdooPdfFileReader(io.BytesIO(buf.getvalue()), strict=False).getPage(0))
            writer.addPage(page)
        out = io.BytesIO()
        writer.write(out)
        self.completed_document = base64.b64encode(out.getvalue())


class SignRequestItem(models.Model):
    _name = 'sign.request.item'
    _description = 'Người ký'
    _order = 'mail_sent_order, id'

    sign_request_id = fields.Many2one('sign.request', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', required=True)
    role_id = fields.Many2one('sign.item.role', required=True)
    state = fields.Selection([('sent', 'To Sign'), ('completed', 'Completed')], default='sent')
    access_token = fields.Char(default=lambda self: str(uuid.uuid4()), copy=False)
    signing_date = fields.Datetime(readonly=True)
    mail_sent_order = fields.Integer(default=1, help='Thứ tự ký. Cùng số = ký song song (ai trước cũng được).')
    sign_item_value_ids = fields.One2many('sign.request.item.value', 'sign_request_item_id')

    def _get_sign_items(self):
        self.ensure_one()
        return self.sign_request_id.template_id.sign_item_ids.filtered(lambda i: i.responsible_id == self.role_id)

    def _is_turn(self):
        """Đến lượt chưa: các người ký có thứ tự nhỏ hơn phải ký xong trước."""
        self.ensure_one()
        earlier = self.sign_request_id.request_item_ids.filtered(
            lambda r: r.mail_sent_order < self.mail_sent_order and r.state != 'completed')
        return not earlier

    def _get_url(self):
        return '/sign/document/%s/%s' % (self.sign_request_id.id, self.access_token)

    def _sign(self, signature, **kwargs):
        self.ensure_one()
        if self.state != 'sent' or self.sign_request_id.state != 'sent':
            raise UserError(_('Tài liệu này đã được ký hoặc không còn chờ ký.'))
        if not self._is_turn():
            raise UserError(_('Chưa đến lượt bạn ký.'))
        items = self._get_sign_items()
        signature = {int(k): v for k, v in (signature or {}).items()}
        missing = items.filtered(lambda i: i.required and not signature.get(i.id))
        if missing:
            raise UserError(_('Bạn chưa điền đủ các ô bắt buộc.'))
        values = []
        for item in items:
            value = signature.get(item.id)
            if value and item.type_id.item_type in ('signature', 'initial'):
                value = value.split(',', 1)[-1]
            if value:
                values.append({'sign_item_id': item.id, 'value': value})
        self.write({'state': 'completed', 'signing_date': fields.Datetime.now(),
                    'sign_item_value_ids': [(0, 0, v) for v in values]})
        if all(r.state == 'completed' for r in self.sign_request_id.request_item_ids):
            self.sign_request_id._sign()
        return True


class SignRequestItemValue(models.Model):
    _name = 'sign.request.item.value'
    _description = 'Giá trị đã ký'

    sign_request_item_id = fields.Many2one('sign.request.item', required=True, ondelete='cascade')
    sign_item_id = fields.Many2one('sign.item', required=True)
    value = fields.Text()
