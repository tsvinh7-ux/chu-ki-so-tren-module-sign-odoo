from odoo import fields, models


class SignRequest(models.Model):
    """Mô phỏng tối thiểu sign.request của Odoo Sign 17 (tên model và các trường chính)."""
    _name = 'sign.request'
    _inherit = ['mail.thread']
    _description = 'Yêu cầu ký (giả lập)'

    reference = fields.Char(required=True)
    state = fields.Selection([('shared', 'Shared'), ('sent', 'To Sign'), ('signed', 'Fully Signed'),
                              ('refused', 'Refused'), ('canceled', 'Cancelled'), ('expired', 'Expired')],
                             default='sent')
    completed_document = fields.Binary(attachment=True)
    request_item_ids = fields.One2many('sign.request.item', 'sign_request_id')


class SignRequestItem(models.Model):
    _name = 'sign.request.item'
    _description = 'Người ký (giả lập)'

    sign_request_id = fields.Many2one('sign.request', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner')
