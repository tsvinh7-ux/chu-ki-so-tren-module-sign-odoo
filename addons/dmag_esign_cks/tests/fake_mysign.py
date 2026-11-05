"""Máy chủ Viettel MySign GIẢ LẬP (cùng đường dẫn và tên trường như tài liệu MySign v1.8).

Dùng trong test (qua ``FakeMySign.post`` thay cho ``requests.post``) và chạy độc lập cho demo:

    python3 -I fake_mysign.py --root-cert root.der --root-key root.pem --port 19990 --confirm-after 4

Chứng thư được cấp từ Root giả lập: KHÔNG có giá trị pháp lý.
"""
import base64
import json
import secrets
import threading
import time

PATH_LOGIN = '/vtss/service/ras/v1/login'
PATH_CERTS = '/vtss/service/certificates/info'
PATH_SIGN = '/vtss/service/signHash'
PATH_STATUS = '/vtss/service/requests/status'


class FakeMySign:
    def __init__(self, root, issue, client_id='dmag-client', client_secret='dmag-secret', confirm_after=0,
                 names=None, reject_users=()):
        """:param issue: hàm ``issue(root, common_name, organization, uid)`` (tools.demo_pki.issue)."""
        self.root, self.issue = root, issue
        self.client_id, self.client_secret = client_id, client_secret
        self.confirm_after = confirm_after
        self.names = names or {}
        self.reject_users = set(reject_users)
        self.tokens, self.users, self.transactions = {}, {}, {}
        self.calls = []
        self.lock = threading.Lock()

    # ------------------------------------------------------------------ nghiệp vụ
    def _user(self, user_id):
        if user_id not in self.users:
            digits = ''.join(ch for ch in user_id if ch.isdigit())
            uid = ('CCCD:%s' if len(digits) == 12 else 'MST:%s') % digits
            name = self.names.get(user_id) or 'NGUOI KY %s' % digits
            issued = self.issue(self.root, name, name if uid.startswith('MST') else None, uid)
            self.users[user_id] = issued
        return self.users[user_id]

    def handle(self, path, body, headers):
        self.calls.append(path)
        if path == PATH_LOGIN:
            if body.get('client_id') != self.client_id or body.get('client_secret') != self.client_secret:
                return 401, {'error': '58001', 'error_description': 'invalid client'}
            token = secrets.token_hex(8)
            self.tokens[token] = body.get('user_id')
            return 200, {'access_token': token, 'token_type': 'Bearer', 'expires_in': 3600}
        token = (headers.get('Authorization') or '').replace('Bearer ', '')
        user_id = self.tokens.get(token)
        if not user_id:
            return 401, {'error': '58002', 'error_description': 'invalid token'}
        if path == PATH_CERTS:
            from cryptography import x509

            issued = self._user(user_id)
            cert = x509.load_der_x509_certificate(issued['cert_der'])
            return 200, [{
                'credential_id': 'cred-%s' % user_id,
                'key': {'status': 'ENABLED', 'algo': ['1.2.840.113549.1.1.1']},
                'cert': {
                    'certificates': [base64.b64encode(issued['cert_der']).decode(),
                                     base64.b64encode(self.root['cert_der']).decode()],
                    'SerialNumber': format(cert.serial_number, 'X'),
                    'subjectDN': cert.subject.rfc4514_string(), 'issuerDN': cert.issuer.rfc4514_string(),
                    'validFrom': cert.not_valid_before.strftime('%Y%m%d%H%M%SZ'),
                    'validTo': cert.not_valid_after.strftime('%Y%m%d%H%M%SZ'),
                },
            }]
        if path == PATH_SIGN:
            if body.get('client_id') != self.client_id or body.get('credentialID') != 'cred-%s' % user_id:
                return 400, {'error': '58010', 'error_description': 'invalid request'}
            digest = base64.b64decode(body['hash'][0])
            if len(digest) != 32 or body.get('hashAlgo') != '2.16.840.1.101.3.4.2.1':
                return 400, {'error': '58011', 'error_description': 'invalid hash'}
            base64.b64decode(body['documents'][0]['document_name'])  # phải là base64
            tran = secrets.token_hex(6)
            with self.lock:
                self.transactions[tran] = {'user': user_id, 'digest': digest, 'at': time.time() + self.confirm_after}
            if int(body.get('async', 0)) == 0 and self.confirm_after <= 0:
                return 200, {'signatures': [base64.b64encode(self._sign(user_id, digest)).decode()]}
            return 200, {'transactionId': tran}
        if path == PATH_STATUS:
            tran = self.transactions.get(str(body.get('transactionId')))
            if not tran or tran['user'] != user_id:
                return 400, {'error': '58020', 'error_description': 'unknown transaction'}
            if user_id in self.reject_users:
                return 200, {'status': '4002'}
            if time.time() < tran['at']:
                return 200, {'status': '4000'}
            return 200, {'status': '1', 'signatures': [base64.b64encode(self._sign(user_id, tran['digest'])).decode()]}
        return 404, {'error': '404'}

    def _sign(self, user_id, digest):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, utils

        key = serialization.load_pem_private_key(self._user(user_id)['key_pem'], password=None)
        return key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))

    # ------------------------------------------------------------------ thay requests.post trong test
    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 – giống chữ ký requests.post
        path = '/' + url.split('://', 1)[-1].split('/', 1)[-1] if '://' in url else url
        status, data = self.handle(path, json or {}, headers or {})
        return _Response(status, data)


class _Response:
    def __init__(self, status, data):
        self.status_code, self._data = status, data
        self.text = json.dumps(data)

    def json(self):
        return self._data


def main():
    import argparse
    import os
    import sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    parser = argparse.ArgumentParser()
    parser.add_argument('--root-cert', required=True)
    parser.add_argument('--root-key', required=True)
    parser.add_argument('--port', type=int, default=19990)
    parser.add_argument('--confirm-after', type=float, default=4)
    parser.add_argument('--names', default='{}', help='JSON {user_id: tên hiển thị}')
    args = parser.parse_args()
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tools'))
    import demo_pki  # noqa: E402

    root = {'cert_der': open(args.root_cert, 'rb').read(), 'key_pem': open(args.root_key, 'rb').read()}
    backend = FakeMySign(root, demo_pki.issue, confirm_after=args.confirm_after, names=json.loads(args.names))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(length) or b'{}')
            status, data = backend.handle(self.path, body, dict(self.headers))
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    print('MySign giả lập chạy tại http://0.0.0.0:%s' % args.port, flush=True)
    ThreadingHTTPServer(('0.0.0.0', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
