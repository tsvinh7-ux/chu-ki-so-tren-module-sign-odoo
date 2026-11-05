from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from .test_sign_cks import SignCksSetup


@tagged('post_install', '-at_install', 'dmag_cks')
class TestSignLink(SignCksSetup, TransactionCase):
    """Cách phụ (ký xong trên Sign rồi mới tạo hồ sơ ký số) vẫn chạy với model sign.request."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_sign()
        cls.env['dmag.cks.provider'].search([]).write({'use_for_company': False, 'use_for_customer': False})
        cls.env['dmag.cks.provider'].create({
            'name': 'DMAG USB', 'sequence': 0, 'provider_type': 'usb_token',
            'use_for_company': True, 'use_for_customer': False})
        cls.link = cls.env.ref('dmag_esign_cks.sign_link_default')
        cls.link.action_activate()

    @classmethod
    def tearDownClass(cls):
        cls._teardown_sign()
        super().tearDownClass()

    def _signed_request(self):
        sign_request = self._request()
        self._item(sign_request, self.role_customer)._sign(self._values(self.role_customer, 'A'))
        self._item(sign_request, self.role_dmag)._sign(self._values(self.role_dmag, 'B'))
        return sign_request

    def test_cron_creates_envelope(self):
        sign_request = self._signed_request()
        self.env['dmag.cks.sign.link']._cron_pull()
        envelope = self.env['dmag.cks.envelope'].search([('res_model', '=', 'sign.request'),
                                                         ('res_id', '=', sign_request.id)])
        self.assertEqual(len(envelope), 1)
        self.assertEqual(envelope.partner_id, self.clinic)

    def test_manual_button_needs_signed(self):
        with self.assertRaisesRegex(UserError, 'chưa ký xong'):
            self.link._send_records(self._request())
