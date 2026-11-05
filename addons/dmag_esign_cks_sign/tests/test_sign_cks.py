import base64
import io
import json
import time
from unittest import SkipTest
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import HttpCase, TransactionCase, tagged

from odoo.addons.dmag_esign_cks.tests.fake_mysign import FakeMySign
from odoo.addons.dmag_esign_cks.tests.test_pades import _sign_rsa
from odoo.addons.dmag_esign_cks.tools import demo_pki, pades, providers


def _png():
    from PIL import Image, ImageDraw

    image = Image.new('RGBA', (300, 100), (255, 255, 255, 0))
    ImageDraw.Draw(image).line((10, 80, 100, 20, 200, 70, 290, 30), fill=(20, 40, 160, 255), width=5)
    buf = io.BytesIO()
    image.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


class SignCksSetup:
    """Dữ liệu chung: mẫu ký 2 vai trò trên module Sign (bản giả lập theo Odoo 17)."""

    @classmethod
    def _setup_sign(cls):
        if 'sign.request' not in cls.env:
            raise SkipTest('Cần module Sign (hoặc bản giả lập e2e/fake_addons/sign) để chạy.')
        cls.config = cls.env.ref('dmag_esign_cks_sign.sign_config_default')
        cls.config.write({'active': True})
        cls.config._dmag_patch_vendor()
        cls.root = demo_pki.make_root()
        cls.env['dmag.cks.trust.root'].search([]).active = False
        cls.env['dmag.cks.trust.root'].create({
            'name': 'Root test', 'cert_file': base64.b64encode(cls.root['cert_der']), 'is_demo': True,
            'demo_key_pem': cls.root['key_pem'].decode()})
        cls.env.company.vat = '0110085510'
        cls.clinic = cls.env['res.partner'].create({'name': 'Nha khoa Test', 'is_company': True, 'vat': '0109999999'})
        cls.staff_user = cls.env['res.users'].create({'name': 'Nhân viên DMAG', 'login': 'nv_dmag_cks'})
        Role = cls.env['sign.item.role']
        cls.role_customer, cls.role_dmag = Role.create({'name': 'Khách hàng'}), Role.create({'name': 'DMAG'})
        Type = cls.env['sign.item.type']
        cls.t_sig = Type.create({'name': 'Chữ ký', 'item_type': 'signature'})
        cls.t_text = Type.create({'name': 'Họ tên', 'item_type': 'text'})
        pdf = cls.env['dmag.cks.trust.root']._dmag_cks_sample_pdf('HĐ qua Sign', 'Nha khoa Test')
        attachment = cls.env['ir.attachment'].create({'name': 'hd.pdf', 'datas': base64.b64encode(pdf)})
        cls.template = cls.env['sign.template'].create({'name': 'HĐ dịch vụ', 'attachment_id': attachment.id})
        Item = cls.env['sign.item']
        common = {'template_id': cls.template.id, 'page': 1, 'height': 0.08, 'width': 0.35}
        cls.i_dmag_sig = Item.create(dict(common, type_id=cls.t_sig.id, responsible_id=cls.role_dmag.id,
                                          posX=0.08, posY=0.62))
        cls.i_dmag_name = Item.create(dict(common, type_id=cls.t_text.id, responsible_id=cls.role_dmag.id,
                                           posX=0.08, posY=0.72, height=0.03))
        cls.i_cust_sig = Item.create(dict(common, type_id=cls.t_sig.id, responsible_id=cls.role_customer.id,
                                          posX=0.55, posY=0.62))
        cls.i_cust_name = Item.create(dict(common, type_id=cls.t_text.id, responsible_id=cls.role_customer.id,
                                           posX=0.55, posY=0.72, height=0.03))
        cls.customer_token = demo_pki.issue(cls.root, 'NGUYEN VAN A', 'NHA KHOA TEST', 'MST:0109999999')
        cls.dmag_token = demo_pki.issue(cls.root, 'DMAG', 'CONG TY DMAG', 'MST:0110085510')

    @classmethod
    def _teardown_sign(cls):
        cls.env['dmag.cks.sign.config']._dmag_unpatch_vendor()

    def _request(self, order_customer=1, order_dmag=1):
        return self.env['sign.request'].create({
            'template_id': self.template.id, 'reference': 'HĐ 1708A',
            'request_item_ids': [
                (0, 0, {'partner_id': self.clinic.id, 'role_id': self.role_customer.id,
                        'mail_sent_order': order_customer}),
                (0, 0, {'partner_id': self.staff_user.partner_id.id, 'role_id': self.role_dmag.id,
                        'mail_sent_order': order_dmag}),
            ]})

    def _item(self, sign_request, role):
        return sign_request.request_item_ids.filtered(lambda r: r.role_id == role)

    def _values(self, role, name):
        sig, txt = (self.i_cust_sig, self.i_cust_name) if role == self.role_customer else \
            (self.i_dmag_sig, self.i_dmag_name)
        return {str(sig.id): _png(), str(txt.id): name}

    def _usb_ready(self, sign_request, role, token, name):
        """Phần trình duyệt làm: chuẩn bị → USB token ký → nhúng (chưa chốt)."""
        item = self._item(sign_request, role)
        doc = self.env['dmag.cks.sign.doc']._for_request(sign_request, lock=True)
        digest, prep = doc._prepare_item(item, self._values(role, name), token['cert_der'])
        return doc._finalize_pending(prep, _sign_rsa(token['key_pem'], digest))

    def _cks(self, sign_request, role, token, name):
        """Ký số rồi module Sign nhận chữ ký (như khi yêu cầu gốc của trang ký đi tiếp)."""
        self.assertEqual(self._usb_ready(sign_request, role, token, name), {'conflict': False})
        self._item(sign_request, role)._sign(self._values(role, name))

    def _results(self, sign_request):
        return pades.verify_pdf(base64.b64decode(sign_request.completed_document), [self.root['cert_der']])


