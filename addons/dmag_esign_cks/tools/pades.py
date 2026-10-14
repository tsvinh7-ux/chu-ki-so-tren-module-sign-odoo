"""Ký PDF chuẩn PAdES bằng chữ ký số của CA Việt Nam, theo kiểu "ký gián đoạn".

Module này không phụ thuộc Odoo để kiểm thử độc lập được.

Luồng ký một chữ ký:

1. :func:`prepare_signature`: thêm ô chữ ký (kèm hình hiển thị) vào PDF theo kiểu
   incremental update, chừa chỗ cho chữ ký, rồi tính **hash cần ký** (SHA-256 của
   signed attributes CMS). Trạng thái trả về được lưu vào DB.
2. Hash được gửi ra ngoài để ký: phần mềm DMAG Ký số (USB token), API ký từ xa
   (VNPT SmartCA, Viettel MySign…) hoặc khóa của DMAG trên máy chủ.
3. :func:`finalize_signature`: nhận chữ ký thô (RSA PKCS#1 v1.5 / ECDSA), dựng CMS
   (có thể kèm dấu thời gian TSA) và ghi vào chỗ đã chừa.

:func:`verify_pdf` kiểm tra mọi chữ ký trong file: toàn vẹn, hợp lệ, có tin cậy
được (chuỗi về Root CA được cấu hình) và nội dung có bị sửa sau khi ký không.
"""
import asyncio
import base64
import hashlib
import io
import logging
import os
import re
import tempfile
import unicodedata
from datetime import datetime, timezone

from asn1crypto import cms, x509 as asn1_x509
from pyhanko.pdf_utils import layout
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.sign import fields, signers, timestamps
from pyhanko.sign.signers.pdf_byterange import PreparedByteRangeDigest
from pyhanko.sign.signers.pdf_signer import PdfTBSDocument
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.stamp import StaticStampStyle, TextStampStyle
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore

DIGEST = 'sha256'

FONT_CANDIDATES = (
    os.path.join(os.path.dirname(__file__), '..', 'static', 'fonts', 'DejaVuSans.ttf'),
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/TTF/DejaVuSans.ttf',
)

# Các loại định danh hay gặp trong chứng thư số do CA Việt Nam cấp (thuộc tính UID
# hoặc serialNumber của Subject), ví dụ "MST:0110085510", "CCCD:001090012345".
ID_PATTERN = re.compile(r'\b(MST|CCCD|CMND|CMT|HC|BHXH|MNS|MDN)\s*[:\-]?\s*([0-9A-Z][0-9A-Z\-]{5,})', re.I)


class PadesError(Exception):
    """Lỗi trong quá trình ký / kiểm tra PDF."""


# --------------------------------------------------------------------------- tiện ích
def _run(coro):
    return asyncio.run(coro)


def load_cert(data):
    """Đọc chứng thư X.509 (DER, PEM hoặc base64 DER) thành ``asn1crypto`` Certificate."""
    if isinstance(data, asn1_x509.Certificate):
        return data
    if isinstance(data, str):
        data = data.strip()
        if data.startswith('-----BEGIN'):
            data = data.encode()
        else:
            data = base64.b64decode(data)
    if data.lstrip().startswith(b'-----BEGIN'):
        body = b''.join(line for line in data.strip().splitlines() if not line.startswith(b'-----'))
        data = base64.b64decode(body)
    return asn1_x509.Certificate.load(data)


def load_certs(items):
    return [load_cert(item) for item in (items or []) if item]


def find_font():
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _subject_values(cert):
    values = []
    for rdn in cert.subject.chosen:
        for attr in rdn:
            value = attr['value'].native
            if isinstance(value, str):
                values.append((attr['type'].native, value))
    return values


