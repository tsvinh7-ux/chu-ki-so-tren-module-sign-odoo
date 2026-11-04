{
    'name': 'DMAG – Module ký GIẢ LẬP (chỉ để kiểm thử)',
    'summary': 'Mô phỏng model sign.request của Odoo Sign để test kết nối của dmag_esign_cks. KHÔNG cài lên hệ thống thật.',
    'version': '17.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['dmag_esign_cks'],
    'data': ['security/ir.model.access.csv'],
    'installable': True,
}
