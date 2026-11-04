import base64
import io

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from odoo.addons.dmag_esign_cks.tests.test_pades import _sign_rsa
from odoo.addons.dmag_esign_cks.tools import demo_pki, pades


def _png():
    from PIL import Image, ImageDraw

    image = Image.new('RGBA', (300, 100), (255, 255, 255, 0))
    ImageDraw.Draw(image).line((10, 80, 100, 20, 200, 70, 290, 30), fill=(20, 40, 160, 255), width=5)
    buf = io.BytesIO()
    image.save(buf, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()


class SignCksCommon(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = demo_pki.make_root()
        cls.env['dmag.cks.trust.root'].search([]).active = False
        cls.env['dmag.cks.trust.root'].create({
            'name': 'Root test', 'cert_file': base64.b64encode(cls.root['cert_der'])})
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
                                          posX=0.08, posY=0.72))
        cls.i_dmag_name = Item.create(dict(common, type_id=cls.t_text.id, responsible_id=cls.role_dmag.id,
                                           posX=0.08, posY=0.82, height=0.03))
        cls.i_cust_sig = Item.create(dict(common, type_id=cls.t_sig.id, responsible_id=cls.role_customer.id,
                                          posX=0.55, posY=0.72))
        cls.i_cust_name = Item.create(dict(common, type_id=cls.t_text.id, responsible_id=cls.role_customer.id,
                                           posX=0.55, posY=0.82, height=0.03))
        cls.customer_token = demo_pki.issue(cls.root, 'NGUYEN VAN A', 'NHA KHOA TEST', 'MST:0109999999')
        cls.dmag_token = demo_pki.issue(cls.root, 'DMAG', 'CONG TY DMAG', 'MST:0110085510')

    def _request(self, order_customer=1, order_dmag=1):
        return self.env['sign.request'].create({
            'template_id': self.template.id, 'reference': 'HĐ 1708A',
            'request_item_ids': [
                (0, 0, {'partner_id': self.clinic.id, 'role_id': self.role_customer.id, 'mail_sent_order': order_customer}),
                (0, 0, {'partner_id': self.staff_user.partner_id.id, 'role_id': self.role_dmag.id,
                        'mail_sent_order': order_dmag}),
            ]})

    def _item(self, sign_request, role):
        return sign_request.request_item_ids.filtered(lambda r: r.role_id == role)

    def _values(self, role, name):
        sig, txt = (self.i_cust_sig, self.i_cust_name) if role == self.role_customer else (self.i_dmag_sig, self.i_dmag_name)
        return {str(sig.id): _png(), str(txt.id): name}

    def _cks(self, sign_request, role, token, name):
        item = self._item(sign_request, role)
        doc = self.env['dmag.cks.sign.doc']._for_request(sign_request, lock=True)
        digest, prep = doc._prepare_item(item, self._values(role, name), token['cert_der'])
        return doc._finalize_item(item, prep, _sign_rsa(token['key_pem'], digest))

    def _results(self, sign_request):
        return pades.verify_pdf(base64.b64decode(sign_request.completed_document), [self.root['cert_der']])