def cert_identity(cert):
    """Thông tin người ký rút ra từ chứng thư: tên, tổ chức, MST/CCCD, hạn dùng."""
    cert = load_cert(cert)
    subject = cert.subject.native
    ids = {}
    for _type, value in _subject_values(cert):
        for kind, number in ID_PATTERN.findall(value):
            ids.setdefault(kind.upper(), re.sub(r'[^0-9A-Z]', '', number.upper()))
    validity = cert['tbs_certificate']['validity']
    common_name = subject.get('common_name') or ''
    organization = subject.get('organization_name') or ''
    display = common_name
    if organization and organization != common_name:
        display = '%s – %s' % (common_name, organization)
    if ids:
        display += ' (%s)' % ', '.join('%s %s' % kv for kv in ids.items())
    return {
        'display': display,
        'common_name': common_name,
        'organization': organization,
        'subject': cert.subject.human_friendly,
        'issuer': cert.issuer.human_friendly,
        'serial': format(cert.serial_number, 'X'),
        'not_before': validity['not_before'].native,
        'not_after': validity['not_after'].native,
        'ids': ids,
        'sha1': hashlib.sha1(cert.dump()).hexdigest().upper(),
    }


def normalize_id(value):
    return re.sub(r'[^0-9A-Z]', '', (value or '').upper())


def identity_matches(identity, tax_id=None, personal_id=None):
    """MST (tổ chức) hoặc CCCD/CMND (cá nhân) trong chứng thư có khớp hồ sơ khách không.

    MST chi nhánh dạng ``0101234567-001`` vẫn khớp MST công ty mẹ ``0101234567``.
    """
    ids = identity.get('ids') or {}
    tax_id, personal_id = normalize_id(tax_id), normalize_id(personal_id)
    if tax_id:
        mst = ids.get('MST', '')
        if mst and (mst == tax_id or mst.startswith(tax_id) or tax_id.startswith(mst)):
            return True
    if personal_id:
        for kind in ('CCCD', 'CMND', 'CMT', 'HC'):
            if ids.get(kind) and ids[kind] == personal_id:
                return True
    return False


def _signature_placeholder_size(cert):
    """Kích thước chữ ký thô dự kiến (để chừa chỗ trong PDF)."""
    key = cert.public_key
    if key.algorithm == 'rsa':
        return (key.bit_size + 7) // 8
    # ECDSA DER: 2 số nguyên + header
    return 2 * ((key.bit_size + 7) // 8) + 9


def _ascii(text):
    """Bỏ dấu tiếng Việt khi máy chủ thiếu font/thư viện hiển thị Unicode."""
    text = text.replace('Đ', 'D').replace('đ', 'd')
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))


