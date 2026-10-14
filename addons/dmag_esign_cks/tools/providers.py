"""Adapter cho dịch vụ ký số từ xa (CKS cloud) của các nhà cung cấp Việt Nam.

Mọi adapter dùng chung một giao diện, và chỉ ký **hash** (32 byte SHA-256). Nội
dung hợp đồng không bao giờ được gửi sang nhà cung cấp.

* ``get_certificate(user_id)`` → ``{'cert': DER, 'chain': [DER], 'credential_id': str}``
* ``sign_hash(user_id, credential_id, digest, doc_id, doc_name)`` →
  ``{'state': 'signed'|'waiting'|'rejected'|'expired'|'error', 'signature': bytes|None,
  'transaction_id': str, 'message': str}``
* ``get_status(user_id, credential_id, transaction_id)`` → như ``sign_hash``

``state = 'waiting'`` nghĩa là người ký cần bấm xác nhận trên app điện thoại.
Odoo sẽ hỏi lại trạng thái theo chu kỳ.

Đường dẫn endpoint và tên trường của VNPT SmartCA và Viettel MySign lấy theo tài
liệu tích hợp công khai. Chúng **cần đối chiếu lại với sandbox** khi có tài khoản
đối tác, nên đều cấu hình được trên bản ghi nhà cung cấp, không phải sửa code.
"""
import base64
import hashlib
import json
import logging
import time
import uuid

from . import demo_pki

_logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class ProviderError(Exception):
    """Lỗi khi gọi nhà cung cấp ký số (mạng, cấu hình, nghiệp vụ)."""


def _pick(data, *keys):
    """Lấy giá trị đầu tiên có mặt trong ``data`` / ``data['data']`` / ``data['content']``."""
    scopes = [data]
    for wrapper in ('data', 'content', 'result'):
        if isinstance(data, dict) and isinstance(data.get(wrapper), dict):
            scopes.append(data[wrapper])
    for scope in scopes:
        if not isinstance(scope, dict):
            continue
        for key in keys:
            if scope.get(key) not in (None, ''):
                return scope[key]
    return None


def _b64(value):
    return base64.b64decode(value) if isinstance(value, str) else value


STATUS_WORDS = {
    'signed': {'1', 'SUCCESS', 'SIGNED', 'COMPLETED', 'DONE', 'APPROVED', 'OK'},
    'rejected': {'REJECT', 'REJECTED', 'DENIED', 'CANCEL', 'CANCELED', 'CANCELLED', 'REFUSED', '4001'},
    'expired': {'EXPIRED', 'TIMEOUT', 'TIME_OUT', '4002'},
    'waiting': {'0', 'PENDING', 'WAITING', 'PROCESSING', 'IN_PROGRESS', 'CREATED', 'SENT', '4000'},
}


def map_status(raw):
    value = str(raw if raw is not None else '').strip().upper()
    for state, words in STATUS_WORDS.items():
        if value in words:
            return state
    return 'waiting' if not value else 'error'


class BaseAdapter:
    """Giao diện chung. ``config`` là dict lấy từ bản ghi ``dmag.cks.provider``."""

    def __init__(self, config):
        self.config = config or {}

    def get_certificate(self, user_id):
        raise NotImplementedError

    def sign_hash(self, user_id, credential_id, digest, doc_id, doc_name):
        raise NotImplementedError

    def get_status(self, user_id, credential_id, transaction_id):
        raise NotImplementedError

    # ----------------------------------------------------------------- HTTP chung
    def _post(self, path, payload, headers=None):
        if requests is None:
            raise ProviderError('Máy chủ thiếu thư viện python "requests".')
        base = (self.config.get('base_url') or '').rstrip('/')
        if not base:
            raise ProviderError('Nhà cung cấp chưa cấu hình địa chỉ API.')
        url = base + path
        try:
            resp = requests.post(url, json=payload, headers=dict({'Content-Type': 'application/json'},
                                                                   **(headers or {})),
                                 timeout=self.config.get('timeout') or 30)
        except requests.RequestException as exc:
            raise ProviderError('Không kết nối được %s: %s' % (url, exc)) from exc
        if resp.status_code >= 400:
            raise ProviderError('Nhà cung cấp trả lỗi HTTP %s tại %s: %s'
                                % (resp.status_code, path, resp.text[:300]))
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError('Nhà cung cấp trả dữ liệu không phải JSON tại %s.' % path) from exc


