import json
T = env['dmag.cks.trust.root']
issued = T._dmag_cks_demo_issue('NGUYEN VAN A', 'CONG TY TNHH NHA KHOA C&N (DEMO)', 'MST:0109999999')
open('/tmp/e2e/token_cert.pem', 'w').write(issued['cert_pem'])
open('/tmp/e2e/token_key.pem', 'w').write(issued['key_pem'])
out = {}
for env_ in env['dmag.cks.envelope'].search([('state', '=', 'draft')], order='id'):
    env_.action_send()
    out[env_.document_name] = {'id': env_.id, 'url': env_.get_portal_url(), 'kind': env_.signer_ids[0].signer_kind}
env.cr.commit()
print('E2E_JSON=' + json.dumps(out))