def _render_stamp_pdf(lines, width, height):
    """Vẽ ô chữ ký bằng reportlab (font Unicode, hiển thị đúng tiếng Việt) → bytes PDF 1 trang.

    pyHanko 0.25 tính sai độ rộng chữ với font TrueType 2048 đơn vị/em (chữ bị dãn),
    nên dùng trang PDF tĩnh làm hình hiển thị của chữ ký.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_path = find_font()
    regular, bold, fix = 'Helvetica', 'Helvetica-Bold', _ascii
    if font_path:
        if 'DMAGSans' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('DMAGSans', font_path))
            bold_path = font_path.replace('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')
            pdfmetrics.registerFont(TTFont('DMAGSans-Bold', bold_path if os.path.isfile(bold_path) else font_path))
        regular, bold, fix = 'DMAGSans', 'DMAGSans-Bold', (lambda text: text)
    lines = [fix(line) for line in lines]
    pad = 6
    usable = width - 2 * pad
    size = 8.5
    while size > 5.5:
        fonts = [bold if i == 0 else regular for i in range(len(lines))]
        if all(pdfmetrics.stringWidth(line, f, size) <= usable for line, f in zip(lines, fonts)) and \
                len(lines) * size * 1.25 <= height - 2 * pad:
            break
        size -= 0.5
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(width, height))
    pdf.setStrokeColorRGB(0.04, 0.43, 0.31)
    pdf.setLineWidth(1.2)
    pdf.roundRect(0.6, 0.6, width - 1.2, height - 1.2, 4)
    y = height - pad - size
    for index, line in enumerate(lines):
        font = bold if index == 0 else regular
        pdf.setFillColorRGB(0.04, 0.43, 0.31) if index == 0 else pdf.setFillColorRGB(0.1, 0.1, 0.1)
        while pdf.stringWidth(line, font, size) > usable and len(line) > 4:
            line = line[:-2] + '…'
        pdf.setFont(font, size)
        pdf.drawString(pad, y, line)
        y -= size * 1.25
    pdf.showPage()
    pdf.save()
    return buf.getvalue()


def _stamp_style(stamp_text, box, workdir):
    """Kiểu hiển thị ô chữ ký. ``workdir``: thư mục tạm, pyHanko đọc file khi dựng PDF."""
    width, height = abs(box[2] - box[0]), abs(box[3] - box[1])
    try:
        stamp_pdf = _render_stamp_pdf(stamp_text.split('\n'), width, height)
    except ImportError:  # thiếu reportlab: dùng chữ không dấu của pyHanko
        return TextStampStyle(stamp_text=_ascii(stamp_text), border_width=1,
                              text_box_style=TextBoxStyle(font_size=8))
    path = os.path.join(workdir, 'stamp.pdf')
    with open(path, 'wb') as handle:
        handle.write(stamp_pdf)
    return StaticStampStyle.from_pdf_file(path, border_width=0, background_layout=layout.SimpleBoxLayoutRule(
        x_align=layout.AxisAlignment.ALIGN_MIN, y_align=layout.AxisAlignment.ALIGN_MIN,
        margins=layout.Margins.uniform(0), inner_content_scaling=layout.InnerScaling.STRETCH_FILL))


def stamp_lines(identity, signed_at, label=None):
    """Nội dung hiển thị trong ô chữ ký."""
    lines = []
    if label:
        lines.append(label)
    lines.append('Ký bởi: %s' % (identity.get('organization') or identity.get('common_name')))
    if identity.get('organization') and identity.get('common_name') and \
            identity['common_name'] != identity['organization']:
        lines.append('Người ký: %s' % identity['common_name'])
    ids = identity.get('ids') or {}
    for kind in ('MST', 'CCCD', 'CMND'):
        if ids.get(kind):
            lines.append('%s: %s' % (kind, ids[kind]))
            break
    lines.append('Ngày ký: %s' % signed_at.strftime('%d/%m/%Y %H:%M'))
    return '\n'.join(lines)


def count_signatures(pdf_bytes):
    return len(PdfFileReader(io.BytesIO(pdf_bytes), strict=False).embedded_signatures)


def _timestamper(tsa_url):
    return timestamps.HTTPTimeStamper(tsa_url, timeout=20) if tsa_url else None


# --------------------------------------------------------------------------- ký gián đoạn
def prepare_signature(pdf_bytes, cert, chain=(), field_name='Signature1', page=-1,
                      box=(40, 40, 280, 110), label=None, reason=None, location=None,
                      signed_at=None, tsa_url=None):
    """Bước 1: chừa chỗ chữ ký và tính hash cần ký.

    :param cert: chứng thư người ký (DER/PEM/base64 hoặc asn1crypto).
    :param chain: các chứng thư trung gian (để nhúng vào CMS).
    :param box: (x1, y1, x2, y2) ô chữ ký, đơn vị point, gốc tọa độ góc dưới-trái.
    :returns: ``(state, digest)``, trong đó ``state`` là dict lưu được (bytes/str/int)
        và ``digest`` là 32 byte SHA-256 cần được ký bằng khóa bí mật.
    """
    cert = load_cert(cert)
    chain = load_certs(chain)
    signed_at = signed_at or datetime.now(timezone.utc)
    identity = cert_identity(cert)
    registry = SimpleCertificateStore.from_certs([cert] + chain)
    signer = signers.ExternalSigner(
        signing_cert=cert, cert_registry=registry,
        signature_value=_signature_placeholder_size(cert))
    meta = signers.PdfSignatureMetadata(
        field_name=field_name, md_algorithm=DIGEST, subfilter=fields.SigSeedSubFilter.PADES,
        reason=reason, location=location, name=identity['common_name'] or None)
    workdir = tempfile.TemporaryDirectory(prefix='dmag-cks-')
    pdf_signer = signers.PdfSigner(
        meta, signer=signer,
        stamp_style=_stamp_style(stamp_lines(identity, signed_at, label), box, workdir.name),
        new_field_spec=fields.SigFieldSpec(field_name, on_page=page, box=tuple(box)))

    async def _prepare():
        writer = IncrementalPdfFileWriter(io.BytesIO(pdf_bytes), strict=False)
        # Chừa dư chỗ cho chuỗi chứng thư và dấu thời gian TSA (thêm ở bước 3).
        reserved = 16384 + sum(len(c.dump()) for c in chain) * 2 + (12288 if tsa_url else 0)
        prep_digest, _tbs, output = await pdf_signer.async_digest_doc_for_signing(
            writer, bytes_reserved=reserved)
        signed_attrs = await signer.signed_attrs(prep_digest.document_digest, DIGEST, use_pades=True)
        return prep_digest, output, signed_attrs

    try:
        prep_digest, output, signed_attrs = _run(_prepare())
    finally:
        workdir.cleanup()
    signed_attrs_der = signed_attrs.dump()
    state = {
        'pdf': output.getvalue(),
        'document_digest': prep_digest.document_digest,
        'reserved_start': prep_digest.reserved_region_start,
        'reserved_end': prep_digest.reserved_region_end,
        'signed_attrs': signed_attrs_der,
        'cert': cert.dump(),
        'chain': [c.dump() for c in chain],
        'field_name': field_name,
    }
    return state, hashlib.sha256(signed_attrs_der).digest()


def verify_raw_signature(cert, digest, signature):
    """Kiểm tra chữ ký thô trên ``digest`` bằng khóa công khai của chứng thư."""
    from cryptography import x509 as c_x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

    pub = c_x509.load_der_x509_certificate(load_cert(cert).dump()).public_key()
    try:
        if isinstance(pub, rsa.RSAPublicKey):
            pub.verify(signature, digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
        elif isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(signature, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        else:
            return False
    except InvalidSignature:
        return False
    return True


def finalize_signature(state, signature, tsa_url=None):
    """Bước 3: nhúng chữ ký thô vào PDF đã chuẩn bị. Trả về bytes PDF đã ký."""
    cert = load_cert(state['cert'])
    chain = load_certs(state.get('chain'))
    digest = hashlib.sha256(state['signed_attrs']).digest()
    if not verify_raw_signature(cert, digest, signature):
        raise PadesError('Chữ ký nhận về không khớp với chứng thư số hoặc với tài liệu.')
    signer = signers.ExternalSigner(
        signing_cert=cert, cert_registry=SimpleCertificateStore.from_certs([cert] + chain),
        signature_value=signature)
    signed_attrs = cms.CMSAttributes.load(state['signed_attrs'])
    prep_digest = PreparedByteRangeDigest(
        document_digest=state['document_digest'],
        reserved_region_start=state['reserved_start'],
        reserved_region_end=state['reserved_end'])

    async def _finish():
        sig_cms = await signer.async_sign_prescribed_attributes(
            DIGEST, signed_attrs=signed_attrs, timestamper=_timestamper(tsa_url))
        output = io.BytesIO(state['pdf'])
        await PdfTBSDocument.async_finish_signing(output, prep_digest, sig_cms)
        return output.getvalue()

    try:
        return _run(_finish())
    except PadesError:
        raise
    except Exception as exc:  # noqa: BLE001 – gom lỗi pyHanko (thiếu chỗ, TSA lỗi…) về một loại
        raise PadesError('Không nhúng được chữ ký vào PDF: %s' % exc) from exc


def sign_with_key(pdf_bytes, key_pem, cert, chain=(), password=None, **kwargs):
    """Ký trọn vẹn bằng khóa bí mật có sẵn trên máy chủ (chứng thư mềm / demo).

    Dùng chung đường prepare/finalize để mọi loại khóa ra cùng một định dạng.
    """
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils

    tsa_url = kwargs.pop('tsa_url', None)
    key = serialization.load_pem_private_key(key_pem, password=password)
    state, digest = prepare_signature(pdf_bytes, cert, chain, tsa_url=tsa_url, **kwargs)
    if isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
    elif isinstance(key, ec.EllipticCurvePrivateKey):
        signature = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    else:
        raise PadesError('Loại khóa không được hỗ trợ.')
    return finalize_signature(state, signature, tsa_url=tsa_url)


def load_pkcs12(data, password=None):
    """Đọc file .p12/.pfx → (key_pem, cert_der, [chain_der])."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import pkcs12

    key, cert, extra = pkcs12.load_key_and_certificates(
        data, password.encode() if isinstance(password, str) else password)
    if key is None or cert is None:
        raise PadesError('File chứng thư không chứa khóa bí mật hoặc chứng thư.')
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    return (key_pem, cert.public_bytes(serialization.Encoding.DER),
            [c.public_bytes(serialization.Encoding.DER) for c in (extra or [])])


