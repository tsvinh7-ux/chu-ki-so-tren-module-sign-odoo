import base64
import json
import logging
from datetime import timedelta
from urllib.parse import unquote

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
    """Giá trị ô chữ ký: base64, có hoặc không có tiền tố data:image/...;base64,."""
    if isinstance(value, (list, tuple)) and len(value) == 2:  # [mimetype, base64] của NameAndSignature
        value = value[1]
    if not isinstance(value, str) or not value:
        return b''
    data = value.split(',', 1)[1] if value.startswith('data:') else value
    try:
        return base64.b64decode(data)
    except ValueError:
        return b''


class DmagCksSignDoc(models.Model):
    """Bản PDF "đang ký" của một yêu cầu ký của module Sign, khi có ít nhất một người ký số.

    Mỗi người ký xong, phần của họ được ghi nối tiếp (incremental) vào file rồi chốt lại.
    ``version`` tăng sau mỗi lần ghi để phát hiện hai người ký cùng lúc.
    """
    _name = 'dmag.cks.sign.doc'
    _description = 'Tài liệu ký số trong module Sign'
    _order = 'id desc'

    res_model = fields.Char('Model', required=True, index=True)
    res_id = fields.Many2oneReference('Yêu cầu ký', model_field='res_model', required=True, index=True)
    reference = fields.Char('Tài liệu')
    current_pdf = fields.Binary('Bản hiện tại', attachment=True)
    version = fields.Integer(default=0)
    cks_count = fields.Integer('Số chữ ký số', default=0)
    signer_count = fields.Integer('Số người ký')
    state = fields.Selection([('signing', 'Đang ký'), ('done', 'Hoàn tất')], default='signing')
    verify_ok = fields.Boolean('Chữ ký hợp lệ')
    verify_html = fields.Html('Kết quả kiểm tra', sanitize=False)
    summary = fields.Char('Ký số', compute='_compute_summary')

    _sql_constraints = [('record_uniq', 'unique(res_model, res_id)', 'Mỗi yêu cầu ký chỉ có một tài liệu ký số.')]

    @api.depends('cks_count', 'signer_count')
    def _compute_summary(self):
        for doc in self:
            doc.summary = _('Ký số: %(n)s/%(t)s bên', n=doc.cks_count, t=doc.signer_count or '?')

    # ------------------------------------------------------------------ truy cập
    @api.model
    def _config(self):
        config = self.env['dmag.cks.sign.config']._get()
        if not config:
            raise UserError(_('Chưa bật ký số trong module Sign (Ký số › Cấu hình).'))
        return config

    @api.model
    def _for_request(self, sign_request, lock=False, create=True):
        doc = self.sudo().search([('res_model', '=', sign_request._name), ('res_id', '=', sign_request.id)], limit=1)
        if not doc and create:
            config = self._config()
            doc = self.sudo().create({
                'res_model': sign_request._name, 'res_id': sign_request.id,
                'reference': str(config._val(sign_request, config.f_req_reference) or sign_request.display_name),
                'signer_count': len(config._val(sign_request, config.f_req_items)),
            })
        if doc and lock:
            # Khóa dòng: hai người ký cùng lúc thì người sau chờ, rồi thấy version đã đổi.
            self.env.cr.execute('SELECT id FROM dmag_cks_sign_doc WHERE id = %s FOR UPDATE', [doc.id])
            doc.invalidate_recordset()
        return doc

    def _request(self):
        self.ensure_one()
        return self.env[self.res_model].sudo().browse(self.res_id)

    # ------------------------------------------------------------------ ô ký
    def _template_pdf(self, config):
        attachment = config._val(self._request(), '%s.%s' % (config.f_req_template, config.f_tpl_attachment))
        return _raw(attachment.datas)

    @staticmethod
    def _box(config, item, sizes):
        """Ô ký (tỉ lệ, gốc trên-trái) → (trang 0-based, (x1, y1, x2, y2) theo point)."""
        page = max(int(config._val(item, config.f_item_page) or 1), 1) - 1
        width, height = sizes[min(page, len(sizes) - 1)]
        x, y = float(config._val(item, config.f_item_x)), float(config._val(item, config.f_item_y))
        w, h = float(config._val(item, config.f_item_w)), float(config._val(item, config.f_item_h))
        return page, (x * width, height - (y + h) * height, (x + w) * width, height - y * height)

    @staticmethod
    def _item_type(config, item):
        return str(config._val(item, config.f_item_type) or '')

    def _overlays(self, config, values, sizes, exclude=()):
        overlays = []
        for item, value in values.items():
            if item in exclude or not value:
                continue
            page, box = self._box(config, item, sizes)
            overlay = {'page': page, 'box': box, 'name': 'item-%s-v%s' % (item.id, self.version + 1)}
            if self._item_type(config, item) in SIGNATURE_TYPES:
                image = _image_bytes(value)
                if not image:
                    continue
                overlay['image'] = image
            elif isinstance(value, str):
                overlay['text'] = value
            else:
                continue
            overlays.append(overlay)
        return overlays

    @api.model
    def _role_items(self, config, request_item):
        sign_request = self._request_of(config, request_item)
        template = config._val(sign_request, config.f_req_template)
        role = config._val(request_item, config.f_ri_role)
        items = self.env[config.item_model].sudo().search([('id', 'in', self._template_item_ids(config, template))])
        return items.filtered(lambda i: config._val(i, config.f_item_role) == role)

    @api.model
    def _template_item_ids(self, config, template):
        Item = self.env[config.item_model]
        for name, field in Item._fields.items():
            if field.type == 'many2one' and field.comodel_name == template._name:
                return Item.sudo().search([(name, '=', template.id)]).ids
        return []

    @api.model
    def _request_of(self, config, request_item):
        for name, field in request_item._fields.items():
            if field.type == 'many2one' and field.comodel_name == config.request_model:
                return request_item[name]
        raise UserError(_('Không tìm thấy yêu cầu ký của người ký.'))

    @api.model
    def _values_for(self, config, request_item, signature):
        """Giá trị gửi lên ({id ô ký: value}) → {ô ký: value}, chỉ gồm ô của người ký này."""
        signature = {int(k): v for k, v in (signature or {}).items() if str(k).isdigit()}
        return {item: signature[item.id] for item in self._role_items(config, request_item) if signature.get(item.id)}

    # ------------------------------------------------------------------ bản đầu tiên
    def _ensure_started(self, config):
        """Bản ký số đầu tiên = file gốc + giá trị của những người đã ký trước đó (ký thường)."""
        self.ensure_one()
        if self.current_pdf:
            return
        base = self._template_pdf(config)
        sizes = pades.page_sizes(base)
        values = {}
        for request_item in config._val(self._request(), config.f_req_items):
            if str(request_item[config.f_ri_state]) != config.v_ri_done:
                continue
            for value in config._val(request_item, config.f_ri_values):
                values[value[config.f_val_item]] = value[config.f_val_value]
        pdf = pades.add_overlays(base, self._overlays(config, values, sizes)) if values else base
        self.write({'current_pdf': _b64(pdf), 'version': 1})

    # ------------------------------------------------------------------ kiểm tra người ký
    def _check_turn(self, config, request_item):
        sign_request = self._request()
        if str(config._val(sign_request, config.f_req_state)) != config.v_req_open or \
                str(request_item[config.f_ri_state]) == config.v_ri_done:
            raise UserError(_('Tài liệu này đã được ký hoặc không còn chờ ký.'))
        if config.f_ri_order and config.f_ri_order in request_item._fields:
            mine = request_item[config.f_ri_order] or 0
            earlier = config._val(sign_request, config.f_req_items).filtered(
                lambda r: (r[config.f_ri_order] or 0) < mine and str(r[config.f_ri_state]) != config.v_ri_done)
            if earlier:
                raise UserError(_('Chưa đến lượt bạn ký.'))

    def _check_identity(self, config, request_item, identity):
        """Chứng thư phải khớp người ký (khi so được): MST của khách / của công ty mình, CCCD trong hồ sơ."""
        ids = identity.get('ids') or {}
        partner = request_item[config.f_ri_partner]
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

    def _cks_item(self, config, request_item, values):
        candidates = [i for i in values if self._item_type(config, i) == 'signature'] or \
                     [i for i in values if self._item_type(config, i) in SIGNATURE_TYPES]
        if not candidates:
            raise UserError(_('Mẫu ký không có ô chữ ký cho vai trò "%s".',
                              config._val(request_item, config.f_ri_role).display_name))
        return sorted(candidates, key=lambda i: (config._val(i, config.f_item_page) or 0,
                                                 config._val(i, config.f_item_y) or 0,
                                                 config._val(i, config.f_item_x) or 0, i.id))[0]

    # ------------------------------------------------------------------ ký số: chuẩn bị / nhúng
    def _prepare_item(self, request_item, signature, cert, chain=(), method='usb', provider=None, remote_user_id=None):
        """Ghi các ô của người ký vào bản hiện tại + chừa chỗ chữ ký số. Trả về (digest, prep)."""
        self.ensure_one()
        config = self._config()
        self._check_turn(config, request_item)
        identity = pades.cert_identity(cert)
        self._check_identity(config, request_item, identity)
        if identity['not_after'].replace(tzinfo=None) < fields.Datetime.now():
            raise UserError(_('Chứng thư số đã hết hạn ngày %s.', identity['not_after'].strftime('%d/%m/%Y')))
        values = self._values_for(config, request_item, signature)
        if config.f_item_required:
            missing = self._role_items(config, request_item).filtered(
                lambda i: config._val(i, config.f_item_required) and i not in values)
            if missing:
                raise UserError(_('Bạn chưa điền đủ các ô bắt buộc.'))
        self._ensure_started(config)
        current = _raw(self.current_pdf)
        sizes = pades.page_sizes(current)
        cks_item = self._cks_item(config, request_item, values)
        pdf = pades.add_overlays(current, self._overlays(config, values, sizes, exclude=(cks_item,)))
        page, box = self._box(config, cks_item, sizes)
        partner = request_item[config.f_ri_partner]
        internal = bool(partner.user_ids.filtered(lambda u: not u.share))
        role = config._val(request_item, config.f_ri_role)
        label = '%s – %s' % (role.display_name, self.env.company.name if internal
                             else partner.commercial_partner_id.name)
        try:
            state, digest = pades.prepare_signature(
                pdf, cert, chain, field_name='CKS_%s_%s' % (request_item.id, self.version),
                page=page, box=box, label=label.upper(), reason=_('Ký %s', self.reference or ''),
                location=self.env.company.city or 'Việt Nam')
        except pades.PadesError as exc:
            raise UserError(str(exc)) from exc
        self.env['dmag.cks.sign.prep'].sudo().search([
            ('doc_id', '=', self.id), ('item_id', '=', request_item.id)]).unlink()
        prep = self.env['dmag.cks.sign.prep'].sudo().create({
            'doc_id': self.id, 'item_model': request_item._name, 'item_id': request_item.id,
            'base_version': self.version, 'method': method, 'status': 'prepared',
            'provider_id': provider.id if provider else False, 'remote_user_id': remote_user_id,
            'identity': identity['display'], 'prepared_pdf': _b64(state['pdf']),
            'state_json': json.dumps({
                'document_digest': state['document_digest'].hex(),
                'reserved_start': state['reserved_start'], 'reserved_end': state['reserved_end'],
                'signed_attrs': base64.b64encode(state['signed_attrs']).decode(),
                'cert': base64.b64encode(state['cert']).decode(),
                'chain': [base64.b64encode(c).decode() for c in state['chain']],
            }),
            'values_json': json.dumps({str(item.id): (value if isinstance(value, str) else '') for item, value
                                       in values.items()}),
        })
        return digest, prep

    def _finalize_pending(self, prep, signature_bytes):
        """Nhúng chữ ký vào bản đã chuẩn bị. Chưa chốt: chốt khi module Sign nhận chữ ký (cùng giao dịch).

        Trả về ``{'conflict': True}`` nếu có người khác vừa ký trước: trang ký sẽ chuẩn bị lại.
        """
        self.ensure_one()
        if prep.doc_id != self or prep.base_version != self.version:
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
            prep.unlink()
            raise UserError(str(exc)) from exc
        prep.write({'signed_pdf': _b64(signed), 'status': 'ready', 'prepared_pdf': False})
        return {'conflict': False}

    # ------------------------------------------------------------------ móc vào module Sign
    @api.model
    def _dmag_before_vendor_sign(self, request_items, args, kwargs):
        """Chạy ngay trước hàm "ký" của module Sign (cùng giao dịch)."""
        config = self.env['dmag.cks.sign.config']._get()
        if not config or self.env.context.get('dmag_cks_skip'):
            return
        error = self._request_header('X-DMAG-CKS-Error')
        if error:
            raise UserError(_('Ký số chưa thành công: %s', unquote(error)))
        signature = kwargs.get('signature', args[0] if args else {}) or {}
        for request_item in request_items:
            sign_request = self._request_of(config, request_item)
            role = config._val(request_item, config.f_ri_role)
            doc = self._for_request(sign_request, create=False)
            prep = self.env['dmag.cks.sign.prep'].sudo().search([
                ('item_model', '=', request_item._name), ('item_id', '=', request_item.id),
                ('status', '=', 'ready')], order='id desc', limit=1)
            if prep:
                doc = self._for_request(sign_request, lock=True)
                if prep.doc_id != doc or prep.base_version != doc.version:
                    prep.unlink()
                    raise UserError(_('Có người vừa ký trước bạn. Vui lòng bấm ký lại để ký số trên bản mới nhất.'))
                doc._commit(config, request_item, prep)
            elif config._role_required(role):
                raise UserError(_('Vai trò "%s" phải ký bằng chữ ký số (USB token hoặc app ký số).',
                                  role.display_name))
            elif doc and doc.cks_count:
                doc = self._for_request(sign_request, lock=True)
                values = self._values_for(config, request_item, signature)
                if values:
                    current = _raw(doc.current_pdf)
                    pdf = pades.add_overlays(current, doc._overlays(config, values, pades.page_sizes(current)))
                    doc.write({'current_pdf': _b64(pdf), 'version': doc.version + 1})

    def _commit(self, config, request_item, prep):
        self.ensure_one()
        partner = request_item[config.f_ri_partner]
        self.write({'current_pdf': prep.signed_pdf, 'version': self.version + 1, 'cks_count': self.cks_count + 1})
        how = _('USB token') if prep.method == 'usb' else (prep.provider_id.customer_label or prep.provider_id.name)
        identity = prep.identity or ''
        prep.unlink()
        sign_request = self._request()
        if hasattr(sign_request, 'message_post'):
            ip = self._request_ip()
            sign_request.message_post(body=Markup(_(
                '%(who)s đã <b>ký số</b> (%(how)s). Chứng thư: %(subject)s%(ip)s.', who=partner.name, how=how,
                subject=identity, ip=(' (IP %s)' % ip) if ip else '')))

    @api.model
    def _dmag_vendor_complete(self, sign_requests):
        """Chạy thay hàm "tạo file hoàn tất": tài liệu có chữ ký số thì dùng bản đã chốt, không vẽ lại.

        Trả về các yêu cầu ký còn lại để module Sign xử lý như bình thường.
        """
        config = self.env['dmag.cks.sign.config']._get()
        if not config:
            return sign_requests
        rest = sign_requests.browse()
        for sign_request in sign_requests:
            doc = self._for_request(sign_request, create=False)
            if not doc or not doc.cks_count or not doc.current_pdf:
                rest |= sign_request
                continue
            sign_request.sudo()[config.f_req_completed] = doc.current_pdf
            results = doc._verify()
            doc.state = 'done'
            if hasattr(sign_request, 'message_post'):
                sign_request.message_post(body=_(
                    'Tài liệu hoàn tất có %(n)s chữ ký số; kiểm tra: %(r)s.', n=len(results),
                    r=_('hợp lệ') if doc.verify_ok else _('cần xem lại')))
        return rest

    @staticmethod
    def _request_header(name):
        try:
            from odoo.http import request
            return request.httprequest.headers.get(name) if request else None
        except RuntimeError:
            return None

    @staticmethod
    def _request_ip():
        try:
            from odoo.http import request
            return request.httprequest.remote_addr if request else None
        except RuntimeError:
            return None

    # ------------------------------------------------------------------ khác
    def _verify(self):
        self.ensure_one()
        roots = self.env['dmag.cks.trust.root']._get_der_list()
        results = pades.verify_pdf(_raw(self.current_pdf), roots)
        self.write({'verify_ok': bool(results) and all(r['ok'] for r in results),
                    'verify_html': self.env['dmag.cks.envelope']._render_verify(results)})
        return results

    def action_open_request(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': self.res_model, 'res_id': self.res_id,
                'view_mode': 'form'}

    def action_verify(self):
        for doc in self.filtered('current_pdf'):
            doc._verify()


