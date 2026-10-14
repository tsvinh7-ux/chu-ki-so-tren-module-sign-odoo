{
    'name': 'DMAG – Chữ ký số cho hợp đồng (USB token & ký từ xa)',
    'summary': 'Gắn chữ ký số công cộng của CA Việt Nam (PAdES) vào tài liệu đã ký trên Odoo: '
               'khách ký bằng USB token mọi nhà cung cấp hoặc app điện thoại, DMAG tự ký CKS tổ chức',
    'description': """
Addon tích hợp, không sửa module ký sẵn có. Sau khi tài liệu được ký xong trên
module ký của Odoo, addon thêm bước ký số:

* Khách ký bằng **USB token của bất kỳ nhà cung cấp nào**, qua phần mềm DMAG Ký số.
* Khách ký bằng **app ký số từ xa** (VNPT SmartCA, Viettel MySign…).
* **DMAG tự động ký** bằng chứng thư số tổ chức, có thể kèm dấu thời gian.
* **Kiểm tra** chữ ký: toàn vẹn, chuỗi tin cậy, và MST/CCCD trong chứng thư khớp với khách hàng.
""",
    'version': '17.0.1.0.0',
    'category': 'Productivity/Documents',
    'author': 'DMAG – Dental Management & Analytics Group',
    'website': 'https://dmagroup.vn',
    'license': 'LGPL-3',
    'depends': ['mail', 'portal'],
    'external_dependencies': {'python': ['pyhanko']},
    'data': [
        'security/cks_security.xml',
        'security/ir.model.access.csv',
        'data/ir_sequence_data.xml',
        'data/ir_cron_data.xml',
        'data/mail_template_data.xml',
        'data/cks_provider_data.xml',
        'views/cks_provider_views.xml',
        'views/cks_trust_root_views.xml',
        'views/cks_envelope_views.xml',
        'views/res_partner_views.xml',
        'views/cks_portal_templates.xml',
        'wizard/cks_send_wizard_views.xml',
        'views/cks_menus.xml',
    ],
    'demo': [
        'demo/cks_demo.xml',
    ],
    'installable': True,
    'application': True,
}
