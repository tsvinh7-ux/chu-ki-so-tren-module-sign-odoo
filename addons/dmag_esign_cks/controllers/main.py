import base64
import json

from markupsafe import Markup
from werkzeug.exceptions import NotFound

from odoo import _, http
from odoo.exceptions import AccessDenied, UserError
from odoo.http import request

AGENT_URL = 'http://127.0.0.1:19876'


class DmagCksPortal(http.Controller):
    """Trang ký số của khách (truy cập bằng link có mã bảo mật, không cần đăng nhập)."""

    def _envelope(self, envelope_id, access_token):
        envelope = request.env['dmag.cks.envelope'].sudo().browse(envelope_id).exists()
        if not envelope:
            raise NotFound()
        try:
            return envelope._check_token(access_token)
        except AccessDenied as exc:
            raise NotFound() from exc

    def _active_signer(self, envelope, signer_id, signer_token=None):
        """Người ký đang đến lượt mà khách truy cập được phép ký (khách, hoặc DMAG có mã riêng)."""
        signer = envelope._signer_for_visitor(signer_token)
        if not signer or signer.id != int(signer_id or 0):
            raise UserError(_('Phiên ký không còn hiệu lực, vui lòng tải lại trang.'))
        return signer

    @http.route('/cks/ky/<int:envelope_id>', type='http', auth='public', website=False, sitemap=False)
    def sign_page(self, envelope_id, access_token=None, signer_token=None, **kw):
        envelope = self._envelope(envelope_id, access_token)
        signer = envelope._signer_for_visitor(signer_token) or envelope._current_signer()
        providers = request.env['dmag.cks.provider'].sudo().search([('use_for_customer', '=', True)])
        if signer.role == 'company':
            providers = providers.browse()  # DMAG ký bằng USB token
        config = {
            'envelopeId': envelope.id, 'token': access_token, 'signerToken': signer_token or '',
            'agentUrl': AGENT_URL,
            'agentDownload': request.env['ir.config_parameter'].sudo().get_param(
                'dmag_esign_cks.agent_download_url', '/dmag_esign_cks/static/agent/DMAGKySo.exe'),
            'summary': envelope._portal_summary(signer_token),
            'defaultRemoteUser': (signer.expected_tax_id or signer.expected_personal_id or '') if signer else '',
        }
        return request.render('dmag_esign_cks.cks_sign_page', {
            'envelope': envelope, 'providers': providers, 'token': access_token,
            'is_company': signer.role == 'company' and bool(envelope._signer_for_visitor(signer_token)),
            # JSON nằm trong <script>: không được escape HTML, chỉ chặn chuỗi "</".
            'config_json': Markup(json.dumps(config).replace('</', '<\\/')),
            'doctype': Markup('<!DOCTYPE html>'),
        })

    @http.route('/cks/ky/<int:envelope_id>/pdf', type='http', auth='public', sitemap=False)
    def document(self, envelope_id, access_token=None, download=None, **kw):
        envelope = self._envelope(envelope_id, access_token)
        data = envelope.signed_pdf or envelope.current_pdf or envelope.source_pdf
        pdf = base64.b64decode(data)
        filename = envelope.signed_filename if envelope.signed_pdf else '%s.pdf' % envelope.document_name
        disposition = 'attachment' if download else 'inline'
        return request.make_response(pdf, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', http.content_disposition(filename, disposition_type=disposition)),
            ('Cache-Control', 'no-store'),
        ])

    # ------------------------------------------------------------------ JSON
    @http.route('/cks/ky/<int:envelope_id>/status', type='json', auth='public')
    def status(self, envelope_id, access_token=None, signer_token=None, **kw):
        envelope = self._envelope(envelope_id, access_token)
        signer = envelope._current_signer()
        if signer and signer.state == 'waiting':
            signer._cloud_poll()
        return envelope._portal_summary(signer_token)

    @http.route('/cks/ky/<int:envelope_id>/usb/prepare', type='json', auth='public')
    def usb_prepare(self, envelope_id, access_token=None, signer_id=None, cert=None, chain=None,
                    signer_token=None, **kw):
        envelope = self._envelope(envelope_id, access_token)
        signer = self._active_signer(envelope, signer_id, signer_token)
        digest = signer._usb_prepare(cert, chain or [])
        return {'hash': base64.b64encode(digest).decode(), 'document': '%s.pdf' % envelope.document_name}

    @http.route('/cks/ky/<int:envelope_id>/usb/finalize', type='json', auth='public')
    def usb_finalize(self, envelope_id, access_token=None, signer_id=None, signature=None,
                     signer_token=None, **kw):
        envelope = self._envelope(envelope_id, access_token)
        signer = self._active_signer(envelope, signer_id, signer_token)
        signer._usb_finalize(signature, ip=request.httprequest.remote_addr)
        return envelope._portal_summary(signer_token)

    @http.route('/cks/ky/<int:envelope_id>/cloud/start', type='json', auth='public')
    def cloud_start(self, envelope_id, access_token=None, signer_id=None, provider_id=None,
                    remote_user_id=None, **kw):
        envelope = self._envelope(envelope_id, access_token)
        signer = self._active_signer(envelope, signer_id)
        if signer.role != 'customer':
            raise UserError(_('DMAG ký bằng USB token, không dùng ký từ xa trên trang này.'))
        provider = request.env['dmag.cks.provider'].sudo().browse(int(provider_id or 0)).exists()
        if not provider or not provider.use_for_customer:
            raise UserError(_('Nhà cung cấp ký số không hợp lệ.'))
        signer.write({'signed_ip': request.httprequest.remote_addr})
        signer._cloud_start(remote_user_id, provider)
        return envelope._portal_summary()
