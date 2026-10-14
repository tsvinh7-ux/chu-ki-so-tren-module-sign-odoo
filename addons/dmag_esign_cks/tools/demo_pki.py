"""Hạ tầng chứng thư GIẢ LẬP để demo và kiểm thử.

Tất cả chứng thư sinh ra ở đây **không có giá trị pháp lý**. Chúng chỉ để chạy thử
luồng ký khi chưa có tài khoản của CA thật.

Định dạng Subject mô phỏng chứng thư của CA Việt Nam: tổ chức mang ``UID=MST:<mã số thuế>``,
cá nhân mang ``UID=CCCD:<số CCCD>``.
"""
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEMO_ROOT_NAME = 'DMAG DEMO ROOT CA - KHONG CO GIA TRI PHAP LY'


def _key(kind='rsa'):
    if kind == 'ec':
        return ec.generate_private_key(ec.SECP256R1())
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem_key(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _der(cert):
    return cert.public_bytes(serialization.Encoding.DER)


def make_root(name=DEMO_ROOT_NAME, days=3650):
    key = _key()
    subject = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'VN'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'DMAG DEMO'),
        x509.NameAttribute(NameOID.COMMON_NAME, name),
    ])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    return {'key_pem': _pem_key(key), 'cert_der': _der(cert)}


def issue(root, common_name, organization=None, uid=None, kind='rsa', days=365, locality='Hà Nội'):
    """Cấp chứng thư người dùng cuối từ Root giả lập.

    :param uid: ví dụ ``'MST:0110085510'`` hoặc ``'CCCD:001090012345'``.
    """
    root_key = serialization.load_pem_private_key(root['key_pem'], password=None)
    root_cert = x509.load_der_x509_certificate(root['cert_der'])
    key = _key(kind)
    attrs = [x509.NameAttribute(NameOID.COUNTRY_NAME, 'VN'),
             x509.NameAttribute(NameOID.LOCALITY_NAME, locality)]
    if organization:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization))
    attrs.append(x509.NameAttribute(NameOID.COMMON_NAME, common_name))
    if uid:
        attrs.append(x509.NameAttribute(NameOID.USER_ID, uid))
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name(attrs)).issuer_name(root_cert.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(hours=1)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                                         key_encipherment=kind == 'rsa', data_encipherment=False,
                                         key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION,
                                                  ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(root_key.public_key()),
                           critical=False)
            .sign(root_key, hashes.SHA256()))
    return {'key_pem': _pem_key(key), 'cert_der': _der(cert),
            'cert_pem': cert.public_bytes(serialization.Encoding.PEM)}


def to_pkcs12(issued, password=b'demo', name=b'demo'):
    from cryptography.hazmat.primitives.serialization import pkcs12

    key = serialization.load_pem_private_key(issued['key_pem'], password=None)
    cert = x509.load_der_x509_certificate(issued['cert_der'])
    return pkcs12.serialize_key_and_certificates(
        name, key, cert, None, serialization.BestAvailableEncryption(password))
