import base64

from odoo.addons.dmag_esign_cks.tests.test_cks_flow import CksCommon
from odoo.addons.dmag_esign_cks.tests.test_pades import _sign_rsa
from odoo.addons.dmag_esign_cks.tools import demo_pki
from odoo.exceptions import UserError
from odoo.tests.common import tagged


@tagged('post_install', '-at_install', 'dmag_cks')
class TestSignLink(CksCommon):
    """Yêu cầu ký hoàn tất trên module ký → hồ sơ ký số → khách USB → DMAG USB → PDF quay về yêu cầu ký."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.company.vat = '0110085510'
        cls.company_provider.use_for_company = False
        cls.env['dmag.cks.provider'].create({
            'name': 'DMAG USB', 'sequence': 0, 'provider_type': 'usb_token',
            'use_for_company': True, 'use_for_customer': False})
        cls.link = cls.env.ref('dmag_esign_cks.sign_link_default')
        cls.link.action_activate()

    def _sign_request(self, state='signed'):
        return self.env['sign.request'].create({
            'reference': 'HĐ 1708A qua module Sign', 'state': state,
            'completed_document': base64.b64encode(self.pdf) if state == 'signed' else False,
            'request_item_ids': [(0, 0, {'partner_id': self.env.user.partner_id.id}),
                                 (0, 0, {'partner_id': self.clinic.id})],
        })

    def _usb(self, envelope, issued):
        signer = envelope._current_signer()
        digest = signer._usb_prepare(base64.b64encode(issued['cert_der']).decode())
        signer._usb_finalize(base64.b64encode(_sign_rsa(issued['key_pem'], digest)).decode())

    def test_activate_binds_button(self):
        self.assertTrue(self.link.active)
        self.assertEqual(self.link.server_action_id.binding_model_id.model, 'sign.request')

    def test_cron_creates_envelope_and_returns_pdf(self):
        request = self._sign_request()
        self.env['dmag.cks.sign.link']._cron_pull()
        envelope = self.env['dmag.cks.envelope'].search([('res_model', '=', 'sign.request'),
                                                         ('res_id', '=', request.id)])
        self.assertEqual(len(envelope), 1)
        self.assertEqual(envelope.partner_id, self.clinic)  # không lấy người nội bộ
        self.assertEqual(envelope.document_name, 'HĐ 1708A qua module Sign')
        self.assertEqual(envelope.state, 'customer')
        # Quét lại không tạo trùng
        self.env['dmag.cks.sign.link']._cron_pull()
        self.assertEqual(self.env['dmag.cks.envelope'].search_count([('res_id', '=', request.id),
                                                                     ('res_model', '=', 'sign.request')]), 1)
        self._usb(envelope, demo_pki.issue(self.root, 'NGUYEN VAN A', 'NHA KHOA TEST', 'MST:0109999999'))
        self.assertEqual(envelope.state, 'company')
        self._usb(envelope, demo_pki.issue(self.root, 'DMAG', 'CONG TY DMAG', 'MST:0110085510'))
        self.assertEqual(envelope.state, 'done')
        self.assertTrue(envelope.verify_ok, envelope.verify_html)
        attachments = self.env['ir.attachment'].search([('res_model', '=', 'sign.request'), ('res_id', '=', request.id)])
        self.assertTrue(attachments.filtered(lambda a: a.mimetype == 'application/pdf'))

    def test_manual_button(self):
        request = self._sign_request()
        action = self.link._send_records(request)
        self.assertEqual(action['res_model'], 'dmag.cks.envelope')
        with self.assertRaisesRegex(UserError, 'chưa ký xong'):
            self.link._send_records(self._sign_request(state='sent'))

    def test_wrong_model_name(self):
        self.link.model_name = 'bizapps.khong.ton.tai'
        with self.assertRaisesRegex(UserError, 'Không tìm thấy model'):
            self.link.action_activate()
