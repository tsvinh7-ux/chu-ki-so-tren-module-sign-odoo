# Chạy trong `odoo shell`: gửi các hồ sơ demo và cấp 2 chứng thư "USB token giả lập"
# (khách hàng và DMAG) để chạy thử phần mềm DMAG Ký số ở chế độ PEM.
import json

T = env['dmag.cks.trust.root']
company = env.company
tokens = {
    'customer': T._dmag_cks_demo_issue('NGUYEN VAN A', 'CONG TY TNHH NHA KHOA C&N (DEMO)', 'MST:0109999999'),
    'dmag': T._dmag_cks_demo_issue(company.name.upper(), company.name.upper(), 'MST:%s' % company.vat),
}
for who, issued in tokens.items():
    open('/tmp/e2e/%s_cert.pem' % who, 'w').write(issued['cert_pem'])
    open('/tmp/e2e/%s_key.pem' % who, 'w').write(issued['key_pem'])
out = []
admin = env.ref('base.user_admin')
for envelope in env['dmag.cks.envelope'].search([('state', '=', 'draft')], order='id'):
    envelope.user_id = admin
    envelope.action_send()
    company_signer = envelope.signer_ids.filtered(lambda s: s.role == 'company')
    out.append({'id': envelope.id, 'name': envelope.document_name, 'url': envelope.get_portal_url(),
                'kind': envelope.signer_ids[0].signer_kind,
                'company_url': envelope._company_sign_url(company_signer)})
env.cr.commit()
print('E2E_JSON=' + json.dumps(out))
