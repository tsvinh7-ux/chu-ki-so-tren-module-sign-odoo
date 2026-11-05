{
    'name': 'DMAG – Ký số USB token / MySign ngay trong module Sign',
    'summary': 'Thêm tab "USB token" và "App ký số" (Viettel MySign…) vào hộp chọn chữ ký của module Sign '
               'kiểu Odoo Sign, không cần mã nguồn của module Sign',
    'description': """
Cầu nối giữa dmag_esign_cks và module Sign có cấu trúc như Odoo Sign 17 (ví dụ bản của Bizapps).

Addon không phụ thuộc tên kỹ thuật của module Sign và không sửa mã nguồn của nó:

* Tên model, trường, hàm, đường dẫn trang ký nằm trong cấu hình (mặc định theo Odoo Sign 17),
  bấm "Kiểm tra" để thấy chỗ nào khớp.
* Khi bật, addon gắn thêm bước ký số vào hàm "ký" và hàm "tạo file hoàn tất" của module Sign
  lúc Odoo khởi động (cùng cách module Tự động hóa của Odoo dùng).
* Trên trang ký, hộp chọn chữ ký có thêm lời nhắc, tab "USB token" và "App ký số". Khi người ký
  bấm hoàn tất, addon ký số trước rồi mới cho dữ liệu đi tiếp tới module Sign.
* Không khớp (trang khác, hộp ký khác): các tab tự ẩn, module Sign chạy như cũ.
""",
    'version': '17.0.2.0.0',
    'category': 'Productivity/Documents',
    'author': 'DMAG – Dental Management & Analytics Group',
    'website': 'https://dmagroup.vn',
    'license': 'LGPL-3',
    'depends': ['dmag_esign_cks'],
    'data': [
        'security/ir.model.access.csv',
        'data/sign_config_data.xml',
        'views/sign_config_views.xml',
        'views/sign_doc_views.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'dmag_esign_cks_sign/static/src/cks_store.js',
            'dmag_esign_cks_sign/static/src/name_and_signature_patch.js',
            'dmag_esign_cks_sign/static/src/name_and_signature_patch.xml',
            'dmag_esign_cks_sign/static/src/submit_interceptor.js',
            'dmag_esign_cks_sign/static/src/cks_sign.scss',
        ],
    },
    'installable': True,
}
