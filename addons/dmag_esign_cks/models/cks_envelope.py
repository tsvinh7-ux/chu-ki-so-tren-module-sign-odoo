import base64
import hashlib
import json
import logging

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import AccessDenied, UserError
from odoo.tools import consteq
from odoo.tools.pdf import merge_pdf

from ..tools import confirm_page, pades

_logger = logging.getLogger(__name__)

STATES = [
    ('draft', 'Nháp'),
    ('customer', 'Chờ khách ký số'),
    ('company', 'Chờ DMAG ký số'),
    ('done', 'Hoàn tất'),
    ('cancel', 'Đã hủy'),
]


def _b64(data):
    return base64.b64encode(data) if data else False


def _raw(value):
    return base64.b64decode(value) if value else b''


class DmagCksEnvelope(models.Model):
    """Hồ sơ ký số: một tài liệu PDF đi qua các chữ ký số (khách trước, DMAG sau)."""
    _name = 'dmag.cks.envelope'
    _description = 'Hồ sơ ký số'
    _inherit = ['mail.thread', 'portal.mixin']
    _order = 'id desc'

    name = fields.Char('Mã hồ sơ', readonly=True, copy=False, default='/')
    document_name = fields.Char('Tài liệu', required=True, tracking=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, required=True)
    user_id = fields.Many2one('res.users', 'Phụ trách', default=lambda self: self.env.user, tracking=True)
    partner_id = fields.Many2one('res.partner', 'Khách hàng', required=True, tracking=True)
    state = fields.Selection(STATES, default='draft', required=True, tracking=True, copy=False)

    res_model = fields.Char('Model nguồn', index=True, readonly=True)
    res_id = fields.Many2oneReference('ID nguồn', model_field='res_model', index=True, readonly=True)

    source_pdf = fields.Binary('Bản gốc', attachment=True, required=True)
    source_filename = fields.Char()
    source_sha256 = fields.Char('SHA-256 bản gốc', readonly=True, copy=False)
    current_pdf = fields.Binary('Bản hiện tại', attachment=True, readonly=True, copy=False)
    signed_pdf = fields.Binary('Bản đã ký số', attachment=True, readonly=True, copy=False)
    signed_filename = fields.Char(compute='_compute_signed_filename')

    signer_ids = fields.One2many('dmag.cks.signer', 'envelope_id', 'Người ký', copy=True)
    company_provider_id = fields.Many2one(
        'dmag.cks.provider', 'CKS của DMAG', domain=[('use_for_company', '=', True)],
        default=lambda self: self.env['dmag.cks.provider'].search([('use_for_company', '=', True)], limit=1))
    add_confirm_page = fields.Boolean('Thêm trang xác nhận ký số', default=True)

    verify_ok = fields.Boolean('Chữ ký hợp lệ', readonly=True, copy=False)
    verify_html = fields.Html('Kết quả kiểm tra', readonly=True, sanitize=False, copy=False)
    verified_at = fields.Datetime('Kiểm tra lúc', readonly=True, copy=False)
    error_message = fields.Char('Lỗi gần nhất', readonly=True, copy=False)
    done_date = fields.Datetime('Hoàn tất lúc', readonly=True, copy=False)

    @api.depends('document_name')
    def _compute_signed_filename(self):
        for env in self:
            env.signed_filename = '%s - da ky so.pdf' % (env.document_name or env.name)

    def _compute_access_url(self):
        super()._compute_access_url()
        for env in self:
            env.access_url = '/cks/ky/%s' % env.id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', '/') == '/':
                vals['name'] = self.env['ir.sequence'].next_by_code('dmag.cks.envelope') or '/'
        return super().create(vals_list)

    # ------------------------------------------------------------------ truy cập công khai
    def _check_token(self, token):
        self.ensure_one()
        if not token or not self.access_token or not consteq(self.access_token, token):
            raise AccessDenied()
        return self.sudo()

    def _current_signer(self):
        """Người ký tiếp theo (ký lần lượt theo thứ tự: khách trước, DMAG sau)."""
        self.ensure_one()
        return self.signer_ids.filtered(lambda s: s.state != 'signed').sorted('sequence')[:1]

    def _customer_signers(self):
        self.ensure_one()
        return self.signer_ids.filtered(lambda s: s.role == 'customer')

    # ------------------------------------------------------------------ chuẩn bị
    def _build_base_pdf(self):
        """Bản gốc + trang xác nhận ký số. Trả về (pdf_bytes, danh sách ô ký)."""
        self.ensure_one()
        source = _raw(self.source_pdf)
        if not source.startswith(b'%PDF'):
            raise UserError(_('Tài liệu "%s" không phải file PDF.', self.document_name))
        signers = self.signer_ids.sorted('sequence')
        existing = pades.count_signatures(source)
        if self.add_confirm_page and not existing:
            page, boxes = confirm_page.build_confirm_page(
                self.document_name, self.name, hashlib.sha256(source).hexdigest(),
                [{'label': s.label, 'name': s.display_label_name, 'detail': s.display_label_detail}
                 for s in signers],
                self.company_id.name)
            return merge_pdf([source, page]), [{'page': -1, 'box': box} for box in boxes]
        # Đã có chữ ký số (hoặc không muốn thêm trang): đặt ô ký ở chân trang cuối,
        # ghi kiểu incremental để không làm hỏng chữ ký có sẵn.
        boxes = []
        for index, _signer in enumerate(signers):
            x = 40 + (index % 2) * 270
            y = 30 + (index // 2) * 75
            boxes.append({'page': -1, 'box': (x, y, x + 240, y + 65)})
        return source, boxes

    def action_send(self):
        for env in self:
            if env.state != 'draft':
                continue
            if not env.company_provider_id:
                raise UserError(_('Chưa cấu hình chứng thư số của DMAG (Ký số › Cấu hình › Nhà cung cấp).'))
            if not env.signer_ids.filtered(lambda s: s.role == 'company'):
                env._add_company_signer()
            base, boxes = env._build_base_pdf()
            for signer, box in zip(env.signer_ids.sorted('sequence'), boxes):
                signer.write({'field_name': 'CKS_%s_%s' % (signer.role.upper(), signer.id),
                              'box_json': json.dumps(box)})
            env._portal_ensure_token()
            env.write({
                'source_sha256': hashlib.sha256(_raw(env.source_pdf)).hexdigest(),
                'current_pdf': _b64(base),
                'state': 'customer' if env._customer_signers() else 'company',
                'error_message': False,
            })
            if env._customer_signers():
                template = self.env.ref('dmag_esign_cks.mail_template_cks_request', raise_if_not_found=False)
                if template and env.partner_id.email:
                    env.message_post_with_source(template, subtype_xmlid='mail.mt_comment')
                env.message_post(body=Markup(_('Đã gửi yêu cầu ký số cho khách. Link ký: <a href="%s">%s</a>'))
                                 % (env.get_portal_url(), env.get_portal_url()))
            else:
                env._sign_company()
        return True

    def _add_company_signer(self):
        self.ensure_one()
        provider = self.company_provider_id
        self.env['dmag.cks.signer'].create({
            'envelope_id': self.id, 'sequence': 1000, 'role': 'company',
            'label': _('BÊN A – %s', self.company_id.name.upper()),
            'partner_id': self.company_id.partner_id.id, 'signer_kind': 'organization',
            'method': 'server' if provider.provider_type == 'local_cert' else 'cloud',
            'provider_id': provider.id,
            'remote_user_id': provider.company_user_id or (
                'MST:%s' % (self.company_id.vat or '') if provider.provider_type == 'demo_cloud' else False),
            'strict_identity': False,
        })

    def action_copy_link(self):
        self.ensure_one()
        self._portal_ensure_token()
        return {'type': 'ir.actions.act_url', 'url': self.get_portal_url(), 'target': 'new'}

    # ------------------------------------------------------------------ ký: chuẩn bị / hoàn tất
    def _prepare_for(self, signer, cert, chain=()):
        """Chừa chỗ chữ ký cho ``signer`` trên bản hiện tại. Trả về digest (32 byte) cần ký."""
        self.ensure_one()
        if self.state not in ('customer', 'company'):
            raise UserError(_('Hồ sơ %s không ở trạng thái chờ ký.', self.name))
        if signer != self._current_signer():
            raise UserError(_('Chưa đến lượt %s ký.', signer.label))
        identity = pades.cert_identity(cert)
        signer._check_identity(identity)
        not_after = identity['not_after'].replace(tzinfo=None)
        if not_after < fields.Datetime.now():
            raise UserError(_('Chứng thư số đã hết hạn ngày %s.', not_after.strftime('%d/%m/%Y')))
        box = json.loads(signer.box_json or '{}')
        provider = signer.provider_id if signer.method != 'usb' else self.env['dmag.cks.provider']
        try:
            state, digest = pades.prepare_signature(
                _raw(self.current_pdf), cert, chain, field_name=signer.field_name,
                page=box.get('page', -1), box=box.get('box') or (40, 40, 280, 110),
                label=signer.label, reason=_('Ký %s', self.document_name),
                location=self.company_id.city or 'Việt Nam',
                tsa_url=provider.tsa_url or self.company_provider_id.tsa_url)
        except pades.PadesError as exc:
            raise UserError(str(exc)) from exc
        signer.write({
            'prepared_pdf': _b64(state['pdf']),
            'prep_state': json.dumps({
                'document_digest': state['document_digest'].hex(),
                'reserved_start': state['reserved_start'], 'reserved_end': state['reserved_end'],
                'signed_attrs': base64.b64encode(state['signed_attrs']).decode(),
                'cert': base64.b64encode(state['cert']).decode(),
                'chain': [base64.b64encode(c).decode() for c in state['chain']],
                'field_name': state['field_name'],
                'base_sha256': hashlib.sha256(_raw(self.current_pdf)).hexdigest(),
            }),
            'prep_digest': digest.hex(),
            'state': 'pending',
            'message': False,
        })
        return digest

    def _finalize_for(self, signer, signature, ip=None):
        self.ensure_one()
        if signer != self._current_signer() or not signer.prep_state:
            raise UserError(_('Phiên ký đã thay đổi, vui lòng thực hiện lại.'))
        prep = json.loads(signer.prep_state)
        if prep['base_sha256'] != hashlib.sha256(_raw(self.current_pdf)).hexdigest():
            raise UserError(_('Tài liệu đã thay đổi trong lúc ký, vui lòng thực hiện lại.'))
        state = {
            'pdf': _raw(signer.prepared_pdf),
            'document_digest': bytes.fromhex(prep['document_digest']),
            'reserved_start': prep['reserved_start'], 'reserved_end': prep['reserved_end'],
            'signed_attrs': base64.b64decode(prep['signed_attrs']),
            'cert': base64.b64decode(prep['cert']),
            'chain': [base64.b64decode(c) for c in prep['chain']],
        }
        provider = signer.provider_id if signer.method != 'usb' else self.env['dmag.cks.provider']
        try:
            signed = pades.finalize_signature(state, signature,
                                              tsa_url=provider.tsa_url or self.company_provider_id.tsa_url)
        except pades.PadesError as exc:
            signer.write({'state': 'error', 'message': str(exc)})
            raise UserError(str(exc)) from exc
        identity = pades.cert_identity(state['cert'])
        signer.write({
            'state': 'signed', 'signed_at': fields.Datetime.now(), 'signed_ip': ip,
            'cert_subject': identity['display'], 'cert_issuer': identity['issuer'],
            'cert_serial': identity['serial'],
            'cert_ids': ', '.join('%s: %s' % kv for kv in identity['ids'].items()),
            'prepared_pdf': False, 'prep_state': False, 'prep_digest': False, 'transaction_id': False,
            'message': False,
        })
        self.write({'current_pdf': _b64(signed), 'error_message': False})
        self.message_post(body=_('%(label)s đã ký số. Chứng thư: %(subject)s, cấp bởi %(issuer)s, số hiệu %(serial)s.',
                                 label=signer.label, subject=identity['display'], issuer=identity['issuer'],
                                 serial=identity['serial']))
        self._advance()
        return True

    def _advance(self):
        self.ensure_one()
        if self._customer_signers().filtered(lambda s: s.state != 'signed'):
            return
        company = self.signer_ids.filtered(lambda s: s.role == 'company')
        if company and company.state != 'signed':
            if self.state != 'company':
                self.state = 'company'
            self._sign_company()
            return
        self._done()

    def _sign_company(self):
        """DMAG ký tự động: chứng thư trên máy chủ ký ngay, ký từ xa thì chờ cron hỏi kết quả."""
        self.ensure_one()
        signer = self.signer_ids.filtered(lambda s: s.role == 'company' and s.state != 'signed')
        if not signer or signer != self._current_signer():
            return
        provider = signer.provider_id
        try:
            if provider.provider_type == 'local_cert':
                key_pem, cert, chain = provider._local_material()
                digest = self._prepare_for(signer, cert, chain)
                from cryptography.hazmat.primitives import hashes, serialization
                from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
                key = serialization.load_pem_private_key(key_pem, password=None)
                if isinstance(key, rsa.RSAPrivateKey):
                    signature = key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
                else:
                    signature = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
                self._finalize_for(signer, signature)
            else:
                signer._cloud_start(signer.remote_user_id)
        except UserError as exc:
            self.write({'error_message': str(exc)})
            self.message_post(body=_('DMAG chưa ký được: %s', exc))
            _logger.warning('CKS %s: DMAG chưa ký được: %s', self.name, exc)

    def _done(self):
        self.ensure_one()
        pdf = _raw(self.current_pdf)
        self.write({'state': 'done', 'signed_pdf': _b64(pdf), 'done_date': fields.Datetime.now()})
        self.action_verify()
        attachment = self.env['ir.attachment'].create({
            'name': self.signed_filename, 'datas': _b64(pdf), 'mimetype': 'application/pdf',
            'res_model': self._name, 'res_id': self.id})
        self.message_post(body=_('Hồ sơ đã được các bên ký số đầy đủ.'), attachment_ids=attachment.ids)
        if self.res_model and self.res_id and self.res_model in self.env:
            record = self.env[self.res_model].browse(self.res_id).exists()
            if record and hasattr(record, '_dmag_cks_on_done'):
                record._dmag_cks_on_done(self)
        template = self.env.ref('dmag_esign_cks.mail_template_cks_done', raise_if_not_found=False)
        if template and self.partner_id.email:
            self.message_post_with_source(template, subtype_xmlid='mail.mt_comment')

    # ------------------------------------------------------------------ kiểm tra
    def action_verify(self):
        roots = self.env['dmag.cks.trust.root']._get_der_list()
        for env in self:
            pdf = _raw(env.signed_pdf or env.current_pdf)
            if not pdf:
                continue
            results = pades.verify_pdf(pdf, roots)
            env.write({
                'verify_ok': bool(results) and all(r['ok'] for r in results),
                'verify_html': env._render_verify(results),
                'verified_at': fields.Datetime.now(),
            })
        return True

    def _render_verify(self, results):
        if not results:
            return Markup('<p>%s</p>') % _('Tài liệu chưa có chữ ký số.')
        rows = []
        for row in results:
            identity = row.get('identity') or {}
            checks = [
                (_('Toàn vẹn'), row.get('intact')), (_('Hợp lệ'), row.get('valid')),
                (_('Chuỗi tin cậy'), row.get('trusted')),
            ]
            badges = Markup(' ').join(
                Markup('<span class="badge %s">%s</span>') % ('text-bg-success' if ok else 'text-bg-danger', label)
                for label, ok in checks)
            signed_at = row.get('signed_at')
            rows.append(Markup('<tr><td>%s</td><td>%s<br/><small class="text-muted">%s</small></td>'
                               '<td>%s</td><td>%s</td><td>%s</td></tr>') % (
                row.get('field') or '', identity.get('display') or identity.get('subject') or '',
                identity.get('issuer') or '',
                ', '.join('%s: %s' % kv for kv in (identity.get('ids') or {}).items()),
                signed_at.strftime('%d/%m/%Y %H:%M') if signed_at else '',
                badges + (Markup('<br/><small class="text-danger">%s</small>') % row['error']
                          if row.get('error') else '')))
        return Markup('<table class="table table-sm"><thead><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th>'
                      '<th>%s</th></tr></thead><tbody>%s</tbody></table>') % (
            _('Ô ký'), _('Chứng thư'), _('Định danh'), _('Thời điểm'), _('Kết quả'), Markup('').join(rows))

    # ------------------------------------------------------------------ khác
    def action_cancel(self):
        self.write({'state': 'cancel'})
        self.signer_ids.filtered(lambda s: s.state != 'signed').write({'state': 'pending'})

    def action_reset_draft(self):
        for env in self:
            if env.signer_ids.filtered(lambda s: s.state == 'signed'):
                raise UserError(_('Đã có bên ký số, không thể đưa về nháp. Hãy hủy và tạo hồ sơ mới.'))
            env.write({'state': 'draft', 'current_pdf': False})

    def action_retry_company(self):
        for env in self.filtered(lambda e: e.state == 'company'):
            env._sign_company()

    @api.model
    def _cron_poll_cloud(self):
        """Hỏi kết quả các yêu cầu ký từ xa đang chờ xác nhận trên app."""
        signers = self.env['dmag.cks.signer'].sudo().search([('state', '=', 'waiting')], limit=50)
        for signer in signers:
            try:
                with self.env.cr.savepoint():
                    signer._cloud_poll()
            except Exception:  # noqa: BLE001 – một yêu cầu lỗi không chặn các yêu cầu khác
                _logger.exception('CKS: lỗi hỏi trạng thái ký từ xa (signer %s)', signer.id)

    @api.model
    def _dmag_cks_create(self, pdf, document_name, partner, record=None, require_customer=True,
                         signer_kind=None, send=True):
        """API cho module khác (module ký của Bizapps, hợp đồng DMAG…) tạo và gửi hồ sơ ký số."""
        vals = {
            'document_name': document_name, 'partner_id': partner.id,
            'source_pdf': _b64(pdf), 'source_filename': '%s.pdf' % document_name,
        }
        if record:
            vals.update(res_model=record._name, res_id=record.id)
        envelope = self.create(vals)
        if require_customer:
            envelope.signer_ids = [(0, 0, self.env['dmag.cks.signer']._customer_vals(partner, signer_kind))]
        if send:
            envelope.action_send()
        return envelope

    def _portal_summary(self):
        """Dữ liệu cho trang ký của khách."""
        self.ensure_one()
        signer = self._current_signer()
        return {
            'state': self.state,
            'state_label': dict(STATES)[self.state],
            'signer_id': signer.id if signer and signer.role == 'customer' else False,
            'signer_state': signer.state if signer else False,
            'signer_message': signer.message if signer else False,
            'done': self.state == 'done',
            'waiting_company': self.state == 'company',
            'signers': [{'label': s.label, 'state': s.state, 'subject': s.cert_subject or '',
                         'signed_at': s.signed_at and s.signed_at.strftime('%d/%m/%Y %H:%M') or ''}
                        for s in self.signer_ids.sorted('sequence')],
            'error': self.error_message or '',
        }
