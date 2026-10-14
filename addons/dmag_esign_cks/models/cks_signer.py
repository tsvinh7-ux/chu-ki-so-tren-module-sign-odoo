import base64
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import pades

SIGNER_STATES = [
    ('pending', 'Chưa ký'),
    ('waiting', 'Chờ xác nhận trên app'),
    ('signed', 'Đã ký số'),
    ('rejected', 'Từ chối'),
    ('error', 'Lỗi'),
]


class DmagCksSigner(models.Model):
    _name = 'dmag.cks.signer'
    _description = 'Người ký số'
    _order = 'envelope_id, sequence, id'

    envelope_id = fields.Many2one('dmag.cks.envelope', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    role = fields.Selection([('customer', 'Khách hàng'), ('company', 'DMAG')], required=True, default='customer')
    label = fields.Char('Nhãn trên tài liệu', required=True, default=lambda self: _('BÊN B – KHÁCH HÀNG'))
    partner_id = fields.Many2one('res.partner', 'Người/đơn vị ký')
    signer_kind = fields.Selection([
        ('organization', 'Tổ chức – CKS công ty'),
        ('individual', 'Cá nhân – CKS cá nhân'),
        ('any', 'Tổ chức hoặc cá nhân'),
    ], string='Loại chữ ký yêu cầu', default='any', required=True)
    expected_tax_id = fields.Char('MST phải khớp', help='Mã số thuế ghi trong chứng thư số tổ chức.')
    expected_personal_id = fields.Char('CCCD phải khớp', help='Số CCCD ghi trong chứng thư số cá nhân.')
    strict_identity = fields.Boolean(
        'Bắt buộc khớp định danh', default=True,
        help='Chặn ký nếu MST/CCCD trong chứng thư không khớp hồ sơ khách hàng.')
    method = fields.Selection([
        ('usb', 'USB token (phần mềm DMAG Ký số)'),
        ('cloud', 'Ký từ xa (app điện thoại)'),
        ('server', 'Chứng thư trên máy chủ'),
    ], string='Cách ký', readonly=True)
    provider_id = fields.Many2one('dmag.cks.provider', 'Nhà cung cấp')
    remote_user_id = fields.Char('Tài khoản ký từ xa')
    credential_id = fields.Char(readonly=True)
    transaction_id = fields.Char(readonly=True)
    request_deadline = fields.Datetime(readonly=True)
    state = fields.Selection(SIGNER_STATES, default='pending', required=True, readonly=True)
    message = fields.Char('Ghi chú', readonly=True)

    # Trạng thái ký gián đoạn (giữa bước chuẩn bị và bước nhúng chữ ký)
    prepared_pdf = fields.Binary(attachment=True, readonly=True, copy=False)
    prep_state = fields.Text(readonly=True, copy=False)
    prep_digest = fields.Char(readonly=True, copy=False)
    field_name = fields.Char(readonly=True, copy=False)
    box_json = fields.Char(readonly=True, copy=False)

    # Kết quả
    signed_at = fields.Datetime('Ký lúc', readonly=True, copy=False)
    signed_ip = fields.Char('IP', readonly=True, copy=False)
    cert_subject = fields.Char('Chứng thư', readonly=True, copy=False)
    cert_issuer = fields.Char('Nhà cung cấp chứng thư', readonly=True, copy=False)
    cert_serial = fields.Char('Số hiệu chứng thư', readonly=True, copy=False)
    cert_ids = fields.Char('Định danh trong chứng thư', readonly=True, copy=False)

    display_label_name = fields.Char(compute='_compute_display_label')
    display_label_detail = fields.Char(compute='_compute_display_label')

    @api.depends('partner_id', 'expected_tax_id', 'expected_personal_id')
    def _compute_display_label(self):
        for signer in self:
            signer.display_label_name = signer.partner_id.name or ''
            if signer.expected_tax_id:
                signer.display_label_detail = 'MST: %s' % signer.expected_tax_id
            elif signer.expected_personal_id:
                signer.display_label_detail = 'CCCD: %s' % signer.expected_personal_id
            else:
                signer.display_label_detail = ''

    @api.onchange('partner_id', 'signer_kind')
    def _onchange_partner(self):
        for signer in self.filtered('partner_id'):
            vals = self._customer_vals(signer.partner_id, signer.signer_kind)
            signer.expected_tax_id = vals['expected_tax_id']
            signer.expected_personal_id = vals['expected_personal_id']

    @api.model
    def _customer_vals(self, partner, signer_kind=None):
        """Giá trị mặc định cho người ký phía khách từ hồ sơ khách hàng."""
        company = partner.commercial_partner_id
        kind = signer_kind or ('organization' if company.is_company else 'individual')
        return {
            'role': 'customer', 'sequence': 10, 'partner_id': partner.id, 'signer_kind': kind,
            'label': _('BÊN B – %s', (company.name or '').upper()),
            'expected_tax_id': pades.normalize_id(company.vat) if kind != 'individual' and company.vat else False,
            'expected_personal_id': pades.normalize_id(partner.dmag_cks_personal_id)
            if kind != 'organization' and partner.dmag_cks_personal_id else False,
        }

    def _check_identity(self, identity):
        """Kiểm tra chứng thư khách dùng có đúng tổ chức / người trong hợp đồng."""
        self.ensure_one()
        ids = identity.get('ids') or {}
        if self.signer_kind == 'organization' and not ids.get('MST'):
            raise UserError(_('Hợp đồng yêu cầu chữ ký số của TỔ CHỨC (chứng thư có mã số thuế). '
                              'Chứng thư "%s" là chứng thư cá nhân.', identity.get('subject')))
        if not self.strict_identity or not (self.expected_tax_id or self.expected_personal_id):
            return True
        if pades.identity_matches(identity, self.expected_tax_id, self.expected_personal_id):
            return True
        expected = ' / '.join(filter(None, [
            self.expected_tax_id and 'MST %s' % self.expected_tax_id,
            self.expected_personal_id and 'CCCD %s' % self.expected_personal_id]))
        found = ', '.join('%s %s' % kv for kv in ids.items()) or _('không có')
        raise UserError(_('Chứng thư số không khớp khách hàng trong hợp đồng: cần %(e)s, chứng thư có %(f)s.',
                          e=expected, f=found))

    # ------------------------------------------------------------------ USB token
    def _usb_prepare(self, cert_b64, chain_b64=()):
        self.ensure_one()
        try:
            cert = base64.b64decode(cert_b64)
            chain = [base64.b64decode(c) for c in chain_b64 or [] if c]
            pades.load_cert(cert)
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('Không đọc được chứng thư từ USB token.')) from exc
        self.write({'method': 'usb', 'provider_id': False})
        return self.envelope_id._prepare_for(self, cert, chain)

    def _usb_finalize(self, signature_b64, ip=None):
        self.ensure_one()
        try:
            signature = base64.b64decode(signature_b64)
        except Exception as exc:  # noqa: BLE001
            raise UserError(_('Chữ ký từ USB token không hợp lệ.')) from exc
        return self.envelope_id._finalize_for(self, signature, ip=ip)

    # ------------------------------------------------------------------ ký từ xa
    def _cloud_start(self, remote_user_id, provider=None):
        """Lấy chứng thư từ nhà cung cấp, chuẩn bị PDF, gửi hash để người ký xác nhận trên app."""
        self.ensure_one()
        provider = provider or self.provider_id
        if not provider:
            raise UserError(_('Chưa chọn nhà cung cấp ký số từ xa.'))
        remote_user_id = (remote_user_id or '').strip()
        if not remote_user_id:
            raise UserError(_('Vui lòng nhập tài khoản ký số (CCCD hoặc mã số thuế).'))
        cert_info = provider._call('get_certificate', remote_user_id)
        self.write({'method': 'cloud', 'provider_id': provider.id, 'remote_user_id': remote_user_id,
                    'credential_id': cert_info.get('credential_id')})
        digest = self.envelope_id._prepare_for(self, cert_info['cert'], cert_info.get('chain') or [])
        result = provider._call('sign_hash', remote_user_id, self.credential_id, digest,
                                '%s-%s' % (self.envelope_id.name, self.id), self.envelope_id.document_name)
        return self._cloud_handle(result)

    def _cloud_poll(self):
        self.ensure_one()
        if self.state != 'waiting':
            return self._cloud_status()
        if self.request_deadline and self.request_deadline < fields.Datetime.now():
            self.write({'state': 'pending', 'message': _('Hết hạn chờ xác nhận trên app. Vui lòng ký lại.'),
                        'transaction_id': False})
            return self._cloud_status()
        result = self.provider_id._call('get_status', self.remote_user_id, self.credential_id,
                                        self.transaction_id)
        return self._cloud_handle(result)

    def _cloud_handle(self, result):
        state = result.get('state')
        if state == 'signed' and result.get('signature'):
            self.envelope_id._finalize_for(self, result['signature'])
        elif state == 'waiting':
            vals = {'state': 'waiting', 'transaction_id': result.get('transaction_id') or self.transaction_id,
                    'message': result.get('message') or _('Mời xác nhận trên ứng dụng ký số.')}
            if self.state != 'waiting':
                vals['request_deadline'] = fields.Datetime.now() + timedelta(
                    minutes=self.provider_id.request_ttl or 10)
            self.write(vals)
        elif state in ('rejected', 'expired'):
            self.write({'state': 'pending', 'transaction_id': False, 'message': {
                'rejected': _('Người ký đã từ chối trên ứng dụng.'),
                'expired': _('Yêu cầu ký đã hết hạn.')}[state]})
        else:
            self.write({'state': 'error', 'message': result.get('message') or _('Nhà cung cấp báo lỗi.')})
        return self._cloud_status()

    def _cloud_status(self):
        return {'state': self.state, 'message': self.message or '',
                'envelope_state': self.envelope_id.state}
