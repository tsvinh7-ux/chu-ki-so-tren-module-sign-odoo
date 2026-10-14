import base64
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools import pades
from ..tools.providers import ProviderError, get_adapter

_logger = logging.getLogger(__name__)

ADMIN = 'base.group_system'


class DmagCksProvider(models.Model):
    """Kênh ký số: CKS tổ chức của DMAG, hoặc dịch vụ ký từ xa mà khách dùng."""
    _name = 'dmag.cks.provider'
    _description = 'Nhà cung cấp chữ ký số'
    _order = 'sequence, id'

    name = fields.Char('Tên', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', string='Công ty', default=lambda self: self.env.company)
    provider_type = fields.Selection([
        ('local_cert', 'Chứng thư số trên máy chủ (.p12/.pfx)'),
        ('vnpt_smartca', 'VNPT SmartCA (ký từ xa)'),
        ('viettel_mysign', 'Viettel MySign (ký từ xa)'),
        ('demo_cloud', 'Ký từ xa GIẢ LẬP (chỉ để demo)'),
    ], string='Loại', required=True, default='vnpt_smartca')
    use_for_company = fields.Boolean(
        'Dùng để DMAG ký', help='Chứng thư tổ chức của công ty mình, ký tự động khi khách đã ký xong.')
    use_for_customer = fields.Boolean(
        'Cho khách ký từ xa', default=True,
        help='Hiện trên trang ký của khách: khách nhập tài khoản và xác nhận trên app điện thoại.')
    customer_label = fields.Char('Tên hiển thị cho khách', help='Ví dụ "VNPT SmartCA".')
    customer_hint = fields.Char('Gợi ý ô tài khoản', default='Số CCCD hoặc mã số thuế đăng ký với nhà cung cấp')

    # Kết nối API (ký từ xa)
    base_url = fields.Char('Địa chỉ API', help='VNPT ví dụ https://gwsca.vnpt.vn/sca/sp769 (theo hợp đồng).')
    client_id = fields.Char('Client ID / SP ID', groups=ADMIN)
    client_secret = fields.Char('Client secret / SP password', groups=ADMIN)
    profile_id = fields.Char('Profile / Service ID')
    timeout = fields.Integer('Thời gian chờ HTTP (giây)', default=30)
    paths_json = fields.Text(
        'Endpoint tùy chỉnh (JSON)',
        help='Ghi đè đường dẫn nếu tài liệu của nhà cung cấp khác mặc định, ví dụ '
             '{"sign": "/v2/signatures/sign"}.')
    request_ttl = fields.Integer('Hạn chờ xác nhận trên app (phút)', default=10)
    company_user_id = fields.Char(
        'Tài khoản ký của DMAG', help='Tài khoản CKS tổ chức (MST) dùng khi DMAG ký từ xa.')

    # Chứng thư trên máy chủ
    p12_file = fields.Binary('File chứng thư (.p12/.pfx)', attachment=True, groups=ADMIN)
    p12_password = fields.Char('Mật khẩu file chứng thư', groups=ADMIN)
    cert_subject = fields.Char('Chứng thư', readonly=True)
    cert_valid_to = fields.Datetime('Hết hạn', readonly=True)

    # Dấu thời gian
    tsa_url = fields.Char('Dịch vụ dấu thời gian (TSA URL)',
                          help='URL RFC 3161 do CA cung cấp. Để trống thì không gắn dấu thời gian.')

    # Giả lập
    demo_delay = fields.Integer('Giả lập: số giây chờ "xác nhận trên app"', default=4)
    demo_root_id = fields.Many2one('dmag.cks.trust.root', string='Root CA giả lập')
    demo_state = fields.Text('Giả lập: khóa đã cấp', groups=ADMIN)

    @api.constrains('paths_json')
    def _check_paths_json(self):
        for provider in self:
            if provider.paths_json:
                try:
                    if not isinstance(json.loads(provider.paths_json), dict):
                        raise ValueError
                except ValueError as exc:
                    raise ValidationError(_('Endpoint tùy chỉnh phải là JSON dạng {"khóa": "/đường/dẫn"}.')) from exc

    # ------------------------------------------------------------------ adapter
    def _adapter_config(self):
        self.ensure_one()
        provider = self.sudo()
        config = {
            'base_url': provider.base_url, 'client_id': provider.client_id,
            'client_secret': provider.client_secret, 'profile_id': provider.profile_id,
            'timeout': provider.timeout, 'paths': json.loads(provider.paths_json or '{}'),
            'demo_delay': provider.demo_delay,
        }
        if provider.provider_type == 'demo_cloud':
            root = provider.demo_root_id
            if not root or not root.demo_key_pem:
                raise UserError(_('Nhà cung cấp giả lập cần một Root CA giả lập có khóa.'))
            config['demo_root'] = {'key_pem': root.demo_key_pem.encode(),
                                   'cert_der': base64.b64decode(root.cert_file)}
            config['state'] = json.loads(provider.demo_state or '{}')
            partners = self.env['res.partner'].sudo().search([('vat', '!=', False)], limit=500)
            config['display_names'] = {'MST:%s' % pades.normalize_id(p.vat): p.name.upper() for p in partners}
            for partner in self.env['res.partner'].sudo().search([('dmag_cks_personal_id', '!=', False)], limit=500):
                config['display_names']['CCCD:%s' % pades.normalize_id(partner.dmag_cks_personal_id)] = \
                    partner.name.upper()
        return config

    def _call(self, method, *args):
        """Gọi adapter. Lỗi nhà cung cấp được chuyển thành ``UserError``."""
        self.ensure_one()
        config = self._adapter_config()
        adapter = get_adapter(self.provider_type, config)
        try:
            result = getattr(adapter, method)(*args)
        except ProviderError as exc:
            raise UserError(str(exc)) from exc
        if config.get('state_changed'):
            self.sudo().demo_state = json.dumps(config['state'])
        return result

    # ------------------------------------------------------------------ chứng thư máy chủ
    def _local_material(self):
        self.ensure_one()
        provider = self.sudo()
        if not provider.p12_file:
            raise UserError(_('Chưa tải lên file chứng thư số (.p12/.pfx) cho "%s".', self.name))
        try:
            return pades.load_pkcs12(base64.b64decode(provider.p12_file), provider.p12_password or '')
        except (ValueError, pades.PadesError) as exc:
            raise UserError(_('Không mở được file chứng thư: %s', exc)) from exc

    @api.onchange('p12_file', 'p12_password')
    def _onchange_p12(self):
        for provider in self:
            if provider.provider_type == 'local_cert' and provider.p12_file:
                try:
                    _key, cert, _chain = pades.load_pkcs12(base64.b64decode(provider.p12_file),
                                                           provider.p12_password or '')
                except Exception:  # noqa: BLE001 – mật khẩu chưa nhập xong
                    continue
                identity = pades.cert_identity(cert)
                provider.cert_subject = identity['subject']
                provider.cert_valid_to = identity['not_after'].replace(tzinfo=None)

    def action_test(self):
        """Kiểm tra cấu hình: đọc chứng thư (máy chủ) hoặc lấy chứng thư tài khoản DMAG (từ xa)."""
        self.ensure_one()
        if self.provider_type == 'local_cert':
            _key, cert, _chain = self._local_material()
        else:
            account = self.company_user_id or (self.provider_type == 'demo_cloud' and 'MST:0110085510')
            if not account:
                raise UserError(_('Nhập "Tài khoản ký của DMAG" để thử kết nối.'))
            cert = self._call('get_certificate', account)['cert']
        identity = pades.cert_identity(cert)
        self.write({'cert_subject': identity['subject'],
                    'cert_valid_to': identity['not_after'].replace(tzinfo=None)})
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': _('Kết nối được'),
            'message': _('Chứng thư: %(s)s, hết hạn %(d)s',
                         s=identity['subject'], d=identity['not_after'].strftime('%d/%m/%Y'))}}
