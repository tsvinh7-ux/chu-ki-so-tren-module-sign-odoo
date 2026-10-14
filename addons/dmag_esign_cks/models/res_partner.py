from odoo import fields, models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    dmag_cks_personal_id = fields.Char(
        'Số CCCD (ký số)', help='Dùng để đối chiếu với chứng thư số cá nhân khi người này ký hợp đồng.')
    dmag_cks_require = fields.Boolean(
        'Luôn yêu cầu ký số', help='Khách này luôn ký bằng chữ ký số (USB token hoặc app), '
                                   'không chỉ ký điện tử trên Odoo.')
