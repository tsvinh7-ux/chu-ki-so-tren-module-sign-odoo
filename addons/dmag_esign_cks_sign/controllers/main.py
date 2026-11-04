import base64

from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.exceptions import UserError
from odoo.http import request
from odoo.tools import consteq


class DmagCksSignController(http.Controller):
    """Ký số USB token từ trang ký của module Sign. Xác thực bằng mã truy cập của người ký (như Sign)."""

    def _request_item(self, request_id, token):
        sign_request = request.env['sign.request'].sudo().browse(request_id).exists()
        if not sign_request:
            raise NotFound()
        item = sign_request.request_item_ids.filtered(
            lambda r: r.access_token and consteq(r.access_token, token or ''))
        if not item:
            raise NotFound()
        return item[:1]

    @http.route('/dmag_cks/sign/<int:request_id>/<token>/prepare', type='json', auth='public')
    def prepare(self, request_id, token, signature=None, cert=None, chain=None, **kw):
        request_item = self._request_item(request_id, token)
        try:
            cert_der = base64.b64decode(cert or '')
            chain_der = [base64.b64decode(c) for c in chain or [] if c]
        except ValueError as exc:
            raise UserError(_('Không đọc được chứng thư từ USB token.')) from exc
        doc = request.env['dmag.cks.sign.doc'].sudo()._for_request(request_item.sign_request_id, lock=True)
        digest, prep = doc._prepare_item(request_item, signature or {}, cert_der, chain_der)
        return {'hash': base64.b64encode(digest).decode(), 'prep': prep.id,
                'document': '%s.pdf' % request_item.sign_request_id.reference}

    @http.route('/dmag_cks/sign/<int:request_id>/<token>/finalize', type='json', auth='public')
    def finalize(self, request_id, token, prep=None, signature=None, **kw):
        request_item = self._request_item(request_id, token)
        doc = request.env['dmag.cks.sign.doc'].sudo()._for_request(request_item.sign_request_id, lock=True)
        prep_rec = request.env['dmag.cks.sign.prep'].sudo().browse(int(prep or 0)).exists()
        if not prep_rec:
            return {'conflict': True}
        try:
            signature_bytes = base64.b64decode(signature or '')
        except ValueError as exc:
            raise UserError(_('Chữ ký từ USB token không hợp lệ.')) from exc
        result = doc._finalize_item(request_item, prep_rec, signature_bytes, ip=request.httprequest.remote_addr)
        result['requestState'] = request_item.sign_request_id.state
        return result
