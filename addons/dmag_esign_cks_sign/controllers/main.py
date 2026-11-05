import base64

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request


class DmagCksSignController(http.Controller):
    """Ký số từ trang ký của module Sign. Xác thực bằng id yêu cầu ký + mã truy cập của người ký."""

    def _ctx(self, request_id, token):
        config = request.env['dmag.cks.sign.config'].sudo()._get()
        if not config:
            raise NotFound()
        sign_request, item = config._request_and_item(request_id, token)
        if not item:
            raise NotFound()
        return config, sign_request, item

    @http.route('/dmag_cks/sign/config', type='json', auth='public')
    def config(self, path=None, **kw):
        """Trang ký hỏi: trang này có ký số được không? Không khớp thì trả {'enabled': False}."""
        config = request.env['dmag.cks.sign.config'].sudo()._get()
        match = config._match_page(path) if config else None
        if not match:
            return {'enabled': False}
        sign_request, item = config._request_and_item(match[0], match[1])
        if not item:
            return {'enabled': False}
        return config._frontend_config(sign_request, item)

    @http.route('/dmag_cks/sign/prepare', type='json', auth='public')
    def prepare(self, request_id=None, token=None, signature=None, cert=None, chain=None, **kw):
        config, sign_request, item = self._ctx(request_id, token)
        try:
            cert_der = base64.b64decode(cert or '')
            chain_der = [base64.b64decode(c) for c in chain or [] if c]
        except ValueError as exc:
            raise UserError(_('Không đọc được chứng thư từ USB token.')) from exc
        doc = request.env['dmag.cks.sign.doc'].sudo()._for_request(sign_request, lock=True)
        digest, prep = doc._prepare_item(item, signature or {}, cert_der, chain_der)
        return {'hash': base64.b64encode(digest).decode(), 'prep': prep.id, 'document': '%s.pdf' % doc.reference}

    @http.route('/dmag_cks/sign/finalize', type='json', auth='public')
    def finalize(self, request_id=None, token=None, prep=None, signature=None, **kw):
        config, sign_request, item = self._ctx(request_id, token)
        prep_rec = self._prep(item, prep)
        if not prep_rec:
            return {'conflict': True}
        try:
            signature_bytes = base64.b64decode(signature or '')
        except ValueError as exc:
            raise UserError(_('Chữ ký từ USB token không hợp lệ.')) from exc
        doc = request.env['dmag.cks.sign.doc'].sudo()._for_request(sign_request, lock=True)
        return doc._finalize_pending(prep_rec, signature_bytes)

    def _prep(self, item, prep_id):
        prep = request.env['dmag.cks.sign.prep'].sudo().browse(int(prep_id or 0)).exists()
        if prep and (prep.item_model != item._name or prep.item_id != item.id):
            raise NotFound()
        return prep

    def _provider(self, provider_id, config, sign_request, item):
        allowed = {p['id'] for p in config._frontend_config(sign_request, item)['cloudProviders']}
        provider = request.env['dmag.cks.provider'].sudo().browse(int(provider_id or 0)).exists()
        if not provider or provider.id not in allowed:
            raise UserError(_('Nhà cung cấp ký số không hợp lệ.'))
        return provider

    @http.route('/dmag_cks/sign/cloud/check', type='json', auth='public')
    def cloud_check(self, request_id=None, token=None, provider_id=None, remote_user_id=None, **kw):
        """Kiểm tra tài khoản ký số từ xa trước khi người ký bấm hoàn tất: lấy chứng thư, đối chiếu MST/CCCD."""
        from odoo.addons.dmag_esign_cks.tools import pades

        config, sign_request, item = self._ctx(request_id, token)
        provider = self._provider(provider_id, config, sign_request, item)
        if not (remote_user_id or '').strip():
            raise UserError(_('Vui lòng nhập tài khoản ký số.'))
        cert = provider._call('get_certificate', remote_user_id.strip())
        identity = pades.cert_identity(cert['cert'])
        doc = request.env['dmag.cks.sign.doc'].sudo()
        doc._check_identity(config, item, identity)
        return {'display': identity['display'], 'common_name': identity['common_name'], 'ids': identity['ids']}

    @http.route('/dmag_cks/sign/cloud/start', type='json', auth='public')
    def cloud_start(self, request_id=None, token=None, signature=None, provider_id=None, remote_user_id=None, **kw):
        config, sign_request, item = self._ctx(request_id, token)
        provider = self._provider(provider_id, config, sign_request, item)
        remote_user_id = (remote_user_id or '').strip()
        cert = provider._call('get_certificate', remote_user_id)
        doc = request.env['dmag.cks.sign.doc'].sudo()._for_request(sign_request, lock=True)
        digest, prep = doc._prepare_item(item, signature or {}, cert['cert'], cert.get('chain') or [],
                                         method='cloud', provider=provider, remote_user_id=remote_user_id)
        prep.credential_id = cert.get('credential_id')
        result = provider._call('sign_hash', remote_user_id, prep.credential_id, digest,
                                'DMAG-%s-%s' % (sign_request.id, item.id), doc.reference or '')
        outcome = prep._cloud_handle(result)
        outcome['prep'] = prep.id if prep.exists() else False
        return outcome

    @http.route('/dmag_cks/sign/cloud/status', type='json', auth='public')
    def cloud_status(self, request_id=None, token=None, prep=None, **kw):
        config, sign_request, item = self._ctx(request_id, token)
        prep_rec = self._prep(item, prep)
        if not prep_rec:
            return {'state': 'expired', 'message': _('Phiên ký không còn, vui lòng ký lại.')}
        return prep_rec._cloud_poll()