# ============================================================================ DEMO
class DemoCloudAdapter(BaseAdapter):
    """Mô phỏng ký từ xa: người ký "xác nhận trên điện thoại" sau ``demo_delay`` giây.

    Chứng thư được cấp từ Root DEMO (không có giá trị pháp lý). ``user_id`` dạng
    ``MST:0109999999`` / ``CCCD:001090012345`` hoặc chỉ dãy số (10/13 số → MST,
    12 số → CCCD). Khóa giả lập lưu trong ``config['state']`` (dict, Odoo lưu JSON).
    """

    def _identity(self, user_id):
        value = (user_id or '').strip().upper()
        if ':' in value:
            return value
        digits = ''.join(ch for ch in value if ch.isdigit() or ch == '-')
        return ('CCCD:%s' if len(digits) == 12 else 'MST:%s') % digits

    def _keypair(self, user_id):
        store = self.config.setdefault('state', {})
        uid = self._identity(user_id)
        if uid not in store:
            root = self.config.get('demo_root')
            if not root:
                raise ProviderError('Chưa có Root CA demo để cấp chứng thư giả lập.')
            name = self.config.get('display_names', {}).get(uid) or 'KHACH HANG %s' % uid.split(':')[-1]
            org = name if uid.startswith('MST') else None
            issued = demo_pki.issue(root, name, org, uid)
            store[uid] = {'key_pem': issued['key_pem'].decode(),
                          'cert': base64.b64encode(issued['cert_der']).decode()}
            self.config['state_changed'] = True
        return store[uid]

    def get_certificate(self, user_id):
        pair = self._keypair(user_id)
        return {'cert': base64.b64decode(pair['cert']), 'chain': [],
                'credential_id': 'DEMO-' + hashlib.sha1(pair['cert'].encode()).hexdigest()[:12]}

    def _sign(self, user_id, digest):
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding, utils

        pair = self._keypair(user_id)
        key = serialization.load_pem_private_key(pair['key_pem'].encode(), password=None)
        return key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))

    def sign_hash(self, user_id, credential_id, digest, doc_id, doc_name):
        delay = int(self.config.get('demo_delay') or 0)
        if delay <= 0:
            return {'state': 'signed', 'signature': self._sign(user_id, digest),
                    'transaction_id': 'DEMO-%s' % uuid.uuid4().hex[:10]}
        # Mã giao dịch mang theo hash + thời điểm hết chờ (giả lập phía nhà cung cấp).
        token = base64.urlsafe_b64encode(json.dumps({
            'd': digest.hex(), 'at': time.time() + delay}).encode()).decode()
        return {'state': 'waiting', 'signature': None, 'transaction_id': 'DEMO.' + token,
                'message': 'Đã gửi yêu cầu, mời xác nhận trên ứng dụng (giả lập %s giây).' % delay}

    def get_status(self, user_id, credential_id, transaction_id):
        try:
            info = json.loads(base64.urlsafe_b64decode(transaction_id.split('.', 1)[1]))
        except (IndexError, ValueError) as exc:
            raise ProviderError('Mã giao dịch demo không hợp lệ.') from exc
        if time.time() < info['at']:
            return {'state': 'waiting', 'signature': None, 'transaction_id': transaction_id}
        return {'state': 'signed', 'signature': self._sign(user_id, bytes.fromhex(info['d'])),
                'transaction_id': transaction_id}


