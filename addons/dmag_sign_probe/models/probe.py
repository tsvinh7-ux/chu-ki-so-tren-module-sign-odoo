"""Kiểm tra tương thích module Sign cho addon ký số DMAG.

Chỉ đọc thông tin cấu trúc mà quản trị viên vốn xem được trong Odoo (chế độ nhà phát triển):
module đã cài, model, tên và kiểu trường, có hay không có một hàm, đường dẫn trang. Không đọc mã
Python/JavaScript của module khác, không ghi dữ liệu nghiệp vụ.
"""
import base64
import importlib
import json
import re

from markupsafe import Markup

import odoo
from odoo import _, fields, models

EXPECTED = {
    'sign.request': ['template_id', 'reference', 'state', 'completed_document', 'request_item_ids'],
    'sign.request.item': ['sign_request_id', 'partner_id', 'role_id', 'access_token', 'state', 'mail_sent_order',
                          'sign_item_value_ids'],
    'sign.request.item.value': ['sign_item_id', 'value'],
    'sign.item': ['template_id', 'type_id', 'responsible_id', 'page', 'posX', 'posY', 'width', 'height', 'required'],
    'sign.item.type': ['item_type'],
    'sign.item.role': ['name'],
    'sign.template': ['attachment_id', 'sign_item_ids'],
}
METHODS = {
    'sign.request.item': ['_sign'],
    'sign.request': ['_generate_completed_document'],
}
ROUTES = [r'/sign/document/<int:[^>]+>/<[^>]+>', r'/sign/sign/<int:[^>]+>/<[^>]+>']
LIBS = ['pyhanko', 'pyhanko_certvalidator', 'cryptography', 'OpenSSL', 'urllib3', 'reportlab', 'PIL', 'requests']