@tagged('post_install', '-at_install', 'dmag_cks')
class TestSignCks(SignCksCommon):

    def test_customer_cks_then_dmag_draws(self):
        sign_request = self._request()
        self.assertEqual(self._cks(sign_request, self.role_customer, self.customer_token, 'Nguyễn Văn A'),
                         {'conflict': False})
        self.assertEqual(self._item(sign_request, self.role_customer).state, 'completed')
        self.assertEqual(sign_request.dmag_cks_summary, 'Ký số: 1/2 bên')
        # DMAG ký thường (vẽ) qua đúng hàm của module Sign
        self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'Trần B'))
        self.assertEqual(sign_request.state, 'signed')
        results = self._results(sign_request)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['ok'], results)
        self.assertEqual(results[0]['modification'], 'ANNOTATIONS')
        self.assertTrue(sign_request.dmag_cks_doc_id.verify_ok)

    def test_reverse_order_both_cks(self):
        sign_request = self._request()
        self._cks(sign_request, self.role_dmag, self.dmag_token, 'Trần B')
        self._cks(sign_request, self.role_customer, self.customer_token, 'Nguyễn Văn A')
        self.assertEqual(sign_request.state, 'signed')
        results = self._results(sign_request)
        self.assertEqual([r['identity']['ids'] for r in results], [{'MST': '0110085510'}, {'MST': '0109999999'}])
        self.assertTrue(all(r['ok'] for r in results), results)
        self.assertEqual(sign_request.dmag_cks_summary, 'Ký số: 2/2 bên')

    def test_draw_before_any_cks_is_kept(self):
        sign_request = self._request()
        self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'Trần B'))
        self._cks(sign_request, self.role_customer, self.customer_token, 'Nguyễn Văn A')
        self.assertEqual(sign_request.state, 'signed')
        pdf = base64.b64decode(sign_request.completed_document)
        names = pades.list_overlays(pdf)
        self.assertTrue(any(n.startswith('item-%d-' % self.i_dmag_sig.id) for n in names), names)
        self.assertTrue(any(n.startswith('item-%d-' % self.i_cust_name.id) for n in names), names)
        self.assertTrue(self._results(sign_request)[0]['ok'])

    def test_concurrent_signers_conflict(self):
        sign_request = self._request()
        doc = self.env['dmag.cks.sign.doc']._for_request(sign_request, lock=True)
        cust, dmag = self._item(sign_request, self.role_customer), self._item(sign_request, self.role_dmag)
        d1, p1 = doc._prepare_item(cust, self._values(self.role_customer, 'A'), self.customer_token['cert_der'])
        d2, p2 = doc._prepare_item(dmag, self._values(self.role_dmag, 'B'), self.dmag_token['cert_der'])
        self.assertEqual(doc._finalize_item(cust, p1, _sign_rsa(self.customer_token['key_pem'], d1)), {'conflict': False})
        self.assertEqual(doc._finalize_item(dmag, p2, _sign_rsa(self.dmag_token['key_pem'], d2)), {'conflict': True})
        self.assertEqual(dmag.state, 'sent')
        self._cks(sign_request, self.role_dmag, self.dmag_token, 'B')
        self.assertTrue(all(r['ok'] for r in self._results(sign_request)))

    def test_wrong_mst_blocked(self):
        sign_request = self._request()
        other = demo_pki.issue(self.root, 'X', 'CONG TY X', 'MST:0100000000')
        with self.assertRaisesRegex(UserError, 'không khớp'):
            self._cks(sign_request, self.role_customer, other, 'X')
        # Nhân viên DMAG phải dùng token của chính DMAG
        with self.assertRaisesRegex(UserError, 'không khớp'):
            self._cks(sign_request, self.role_dmag, self.customer_token, 'X')

    def test_order_respected(self):
        sign_request = self._request(order_customer=1, order_dmag=2)
        with self.assertRaisesRegex(UserError, 'Chưa đến lượt'):
            self._cks(sign_request, self.role_dmag, self.dmag_token, 'B')

    def test_required_role_cannot_draw(self):
        self.role_dmag.dmag_cks_required = True
        sign_request = self._request()
        with self.assertRaisesRegex(UserError, 'phải ký bằng chữ ký số'):
            self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'B'))

    def test_no_cks_keeps_sign_behaviour(self):
        sign_request = self._request()
        self._item(sign_request, self.role_customer)._sign(self._values(self.role_customer, 'A'))
        self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'B'))
        self.assertEqual(sign_request.state, 'signed')
        self.assertFalse(sign_request.dmag_cks_doc_id)
        self.assertEqual(pades.count_signatures(base64.b64decode(sign_request.completed_document)), 0)

    def test_frontend_config(self):
        import json
        item = self._item(self._request(), self.role_customer)
        config = json.loads(item._dmag_cks_frontend_config())
        self.assertEqual(config['token'], item.access_token)
        self.assertFalse(config['required'])
