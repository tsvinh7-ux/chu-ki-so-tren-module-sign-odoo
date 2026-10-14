"""Trang "XÁC NHẬN KÝ SỐ" nối vào cuối hợp đồng trước khi ký.

Ô chữ ký số của từng bên được đặt trên trang riêng này. Như vậy không đè lên nội
dung hợp đồng, và trang ghi rõ mã SHA-256 của bản gốc.
"""
import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .pades import _ascii, find_font

PAGE_W, PAGE_H = A4
BOX_W, BOX_H = 245, 82
LEFT_X, RIGHT_X = 45, PAGE_W - 45 - BOX_W
TOP_Y = PAGE_H - 330
ROW_GAP = 128


def signer_boxes(count):
    """Vị trí ô chữ ký thứ i (x1, y1, x2, y2). Bên khách bên trái, DMAG bên phải."""
    boxes = []
    for index in range(count):
        row, col = divmod(index, 2)
        x = LEFT_X if col == 0 else RIGHT_X
        y = TOP_Y - row * ROW_GAP
        boxes.append((x, y - BOX_H, x + BOX_W, y))
    return boxes


def _font():
    path = find_font()
    if not path:
        return 'Helvetica', 'Helvetica-Bold', _ascii
    if 'DMAGSans' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('DMAGSans', path))
        bold = path.replace('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')
        try:
            pdfmetrics.registerFont(TTFont('DMAGSans-Bold', bold))
        except Exception:  # noqa: BLE001 – không có bản đậm thì dùng bản thường
            pdfmetrics.registerFont(TTFont('DMAGSans-Bold', path))
    return 'DMAGSans', 'DMAGSans-Bold', (lambda text: text)


def build_confirm_page(title, reference, source_sha256, signers, company_name, note=None):
    """Sinh trang xác nhận.

    :param signers: list dict ``{'label': 'BÊN B (Khách hàng)', 'name': ..., 'detail': ...}``
        theo đúng thứ tự ô ký.
    :returns: ``(pdf_bytes, boxes)``
    """
    regular, bold, fix = _font()
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    pdf.setTitle(fix('Xác nhận ký số - %s' % reference))
    y = PAGE_H - 70
    pdf.setFont(bold, 15)
    pdf.drawCentredString(PAGE_W / 2, y, fix('TRANG XÁC NHẬN KÝ SỐ'))
    y -= 22
    pdf.setFont(regular, 9)
    pdf.drawCentredString(PAGE_W / 2, y, fix('(Trang này là một phần không tách rời của tài liệu)'))
    y -= 34
    rows = [
        ('Tài liệu', title),
        ('Mã hồ sơ ký', reference),
        ('SHA-256 bản gốc', source_sha256),
        ('Đơn vị phát hành', company_name),
    ]
    for label, value in rows:
        pdf.setFont(bold, 9.5)
        pdf.drawString(LEFT_X, y, fix(label + ':'))
        pdf.setFont(regular, 9.5 if label != 'SHA-256 bản gốc' else 7.5)
        pdf.drawString(LEFT_X + 110, y, fix(value or ''))
        y -= 17
    y -= 6
    pdf.setFont(regular, 8.5)
    text = note or ('Các bên ký tài liệu này bằng chữ ký số theo Luật Giao dịch điện tử 2023. '
                    'Mọi thay đổi nội dung sau khi ký sẽ làm chữ ký số không còn hợp lệ.')
    for line in _wrap(fix(text), regular, 8.5, PAGE_W - 2 * LEFT_X):
        pdf.drawString(LEFT_X, y, line)
        y -= 12
    boxes = signer_boxes(len(signers))
    for signer, (x1, y1, x2, y2) in zip(signers, boxes):
        _fit_text(pdf, x1, y2 + 26, fix(signer.get('label') or ''), bold, 10, x2 - x1)
        _fit_text(pdf, x1, y2 + 13, fix(signer.get('name') or ''), regular, 8.5, x2 - x1)
        if signer.get('detail'):
            _fit_text(pdf, x1, y2 + 3, fix(signer['detail']), regular, 8.5, x2 - x1)
        pdf.setDash(2, 2)
        pdf.setStrokeGray(0.6)
        pdf.rect(x1, y1, x2 - x1, y2 - y1)
        pdf.setDash()
    pdf.showPage()
    pdf.save()
    return buf.getvalue(), boxes


def _fit_text(pdf, x, y, text, font, size, width, min_size=6.5):
    """Vẽ một dòng vừa khung: thu nhỏ chữ, nếu vẫn dài thì cắt bớt."""
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > width:
        size -= 0.5
    while len(text) > 4 and pdfmetrics.stringWidth(text, font, size) > width:
        text = text[:-2] + '…'
    pdf.setFont(font, size)
    pdf.drawString(x, y, text)


def _wrap(text, font, size, width):
    words, line, lines = text.split(), '', []
    for word in words:
        candidate = (line + ' ' + word).strip()
        if pdfmetrics.stringWidth(candidate, font, size) > width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines
