import base64
import json
import time

from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, TransactionCase, tagged

from ..tools import demo_pki, pades
from .test_pades import _sign_rsa


class CksCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = demo_pki.make_root()
        cls.trust = cls.env['dmag.cks.trust.root'].create({
            'name': 'Root test', 'is_demo': True, 'cert_file': base64.b64encode(cls.root['cert_der']),
            'demo_key_pem': cls.root['key_pem'].decode()})
        # Chỉ tin Root test: tắt các root khác (vd. dữ liệu demo) trong giao dịch test
        cls.env['dmag.cks.trust.root'].search([('id', '!=', cls.trust.id)]).active = False
        cls.env['dmag.cks.provider'].search([]).write({'use_for_company': False, 'use_for_customer': False})
        dmag = demo_pki.issue(cls.root, 'DMAG TEST', 'DMAG TEST', 'MST:0110085510')
        cls.company_provider = cls.env['dmag.cks.provider'].create({
            'name': 'DMAG p12', 'sequence': 0, 'provider_type': 'local_cert', 'use_for_company': True,
            'use_for_customer': False, 'p12_file': base64.b64encode(demo_pki.to_pkcs12(dmag, b'pw')),
            'p12_password': 'pw'})
        cls.cloud = cls.env['dmag.cks.provider'].create({
            'name': 'Cloud demo', 'provider_type': 'demo_cloud', 'use_for_customer': True,
            'demo_delay': 0, 'demo_root_id': cls.trust.id})
        cls.clinic = cls.env['res.partner'].create({
            'name': 'Nha khoa Test', 'is_company': True, 'vat': '0109999999', 'email': 'nk@example.com'})
        cls.doctor = cls.env['res.partner'].create({
            'name': 'BS Test', 'dmag_cks_personal_id': '001090012345', 'email': 'bs@example.com'})
        cls.pdf = cls.env['dmag.cks.trust.root']._dmag_cks_sample_pdf('HĐ test', 'Nha khoa Test')

    def _envelope(self, partner=None, kind='organization', require_customer=True):
        partner = partner or self.clinic
        return self.env['dmag.cks.envelope']._dmag_cks_create(
            self.pdf, 'HĐ test', partner, require_customer=require_customer, signer_kind=kind)

    def _usb_sign(self, envelope, issued):
        signer = envelope._current_signer()
        digest = signer._usb_prepare(base64.b64encode(issued['cert_der']).decode())
        signature = _sign_rsa(issued['key_pem'], digest)
        signer._usb_finalize(base64.b64encode(signature).decode(), ip='127.0.0.1')
        return signer


