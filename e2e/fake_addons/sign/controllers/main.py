import base64
import json

from werkzeug.exceptions import NotFound

from odoo import http
from odoo.http import request
from odoo.tools import consteq


class SignMockController(http.Controller):

    def _item(self, request_id, token):
        sign_request = request.env['sign.request'].sudo().browse(request_id).exists()
        if not sign_request:
            raise NotFound()
        item = sign_request.request_item_ids.filtered(lambda r: r.access_token and consteq(r.access_token, token or ''))
        if not item:
            raise NotFound()
        return item[:1]

    @http.route('/sign/document/<int:request_id>/<token>', type='http', auth='public', sitemap=False)
    def sign_document(self, request_id, token, **kw):
        current_request_item = self._item(request_id, token)
        sign_request = current_request_item.sign_request_id
        values = {v.sign_item_id.id: v.value for v in sign_request.request_item_ids.sign_item_value_ids}
        items = []
        for item in sign_request.template_id.sign_item_ids:
            value = values.get(item.id)
            if value and item.type_id.item_type in ('signature', 'initial'):
                value = 'data:image/png;base64,' + value
            items.append({
                'id': item.id, 'type': item.type_id.item_type, 'name': item.name or item.type_id.name,
                'page': item.page, 'posX': item.posX, 'posY': item.posY, 'width': item.width, 'height': item.height,
                'required': item.required, 'role': item.responsible_id.name,
                'mine': item.responsible_id == current_request_item.role_id, 'value': value or '',
            })
        props = {
            'requestId': sign_request.id, 'token': token, 'reference': sign_request.reference,
            'signerName': current_request_item.partner_id.name, 'role': current_request_item.role_id.name,
            'itemState': current_request_item.state, 'requestState': sign_request.state,
            'isTurn': current_request_item._is_turn(),
            'pdfUrl': '/sign/download/%s/%s/%s' % (sign_request.id, token,
                                                   'completed' if sign_request.completed_document else 'origin'),
            'items': items,
        }
        return request.render('sign.doc_sign', {
            'sign_request': sign_request, 'current_request_item': current_request_item,
            'props_json': json.dumps(props),
        })

    @http.route('/sign/download/<int:request_id>/<token>/<string:kind>', type='http', auth='public', sitemap=False)
    def sign_download(self, request_id, token, kind, **kw):
        item = self._item(request_id, token)
        sign_request = item.sign_request_id
        data = sign_request.completed_document if kind == 'completed' else sign_request.template_id.attachment_id.datas
        if not data:
            raise NotFound()
        return request.make_response(base64.b64decode(data), headers=[
            ('Content-Type', 'application/pdf'), ('Cache-Control', 'no-store'),
            ('Content-Disposition', http.content_disposition('%s.pdf' % sign_request.reference, 'inline'))])

    @http.route('/sign/sign/<int:request_id>/<token>', type='json', auth='public')
    def sign(self, request_id, token, signature=None, **kw):
        item = self._item(request_id, token)
        item._sign(signature or {})
        return {'success': True, 'requestState': item.sign_request_id.state}
