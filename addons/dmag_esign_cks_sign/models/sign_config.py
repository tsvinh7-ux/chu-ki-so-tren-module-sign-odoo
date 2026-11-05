import logging
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

PATCH_FLAG = '_dmag_cks_patched'


class DmagCksSignConfig(models.Model):
    """Cấu hình nối với module Sign mà không cần mã nguồn của nó.

    Mọi tên đều điền sẵn theo Odoo Sign 17. Nếu module của Bizapps đặt tên khác, sửa ở đây rồi bấm
    "Kiểm tra". Chỉ một cấu hình được bật cùng lúc.
    """
    _name = 'dmag.cks.sign.config'
    _description = 'Ký số trong module Sign: cấu hình'

    name = fields.Char('Tên', required=True)
    active = fields.Boolean('Đang bật', default=False)
    required_roles = fields.Char(
        'Vai trò bắt buộc ký số', help='Tên vai trò ký, cách nhau bởi dấu phẩy, ví dụ "DMAG, Giám đốc". '
                                       'Người ký ở các vai trò này chỉ được ký bằng USB token / app ký số.')
    check_html = fields.Html('Kết quả kiểm tra', readonly=True, sanitize=False)

    # Model
    request_model = fields.Char('Model yêu cầu ký', required=True, default='sign.request')
    request_item_model = fields.Char('Model người ký', required=True, default='sign.request.item')
    item_model = fields.Char('Model ô ký', required=True, default='sign.item')
    # Yêu cầu ký
    f_req_template = fields.Char('Yêu cầu → mẫu', required=True, default='template_id')
    f_tpl_attachment = fields.Char('Mẫu → tệp PDF gốc (ir.attachment)', required=True, default='attachment_id')
    f_req_reference = fields.Char('Yêu cầu → tên tài liệu', required=True, default='reference')
    f_req_state = fields.Char('Yêu cầu → trạng thái', required=True, default='state')
    v_req_open = fields.Char('Giá trị "đang chờ ký"', required=True, default='sent')
    f_req_completed = fields.Char('Yêu cầu → file hoàn tất', required=True, default='completed_document')
    f_req_items = fields.Char('Yêu cầu → danh sách người ký', required=True, default='request_item_ids')
    # Người ký
    f_ri_partner = fields.Char('Người ký → đối tác', required=True, default='partner_id')
    f_ri_role = fields.Char('Người ký → vai trò', required=True, default='role_id')
    f_ri_token = fields.Char('Người ký → mã truy cập', required=True, default='access_token')
    f_ri_state = fields.Char('Người ký → trạng thái', required=True, default='state')
    v_ri_done = fields.Char('Giá trị "đã ký"', required=True, default='completed')
    f_ri_order = fields.Char('Người ký → thứ tự ký', default='mail_sent_order')
    f_ri_values = fields.Char('Người ký → giá trị đã ký', required=True, default='sign_item_value_ids')
    f_val_item = fields.Char('Giá trị → ô ký', required=True, default='sign_item_id')
    f_val_value = fields.Char('Giá trị → nội dung', required=True, default='value')
    # Ô ký
    f_item_role = fields.Char('Ô ký → vai trò', required=True, default='responsible_id')
    f_item_type = fields.Char('Ô ký → loại (signature/initial/text…)', required=True, default='type_id.item_type')
    f_item_page = fields.Char('Ô ký → trang (từ 1)', required=True, default='page')
    f_item_x = fields.Char('Ô ký → X (0..1)', required=True, default='posX')
    f_item_y = fields.Char('Ô ký → Y (0..1, từ trên)', required=True, default='posY')
    f_item_w = fields.Char('Ô ký → rộng (0..1)', required=True, default='width')
    f_item_h = fields.Char('Ô ký → cao (0..1)', required=True, default='height')
    f_item_required = fields.Char('Ô ký → bắt buộc', default='required')
    # Hàm và trang ký
    m_item_sign = fields.Char('Hàm "người ký ký xong"', required=True, default='_sign')
    m_request_complete = fields.Char('Hàm "tạo file hoàn tất"', required=True, default='_generate_completed_document')
    page_url_regex = fields.Char('Đường dẫn trang ký (regex: id, mã)', required=True,
                                 default=r'^/sign/document/(\d+)/([^/?#]+)')
    submit_url_regex = fields.Char('Đường dẫn gửi chữ ký (regex)', required=True,
                                   default=r'/sign/sign/(\d+)/([^/?#]+)')
    submit_param = fields.Char('Tham số chứa các ô đã ký', required=True, default='signature')

    # ------------------------------------------------------------------ truy cập dữ liệu module Sign
    @api.model
    def _get(self):
        return self.sudo().search([('active', '=', True)], limit=1)

    @staticmethod
    def _val(record, path):
        value = record
        for part in (path or '').split('.'):
            if not part:
                continue
            value = value[part]
        return value

    def _role_required(self, role):
        names = {n.strip().lower() for n in (self.required_roles or '').split(',') if n.strip()}
        return bool(role) and (role.display_name or '').strip().lower() in names

    def _request_and_item(self, request_id, token):
        """Tìm yêu cầu ký và người ký theo id + mã truy cập (như trang ký của Sign)."""
        from odoo.tools import consteq

        self.ensure_one()
        if self.request_model not in self.env:
            return None, None
        sign_request = self.env[self.request_model].sudo().browse(int(request_id)).exists()
        if not sign_request:
            return None, None
        for item in self._val(sign_request, self.f_req_items):
            token_value = item[self.f_ri_token] or ''
            if token_value and token and consteq(token_value, token):
                return sign_request, item
        return sign_request, None

    def _match_page(self, path):
        self.ensure_one()
        match = re.search(self.page_url_regex or '', path or '')
        return (match.group(1), match.group(2)) if match and match.lastindex and match.lastindex >= 2 else None

    # ------------------------------------------------------------------ kiểm tra
    def _checks(self):
        """Danh sách (nhóm, mô tả, ok, ghi chú) cho bảng kiểm tra."""
        self.ensure_one()
        rows = []

        def add(group, label, ok, note=''):
            rows.append((group, label, bool(ok), note))

        def check_fields(model, paths):
            if model not in self.env:
                return
            for label, path in paths:
                if not path:
                    continue
                Model, ok, note = self.env[model], True, ''
                for part in path.split('.'):
                    field = Model._fields.get(part)
                    if not field:
                        ok, note = False, _('không có trường %s', part)
                        break
                    if field.relational:
                        Model = self.env[field.comodel_name]
                add(model, '%s (%s)' % (label, path), ok, note)

        for label, model in ((_('Model yêu cầu ký'), self.request_model),
                             (_('Model người ký'), self.request_item_model), (_('Model ô ký'), self.item_model)):
            add(_('Model'), '%s: %s' % (label, model), model in self.env)
        check_fields(self.request_model, [
            (_('mẫu'), self.f_req_template), (_('tên'), self.f_req_reference), (_('trạng thái'), self.f_req_state),
            (_('file hoàn tất'), self.f_req_completed), (_('người ký'), self.f_req_items),
            (_('PDF gốc'), '%s.%s' % (self.f_req_template, self.f_tpl_attachment))])
        check_fields(self.request_item_model, [
            (_('đối tác'), self.f_ri_partner), (_('vai trò'), self.f_ri_role), (_('mã truy cập'), self.f_ri_token),
            (_('trạng thái'), self.f_ri_state), (_('thứ tự'), self.f_ri_order), (_('giá trị'), self.f_ri_values),
            (_('giá trị → ô'), '%s.%s' % (self.f_ri_values, self.f_val_item)),
            (_('giá trị → nội dung'), '%s.%s' % (self.f_ri_values, self.f_val_value))])
        check_fields(self.item_model, [
            (_('vai trò'), self.f_item_role), (_('loại'), self.f_item_type), (_('trang'), self.f_item_page),
            ('X', self.f_item_x), ('Y', self.f_item_y), (_('rộng'), self.f_item_w), (_('cao'), self.f_item_h),
            (_('bắt buộc'), self.f_item_required)])
        for model, method in ((self.request_item_model, self.m_item_sign),
                              (self.request_model, self.m_request_complete)):
            ok = model in self.env and callable(getattr(self.env[model], method or '', None))
            add(_('Hàm'), '%s.%s()' % (model, method), ok)
        try:
            routes = [rule.rule for rule in self.env['ir.http'].routing_map().iter_rules()]
        except Exception:  # noqa: BLE001 – không lấy được bảng route thì bỏ qua mục này
            routes = []
        if routes:
            for label, pattern in ((_('Trang ký'), self.page_url_regex), (_('Gửi chữ ký'), self.submit_url_regex)):
                # Đổi regex của URL thật sang dạng quy tắc route của Odoo: /sign/document/<int:id>/<token>
                rule_pattern = pattern.lstrip('^').replace(r'(\d+)', r'<int:[^>]+>').replace(r'([^/?#]+)', r'<[^>]+>')
                found = [r for r in routes if re.search(rule_pattern, r)]
                add(_('Đường dẫn'), '%s: %s' % (label, pattern), bool(found), ', '.join(found[:3]))
        return rows

    def action_check(self):
        for config in self:
            rows = config._checks()
            body = Markup('').join(
                Markup('<tr><td>%s</td><td>%s</td><td><span class="badge %s">%s</span></td><td>%s</td></tr>') % (
                    group, label, 'text-bg-success' if ok else 'text-bg-danger', _('Khớp') if ok else _('Không khớp'),
                    note) for group, label, ok, note in rows)
            ok_count = sum(1 for row in rows if row[2])
            config.check_html = Markup(
                '<p><b>%s/%s mục khớp.</b></p><table class="table table-sm"><thead><tr><th>%s</th><th>%s</th>'
                '<th>%s</th><th>%s</th></tr></thead><tbody>%s</tbody></table>') % (
                ok_count, len(rows), _('Nhóm'), _('Mục'), _('Kết quả'), _('Ghi chú'), body)
        return True

    def action_activate(self):
        self.ensure_one()
        self.action_check()
        failed = [row for row in self._checks() if not row[2] and row[0] != _('Đường dẫn')]
        if failed:
            raise UserError(_('Chưa bật được: %s mục chưa khớp. Xem bảng kiểm tra, sửa tên cho đúng rồi thử lại.',
                              len(failed)))
        self.search([('id', '!=', self.id)]).write({'active': False})
        self.active = True
        self._dmag_update_registry()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'type': 'success', 'title': _('Đã bật ký số trong module Sign'),
            'message': _('Hộp chọn chữ ký trên trang ký đã có tab USB token và App ký số.')}}

    def action_deactivate(self):
        self.write({'active': False})
        self._dmag_update_registry()

    def _dmag_update_registry(self):
        """Gắn / gỡ lại ngay trên tiến trình này và báo các tiến trình khác tải lại."""
        self.env.registry.clear_cache()
        self._dmag_unpatch_vendor()
        self._dmag_patch_vendor()
        self.env.registry.registry_invalidated = True

    # ------------------------------------------------------------------ gắn hàm vào module Sign
    def _register_hook(self):
        super()._register_hook()
        try:
            self._dmag_patch_vendor()
        except Exception:  # noqa: BLE001 – lỗi cấu hình không được làm Odoo dừng khởi động
            _logger.exception('Ký số trong Sign: không gắn được vào module Sign')

    @api.model
    def _dmag_patch_vendor(self):
        self.env.cr.execute("SELECT to_regclass('dmag_cks_sign_config')")
        if not self.env.cr.fetchone()[0]:
            return
        config = self._get()
        if not config:
            return
        registry = self.env.registry
        if config.request_item_model not in registry or config.request_model not in registry:
            _logger.warning('Ký số trong Sign: không thấy model %s / %s', config.request_item_model,
                            config.request_model)
            return
        item_cls, request_cls = registry[config.request_item_model], registry[config.request_model]
        sign_name, complete_name = config.m_item_sign, config.m_request_complete

        def make_sign():
            def patched_sign(self, *args, **kwargs):
                self.env['dmag.cks.sign.doc']._dmag_before_vendor_sign(self, args, kwargs)
                return patched_sign.origin(self, *args, **kwargs)
            return patched_sign

        def make_complete():
            def patched_complete(self, *args, **kwargs):
                todo = self.env['dmag.cks.sign.doc']._dmag_vendor_complete(self)
                if todo:
                    return patched_complete.origin(todo, *args, **kwargs)
                return None
            return patched_complete

        for cls, name, factory in ((item_cls, sign_name, make_sign), (request_cls, complete_name, make_complete)):
            if getattr(cls, PATCH_FLAG + name, False) or not callable(getattr(cls, name, None)):
                continue
            method = factory()
            method.origin = getattr(cls, name)
            setattr(cls, name, method)
            setattr(cls, PATCH_FLAG + name, True)
        _logger.info('Ký số trong Sign: đã gắn vào %s và %s', config.request_item_model, config.request_model)

    @api.model
    def _dmag_unpatch_vendor(self):
        for cls in self.env.registry.values():
            for attr in [a for a in vars(cls) if a.startswith(PATCH_FLAG)]:
                name = attr[len(PATCH_FLAG):]
                for target in (name, attr):
                    try:
                        delattr(cls, target)
                    except AttributeError:
                        pass

    # ------------------------------------------------------------------ cho trang ký
    def _frontend_config(self, sign_request, request_item):
        self.ensure_one()
        partner = request_item[self.f_ri_partner]
        internal = bool(partner.user_ids.filtered(lambda u: not u.share))
        Provider = self.env['dmag.cks.provider'].sudo()
        cloud_types = ('vnpt_smartca', 'viettel_mysign', 'demo_cloud')
        domain = [('provider_type', 'in', cloud_types)]
        domain += ['|', ('use_for_customer', '=', True), ('use_for_company', '=', True)] if internal else \
            [('use_for_customer', '=', True)]
        providers = Provider.search(domain)
        default_user = ''
        if internal:
            default_user = providers.filtered('company_user_id')[:1].company_user_id or self.env.company.vat or ''
        else:
            commercial = partner.commercial_partner_id
            default_user = commercial.vat or partner.dmag_cks_personal_id or ''
        param = self.env['ir.config_parameter'].sudo()
        return {
            'enabled': True,
            'required': self._role_required(request_item[self.f_ri_role]),
            'requestId': sign_request.id,
            'token': request_item[self.f_ri_token],
            'submitUrlRegex': self.submit_url_regex,
            'submitParam': self.submit_param,
            'agentUrl': 'http://127.0.0.1:19876',
            'agentDownload': param.get_param('dmag_esign_cks.agent_download_url',
                                             '/dmag_esign_cks/static/agent/DMAGKySo.exe'),
            'cloudProviders': [{'id': p.id, 'label': p.customer_label or p.name,
                                'hint': p.customer_hint or ''} for p in providers],
            'defaultRemoteUser': default_user,
        }