@tagged('post_install', '-at_install', 'dmag_cks')
class TestCksFlow(CksCommon):

    def test_usb_organization_then_company(self):
        envelope = self._envelope()
        self.assertEqual(envelope.state, 'customer')
        self.assertEqual(len(envelope.signer_ids), 2)
        self.assertTrue(envelope.access_token)
        token = demo_pki.issue(self.root, 'NGUYEN VAN A', 'NHA KHOA TEST', 'MST:0109999999')
        signer = self._usb_sign(envelope, token)
        self.assertEqual(signer.state, 'signed')
        self.assertEqual(signer.method, 'usb')
        self.assertIn('MST: 0109999999', signer.cert_ids)
        # DMAG (chứng thư trên máy chủ) ký ngay sau khách → hoàn tất
        self.assertEqual(envelope.state, 'done')
        self.assertTrue(envelope.verify_ok, envelope.verify_html)
        results = pades.verify_pdf(base64.b64decode(envelope.signed_pdf), [self.root['cert_der']])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['ok'] for r in results))
        self.assertEqual(results[1]['identity']['ids']['MST'], '0110085510')

    def test_wrong_company_blocked(self):
        envelope = self._envelope()
        other = demo_pki.issue(self.root, 'CONG TY KHAC', 'CONG TY KHAC', 'MST:0100000000')
        with self.assertRaisesRegex(UserError, 'không khớp'):
            self._usb_sign(envelope, other)
        self.assertEqual(envelope.state, 'customer')

    def test_personal_cert_rejected_for_organization(self):
        envelope = self._envelope()
        person = demo_pki.issue(self.root, 'NGUYEN VAN A', None, 'CCCD:001090099999')
        with self.assertRaisesRegex(UserError, 'TỔ CHỨC'):
            self._usb_sign(envelope, person)

    def test_individual_with_cccd(self):
        envelope = self._envelope(self.doctor, kind='individual')
        person = demo_pki.issue(self.root, 'BS TEST', None, 'CCCD:001090012345')
        self._usb_sign(envelope, person)
        self.assertEqual(envelope.state, 'done')
        self.assertTrue(envelope.verify_ok)

    def test_cloud_immediate(self):
        envelope = self._envelope()
        signer = envelope._current_signer()
        signer._cloud_start('0109999999', self.cloud)
        self.assertEqual(signer.method, 'cloud')
        self.assertEqual(envelope.state, 'done')
        self.assertTrue(envelope.verify_ok, envelope.verify_html)

    def test_cloud_waiting_then_poll(self):
        self.cloud.demo_delay = 1
        envelope = self._envelope()
        signer = envelope._current_signer()
        status = signer._cloud_start('MST:0109999999', self.cloud)
        self.assertEqual(status['state'], 'waiting')
        self.assertTrue(signer.request_deadline)
        signer._cloud_poll()
        self.assertEqual(signer.state, 'waiting')
        time.sleep(1.2)
        self.env['dmag.cks.envelope']._cron_poll_cloud()
        self.assertEqual(signer.state, 'signed')
        self.assertEqual(envelope.state, 'done')

    def test_company_only(self):
        """Khách đã ký điện tử trên Odoo: chỉ DMAG đóng chữ ký số tổ chức."""
        envelope = self._envelope(require_customer=False)
        self.assertEqual(envelope.state, 'done')
        self.assertEqual(len(envelope.signer_ids), 1)
        self.assertTrue(envelope.verify_ok)

    def test_cannot_skip_turn(self):
        envelope = self._envelope()
        company_signer = envelope.signer_ids.filtered(lambda s: s.role == 'company')
        with self.assertRaisesRegex(UserError, 'Chưa đến lượt'):
            envelope._prepare_for(company_signer, self.company_provider._local_material()[1])

    def test_on_done_hook(self):
        envelope = self._envelope(require_customer=False)
        self.assertEqual(envelope.state, 'done')
        attachment = self.env['ir.attachment'].search([('res_model', '=', 'dmag.cks.envelope'),
                                                       ('res_id', '=', envelope.id)])
        self.assertTrue(attachment)