# ============================================================================ VNPT SmartCA
class VnptSmartCAAdapter(BaseAdapter):
    """VNPT SmartCA, API cho nhà tích hợp (SP).

    Luồng theo tài liệu "Hướng dẫn tích hợp dịch vụ ký số từ xa VNPT SmartCA"
    (**CẦN ĐỐI CHIẾU** với tài liệu và sandbox VNPT cấp khi ký hợp đồng):

    1. ``POST {base}/v1/credentials/get_certificate``: sp_id, sp_password, user_id
       → ``user_certificates[]`` (cert_data base64, chain_data, serial_number, cert_status).
    2. ``POST {base}/v1/signatures/sign``: sign_files[{data_to_be_signed: hash hex,
       doc_id, file_type, sign_type: 'hash'}] → ``tran_id``. App SmartCA hiện thông báo.
    3. ``POST {base}/v1/signatures/sign/{tran_id}/status`` → ``signatures[].signature_value``.
    """

    PATHS = {
        'certificate': '/v1/credentials/get_certificate',
        'sign': '/v1/signatures/sign',
        'status': '/v1/signatures/sign/{transaction_id}/status',
    }

    def _path(self, key):
        return (self.config.get('paths') or {}).get(key) or self.PATHS[key]

    def _auth(self, user_id):
        if not self.config.get('client_id') or not self.config.get('client_secret'):
            raise ProviderError('VNPT SmartCA chưa cấu hình sp_id / sp_password.')
        return {'sp_id': self.config['client_id'], 'sp_password': self.config['client_secret'],
                'user_id': user_id}

    def _check(self, data):
        code = _pick(data, 'status_code', 'code')
        if code not in (None, 200, '200', 0, '0'):
            raise ProviderError('VNPT SmartCA báo lỗi %s: %s' % (code, _pick(data, 'message', 'msg') or ''))
        return data

    def get_certificate(self, user_id):
        payload = dict(self._auth(user_id), serial_number='', transaction_id=uuid.uuid4().hex)
        data = self._check(self._post(self._path('certificate'), payload))
        certs = _pick(data, 'user_certificates', 'certificates') or []
        active = [c for c in certs if str(c.get('cert_status', '')).lower() in ('', 'đang hoạt động', 'active', '1')]
        cert = (active or certs or [None])[0]
        if not cert or not cert.get('cert_data'):
            raise ProviderError('Tài khoản SmartCA %s chưa có chứng thư số đang hoạt động.' % user_id)
        chain = cert.get('chain_data') or {}
        chain_list = [chain.get('ca_cert'), chain.get('root_cert')] if isinstance(chain, dict) else chain
        return {'cert': _b64(cert['cert_data']), 'chain': [_b64(c) for c in chain_list or [] if c],
                'credential_id': cert.get('serial_number') or ''}

    def _parse(self, data, transaction_id=None):
        signatures = _pick(data, 'signatures') or []
        signature = None
        if isinstance(signatures, list) and signatures:
            signature = signatures[0].get('signature_value')
        tran_id = _pick(data, 'tran_id', 'transaction_id') or transaction_id
        if signature:
            return {'state': 'signed', 'signature': _b64(signature), 'transaction_id': tran_id}
        return {'state': map_status(_pick(data, 'tran_status', 'status')), 'signature': None,
                'transaction_id': tran_id, 'message': _pick(data, 'message', 'msg')}

    def sign_hash(self, user_id, credential_id, digest, doc_id, doc_name):
        payload = dict(self._auth(user_id), transaction_id=uuid.uuid4().hex,
                       transaction_desc='Ký hợp đồng %s' % doc_name, serial_number=credential_id or '',
                       time_stamp=time.strftime('%Y%m%d%H%M%S'),
                       sign_files=[{'data_to_be_signed': digest.hex(), 'doc_id': doc_id,
                                    'file_type': 'pdf', 'sign_type': 'hash'}])
        result = self._parse(self._check(self._post(self._path('sign'), payload)))
        if result['state'] == 'signed' or result['transaction_id']:
            if result['state'] == 'error':
                result['state'] = 'waiting'
            return result
        raise ProviderError('VNPT SmartCA không trả mã giao dịch.')

    def get_status(self, user_id, credential_id, transaction_id):
        path = self._path('status').format(transaction_id=transaction_id)
        data = self._post(path, dict(self._auth(user_id), transaction_id=transaction_id))
        return self._parse(self._check(data), transaction_id)