@tagged('post_install', '-at_install', 'dmag_cks')
class TestSignCks(SignCksSetup, TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_sign()

    @classmethod
    def tearDownClass(cls):
        cls._teardown_sign()
        super().tearDownClass()

    def test_config_check_all_green(self):
        rows = self.config._checks()
        self.assertTrue(rows)
        self.assertEqual([r for r in rows if not r[2]], [])

    def test_wrong_model_name_cannot_activate(self):
        other = self.config.copy({'name': 'Sai', 'request_model': 'bizapps.khong.ton.tai', 'active': False})
        with self.assertRaisesRegex(UserError, 'chưa khớp'):
            other.action_activate()

    def test_customer_cks_then_dmag_draws(self):
        sign_request = self._request()
        self._cks(sign_request, self.role_customer, self.customer_token, 'Nguyễn Văn A')
        self.assertEqual(self._item(sign_request, self.role_customer).state, 'completed')
        doc = self.env['dmag.cks.sign.doc']._for_request(sign_request, create=False)
        self.assertEqual(doc.summary, 'Ký số: 1/2 bên')
        self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'Trần B'))
        self.assertEqual(sign_request.state, 'signed')
        results = self._results(sign_request)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['ok'], results)
        self.assertEqual(results[0]['modification'], 'ANNOTATIONS')
        self.assertEqual(doc.state, 'done')
        self.assertTrue(doc.verify_ok)

    def test_reverse_order_both_cks(self):
        sign_request = self._request()
        self._cks(sign_request, self.role_dmag, self.dmag_token, 'Trần B')
        self._cks(sign_request, self.role_customer, self.customer_token, 'Nguyễn Văn A')
        results = self._results(sign_request)
        self.assertEqual([r['identity']['ids'] for r in results], [{'MST': '0110085510'}, {'MST': '0109999999'}])
        self.assertTrue(all(r['ok'] for r in results), results)

    def test_draw_before_any_cks_is_kept(self):
        sign_request = self._request()
        self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'Trần B'))
        self._cks(sign_request, self.role_customer, self.customer_token, 'Nguyễn Văn A')
        names = pades.list_overlays(base64.b64decode(sign_request.completed_document))
        self.assertTrue(any(n.startswith('item-%d-' % self.i_dmag_sig.id) for n in names), names)
        self.assertTrue(self._results(sign_request)[0]['ok'])

    def test_conflict_before_and_after_commit(self):
        sign_request = self._request()
        doc = self.env['dmag.cks.sign.doc']._for_request(sign_request, lock=True)
        cust, dmag = self._item(sign_request, self.role_customer), self._item(sign_request, self.role_dmag)
        d1, p1 = doc._prepare_item(cust, self._values(self.role_customer, 'A'), self.customer_token['cert_der'])
        d2, p2 = doc._prepare_item(dmag, self._values(self.role_dmag, 'B'), self.dmag_token['cert_der'])
        # Cả hai đã ký trên thiết bị của mình; người gửi trước được chốt, người sau phải ký lại.
        self.assertEqual(doc._finalize_pending(p1, _sign_rsa(self.customer_token['key_pem'], d1)), {'conflict': False})
        self.assertEqual(doc._finalize_pending(p2, _sign_rsa(self.dmag_token['key_pem'], d2)), {'conflict': False})
        cust._sign(self._values(self.role_customer, 'A'))
        with self.assertRaisesRegex(UserError, 'vừa ký trước'):
            dmag._sign(self._values(self.role_dmag, 'B'))
        self._cks(sign_request, self.role_dmag, self.dmag_token, 'B')
        self.assertTrue(all(r['ok'] for r in self._results(sign_request)))

    def test_wrong_mst_and_staff_must_use_company_token(self):
        sign_request = self._request()
        other = demo_pki.issue(self.root, 'X', 'CONG TY X', 'MST:0100000000')
        with self.assertRaisesRegex(UserError, 'không khớp'):
            self._usb_ready(sign_request, self.role_customer, other, 'X')
        with self.assertRaisesRegex(UserError, 'không khớp'):
            self._usb_ready(sign_request, self.role_dmag, self.customer_token, 'X')

    def test_order_respected(self):
        sign_request = self._request(order_customer=1, order_dmag=2)
        with self.assertRaisesRegex(UserError, 'Chưa đến lượt'):
            self._usb_ready(sign_request, self.role_dmag, self.dmag_token, 'B')

    def test_required_role(self):
        self.config.required_roles = 'DMAG, Giám đốc'
        sign_request = self._request()
        with self.assertRaisesRegex(UserError, 'phải ký bằng chữ ký số'):
            self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'B'))
        self._cks(sign_request, self.role_dmag, self.dmag_token, 'B')
        self.assertEqual(self._item(sign_request, self.role_dmag).state, 'completed')

    def test_browser_error_is_reported_by_sign(self):
        sign_request = self._request()
        doc_model = type(self.env['dmag.cks.sign.doc'])
        with patch.object(doc_model, '_request_header', staticmethod(lambda name: 'Ban%20da%20huy%20ky')):
            with self.assertRaisesRegex(UserError, 'Ban da huy ky'):
                self._item(sign_request, self.role_customer)._sign(self._values(self.role_customer, 'A'))

    def test_no_cks_keeps_sign_behaviour(self):
        sign_request = self._request()
        self._item(sign_request, self.role_customer)._sign(self._values(self.role_customer, 'A'))
        self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'B'))
        self.assertEqual(sign_request.state, 'signed')
        self.assertFalse(self.env['dmag.cks.sign.doc']._for_request(sign_request, create=False))
        self.assertEqual(pades.count_signatures(base64.b64decode(sign_request.completed_document)), 0)

    def test_deactivated_does_nothing(self):
        self.config._dmag_unpatch_vendor()
        try:
            self.config.required_roles = 'DMAG'
            sign_request = self._request()
            self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'B'))
            self.assertEqual(self._item(sign_request, self.role_dmag).state, 'completed')
        finally:
            self.config._dmag_patch_vendor()

    def test_mysign_cloud_for_dmag(self):
        fake = FakeMySign(self.root, demo_pki.issue, confirm_after=1, names={'0110085510': 'CONG TY DMAG'})
        mysign = self.env['dmag.cks.provider'].create({
            'name': 'Viettel MySign', 'provider_type': 'viettel_mysign', 'use_for_company': True,
            'use_for_customer': False, 'company_user_id': '0110085510', 'base_url': 'https://mysign.test',
            'client_id': 'dmag-client', 'client_secret': 'dmag-secret'})
        sign_request = self._request()
        dmag = self._item(sign_request, self.role_dmag)
        config = self.config._frontend_config(sign_request, dmag)
        self.assertIn(mysign.id, [p['id'] for p in config['cloudProviders']])
        self.assertEqual(config['defaultRemoteUser'], '0110085510')
        customer_config = self.config._frontend_config(sign_request, self._item(sign_request, self.role_customer))
        self.assertNotIn(mysign.id, [p['id'] for p in customer_config['cloudProviders']])
        with patch.object(providers.requests, 'post', fake.post):
            cert = mysign._call('get_certificate', '0110085510')
            doc = self.env['dmag.cks.sign.doc']._for_request(sign_request, lock=True)
            digest, prep = doc._prepare_item(dmag, self._values(self.role_dmag, 'B'), cert['cert'], cert['chain'],
                                             method='cloud', provider=mysign, remote_user_id='0110085510')
            prep.credential_id = cert['credential_id']
            result = mysign._call('sign_hash', '0110085510', prep.credential_id, digest, 'x', 'HĐ 1708A')
            self.assertEqual(prep._cloud_handle(result)['state'], 'waiting')
            self.assertEqual(prep._cloud_poll()['state'], 'waiting')
            time.sleep(1.1)
            self.assertEqual(prep._cloud_poll()['state'], 'signed')
        dmag._sign(self._values(self.role_dmag, 'B'))
        self._cks(sign_request, self.role_customer, self.customer_token, 'A')
        results = self._results(sign_request)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['ok'] for r in results), results)


