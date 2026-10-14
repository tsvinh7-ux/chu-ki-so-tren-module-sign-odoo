from odoo.tests.common import BaseCase, tagged

from ..tools import confirm_page, demo_pki, pades


def _sign_rsa(key_pem, digest):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, utils

    key = serialization.load_pem_private_key(key_pem, password=None)
    return key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))


@tagged('post_install', '-at_install', 'dmag_cks')
class TestPades(BaseCase):
    """Thư viện PAdES: không cần cơ sở dữ liệu."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = demo_pki.make_root()
        cls.customer = demo_pki.issue(cls.root, 'NGUYEN VAN A', 'CONG TY TNHH NHA KHOA C&N', 'MST:0109999999')
        cls.person = demo_pki.issue(cls.root, 'TRẦN THỊ B', None, 'CCCD:001090012345', kind='ec')
        cls.dmag = demo_pki.issue(cls.root, 'CÔNG TY TNHH DMAG', 'CÔNG TY TNHH DMAG', 'MST:0110085510')
        page, cls.boxes = confirm_page.build_confirm_page(
            'Hợp đồng thử', 'KS/TEST/0001', 'ab' * 32,
            [{'label': 'BÊN B'}, {'label': 'CÁ NHÂN'}, {'label': 'BÊN A'}], 'DMAG')
        cls.pdf = page

    def test_identity(self):
        identity = pades.cert_identity(self.customer['cert_der'])
        self.assertEqual(identity['ids'], {'MST': '0109999999'})
        self.assertTrue(pades.identity_matches(identity, tax_id='0109999999'))
        self.assertTrue(pades.identity_matches(identity, tax_id='0109999999-001'))
        self.assertFalse(pades.identity_matches(identity, tax_id='0100000000'))
        person = pades.cert_identity(self.person['cert_der'])
        self.assertTrue(pades.identity_matches(person, personal_id='001090012345'))
        self.assertFalse(pades.identity_matches(person, tax_id='0109999999'))

    def test_three_signatures_and_tamper(self):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, utils

        state, digest = pades.prepare_signature(self.pdf, self.customer['cert_der'], field_name='B',
                                                box=self.boxes[0], label='BÊN B')
        with self.assertRaises(pades.PadesError):
            pades.finalize_signature(state, b'\x00' * 256)
        pdf = pades.finalize_signature(state, _sign_rsa(self.customer['key_pem'], digest))
        state, digest = pades.prepare_signature(pdf, self.person['cert_der'], field_name='P', box=self.boxes[1])
        key = serialization.load_pem_private_key(self.person['key_pem'], password=None)
        pdf = pades.finalize_signature(state, key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))))
        pdf = pades.sign_with_key(pdf, self.dmag['key_pem'], self.dmag['cert_der'], field_name='A',
                                  box=self.boxes[2], label='BÊN A')
        results = pades.verify_pdf(pdf, [self.root['cert_der']])
        self.assertEqual([r['field'] for r in results], ['B', 'P', 'A'])
        self.assertTrue(all(r['ok'] for r in results), results)
        # Root không tin cậy → không "ok"
        other_root = demo_pki.make_root('OTHER ROOT')
        self.assertFalse(any(r['ok'] for r in pades.verify_pdf(pdf, [other_root['cert_der']])))
        # Sửa 1 byte ở bản gốc → mọi chữ ký đều hỏng
        tampered = bytearray(pdf)
        tampered[600] ^= 0x01
        self.assertFalse(any(r['ok'] for r in pades.verify_pdf(bytes(tampered), [self.root['cert_der']])))