class DmagCksSignPrep(models.Model):
    """Phiên ký số đang dở của một người ký (giữa lúc chuẩn bị và lúc module Sign nhận chữ ký)."""
    _name = 'dmag.cks.sign.prep'
    _description = 'Phiên ký số đang dở'

    doc_id = fields.Many2one('dmag.cks.sign.doc', required=True, ondelete='cascade')
    item_model = fields.Char(required=True)
    item_id = fields.Many2oneReference(model_field='item_model', required=True, index=True)
    base_version = fields.Integer(required=True)
    method = fields.Selection([('usb', 'USB token'), ('cloud', 'App ký số')], required=True, default='usb')
    status = fields.Selection([('prepared', 'Đã chuẩn bị'), ('waiting', 'Chờ xác nhận trên app'),
                               ('ready', 'Đã ký, chờ module Sign nhận')], default='prepared')
    provider_id = fields.Many2one('dmag.cks.provider')
    remote_user_id = fields.Char()
    credential_id = fields.Char()
    transaction_id = fields.Char()
    deadline = fields.Datetime()
    identity = fields.Char()
    prepared_pdf = fields.Binary(attachment=True)
    signed_pdf = fields.Binary(attachment=True)
    state_json = fields.Text()
    values_json = fields.Text()

    def _digest(self):
        self.ensure_one()
        import hashlib

        return hashlib.sha256(base64.b64decode(json.loads(self.state_json)['signed_attrs'])).digest()

    def _cloud_poll(self):
        """Hỏi nhà cung cấp kết quả xác nhận trên app; ký xong thì nhúng chữ ký."""
        self.ensure_one()
        if self.status != 'waiting':
            return {'state': {'ready': 'signed'}.get(self.status, self.status)}
        if self.deadline and self.deadline < fields.Datetime.now():
            self.unlink()
            return {'state': 'expired', 'message': _('Hết hạn chờ xác nhận trên app. Vui lòng ký lại.')}
        result = self.provider_id._call('get_status', self.remote_user_id, self.credential_id, self.transaction_id)
        return self._cloud_handle(result)

    def _cloud_handle(self, result):
        self.ensure_one()
        state = result.get('state')
        if state == 'signed' and result.get('signature'):
            doc = self.doc_id._for_request(self.doc_id._request(), lock=True)
            outcome = doc._finalize_pending(self, result['signature'])
            return {'state': 'conflict' if outcome['conflict'] else 'signed'}
        if state == 'waiting':
            vals = {'status': 'waiting', 'transaction_id': result.get('transaction_id') or self.transaction_id}
            if not self.deadline:
                vals['deadline'] = fields.Datetime.now() + timedelta(minutes=self.provider_id.request_ttl or 10)
            self.write(vals)
            return {'state': 'waiting', 'message': result.get('message') or ''}
        message = {'rejected': _('Bạn đã từ chối trên ứng dụng ký số.'),
                   'expired': _('Yêu cầu ký đã hết hạn.')}.get(state, result.get('message') or _('Nhà cung cấp báo lỗi.'))
        self.unlink()
        return {'state': state or 'error', 'message': message}

    @api.autovacuum
    def _gc_old(self):
        limit = fields.Datetime.subtract(fields.Datetime.now(), hours=6)
        self.sudo().search([('create_date', '<', limit)]).unlink()
