{
    'name': 'Sign – bản GIẢ LẬP theo cấu trúc Odoo 17 (chỉ để kiểm thử)',
    'summary': 'Mô phỏng tối thiểu Odoo Sign 17 (model, trang ký, hộp chọn chữ ký) để phát triển và test '
               'dmag_esign_cks_sign khi chưa có mã nguồn module Sign của Bizapps. KHÔNG cài lên hệ thống thật.',
    'version': '17.0.1.0.0',
    'license': 'LGPL-3',
    'depends': ['mail', 'portal'],
    'data': [
        'security/ir.model.access.csv',
        'views/sign_views.xml',
        'views/sign_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'sign/static/src/signing_page.js',
            'sign/static/src/signing_page.xml',
            'sign/static/src/signing_page.scss',
        ],
    },
    'installable': True,
}
