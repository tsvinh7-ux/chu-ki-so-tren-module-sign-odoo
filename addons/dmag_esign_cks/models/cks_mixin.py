from odoo import _, api, fields, models


class DmagCksMixin(models.AbstractModel):
    """Gắn chức năng ký số vào bất kỳ model nào (hợp đồng, yêu cầu ký của module Sign…).

    Model kế thừa có thể định nghĩa ``_dmag_cks_on_done(envelope)`` để nhận bản PDF
    đã ký số khi hoàn tất, ví dụ chuyển hợp đồng sang "Đã ký" và gắn file.
    """
    _name = 'dmag.cks.mixin'
    _description = 'Mixin ký số'

    dmag_cks_envelope_ids = fields.One2many(
        'dmag.cks.envelope', 'res_id', string='Hồ sơ ký số',
        domain=lambda self: [('res_model', '=', self._name)])
    dmag_cks_state = fields.Selection(
        [('none', 'Chưa ký số'), ('progress', 'Đang ký số'), ('done', 'Đã ký số')],
        string='Ký số', compute='_compute_dmag_cks_state')

    @api.depends('dmag_cks_envelope_ids.state')
    def _compute_dmag_cks_state(self):
        for record in self:
            states = set(record.dmag_cks_envelope_ids.mapped('state'))
            record.dmag_cks_state = 'done' if 'done' in states else (
                'progress' if states & {'customer', 'company'} else 'none')

    def _dmag_cks_send(self, pdf, document_name=None, partner=None, require_customer=True, signer_kind=None):
        """Tạo và gửi hồ sơ ký số cho bản ghi này. Trả về ``dmag.cks.envelope``."""
        self.ensure_one()
        return self.env['dmag.cks.envelope']._dmag_cks_create(
            pdf, document_name or self.display_name, partner or self.partner_id, record=self,
            require_customer=require_customer, signer_kind=signer_kind)

    def action_dmag_cks_open(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window', 'name': _('Hồ sơ ký số'), 'res_model': 'dmag.cks.envelope',
            'view_mode': 'tree,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
            'context': {'default_res_model': self._name, 'default_res_id': self.id},
        }
