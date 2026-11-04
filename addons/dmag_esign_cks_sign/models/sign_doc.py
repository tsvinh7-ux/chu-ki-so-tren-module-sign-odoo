import base64
import json
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.dmag_esign_cks.tools import pades

_logger = logging.getLogger(__name__)

SIGNATURE_TYPES = ('signature', 'initial')


def _b64(data):
    return base64.b64encode(data) if data else False


def _raw(value):
    return base64.b64decode(value) if value else b''


def _image_bytes(value):
    """Giá trị ô chữ ký của Sign: base64, có hoặc không có tiền tố data:image/...;base64,."""
    if not value:
        return b''
    data = value.split(',', 1)[1] if value.startswith('data:') else value
    try:
        return base64.b64decode(data)
    except ValueError:
        return b''


class DmagCksSignDoc(models.Model):
    """Bản PDF "đang ký" của một yêu cầu ký, khi có ít nhất một người ký bằng chữ ký số.

    Mỗi người ký xong, phần của họ được ghi nối tiếp vào file (incremental) rồi chốt lại.
    ``version`` tăng sau mỗi lần ghi để phát hiện hai người ký cùng lúc.
    """
    _name = 'dmag.cks.sign.doc'
    _description = 'Tài liệu ký số trong Sign'

    request_id = fields.Many2one('sign.request', required=True, ondelete='cascade', index=True)
    current_pdf = fields.Binary('Bản hiện tại', attachment=True)
    version = fields.Integer(default=0)
    cks_count = fields.Integer('Số chữ ký số', default=0)
    verify_ok = fields.Boolean('Chữ ký hợp lệ')
    verify_html = fields.Html('Kết quả kiểm tra', sanitize=False)

    _sql_constraints = [('request_uniq', 'unique(request_id)', 'Mỗi yêu cầu ký chỉ có một tài liệu ký số.')]

    # ------------------------------------------------------------------ truy cập
    @api.model
    def _for_request(self, sign_request, lock=False):
        doc = self.sudo().search([('request_id', '=', sign_request.id)], limit=1)
        if not doc:
            doc = self.sudo().create({'request_id': sign_request.id})
        if lock:
            # Khóa dòng: hai người ký cùng lúc thì người sau chờ, rồi thấy version đã đổi.
            self.env.cr.execute('SELECT id FROM dmag_cks_sign_doc WHERE id = %s FOR UPDATE', [doc.id])
            doc.invalidate_recordset()
        return doc

    # ------------------------------------------------------------------ hình học ô ký
    def _template_pdf(self):
        attachment = self.request_id.template_id.attachment_id
        return _raw(attachment.datas)

    @staticmethod
    def _box(item, sizes):
        """Ô ký Odoo (tỉ lệ, gốc trên-trái) → (trang 0-based, (x1, y1, x2, y2) theo point)."""
        page = max(int(item.page or 1), 1) - 1
        width, height = sizes[min(page, len(sizes) - 1)]
        x1 = item.posX * width
        x2 = (item.posX + item.width) * width
        y2 = height - item.posY * height
        y1 = height - (item.posY + item.height) * height
        return page, (x1, y1, x2, y2)

    def _overlays(self, values, sizes, exclude=()):
        """Giá trị {sign.item: value} → danh sách lớp ghi thêm cho pades.add_overlays."""
        overlays = []
        for item, value in values.items():
            if item in exclude or not value:
                continue
            page, box = self._box(item, sizes)
            overlay = {'page': page, 'box': box, 'name': 'item-%s-v%s' % (item.id, self.version + 1)}
            if item.type_id.item_type in SIGNATURE_TYPES:
                image = _image_bytes(value)
                if not image:
                    continue
                overlay['image'] = image
                overlay['label'] = item.type_id.name
            else:
                overlay['text'] = value
            overlays.append(overlay)
        return overlays

    @staticmethod
    def _values_for(request_item, signature):
        """Giá trị gửi lên ({id ô: value}) → {sign.item: value} chỉ gồm ô của người ký này."""
        items = request_item.sign_request_id.template_id.sign_item_ids.filtered(
            lambda i: i.responsible_id == request_item.role_id)
        signature = {int(k): v for k, v in (signature or {}).items()}
        return {item: signature[item.id] for item in items if signature.get(item.id)}

    # ------------------------------------------------------------------ bản đầu tiên
    def _ensure_started(self):
        """Bản ký số đầu tiên = file gốc + mọi giá trị của những người đã ký trước đó (ký thường)."""
        self.ensure_one()
        if self.current_pdf:
            return
        base = self._template_pdf()
        sizes = pades.page_sizes(base)
        values = {}
        for request_item in self.request_id.request_item_ids.filtered(lambda r: r.state == 'completed'):
            for value in request_item.sign_item_value_ids:
                values[value.sign_item_id] = value.value
        pdf = pades.add_overlays(base, self._overlays(values, sizes)) if values else base
        self.write({'current_pdf': _b64(pdf), 'version': 1})

    # ------------------------------------------------------------------ ký số
    def _check_turn(self, request_item):
        if request_item.state != 'sent' or self.request_id.state != 'sent':
            raise UserError(_('Tài liệu này đã được ký hoặc không còn chờ ký.'))
        if hasattr(request_item, '_is_turn') and not request_item._is_turn():
            raise UserError(_('Chưa đến lượt bạn ký.'))
        if 'mail_sent_order' in request_item._fields:
            earlier = self.request_id.request_item_ids.filtered(
                lambda r: r.mail_sent_order < request_item.mail_sent_order and r.state != 'completed')
            if earlier:
                raise UserError(_('Chưa đến lượt bạn ký.'))

    def _check_identity(self, request_item, identity):
        """Chứng thư phải khớp người ký trong yêu cầu ký (khi so được).

        - Chứng thư tổ chức (có MST): MST phải khớp MST của khách, hoặc của công ty mình nếu người ký
          là nhân viên nội bộ.
        - Chứng thư cá nhân (có CCCD): khớp số CCCD trong hồ sơ người ký, nếu hồ sơ có số CCCD.
        """
        ids = identity.get('ids') or {}
        partner = request_item.partner_id
        internal = bool(partner.user_ids.filtered(lambda u: not u.share))
        expected_tax = (self.env.company.vat if internal else partner.commercial_partner_id.vat) or ''
        problems = []
        if ids.get('MST') and expected_tax and not pades.identity_matches(identity, tax_id=expected_tax):
            problems.append(_('MST trong chứng thư là %(f)s, hồ sơ là %(e)s', f=ids['MST'],
                              e=pades.normalize_id(expected_tax)))
        personal = partner.dmag_cks_personal_id
        for kind in ('CCCD', 'CMND'):
            if ids.get(kind) and personal and not pades.identity_matches(identity, personal_id=personal):
                problems.append(_('%(k)s trong chứng thư là %(f)s, hồ sơ là %(e)s', k=kind, f=ids[kind],
                                  e=pades.normalize_id(personal)))
        if problems:
            raise UserError(_('Chứng thư số không khớp người ký: %s.', '; '.join(problems)))

    def _cks_item(self, request_item, values):
        """Ô dùng làm vị trí chữ ký số: ô chữ ký đầu tiên của người ký (theo trang, vị trí)."""
        candidates = [item for item in values if item.type_id.item_type == 'signature'] or \
                     [item for item in values if item.type_id.item_type in SIGNATURE_TYPES]
        if not candidates:
            raise UserError(_('Mẫu ký không có ô chữ ký cho vai trò "%s".', request_item.role_id.name))
        return sorted(candidates, key=lambda i: (i.page, i.posY, i.posX, i.id))[0]

    def _prepare_item(self, request_item, signature, cert, chain=()):
        """Ghi các ô của người ký vào bản hiện tại + chừa chỗ chữ ký số. Trả về (digest, prep)."""
        self.ensure_one()
        self._check_turn(request_item)
        identity = pades.cert_identity(cert)
        self._check_identity(request_item, identity)
        if identity['not_after'].replace(tzinfo=None) < fields.Datetime.now():
            raise UserError(_('Chứng thư số đã hết hạn ngày %s.', identity['not_after'].strftime('%d/%m/%Y')))
        values = self._values_for(request_item, signature)
        missing = request_item.sign_request_id.template_id.sign_item_ids.filtered(
            lambda i: i.responsible_id == request_item.role_id and i.required and i not in values)
        if missing:
            raise UserError(_('Bạn chưa điền đủ các ô bắt buộc.'))
        self._ensure_started()
        current = _raw(self.current_pdf)
        sizes = pades.page_sizes(current)
        cks_item = self._cks_item(request_item, values)
        pdf = pades.add_overlays(current, self._overlays(values, sizes, exclude=(cks_item,)))
        page, box = self._box(cks_item, sizes)
        partner = request_item.partner_id
        internal = bool(partner.user_ids.filtered(lambda u: not u.share))
        label = '%s – %s' % (request_item.role_id.name,
                             self.env.company.name if internal else partner.commercial_partner_id.name)
        try:
            state, digest = pades.prepare_signature(
                pdf, cert, chain, field_name='CKS_%s_%s' % (request_item.id, self.version),
                page=page, box=box, label=label.upper(), reason=_('Ký %s', self.request_id.reference),
                location=self.env.company.city or 'Việt Nam')
        except pades.PadesError as exc:
            raise UserError(str(exc)) from exc
        prep = self.env['dmag.cks.sign.prep'].sudo().create({
            'doc_id': self.id, 'request_item_id': request_item.id, 'base_version': self.version,
            'prepared_pdf': _b64(state['pdf']),
            'state_json': json.dumps({
                'document_digest': state['document_digest'].hex(),
                'reserved_start': state['reserved_start'], 'reserved_end': state['reserved_end'],
                'signed_attrs': base64.b64encode(state['signed_attrs']).decode(),
                'cert': base64.b64encode(state['cert']).decode(),
                'chain': [base64.b64encode(c).decode() for c in state['chain']],
            }),
            'values_json': json.dumps({str(item.id): value for item, value in values.items()}),
        })
        return digest, prep

    def _finalize_item(self, request_item, prep, signature_bytes, ip=None):
        """Nhúng chữ ký, chốt bản mới, rồi đánh dấu người ký đã ký trên module Sign (cùng giao dịch).

        Trả về ``{'conflict': True}`` nếu có người khác vừa ký trước: trang ký tự chuẩn bị lại.
        """
        self.ensure_one()
        if prep.request_item_id != request_item or prep.doc_id != self:
            raise UserError(_('Phiên ký không hợp lệ, vui lòng ký lại.'))
        if prep.base_version != self.version:
            prep.unlink()
            return {'conflict': True}
        state = json.loads(prep.state_json)
        try:
            signed = pades.finalize_signature({
                'pdf': _raw(prep.prepared_pdf),
                'document_digest': bytes.fromhex(state['document_digest']),
                'reserved_start': state['reserved_start'], 'reserved_end': state['reserved_end'],
                'signed_attrs': base64.b64decode(state['signed_attrs']),
                'cert': base64.b64decode(state['cert']),
                'chain': [base64.b64decode(c) for c in state['chain']],
            }, signature_bytes)
        except pades.PadesError as exc:
            raise UserError(str(exc)) from exc
        identity = pades.cert_identity(base64.b64decode(state['cert']))
        values = json.loads(prep.values_json)
        self.write({'current_pdf': _b64(signed), 'version': self.version + 1, 'cks_count': self.cks_count + 1})
        prep.unlink()
        request_item.with_context(dmag_cks_signing=True)._sign(values)
        self.request_id.message_post(body=Markup(_(
            '%(who)s đã <b>ký số bằng USB token</b>. Chứng thư: %(subject)s, cấp bởi %(issuer)s, số hiệu %(serial)s%(ip)s.',
            who=request_item.partner_id.name, subject=identity['display'], issuer=identity['issuer'],
            serial=identity['serial'], ip=(' (IP %s)' % ip) if ip else '')))
        return {'conflict': False}

    def _append_values(self, request_item, signature):
        """Người ký thường (vẽ / gõ / tải ảnh) sau khi đã có chữ ký số: ghi thêm thành lớp mới."""
        self.ensure_one()
        values = self._values_for(request_item, signature)
        if not values:
            return
        current = _raw(self.current_pdf)
        pdf = pades.add_overlays(current, self._overlays(values, pades.page_sizes(current)))
        self.write({'current_pdf': _b64(pdf), 'version': self.version + 1})

    # ------------------------------------------------------------------ hoàn tất
    def _verify(self):
        self.ensure_one()
        roots = self.env['dmag.cks.trust.root']._get_der_list()
        results = pades.verify_pdf(_raw(self.current_pdf), roots)
        html = self.env['dmag.cks.envelope']._render_verify(results)
        self.write({'verify_ok': bool(results) and all(r['ok'] for r in results), 'verify_html': html})
        return results


class DmagCksSignPrep(models.Model):
    """Phiên ký đang chờ USB token (giữa bước chuẩn bị và bước nhúng chữ ký)."""
    _name = 'dmag.cks.sign.prep'
    _description = 'Phiên ký số đang chờ'

    doc_id = fields.Many2one('dmag.cks.sign.doc', required=True, ondelete='cascade')
    request_item_id = fields.Many2one('sign.request.item', required=True, ondelete='cascade')
    base_version = fields.Integer(required=True)
    prepared_pdf = fields.Binary(attachment=True)
    state_json = fields.Text()
    values_json = fields.Text()

    @api.autovacuum
    def _gc_old(self):
        limit = fields.Datetime.subtract(fields.Datetime.now(), hours=6)
        self.sudo().search([('create_date', '<', limit)]).unlink()
