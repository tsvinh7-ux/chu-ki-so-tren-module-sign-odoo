import base64
import logging
from datetime import timedelta

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DmagCksSignLink(models.Model):
    """Nối addon ký số với module ký sẵn có (Odoo Sign, hoặc bản Bizapps tùy biến).

    Không sửa module ký. Addon đọc các yêu cầu ký đã hoàn tất theo tên model và
    tên trường cấu hình ở đây, rồi tạo hồ sơ ký số từ file PDF đã ký. Mặc định
    theo Odoo Sign: ``sign.request``, ``state = 'signed'``, ``completed_document``.
    """
    _name = 'dmag.cks.sign.link'
    _description = 'Kết nối module ký'

    name = fields.Char('Tên', required=True, default='Module Sign')
    active = fields.Boolean(default=True)
    model_name = fields.Char('Model yêu cầu ký', required=True, default='sign.request')
    state_field = fields.Char('Trường trạng thái', required=True, default='state')
    done_value = fields.Char('Giá trị "đã ký xong"', required=True, default='signed')
    document_field = fields.Char('Trường file PDF đã ký', required=True, default='completed_document')
    name_field = fields.Char('Trường tên tài liệu', default='reference')
    partner_field = fields.Char(
        'Đường dẫn tới người ký', required=True, default='request_item_ids.partner_id',
        help='Odoo sẽ chọn người ký đầu tiên không phải người của công ty mình làm khách hàng.')
    mode = fields.Selection([
        ('auto', 'Tự động khi module ký hoàn tất'),
        ('manual', 'Chỉ khi bấm "Gửi ký số (CKS)"'),
    ], string='Cách gửi', default='auto', required=True)
    require_customer = fields.Selection([
        ('always', 'Khách luôn ký số (USB token / app)'),
        ('partner', 'Theo khách hàng (ô "Luôn yêu cầu ký số")'),
        ('never', 'Khách không ký số, chỉ DMAG ký'),
    ], string='Khách ký số', default='always', required=True)
    days_back = fields.Integer('Chỉ lấy yêu cầu hoàn tất trong (ngày)', default=30)
    server_action_id = fields.Many2one('ir.actions.server', 'Nút "Gửi ký số (CKS)"', readonly=True, copy=False)
    last_run = fields.Datetime('Lần quét gần nhất', readonly=True)
    last_message = fields.Char('Kết quả gần nhất', readonly=True)

    # ------------------------------------------------------------------ cấu hình
    def _check_config(self):
        self.ensure_one()
        if self.model_name not in self.env:
            raise UserError(_('Không tìm thấy model "%s". Kiểm tra module ký đã cài và đúng tên model.',
                              self.model_name))
        model_fields = self.env[self.model_name]._fields
        for label, name in ((_('trạng thái'), self.state_field), (_('file PDF'), self.document_field)):
            if name not in model_fields:
                raise UserError(_('Model %(m)s không có trường %(f)s (%(l)s).', m=self.model_name, f=name, l=label))
        if self.name_field and self.name_field not in model_fields:
            raise UserError(_('Model %(m)s không có trường %(f)s.', m=self.model_name, f=self.name_field))
        first = self.partner_field.split('.')[0]
        if first not in model_fields:
            raise UserError(_('Model %(m)s không có trường %(f)s.', m=self.model_name, f=first))
        return True

    def action_activate(self):
        """Kiểm tra cấu hình, bật kết nối và gắn nút "Gửi ký số (CKS)" vào menu Thao tác của module ký."""
        for link in self:
            link._check_config()
            link.active = True
            model = self.env['ir.model']._get(link.model_name)
            vals = {
                'name': _('Gửi ký số (CKS)'),
                'model_id': model.id, 'binding_model_id': model.id, 'binding_view_types': 'form,list',
                'state': 'code',
                'code': "action = env['dmag.cks.sign.link'].browse(%d)._send_records(records)" % link.id,
                'groups_id': [(6, 0, [self.env.ref('dmag_esign_cks.group_cks_user').id])],
            }
            if link.server_action_id:
                link.server_action_id.write(vals)
            else:
                link.server_action_id = self.env['ir.actions.server'].sudo().create(vals)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': _('Đã kết nối'),
            'message': _('Module ký có nút "Gửi ký số (CKS)" trong menu Thao tác.')}}

    def action_deactivate(self):
        self.mapped('server_action_id').sudo().unlink()
        self.write({'active': False})

    def unlink(self):
        self.mapped('server_action_id').sudo().unlink()
        return super().unlink()

    # ------------------------------------------------------------------ đọc bản ghi của module ký
    def _record_pdf(self, record):
        value = record[self.document_field]
        if isinstance(value, models.BaseModel):  # Many2one / Many2many tới ir.attachment
            value = value[:1].datas if value._name == 'ir.attachment' else False
        return base64.b64decode(value) if value else b''

    def _record_partner(self, record):
        """Khách hàng: người ký đầu tiên không thuộc công ty mình."""
        partners = record.mapped(self.partner_field)
        internal = self.env['res.users'].sudo().with_context(active_test=False).search(
            [('share', '=', False)]).partner_id
        companies = self.env['res.company'].sudo().search([]).partner_id
        customers = partners.filtered(lambda p: p not in internal and p.commercial_partner_id not in companies)
        return customers[:1]

    def _record_done(self, record):
        return str(record[self.state_field]) == self.done_value

    def _send_records(self, records, raise_errors=True):
        """Tạo hồ sơ ký số cho các yêu cầu ký đã hoàn tất. Trả về thông báo cho người dùng."""
        self.ensure_one()
        self._check_config()
        Envelope = self.env['dmag.cks.envelope']
        sent, skipped = Envelope, []
        for record in records:
            existing = Envelope.search([('res_model', '=', record._name), ('res_id', '=', record.id),
                                        ('state', '!=', 'cancel')], limit=1)
            if existing:
                skipped.append(_('%s: đã có hồ sơ %s', record.display_name, existing.name))
                continue
            problem = None
            if not self._record_done(record):
                problem = _('%s: chưa ký xong trên module ký', record.display_name)
            pdf = self._record_pdf(record) if not problem else b''
            partner = self._record_partner(record) if not problem else False
            if not problem and not pdf:
                problem = _('%s: chưa có file PDF đã ký', record.display_name)
            if not problem and not partner:
                problem = _('%s: không tìm thấy khách hàng trong danh sách người ký', record.display_name)
            if problem:
                if raise_errors and len(records) == 1:
                    raise UserError(problem)
                skipped.append(problem)
                continue
            require = {'always': True, 'never': False}.get(self.require_customer, partner.dmag_cks_require)
            name = (self.name_field and record[self.name_field]) or record.display_name
            try:
                with self.env.cr.savepoint():
                    envelope = Envelope._dmag_cks_create(pdf, name, partner, record=record,
                                                         require_customer=require)
            except UserError as exc:
                if raise_errors and len(records) == 1:
                    raise
                skipped.append('%s: %s' % (record.display_name, exc))
                continue
            sent |= envelope
            if hasattr(record, 'message_post'):
                record.message_post(body=Markup(_('Đã tạo hồ sơ ký số <a href="#" data-oe-model="%s" '
                                                  'data-oe-id="%s">%s</a>.')) % (Envelope._name, envelope.id,
                                                                              envelope.name))
        message = _('Đã tạo %s hồ sơ ký số.', len(sent))
        if skipped:
            message += ' ' + _('Bỏ qua: %s', '; '.join(skipped))
        self.write({'last_run': fields.Datetime.now(), 'last_message': message[:250]})
        if len(sent) == 1:
            return {'type': 'ir.actions.act_window', 'res_model': Envelope._name, 'res_id': sent.id,
                    'view_mode': 'form', 'views': [(False, 'form')]}
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success' if sent else 'warning', 'title': _('Gửi ký số'), 'message': message,
            'sticky': bool(skipped)}}

    @api.model
    def _cron_pull(self):
        """Tự động tạo hồ sơ ký số cho yêu cầu ký vừa hoàn tất."""
        for link in self.search([('mode', '=', 'auto')]):
            if link.model_name not in self.env:
                continue
            Model = self.env[link.model_name].sudo()
            since = fields.Datetime.now() - timedelta(days=link.days_back or 30)
            records = Model.search([(link.state_field, '=', link.done_value), ('write_date', '>=', since)],
                                   order='id', limit=200)
            linked = set(self.env['dmag.cks.envelope'].sudo().search([
                ('res_model', '=', link.model_name), ('res_id', 'in', records.ids)]).mapped('res_id'))
            todo = records.filtered(lambda r: r.id not in linked)[:20]
            if not todo:
                continue
            try:
                with self.env.cr.savepoint():
                    link.sudo()._send_records(todo, raise_errors=False)
            except Exception:  # noqa: BLE001 – lỗi cấu hình một kết nối không chặn kết nối khác
                _logger.exception('CKS: lỗi tạo hồ sơ ký số từ %s', link.model_name)
