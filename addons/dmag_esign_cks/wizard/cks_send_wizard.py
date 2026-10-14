import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class DmagCksSendWizard(models.TransientModel):
    """Gửi một file PDF (hoặc tệp đính kèm của bản ghi) đi ký số."""
    _name = 'dmag.cks.send.wizard'
    _description = 'Gửi ký số'

    res_model = fields.Char()
    res_id = fields.Integer()
    attachment_id = fields.Many2one(
        'ir.attachment', 'Tệp PDF của bản ghi',
        domain="[('res_model', '=', res_model), ('res_id', '=', res_id), ('mimetype', '=', 'application/pdf')]")
    pdf_file = fields.Binary('Hoặc tải lên PDF')
    pdf_filename = fields.Char()
    document_name = fields.Char('Tên tài liệu', required=True)
    partner_id = fields.Many2one('res.partner', 'Khách hàng', required=True)
    require_customer = fields.Boolean(
        'Khách ký bằng chữ ký số', default=True,
        help='Bỏ chọn nếu khách đã ký điện tử trên Odoo và chỉ cần DMAG đóng chữ ký số tổ chức.')
    signer_kind = fields.Selection([
        ('organization', 'Tổ chức – CKS công ty'),
        ('individual', 'Cá nhân – CKS cá nhân'),
        ('any', 'Tổ chức hoặc cá nhân'),
    ], string='Loại chữ ký của khách', default='any', required=True)

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        model, rec_id = self.env.context.get('active_model'), self.env.context.get('active_id')
        if model and rec_id and model != 'dmag.cks.envelope' and model in self.env:
            record = self.env[model].browse(rec_id).exists()
            if record:
                vals.update(res_model=model, res_id=rec_id, document_name=record.display_name)
                partner = getattr(record, 'partner_id', False)
                if partner:
                    vals['partner_id'] = partner.id
                    vals['require_customer'] = partner.dmag_cks_require or vals.get('require_customer', True)
        return vals

    @api.onchange('pdf_filename')
    def _onchange_filename(self):
        if self.pdf_filename and not self.document_name:
            self.document_name = self.pdf_filename.rsplit('.', 1)[0]

    def action_send(self):
        self.ensure_one()
        if self.pdf_file:
            pdf = base64.b64decode(self.pdf_file)
        elif self.attachment_id:
            pdf = base64.b64decode(self.attachment_id.datas)
        else:
            raise UserError(_('Chọn tệp PDF của bản ghi hoặc tải lên một file PDF.'))
        record = self.env[self.res_model].browse(self.res_id) if self.res_model and self.res_id else None
        envelope = self.env['dmag.cks.envelope']._dmag_cks_create(
            pdf, self.document_name, self.partner_id, record=record,
            require_customer=self.require_customer, signer_kind=self.signer_kind)
        return {'type': 'ir.actions.act_window', 'res_model': 'dmag.cks.envelope',
                'res_id': envelope.id, 'view_mode': 'form'}