class DmagSignProbe(models.TransientModel):
    _name = 'dmag.sign.probe'
    _description = 'Kiểm tra tương thích module Sign'

    report_html = fields.Html('Kết quả', readonly=True, sanitize=False)
    report_json = fields.Text('Dữ liệu', readonly=True)
    summary = fields.Char('Tóm tắt', readonly=True)

    def _collect(self):
        env = self.env
        data = {'odoo': {'version': odoo.release.version,
                         'enterprise': bool(env['ir.module.module'].sudo().search_count(
                             [('name', '=', 'web_enterprise'), ('state', '=', 'installed')])),
                         'base_url': env['ir.config_parameter'].sudo().get_param('web.base.url')}}
        mods = env['ir.module.module'].sudo().search(
            [('state', '=', 'installed'), '|', '|', ('name', 'ilike', 'sign'), ('shortdesc', 'ilike', 'sign'),
             ('shortdesc', 'ilike', 'ký')])
        data['modules'] = [{'name': m.name, 'title': m.shortdesc, 'author': m.author, 'version': m.installed_version,
                            'application': m.application} for m in mods]
        sign_models = env['ir.model'].sudo().search([('model', 'ilike', 'sign')])
        data['models'] = {}
        for model in sign_models:
            if model.model not in env:
                continue
            data['models'][model.model] = {
                'name': model.name,
                'fields': {f.name: (f.ttype + ('→' + f.relation if f.relation else ''))
                           for f in model.field_id.sorted('name')},
            }
        data['expected'] = {}
        for model, expected in EXPECTED.items():
            present = data['models'].get(model, {}).get('fields', {})
            data['expected'][model] = {'model': model in env, 'fields': {f: f in present for f in expected}}
        data['methods'] = {model: {m: model in env and callable(getattr(env[model], m, None)) for m in methods}
                           for model, methods in METHODS.items()}
        try:
            rules = [(r.rule, getattr(r.endpoint, 'routing', {}) or {})
                     for r in env['ir.http'].routing_map().iter_rules() if 'sign' in r.rule]
        except Exception as exc:  # noqa: BLE001
            rules, data['routes_error'] = [], str(exc)
        data['routes'] = sorted({'%s [%s, %s]' % (rule, routing.get('type', ''), routing.get('auth', ''))
                                 for rule, routing in rules})
        data['expected_routes'] = {pattern: any(re.search(pattern, rule) for rule, _r in rules) for pattern in ROUTES}
        data['asset_bundles'] = sorted(set(env['ir.asset'].sudo().search([('bundle', 'ilike', 'sign')]).mapped('bundle')))
        data['templates'] = sorted(set(env['ir.ui.view'].sudo().search(
            [('type', '=', 'qweb'), ('key', 'ilike', 'sign')]).mapped('key')))[:80]
        libs = {}
        for name in LIBS:
            try:
                module = importlib.import_module(name)
                libs[name] = getattr(module, '__version__', 'có')
            except Exception:  # noqa: BLE001
                libs[name] = None
        data['libs'] = libs
        return data

    def _render(self, data):
        def badge(ok, yes=_('Có'), no=_('Không')):
            return Markup('<span class="badge %s">%s</span>') % ('text-bg-success' if ok else 'text-bg-danger',
                                                                yes if ok else no)

        parts = [Markup('<h3>Odoo</h3><p>Phiên bản %s · %s · %s</p>') % (
            data['odoo']['version'], 'Enterprise' if data['odoo']['enterprise'] else 'Community',
            data['odoo']['base_url'] or '')]
        rows = Markup('').join(Markup('<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>') % (
            m['name'], m['title'] or '', m['author'] or '', m['version'] or '') for m in data['modules'])
        parts.append(Markup('<h3>Module liên quan đến ký</h3><table class="table table-sm"><thead><tr><th>Tên kỹ thuật'
                            '</th><th>Tên</th><th>Tác giả</th><th>Phiên bản</th></tr></thead><tbody>%s</tbody>'
                            '</table>') % rows)
        rows = []
        for model, info in data['expected'].items():
            missing = [f for f, ok in info['fields'].items() if not ok]
            rows.append(Markup('<tr><td><code>%s</code></td><td>%s</td><td>%s</td></tr>') % (
                model, badge(info['model']), ', '.join(missing) if info['model'] and missing else
                (_('đủ trường') if info['model'] else '')))
        for model, methods in data['methods'].items():
            for method, ok in methods.items():
                rows.append(Markup('<tr><td><code>%s.%s()</code></td><td>%s</td><td></td></tr>') % (
                    model, method, badge(ok)))
        for pattern, ok in data['expected_routes'].items():
            rows.append(Markup('<tr><td><code>%s</code></td><td>%s</td><td></td></tr>') % (pattern, badge(ok)))
        parts.append(Markup('<h3>Khớp với cấu trúc Odoo Sign 17</h3><table class="table table-sm"><thead><tr><th>Mục'
                            '</th><th>Có</th><th>Thiếu trường</th></tr></thead><tbody>%s</tbody></table>')
                     % Markup('').join(rows))
        parts.append(Markup('<h3>Đường dẫn có chữ "sign"</h3><ul>%s</ul>') % Markup('').join(
            Markup('<li><code>%s</code></li>') % r for r in data['routes'][:60]))
        parts.append(Markup('<h3>Thư viện Python</h3><p>%s</p>') % Markup(' · ').join(
            Markup('%s: %s') % (name, badge(bool(v), str(v), _('chưa cài'))) for name, v in data['libs'].items()))
        parts.append(Markup('<p class="text-muted">Bundle tài nguyên có chữ "sign": %s</p>') % (
            ', '.join(data['asset_bundles']) or '—'))
        return Markup('').join(parts)

    def action_run(self):
        self.ensure_one()
        data = self._collect()
        checks = [ok for info in data['expected'].values() for ok in [info['model'], *info['fields'].values()]]
        checks += [ok for methods in data['methods'].values() for ok in methods.values()]
        checks += list(data['expected_routes'].values())
        self.write({
            'report_json': json.dumps(data, ensure_ascii=False, indent=1, default=str),
            'report_html': self._render(data),
            'summary': _('%(ok)s/%(total)s mục khớp cấu trúc Odoo Sign 17', ok=sum(checks), total=len(checks)),
        })
        return {'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id, 'view_mode': 'form',
                'target': 'current'}

    def action_download(self):
        self.ensure_one()
        if not self.report_json:
            self.action_run()
        attachment = self.env['ir.attachment'].create({
            'name': 'kiem-tra-sign-dmag.json', 'datas': base64.b64encode(self.report_json.encode()),
            'mimetype': 'application/json', 'res_model': self._name, 'res_id': self.id})
        return {'type': 'ir.actions.act_url', 'url': '/web/content/%s?download=true' % attachment.id}