@tagged('post_install', '-at_install', 'dmag_cks')
class TestCksPortal(HttpCase):

    def setUp(self):
        super().setUp()
        root = demo_pki.make_root()
        self.root = root
        trust = self.env['dmag.cks.trust.root'].create({
            'name': 'Root http', 'is_demo': True, 'cert_file': base64.b64encode(root['cert_der']),
            'demo_key_pem': root['key_pem'].decode()})
        self.env['dmag.cks.provider'].search([]).write({'use_for_company': False, 'use_for_customer': False})
        dmag = demo_pki.issue(root, 'DMAG TEST', 'DMAG TEST', 'MST:0110085510')
        self.env['dmag.cks.provider'].create({
            'name': 'DMAG p12', 'sequence': 0, 'provider_type': 'local_cert', 'use_for_company': True,
            'use_for_customer': False, 'p12_file': base64.b64encode(demo_pki.to_pkcs12(dmag, b'pw')),
            'p12_password': 'pw'})
        self.cloud = self.env['dmag.cks.provider'].create({
            'name': 'Cloud demo', 'provider_type': 'demo_cloud', 'use_for_customer': True,
            'demo_delay': 0, 'demo_root_id': trust.id})
        clinic = self.env['res.partner'].create({'name': 'NK Portal', 'is_company': True, 'vat': '0109999999'})
        pdf = self.env['dmag.cks.trust.root']._dmag_cks_sample_pdf('HĐ portal', 'NK Portal')
        self.envelope = self.env['dmag.cks.envelope']._dmag_cks_create(pdf, 'HĐ portal', clinic,
                                                                     signer_kind='organization')

    def _json(self, path, params):
        resp = self.url_open(path, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
                             headers={'Content-Type': 'application/json'})
        return resp.json()

    def test_page_and_token(self):
        url = '/cks/ky/%s' % self.envelope.id
        self.assertEqual(self.url_open(url + '?access_token=sai').status_code, 404)
        resp = self.url_open(url + '?access_token=%s' % self.envelope.access_token)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Chọn cách ký số', resp.text)
        self.assertIn('"envelopeId": %s' % self.envelope.id, resp.text)
        pdf = self.url_open(url + '/pdf?access_token=%s' % self.envelope.access_token)
        self.assertTrue(pdf.content.startswith(b'%PDF'))

    def test_usb_over_http(self):
        base = '/cks/ky/%s' % self.envelope.id
        token = self.envelope.access_token
        signer = self.envelope._current_signer()
        issued = demo_pki.issue(self.root, 'NGUYEN VAN A', 'NK PORTAL', 'MST:0109999999')
        prep = self._json(base + '/usb/prepare', {
            'access_token': token, 'signer_id': signer.id,
            'cert': base64.b64encode(issued['cert_der']).decode()})
        self.assertIn('result', prep, prep)
        digest = base64.b64decode(prep['result']['hash'])
        done = self._json(base + '/usb/finalize', {
            'access_token': token, 'signer_id': signer.id,
            'signature': base64.b64encode(_sign_rsa(issued['key_pem'], digest)).decode()})
        self.assertTrue(done['result']['done'], done)

    def _usb_http(self, envelope, signer, issued, signer_token=None):
        base = '/cks/ky/%s' % envelope.id
        params = {'access_token': envelope.access_token, 'signer_id': signer.id,
                  'cert': base64.b64encode(issued['cert_der']).decode()}
        if signer_token:
            params['signer_token'] = signer_token
        prep = self._json(base + '/usb/prepare', params)
        if 'error' in prep:
            return prep
        digest = base64.b64decode(prep['result']['hash'])
        params.pop('cert')
        params['signature'] = base64.b64encode(_sign_rsa(issued['key_pem'], digest)).decode()
        return self._json(base + '/usb/finalize', params)

    def test_company_usb_over_http(self):
        self.env.company.vat = '0110085510'
        self.env['dmag.cks.provider'].create({
            'name': 'DMAG USB', 'sequence': -1, 'provider_type': 'usb_token',
            'use_for_company': True, 'use_for_customer': False})
        clinic = self.envelope.partner_id
        pdf = self.env['dmag.cks.trust.root']._dmag_cks_sample_pdf('HĐ usb', clinic.name)
        envelope = self.env['dmag.cks.envelope']._dmag_cks_create(pdf, 'HĐ usb', clinic, signer_kind='organization')
        customer = envelope._current_signer()
        done = self._usb_http(envelope, customer,
                              demo_pki.issue(self.root, 'NGUYEN VAN A', 'NK PORTAL', 'MST:0109999999'))
        self.assertEqual(done['result']['state'], 'company', done)
        self.assertTrue(done['result']['waiting_company'])
        company = envelope._current_signer()
        dmag = demo_pki.issue(self.root, 'DMAG', 'CONG TY DMAG', 'MST:0110085510')
        # Link chung của khách không ký thay DMAG được
        denied = self._usb_http(envelope, company, dmag)
        self.assertIn('error', denied)
        page = self.url_open('/cks/ky/%s?access_token=%s&signer_token=%s' % (
            envelope.id, envelope.access_token, company.access_token))
        self.assertIn('DMAG ký số bằng USB token', page.text)
        done = self._usb_http(envelope, company, dmag, signer_token=company.access_token)
        self.assertTrue(done['result']['done'], done)

    def test_cloud_over_http_and_wrong_token(self):
        base = '/cks/ky/%s' % self.envelope.id
        signer = self.envelope._current_signer()
        bad = self._json(base + '/cloud/start', {'access_token': 'x', 'signer_id': signer.id,
                                                 'provider_id': self.cloud.id, 'remote_user_id': '0109999999'})
        self.assertIn('error', bad)
        ok = self._json(base + '/cloud/start', {'access_token': self.envelope.access_token, 'signer_id': signer.id,
                                                'provider_id': self.cloud.id, 'remote_user_id': '0109999999'})
        self.assertTrue(ok['result']['done'], ok)


