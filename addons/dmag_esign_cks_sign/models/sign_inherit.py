import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SignItemRole(models.Model):
    _inherit = 'sign.item.role'

    dmag_cks_required = fields.Boolean(
        'Bắt buộc ký số', help='Người ký ở vai trò này chỉ được ký bằng USB token / app ký số, '
                               'không được vẽ, gõ tên hay tải ảnh chữ ký.')


class SignRequestItem(models.Model):
    _inherit = 'sign.request.item'

    def _sign(self, signature, **kwargs):
        """Ký thường (vẽ / gõ / tải ảnh). Nếu tài liệu đã có chữ ký số thì ghi thêm thành lớp mới."""
        if not self.env.context.get('dmag_cks_signing'):
            for request_item in self:
                if request_item.role_id.dmag_cks_required:
                    raise UserError(_('Vai trò "%s" phải ký bằng chữ ký số (USB token).', request_item.role_id.name))
                doc = self.env['dmag.cks.sign.doc'].sudo().search(
                    [('request_id', '=', request_item.sign_request_id.id)], limit=1)
                if doc.cks_count:
                    doc = doc._for_request(request_item.sign_request_id, lock=True)
                    doc._append_values(request_item, signature)
        return super()._sign(signature, **kwargs)


class SignRequest(models.Model):
    _inherit = 'sign.request'

    dmag_cks_doc_id = fields.Many2one('dmag.cks.sign.doc', compute='_compute_dmag_cks', string='Tài liệu ký số')
    dmag_cks_summary = fields.Char('Ký số', compute='_compute_dmag_cks')
    dmag_cks_verify_html = fields.Html(related='dmag_cks_doc_id.verify_html', string='Kết quả kiểm tra')

    @api.depends('request_item_ids.state')
    def _compute_dmag_cks(self):
        docs = self.env['dmag.cks.sign.doc'].sudo().search([('request_id', 'in', self.ids)])
        by_request = {doc.request_id.id: doc for doc in docs}
        for sign_request in self:
            doc = by_request.get(sign_request.id, docs.browse())
            sign_request.dmag_cks_doc_id = doc
            total = len(sign_request.request_item_ids)
            sign_request.dmag_cks_summary = _('Ký số: %(n)s/%(t)s bên', n=doc.cks_count, t=total) if total else ''

    def _generate_completed_document(self, *args, **kwargs):
        """Đã có chữ ký số: file hoàn tất là bản đã chốt, không vẽ lại (vẽ lại sẽ làm mất chữ ký số)."""
        for sign_request in self:
            doc = self.env['dmag.cks.sign.doc'].sudo().search([('request_id', '=', sign_request.id)], limit=1)
            if doc.cks_count and doc.current_pdf:
                sign_request.completed_document = doc.current_pdf
                results = doc._verify()
                sign_request.message_post(body=_(
                    'Tài liệu hoàn tất có %(n)s chữ ký số; kiểm tra: %(r)s.', n=len(results),
                    r=_('hợp lệ') if doc.verify_ok else _('cần xem lại')))
            else:
                super(SignRequest, sign_request)._generate_completed_document(*args, **kwargs)

    def action_dmag_cks_download(self):
        self.ensure_one()
        if not self.completed_document:
            raise UserError(_('Tài liệu chưa hoàn tất.'))
        attachment = self.env['ir.attachment'].create({
            'name': '%s - da ky so.pdf' % self.reference, 'datas': self.completed_document,
            'mimetype': 'application/pdf', 'res_model': self._name, 'res_id': self.id})
        return {'type': 'ir.actions.act_url', 'url': '/web/content/%s?download=true' % attachment.id}


class SignRequestItemCksConfig(models.Model):
    _inherit = 'sign.request.item'

    def _dmag_cks_frontend_config(self):
        self.ensure_one()
        param = self.env['ir.config_parameter'].sudo()
        return json.dumps({
            'enabled': True,
            'required': bool(self.role_id.dmag_cks_required),
            'requestId': self.sign_request_id.id,
            'token': self.access_token,
            'agentUrl': 'http://127.0.0.1:19876',
            'agentDownload': param.get_param('dmag_esign_cks.agent_download_url',
                                             '/dmag_esign_cks/static/agent/DMAGKySo.exe'),
        })