# --------------------------------------------------------------------------- kiểm tra
def verify_pdf(pdf_bytes, trust_roots=(), allow_fetching=False):
    """Kiểm tra toàn bộ chữ ký số trong PDF.

    :param trust_roots: các Root CA được tin cậy (Root CA quốc gia, CA của nhà cung cấp…).
    :param allow_fetching: cho phép tải OCSP/CRL qua mạng để kiểm tra thu hồi.
    :returns: list dict, mỗi chữ ký một dòng.
    """
    roots = load_certs(trust_roots)
    reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    results = []
    # pyHanko ghi traceback mỗi khi không dựng được chuỗi tin cậy; kết quả đã nằm
    # trong 'trusted' nên tạm tắt log này để không làm rối log Odoo.
    noisy = logging.getLogger('pyhanko.sign.validation.generic_cms')
    previous_level = noisy.level
    noisy.setLevel(logging.CRITICAL)
    try:
        for embedded in reader.embedded_signatures:
            results.append(_verify_one(embedded, roots, allow_fetching))
    finally:
        noisy.setLevel(previous_level)
    return results


def _verify_one(embedded, roots, allow_fetching):
    vc = ValidationContext(trust_roots=roots or None, allow_fetching=allow_fetching,
                           revocation_mode='soft-fail')
    row = {'field': embedded.field_name}
    try:
        status = validate_pdf_signature(embedded, signer_validation_context=vc)
        timestamp = status.timestamp_validity.timestamp if status.timestamp_validity else None
        row.update({
            'intact': bool(status.intact),
            'valid': bool(status.valid),
            'trusted': bool(status.trusted) if roots else False,
            'covers_document': status.coverage.name in ('ENTIRE_FILE', 'ENTIRE_REVISION'),
            'modification': status.modification_level.name if status.modification_level else '',
            'docmdp_ok': status.docmdp_ok is not False,
            'signed_at': timestamp or status.signer_reported_dt,
            'timestamped': bool(timestamp),
            'identity': cert_identity(status.signing_cert),
        })
    except Exception as exc:  # noqa: BLE001 – một chữ ký hỏng không làm hỏng cả báo cáo
        row.update({'intact': False, 'valid': False, 'trusted': False, 'error': str(exc),
                    'identity': cert_identity(embedded.signer_cert) if embedded.signer_cert else {}})
    row['ok'] = bool(row.get('intact') and row.get('valid') and row.get('docmdp_ok', True)
                     and (row.get('trusted') or not roots))
    return row
