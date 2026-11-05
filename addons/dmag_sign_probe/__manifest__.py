{
    'name': 'DMAG – Kiểm tra tương thích module Sign (chỉ đọc)',
    'summary': 'Kiểm tra module Sign đang cài có khớp với addon ký số DMAG không. Chỉ đọc thông tin cấu trúc '
               '(tên module, model, trường, hàm, đường dẫn), không đọc mã nguồn, không sửa dữ liệu.',
    'version': '17.0.1.0.0',
    'category': 'Hidden/Tools',
    'author': 'DMAG – Dental Management & Analytics Group',
    'license': 'LGPL-3',
    'depends': ['base', 'web'],
    'data': ['security/ir.model.access.csv', 'views/probe_views.xml'],
    'installable': True,
}