@tagged('post_install', '-at_install', 'dmag_cks')
class TestCksCompanyUsb(CksCommon):
    """Cả hai bên ký bằng USB token: khách trước, người của DMAG sau."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.vat = '0110085510'
        cls.company_provider.use_for_company = False
        cls.usb_provider = cls.env['dmag.cks.provider'].create({
            'name': 'DMAG USB', 'sequence': 0, 'provider_type': 'usb_token',
            'use_for_company': True, 'use_for_customer': False})
        cls.dmag_token = demo_pki.issue(cls.root, 'DMAG', 'CONG TY DMAG', 'MST:0110085510')

    def test_both_sides_usb(self):
        envelope = self._envelope()
        self.assertEqual(envelope.company_provider_id, self.usb_provider)
        company_signer = envelope.signer_ids.filtered(lambda s: s.role == 'company')
        self.assertEqual(company_signer.method, 'usb')
        self.assertEqual(company_signer.expected_tax_id, '0110085510')
        self.assertFalse(envelope.company_usb_turn)
        self._usb_sign(envelope, demo_pki.issue(self.root, 'NGUYEN VAN A', 'NHA KHOA TEST', 'MST:0109999999'))
        # Khách xong → chờ DMAG, không tự ký, có việc cần làm cho người phụ trách
        self.assertEqual(envelope.state, 'company')
        self.assertTrue(envelope.company_usb_turn)
        self.assertTrue(envelope.activity_ids)
        action = envelope.action_sign_company_usb()
        self.assertIn('signer_token=%s' % company_signer.access_token, action['url'])
        # Token khác công ty bị chặn
        with self.assertRaisesRegex(UserError, 'không khớp'):
            self._usb_sign(envelope, demo_pki.issue(self.root, 'X', 'CONG TY X', 'MST:0100000000'))
        self._usb_sign(envelope, self.dmag_token)
        self.assertEqual(envelope.state, 'done')
        self.assertTrue(envelope.verify_ok, envelope.verify_html)
        self.assertFalse(envelope.activity_ids)

    def test_visitor_rules(self):
        envelope = self._envelope()
        company_signer = envelope.signer_ids.filtered(lambda s: s.role == 'company')
        self.assertEqual(envelope._signer_for_visitor(), envelope._customer_signers())
        self._usb_sign(envelope, demo_pki.issue(self.root, 'NGUYEN VAN A', 'NHA KHOA TEST', 'MST:0109999999'))
        # Link chung của khách không ký thay DMAG được
        self.assertFalse(envelope._signer_for_visitor())
        self.assertFalse(envelope._signer_for_visitor('sai-ma'))
        self.assertEqual(envelope._signer_for_visitor(company_signer.access_token), company_signer)
        self.assertEqual(envelope._portal_summary(company_signer.access_token)['signer_role'], 'company')
        self.assertTrue(envelope._portal_summary()['waiting_company'])
