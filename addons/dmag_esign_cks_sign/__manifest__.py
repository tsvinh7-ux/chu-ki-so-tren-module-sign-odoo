{
    'name': 'DMAG – Ký số USB token ngay trong Odoo Sign',
    'summary': 'Thêm tab "USB token" vào hộp chọn chữ ký của Odoo Sign: người ký chọn chứng thư, nhập PIN, '
               'chữ ký số PAdES được ghi vào hợp đồng ngay lúc ký. Ai ký trước cũng được.',
    'description': """
Cầu nối giữa dmag_esign_cks và module Sign (Odoo Sign 17 hoặc bản Bizapps làm giống Odoo Sign).

* Hộp chọn chữ ký có thêm lời nhắc và tab "USB token". Các cách ký vẽ / gõ / tải ảnh vẫn giữ.
* Người ký chọn USB token: Odoo ghi các ô người đó điền và chữ ký số vào bản PDF hiện tại, rồi chốt lại.
* Người ký sau (ký số hay ký thường) được ghi thêm thành lớp mới, không làm hỏng chữ ký số trước.
* Khi mọi bên ký xong, file hoàn tất là bản đã chốt (không vẽ lại), Odoo tự kiểm tra chữ ký.
* Vai trò có thể đặt "Bắt buộc ký số".

Phụ thuộc module có tên kỹ thuật ``sign``. Nếu module của Bizapps tên khác, sửa ``depends`` cho khớp.
""",
    'version': '17.0.1.0.0',
    'category': 'Productivity/Documents',
    'author': 'DMAG – Dental Management & Analytics Group',
    'website': 'https://dmagroup.vn',
    'license': 'LGPL-3',
    'depends': ['dmag_esign_cks', 'sign'],
    'data': [
        'security/ir.model.access.csv',
        'views/sign_views.xml',
        'views/sign_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'dmag_esign_cks_sign/static/src/cks_store.js',
            'dmag_esign_cks_sign/static/src/name_and_signature_patch.js',
            'dmag_esign_cks_sign/static/src/name_and_signature_patch.xml',
            'dmag_esign_cks_sign/static/src/sign_page_patch.js',
            'dmag_esign_cks_sign/static/src/cks_sign.scss',
        ],
    },
    'installable': True,
}
