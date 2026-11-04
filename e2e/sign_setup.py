# Chạy trong `odoo shell` (DB có dmag_esign_cks, sign giả lập, dmag_esign_cks_sign và dữ liệu demo):
# tạo mẫu ký 2 vai trò (Khách hàng, DMAG), một yêu cầu ký (ai ký trước cũng được),
# cấp 2 chứng thư "USB token giả lập" và in ra link ký của từng người.
import base64
import json

T = env['dmag.cks.trust.root']
company = env.company
clinic = env['res.partner'].search([('vat', '=', '0109999999')], limit=1)
staff = env.ref('base.user_admin')
tokens = {
    'customer': T._dmag_cks_demo_issue('NGUYEN VAN A', 'CONG TY TNHH NHA KHOA C&N (DEMO)', 'MST:0109999999'),
    'dmag': T._dmag_cks_demo_issue(company.name.upper(), company.name.upper(), 'MST:%s' % company.vat),
}
for who, issued in tokens.items():
    open('/tmp/e2e/%s_cert.pem' % who, 'w').write(issued['cert_pem'])
    open('/tmp/e2e/%s_key.pem' % who, 'w').write(issued['key_pem'])

Role, Type, Item = env['sign.item.role'], env['sign.item.type'], env['sign.item']
role_c, role_d = Role.create({'name': 'Khách hàng'}), Role.create({'name': 'DMAG'})
t_sig = Type.create({'name': 'Chữ ký', 'item_type': 'signature'})
t_text = Type.create({'name': 'Họ tên', 'item_type': 'text'})
pdf = T._dmag_cks_sample_pdf('Hợp đồng dịch vụ 1708A (ký trên Odoo Sign)', clinic.name)
attachment = env['ir.attachment'].create({'name': 'hop-dong.pdf', 'datas': base64.b64encode(pdf)})
template = env['sign.template'].create({'name': 'Hợp đồng dịch vụ', 'attachment_id': attachment.id})
for role, x in ((role_d, 0.08), (role_c, 0.55)):
    Item.create({'template_id': template.id, 'type_id': t_sig.id, 'responsible_id': role.id, 'page': 1,
                 'posX': x, 'posY': 0.62, 'width': 0.36, 'height': 0.09, 'name': 'Chữ ký'})
    Item.create({'template_id': template.id, 'type_id': t_text.id, 'responsible_id': role.id, 'page': 1,
                 'posX': x, 'posY': 0.725, 'width': 0.36, 'height': 0.03, 'name': 'Họ tên người ký'})
sign_request = env['sign.request'].create({
    'template_id': template.id, 'reference': 'HĐ 1708A/2026/DMAG-HĐDV',
    'request_item_ids': [(0, 0, {'partner_id': clinic.id, 'role_id': role_c.id, 'mail_sent_order': 1}),
                         (0, 0, {'partner_id': staff.partner_id.id, 'role_id': role_d.id, 'mail_sent_order': 1})],
})
env.cr.commit()
urls = {r.role_id.name: r._get_url() for r in sign_request.request_item_ids}
print('E2E_JSON=' + json.dumps({'request_id': sign_request.id, 'customer': urls['Khách hàng'], 'dmag': urls['DMAG']}))