# ============================================================================ Viettel MySign
class ViettelMySignAdapter(BaseAdapter):
    """Viettel MySign / Viettel-CA Remote Signing.

    Kế thừa từ module ``dmag_esign_mysign`` (repo ERP-module-nha-khoa). Luồng kiểu
    CSC: login → certificates/info → signHash (async, đẩy thông báo lên app MySign)
    → requests/status. **CẦN ĐỐI CHIẾU** endpoint với Viettel-CA.
    """

    PATHS = {
        'token': '/vtss/service/ras/v1/login',
        'certificate': '/vtss/service/certificates/info',
        'sign': '/vtss/service/signHash',
        'status': '/vtss/service/requests/status',
    }
    OID_SHA256 = '2.16.840.1.101.3.4.2.1'
    OID_RSA = '1.2.840.113549.1.1.1'

    def __init__(self, config):
        super().__init__(config)
        self._tokens = {}

    def _path(self, key):
        return (self.config.get('paths') or {}).get(key) or self.PATHS[key]

    def _check(self, data):
        code = _pick(data, 'error', 'errorCode', 'error_code')
        if code not in (None, 0, '0', 'SUCCESS', False):
            raise ProviderError('MySign báo lỗi %s: %s' % (
                code, _pick(data, 'error_description', 'message', 'errorMessage', 'msg') or ''))
        return data

    def _token(self, user_id):
        if user_id not in self._tokens:
            data = self._check(self._post(self._path('token'), {
                'client_id': self.config.get('client_id'), 'client_secret': self.config.get('client_secret'),
                'user_id': user_id, 'profile_id': self.config.get('profile_id')}))
            token = _pick(data, 'access_token', 'accessToken', 'token')
            if not token:
                raise ProviderError('MySign không trả access_token: kiểm tra client_id / secret / tài khoản.')
            self._tokens[user_id] = token
        return {'Authorization': 'Bearer %s' % self._tokens[user_id]}

    def get_certificate(self, user_id):
        data = self._check(self._post(self._path('certificate'), {
            'client_id': self.config.get('client_id'), 'client_secret': self.config.get('client_secret'),
            'profile_id': self.config.get('profile_id'), 'user_id': user_id,
            'certificates': 'chain', 'certInfo': True, 'authInfo': True}, headers=self._token(user_id)))
        items = data if isinstance(data, list) else (
            _pick(data, 'credentials', 'certificates', 'items') or [])
        if isinstance(items, dict):
            items = [items]
        for item in items:
            cert = item.get('cert') if isinstance(item.get('cert'), dict) else item
            chain = cert.get('certificates') or item.get('certificates') or []
            if isinstance(chain, str):
                chain = [chain]
            if chain:
                return {'cert': _b64(chain[0]), 'chain': [_b64(c) for c in chain[1:]],
                        'credential_id': item.get('credential_id') or item.get('credentialID') or ''}
        raise ProviderError('Tài khoản MySign %s chưa có chứng thư số.' % user_id)

    def _parse(self, data, transaction_id=None):
        signatures = _pick(data, 'signatures', 'signature')
        if isinstance(signatures, str):
            signatures = [signatures]
        tran_id = _pick(data, 'transactionId', 'transaction_id', 'tranId', 'sad') or transaction_id
        if signatures:
            return {'state': 'signed', 'signature': _b64(signatures[0]), 'transaction_id': tran_id}
        return {'state': map_status(_pick(data, 'status', 'transactionStatus', 'state')),
                'signature': None, 'transaction_id': tran_id, 'message': _pick(data, 'message', 'msg')}

    def sign_hash(self, user_id, credential_id, digest, doc_id, doc_name):
        data = self._check(self._post(self._path('sign'), {
            'credentialID': credential_id, 'numSignatures': 1, 'description': 'Ký hợp đồng %s' % doc_name,
            'documents': [{'document_id': doc_id, 'document_name': doc_name}],
            'hash': [base64.b64encode(digest).decode()], 'hashAlgo': self.OID_SHA256,
            'signAlgo': self.OID_RSA, 'async': 1}, headers=self._token(user_id)))
        return self._parse(data)

    def get_status(self, user_id, credential_id, transaction_id):
        data = self._check(self._post(self._path('status'), {
            'transactionId': transaction_id, 'transaction_id': transaction_id},
            headers=self._token(user_id)))
        return self._parse(data, transaction_id)


ADAPTERS = {
    'demo_cloud': DemoCloudAdapter,
    'vnpt_smartca': VnptSmartCAAdapter,
    'viettel_mysign': ViettelMySignAdapter,
}


def get_adapter(provider_type, config):
    try:
        return ADAPTERS[provider_type](config)
    except KeyError as exc:
        raise ProviderError('Chưa hỗ trợ nhà cung cấp "%s".' % provider_type) from exc