@tagged('post_install', '-at_install', 'dmag_cks')
class TestSignCksHttp(SignCksSetup, HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_sign()

    @classmethod
    def tearDownClass(cls):
        cls._teardown_sign()
        super().tearDownClass()

    def _json(self, path, params):
        resp = self.url_open(path, data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params}),
                             headers={'Content-Type': 'application/json'})
        return resp.json()

    def test_config_and_usb_over_http(self):
        sign_request = self._request()
        item = self._item(sign_request, self.role_customer)
        page = '/sign/document/%s/%s' % (sign_request.id, item.access_token)
        self.assertEqual(self._json('/dmag_cks/sign/config', {'path': '/shop/cart'})['result'], {'enabled': False})
        self.assertEqual(self._json('/dmag_cks/sign/config', {'path': '/sign/document/%s/sai' % sign_request.id})
                         ['result'], {'enabled': False})
        config = self._json('/dmag_cks/sign/config', {'path': page})['result']
        self.assertTrue(config['enabled'])
        self.assertEqual(config['submitUrlRegex'], self.config.submit_url_regex)
        base = {'request_id': sign_request.id, 'token': item.access_token}
        values = self._values(self.role_customer, 'A')
        prep = self._json('/dmag_cks/sign/prepare', dict(base, signature=values,
                                                          cert=base64.b64encode(self.customer_token['cert_der']).decode()))
        self.assertIn('result', prep, prep)
        digest = base64.b64decode(prep['result']['hash'])
        done = self._json('/dmag_cks/sign/finalize', dict(base, prep=prep['result']['prep'], signature=base64.b64encode(
            _sign_rsa(self.customer_token['key_pem'], digest)).decode()))
        self.assertEqual(done['result'], {'conflict': False})
        # Yêu cầu gốc của trang ký (module Sign) đi tiếp
        signed = self._json('/sign/sign/%s/%s' % (sign_request.id, item.access_token), {'signature': values})
        self.assertTrue(signed['result']['success'], signed)
        self.assertEqual(item.state, 'completed')
        # Mã truy cập sai
        bad = self._json('/dmag_cks/sign/prepare', dict(base, token='sai', signature=values, cert=''))
        self.assertIn('error', bad)
