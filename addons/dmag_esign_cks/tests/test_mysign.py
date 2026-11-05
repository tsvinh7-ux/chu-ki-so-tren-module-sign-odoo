import base64
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import tagged

from ..tools import demo_pki, providers
from .fake_mysign import FakeMySign
from .test_cks_flow import CksCommon


@tagged('post_install', '-at_install', 'dmag_cks')
class TestMySign(CksCommon):
    """Adapter Viettel MySign chạy với máy chủ giả lập cùng giao thức (tài liệu MySign v1.8)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fake = FakeMySign(cls.root, demo_pki.issue, names={'0109999999': 'NHA KHOA TEST',
                                                               '0110085510': 'CONG TY DMAG'})
        cls.mysign = cls.env['dmag.cks.provider'].create({
            'name': 'Viettel MySign', 'provider_type': 'viettel_mysign', 'use_for_customer': True,
            'base_url': 'https://remotesigning.test', 'client_id': 'dmag-client', 'client_secret': 'dmag-secret',
            'profile_id': 'p1', 'request_ttl': 5})

    def setUp(self):
        super().setUp()
        patcher = patch.object(providers.requests, 'post', self.fake.post)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_label_encoding(self):
        self.assertEqual(base64.b64decode(providers.ascii_label('Hợp đồng 1708A/2026 – Đợt 1')).decode(),
                         'Hop dong 1708A 2026 Dot 1')
        self.assertLess(len(providers.ascii_label('x' * 500)), 100)

    def test_certificate_and_waiting_flow(self):
        self.fake.confirm_after = 1
        envelope = self._envelope()
        signer = envelope._current_signer()
        status = signer._cloud_start('0109999999', self.mysign)
        self.assertEqual(status['state'], 'waiting')
        self.assertIn(providers.ViettelMySignAdapter.PATHS['sign'], self.fake.calls)
        import time
        time.sleep(1.1)
        signer._cloud_poll()
        self.assertEqual(signer.state, 'signed')
        self.assertIn('MST: 0109999999', signer.cert_ids)
        self.assertEqual(envelope.state, 'done')
        self.assertTrue(envelope.verify_ok, envelope.verify_html)

    def test_rejected(self):
        self.fake.reject_users = {'0109999999'}
        envelope = self._envelope()
        signer = envelope._current_signer()
        signer._cloud_start('0109999999', self.mysign)
        signer._cloud_poll()
        self.assertEqual(signer.state, 'pending')
        self.assertIn('từ chối', signer.message)

    def test_bad_credentials(self):
        self.mysign.client_secret = 'sai'
        envelope = self._envelope()
        with self.assertRaisesRegex(UserError, '58001'):
            envelope._current_signer()._cloud_start('0109999999', self.mysign)
