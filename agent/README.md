# DMAG Ký số — phần mềm ký số cục bộ (local signing agent)

**DMAG Ký số** (`DMAGKySo.exe`) là một chương trình nhỏ chạy ngầm trên máy tính Windows của khách hàng (phòng khám). Khi khách hàng mở trang ký hợp đồng do Odoo phục vụ (ví dụ `https://dmagroup.vn`), JavaScript của trang gọi tới phần mềm này tại `http://127.0.0.1:19876` để:

1. liệt kê các chứng thư số của người dùng — từ USB token của **bất kỳ CA Việt Nam nào** (VNPT, Viettel, BKAV, FPT, MISA, CA2, …): driver của token đăng ký chứng thư vào kho `CurrentUser\MY` của Windows;
2. ký một giá trị băm SHA-256 (32 byte) bằng khóa riêng của chứng thư được chọn. Hộp thoại nhập **mã PIN là của driver token**, phần mềm không bao giờ nhìn thấy PIN.

Máy chủ (Odoo/pyHanko) tự dựng cấu trúc PAdES/CMS; phần mềm **chỉ trả về chữ ký thô** trên giá trị băm.

- Viết bằng Go thuần (stdlib + `golang.org/x/sys`), không cgo, biên dịch chéo từ Linux.
- Người dùng chỉ cần **nhấp đúp một lần**: phần mềm tự cài vào `%LOCALAPPDATA%\DMAGKySo\` (không cần quyền quản trị), tự chạy khi đăng nhập Windows. Không phải chỉnh cờ (flag) gì trong trình duyệt.

## Cấu trúc mã nguồn

| Tệp | Nội dung |
|---|---|
| `main.go` | Khởi động, mở cổng `127.0.0.1:19876` (bận → coi như đã có bản đang chạy, thoát lặng lẽ) |
| `server.go` | Giao thức HTTP, CORS / Private Network Access, xác nhận ký |
| `origins.go` | Danh sách trang web được phép (allowlist), tệp cấu hình `config.json` |
| `store.go` | Interface `Store`, mã lỗi |
| `certinfo.go` | Bộ đọc chứng thư "dễ tính" (chịu được serial âm, BMPString…), định dạng subject kiểu RFC 4514, chuyển chữ ký ECDSA r‖s → DER |
| `store_pem.go` | Backend PEM (Linux/macOS, dùng cho kiểm thử tự động) |
| `store_windows.go` | Backend Windows: kho `CurrentUser\MY`, CNG (NCrypt) và CryptoAPI cũ (CSP) |
| `platform_windows.go`, `install_windows.go` | Hộp thoại MessageBox, tự cài đặt / gỡ, ghi log |
| `platform_other.go` | Hành vi trên hệ điều hành khác Windows |
| `*_test.go` | Kiểm thử đơn vị và end-to-end (backend PEM) |

## Biên dịch

Yêu cầu Go ≥ 1.24.

```bash
cd agent
./build.sh
# → dist/DMAGKySo.exe            (windows/amd64, CGO_ENABLED=0, -H windowsgui: không có cửa sổ console)
# → dist/dmag-kyso-linux-amd64   (dùng để kiểm thử)
```

`build.sh` chạy `go vet` (Linux và Windows), `go test`, rồi build. Tùy chọn:

```bash
ORIGINS="https://dmagroup.vn,https://www.dmagroup.vn,http://localhost:8069" VERSION=1.0.0 ./build.sh
```

`ORIGINS` được nhúng lúc build qua `-ldflags "-X main.defaultOrigins=..."` (mặc định trong mã nguồn cũng là danh sách trên). Thư mục `dist/` không được commit (xem `.gitignore`).

Khuyến nghị: **ký mã (Authenticode) cho `DMAGKySo.exe`** trước khi phát hành, nếu không Windows SmartScreen/antivirus có thể cảnh báo một exe chưa ký vừa tải từ Internet.

## Giao thức HTTP

Chỉ lắng nghe trên `127.0.0.1:19876` (không bao giờ `0.0.0.0`). Mọi phản hồi là JSON UTF-8, có `Cache-Control: no-store`.

| Phương thức & đường dẫn | Origin | Yêu cầu | Phản hồi thành công |
|---|---|---|---|
| `GET /v1/info` | được phép **hoặc không có** Origin (gõ thẳng trên thanh địa chỉ để thử) | — | `{"name":"DMAG Ky so","version":"1.0.0","os":"windows\|linux\|darwin","backend":"windows-store\|pem"}` |
| `GET /v1/certificates` | bắt buộc, được phép (Windows: hỏi người dùng nếu chưa biết) | — | `{"certificates":[{"id","subject","common_name","issuer","serial","not_before","not_after","expired","key_type","der","chain"}]}` |
| `POST /v1/sign` | bắt buộc, được phép (Windows: hỏi người dùng nếu chưa biết) | `{"id":"<thumbprint>","hash":"<base64 32 byte>","hash_alg":"sha256","document":"HD-1708A.pdf"}` | `{"signature":"<base64>","key_type":"rsa\|ec"}` |
| `OPTIONS <các đường dẫn trên>` | preflight CORS / Private Network Access | — | `204` kèm các header CORS bên dưới |

Chi tiết trường của chứng thư:

- `id`: thumbprint SHA-1 của DER, chữ HOA hex (khi gửi lên `/v1/sign` chấp nhận cả chữ thường, dấu `:` hay khoảng trắng).
- `subject`, `issuer`: chuỗi kiểu RFC 4514 (phần tử cụ thể nhất đứng trước), ví dụ `UID=MST:0101234567, CN=Phòng khám Ánh Dương, O=..., C=VN`. Các OID lạ được in dạng `1.2.3=#<hex DER>`.
- `serial`: các byte của số serial (dạng bù hai, như Windows hiển thị), hex HOA.
- `not_before`, `not_after`: RFC 3339 (UTC). `expired`: đã quá `not_after`.
- `key_type`: `rsa` hoặc `ec` (chứng thư có khóa loại khác bị bỏ qua).
- `der`: base64 DER của chứng thư; `chain`: danh sách base64 DER các chứng thư trung gian (Windows: dựng bằng `CertGetCertificateChain` chỉ dùng dữ liệu đã có trên máy, bỏ chứng thư gốc tự ký; có thể là danh sách rỗng `[]`).
- Chỉ liệt kê chứng thư **có khóa riêng đi kèm**. Trên Windows việc này được xác định qua thuộc tính `CERT_KEY_PROV_INFO_PROP_ID`, **không chạm vào khóa** nên không hiện hộp thoại PIN khi liệt kê.
- Danh sách được sắp xếp: chứng thư còn hạn trước, hết hạn muộn nhất trước.

Chữ ký:

- RSA: RSASSA-PKCS1-v1_5 trên `DigestInfo(SHA-256, digest)`, byte big-endian (giống `rsa.SignPKCS1v15(..., crypto.SHA256, digest)`).
- EC: chữ ký ECDSA mã hóa ASN.1 DER (`SEQUENCE { r, s }`).
- Chỉ hỗ trợ `hash_alg = "sha256"` (chấp nhận cả `SHA256`, `SHA-256`); thuật toán khác hoặc bỏ trống → lỗi `unsupported`.
- `hash` là base64 chuẩn (cũng chấp nhận base64 không padding / URL-safe) và phải giải mã ra **đúng 32 byte**.
- Thân yêu cầu tối đa 64 KB.

Header CORS cho origin được phép (preflight):

```
Access-Control-Allow-Origin: <origin>
Vary: Origin
Access-Control-Allow-Methods: GET, POST, OPTIONS
Access-Control-Allow-Headers: Content-Type
Access-Control-Allow-Private-Network: true
Access-Control-Max-Age: 600
```

Các phản hồi thật (GET/POST) cho origin được phép có `Access-Control-Allow-Origin: <origin>` và `Vary: Origin`.

### Lỗi

Dạng `{"error":"<mã>","message":"<thông báo tiếng Việt để hiển thị cho người dùng>"}`:

| Mã | HTTP | Khi nào |
|---|---|---|
| `origin_not_allowed` | 403 | Thiếu Origin (trừ `GET /v1/info`), Origin không được phép, người dùng bấm "No" ở hộp thoại cho phép trang, Host không phải `127.0.0.1`/`localhost` |
| `user_cancelled` | 409 | Người dùng bấm "No" ở hộp thoại xác nhận ký, hoặc hủy hộp thoại PIN của token |
| `cert_not_found` | 404 | Không có chứng thư với `id` này (token chưa cắm…) |
| `bad_request` | 400 (413 nếu quá 64 KB; 404/405 nếu sai đường dẫn/phương thức) | JSON sai, `hash` không phải 32 byte, thiếu `id` |
| `unsupported` | 400 | `hash_alg` khác sha256, loại khóa không hỗ trợ, CSP không hỗ trợ SHA-256 |
| `sign_failed` | 500 | Lỗi khác khi ký: sai PIN, token bị khóa, token bị rút ra… (thông báo nói rõ nguyên nhân khi nhận diện được) |

Lưu ý: phản hồi `origin_not_allowed` cho một Origin hợp lệ về cú pháp vẫn kèm `Access-Control-Allow-Origin` để trang đọc được thông báo lỗi (không lộ dữ liệu nào ngoài việc phần mềm đang chạy).

### Endpoint nội bộ

`POST /v1/shutdown` (header `X-DMAG-Shutdown: 1`, **không** có header `Origin`) — dùng bởi trình cài đặt phiên bản mới để dừng bản cũ đang chạy. Trình duyệt luôn gửi `Origin` với yêu cầu POST nên trang web không gọi được endpoint này. Trang Odoo không dùng endpoint này.

### Ví dụ JavaScript (phía Odoo)

```js
const AGENT = "http://127.0.0.1:19876";
const info = await fetch(`${AGENT}/v1/info`).then(r => r.json());          // phát hiện phần mềm
const {certificates} = await fetch(`${AGENT}/v1/certificates`).then(r => r.json());
const res = await fetch(`${AGENT}/v1/sign`, {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({id: certificates[0].id, hash: digestB64, hash_alg: "sha256", document: "HD-1708A.pdf"}),
});
const body = await res.json();   // {signature, key_type} hoặc {error, message}
```

Nếu `fetch` ném lỗi mạng (`TypeError`) thì nhiều khả năng phần mềm chưa được cài/chưa chạy → hiển thị liên kết tải `DMAGKySo.exe`.

## Mô hình bảo mật

- **Chỉ lắng nghe trên loopback** `127.0.0.1`. Máy khác trong mạng LAN không gọi được.
- **Kiểm tra header `Host`** phải là `127.0.0.1:<cổng>` hoặc `localhost:<cổng>` — chống tấn công DNS rebinding.
- **Danh sách trang web được phép (allowlist)** = danh sách nhúng lúc build ∪ các trang người dùng đã đồng ý (lưu ở `%APPDATA%\DMAGKySo\config.json`, dạng `{"allowed_origins":[...]}`; trên Linux/macOS là `os.UserConfigDir()/DMAGKySo/config.json`). Origin được chuẩn hóa (chữ thường, bỏ cổng mặc định); `null`, `file://`, extension, origin có path… đều bị từ chối. Yêu cầu `GET /v1/certificates` và `POST /v1/sign` **bắt buộc có Origin** hợp lệ.
- **Trang lạ (Windows)**: khi một origin chưa biết gọi `/v1/certificates` hoặc `/v1/sign`, phần mềm hiện hộp thoại Yes/No: *"Trang &lt;origin&gt; muốn dùng phần mềm DMAG Ký số. Chỉ đồng ý nếu bạn tin cậy trang này. Cho phép?"* (thêm cảnh báo nếu trang dùng `http://` không phải localhost). Yes → lưu vào `config.json`. No → từ chối, và không hỏi lại trang đó trong 2 phút (chống spam hộp thoại). Mỗi lúc chỉ hiện một hộp thoại. Preflight (OPTIONS) từ origin lạ được cho qua trên Windows để yêu cầu thật có thể hiện hộp thoại; bản thân yêu cầu thật luôn bị kiểm tra lại.
- **Trang lạ (Linux/macOS)**: biến môi trường `DMAG_AGENT_ALLOWED_ORIGINS` (danh sách cách nhau bởi dấu phẩy) bổ sung vào allowlist; origin khác bị từ chối, không hỏi.
- **Xác nhận trước mỗi chữ ký (Windows)**: hộp thoại luôn nằm trên cùng *"Trang &lt;origin&gt; yêu cầu ký tài liệu: &lt;document&gt; / Chứng thư: &lt;common_name&gt; / Đồng ý ký?"*; nút mặc định là **No**. Tên tài liệu được làm sạch (bỏ ký tự điều khiển, xuống dòng, ký tự đảo chiều Unicode; tối đa 200 ký tự) để trang web không giả mạo được nội dung hộp thoại. Sau đó mới tới hộp thoại PIN của token. Mỗi lúc chỉ xử lý một yêu cầu ký.
- **Không bao giờ thấy PIN**: PIN do driver token thu thập. Phần mềm không dùng cờ "silent", không cache PIN.
- **Log** tại `%LOCALAPPDATA%\DMAGKySo\agent.log` (xoay vòng khi vượt ~1 MB, giữ thêm `agent.log.1`): chỉ ghi origin, tên tài liệu, thumbprint chứng thư, kết quả. Không ghi giá trị băm, chữ ký hay PIN.
- **Giới hạn**: thân yêu cầu ≤ 64 KB, header ≤ 16 KB, timeout đọc yêu cầu.
- Phần mềm ký **bất kỳ giá trị băm nào** mà trang được phép gửi tới — phần mềm không thể biết giá trị băm đó tương ứng với tài liệu nào. Vì vậy an toàn phụ thuộc vào: (1) allowlist chỉ gồm trang tin cậy, (2) người dùng đọc hộp thoại xác nhận. Đây là giới hạn chung của mọi plugin ký số dạng này.

## Cài đặt và gỡ trên Windows

- Nhấp đúp `DMAGKySo.exe` (tải từ trang ký hợp đồng):
  - chép chính nó vào `%LOCALAPPDATA%\DMAGKySo\DMAGKySo.exe` (không chép luồng "Mark of the Web");
  - ghi `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` giá trị `DMAGKySo` = `"<đường dẫn>" /autostart` (không cần quyền admin);
  - chạy bản đã cài (tách tiến trình), báo *"Đã cài đặt DMAG Ký số. Phần mềm sẽ tự chạy khi bật máy. Bạn có thể quay lại trang ký hợp đồng."* rồi thoát.
- Nhấp đúp lần nữa khi đang chạy (cùng phiên bản): báo *"DMAG Ký số đang chạy."*.
- Cập nhật: chạy exe phiên bản mới → nó gọi `POST /v1/shutdown` để dừng bản cũ, chép đè (nếu tệp đang bị khóa thì đổi tên bản cũ thành `.old`), chạy bản mới.
- Khi khởi động tự động lúc đăng nhập (`/autostart`) mà cổng bận: thoát lặng lẽ.
- `DMAGKySo.exe /uninstall`: xóa khóa Run, dừng bản đang chạy (nếu được), hỏi có xóa `config.json` không, rồi báo đã gỡ và hướng dẫn xóa thư mục `%LOCALAPPDATA%\DMAGKySo`.
- `DMAGKySo.exe /run`: chạy tại chỗ không cài đặt (để gỡ lỗi).

## Chi tiết backend Windows (`store_windows.go`)

- Mở kho bằng `CertOpenStore(CERT_STORE_PROV_SYSTEM_W, CERT_SYSTEM_STORE_CURRENT_USER, "MY")` (chỉ đọc), duyệt bằng `CertEnumCertificatesInStore`, thumbprint = SHA-1 của DER.
- Ký: `CryptAcquireCertificatePrivateKey(CRYPT_ACQUIRE_ALLOW_NCRYPT_KEY_FLAG)` — **không** silent, không cache; giải phóng handle nếu `callerFree`.
  - Khóa CNG (`keySpec == CERT_NCRYPT_KEY_SPEC`): `NCryptSignHash` với `BCRYPT_PKCS1_PADDING_INFO{"SHA256"}` + `BCRYPT_PAD_PKCS1` cho RSA; ECDSA không padding, kết quả r‖s được chuyển sang DER. Gọi một lần với bộ đệm đúng kích thước dự kiến (tránh driver hỏi PIN hai lần), nếu `NTE_BUFFER_TOO_SMALL` thì dùng kiểu gọi hai lần.
  - Khóa CryptoAPI cũ (`AT_SIGNATURE`/`AT_KEYEXCHANGE`): `CryptCreateHash(CALG_SHA_256)`, `CryptSetHashParam(HP_HASHVAL)`, `CryptSignHashW`; kết quả little-endian được đảo byte.
  - Nếu CSP kiểu `PROV_RSA_FULL` báo `NTE_BAD_ALGID` (không biết SHA-256): **cố gắng** mở lại cùng key container bằng `CryptAcquireContextW(container, <cùng tên provider>, PROV_RSA_AES)`, rồi với provider AES mặc định của Microsoft. Cách này chạy được với khóa lưu trong CSP phần mềm của Microsoft; với token, CSP của nhà cung cấp thường tự hỗ trợ SHA-256. Nếu vẫn thất bại → lỗi `unsupported`.
  - Đặt cửa sổ cha cho hộp thoại PIN (`NCRYPT_WINDOW_HANDLE_PROPERTY` / `PP_CLIENT_HWND`) là cửa sổ đang ở foreground (thường là trình duyệt) — best effort.
  - Ánh xạ lỗi: `ERROR_CANCELLED`, `SCARD_W_CANCELLED_BY_USER` (0x8010006E), `SCARD_E_CANCELLED`, `NTE_USER_CANCELLED` → `user_cancelled`; `SCARD_W_WRONG_CHV` → "Mã PIN không đúng"; `SCARD_W_CHV_BLOCKED` → "USB token đã bị khóa"; `SCARD_E_NO_SMARTCARD`, `SCARD_W_REMOVED_CARD`, `NTE_BAD_KEYSET`… → "Không truy cập được USB token".
- Bộ đọc chứng thư tự viết (`certinfo.go`) thay cho `x509.ParseCertificate` để không bỏ sót chứng thư có serial âm hay mã hóa chuỗi bất thường; ngoài ra `go.mod` bật `godebug x509negativeserial=1`.

## Kiểm thử

### Tự động (Linux)

```bash
cd agent
go vet ./...
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go vet ./...     # kiểm tra kiểu cho mã Windows
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -o /dev/null .
go test ./...
```

Các kiểm thử bao gồm: header preflight CORS (kể cả `Access-Control-Allow-Private-Network`), từ chối origin thiếu/lạ/`null`/giả dạng, `/v1/info` không cần Origin, kiểm tra Host (DNS rebinding), độ dài digest sai, `hash_alg` khác sha256, thân quá 64 KB, chứng thư không tồn tại, hộp thoại xác nhận (từ chối → `user_cancelled`, làm sạch tên tài liệu), hỏi/lưu/từ chối origin lạ, lưu và đọc lại `config.json`, endpoint shutdown, **ký thật end-to-end qua HTTP với backend PEM cho RSA và ECDSA** (chữ ký được kiểm tra bằng `rsa.VerifyPKCS1v15` / `ecdsa.VerifyASN1`), chứng thư serial âm + BMPString, chuyển r‖s → DER, xoay vòng log.

### Thử thủ công backend PEM

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 2 -utf8 \
  -subj "/C=VN/O=Phòng khám Thử/CN=Nguyễn Văn Thử"
DMAG_AGENT_PEM_CERT=cert.pem DMAG_AGENT_PEM_KEY=key.pem ./dist/dmag-kyso-linux-amd64 &
curl -s http://127.0.0.1:19876/v1/info
curl -s -H 'Origin: http://localhost:8069' http://127.0.0.1:19876/v1/certificates
```

Biến môi trường (chỉ cho backend PEM / kiểm thử): `DMAG_AGENT_PEM_CERT`, `DMAG_AGENT_PEM_KEY` (PEM; khóa PKCS#8, PKCS#1 RSA hoặc EC; các CERTIFICATE sau chứng thư đầu tiên được coi là chain), `DMAG_AGENT_ALLOWED_ORIGINS`, `DMAG_AGENT_AUTO_REJECT=1` (từ chối mọi yêu cầu ký như người dùng bấm No), `DMAG_AGENT_PEM_CANCEL=1` (giả lập hủy PIN), `DMAG_AGENT_PORT` (đổi cổng, vẫn chỉ trên 127.0.0.1), `DMAG_AGENT_CONFIG_DIR` (đổi thư mục cấu hình).

### ⚠️ Chưa được kiểm thử: cần kiểm thử trên máy Windows có USB token thật

Mã Windows (`store_windows.go`, `platform_windows.go`, `install_windows.go`) **chỉ được kiểm tra kiểu và biên dịch chéo** trong môi trường Linux; **chưa từng được chạy**. Các phần dùng chung (giao thức, allowlist, đọc chứng thư, chuyển chữ ký ECDSA) đã được kiểm thử tự động. Danh sách kiểm thử thủ công:

- [ ] Nhấp đúp `DMAGKySo.exe` từ thư mục Downloads trên Windows 10 và 11, tài khoản **không** có quyền admin: có thông báo cài đặt, tệp nằm ở `%LOCALAPPDATA%\DMAGKySo\`, có khóa `HKCU\...\Run\DMAGKySo`, không có cửa sổ console, `http://127.0.0.1:19876/v1/info` trả JSON.
- [ ] Đăng xuất/đăng nhập lại: phần mềm tự chạy, không hiện hộp thoại nào.
- [ ] Nhấp đúp lần hai: "DMAG Ký số đang chạy."; build phiên bản khác (`VERSION=1.0.1`) và chạy: bản cũ dừng, bản mới được chép đè và chạy.
- [ ] SmartScreen / Windows Defender / phần mềm diệt virus phổ biến tại VN (BKAV, Kaspersky) có chặn exe không (đặc biệt khi chưa ký Authenticode).
- [ ] Token **VNPT-CA**, **Viettel-CA**, **BKAV-CA**, và nếu có: FPT-CA, MISA eSign, CA2, EasyCA, SafeCert…: `/v1/certificates` liệt kê đúng chứng thư, **không hiện hộp thoại PIN khi liệt kê**; `chain` có chứng thư trung gian.
- [ ] Ký RSA với token dùng **minidriver/CNG (Smart Card KSP)** và token dùng **CSP cũ của nhà cung cấp**: chữ ký kiểm tra hợp lệ ở phía máy chủ (pyHanko) — xác nhận đúng thứ tự byte (CAPI phải đảo byte).
- [ ] Chứng thư ECDSA (nếu có CA cấp): chữ ký DER hợp lệ.
- [ ] Khóa trong CSP `PROV_RSA_FULL` (ví dụ PFX nhập vào "Microsoft Base Cryptographic Provider"): đường thử lại `PROV_RSA_AES` hoạt động.
- [ ] Bấm **Cancel** ở hộp thoại PIN → `user_cancelled`; bấm **No** ở hộp thoại xác nhận → `user_cancelled`.
- [ ] Nhập **sai PIN** → `sign_failed` với thông báo "Mã PIN không đúng" (kiểm tra mã lỗi thực tế driver trả về, xem `agent.log`); token bị khóa PIN.
- [ ] **Rút token** rồi ký (chứng thư vẫn còn trong kho `MY`) → thông báo "Không truy cập được USB token"; rút token rồi gọi `/v1/certificates`.
- [ ] Hộp thoại xác nhận và hộp thoại PIN có hiện **lên trên** trình duyệt (Chrome, Edge, Cốc Cốc, Firefox) hay bị chìm phía sau.
- [ ] Chrome/Edge với Private Network Access đang bật: trang HTTPS gọi được `http://127.0.0.1:19876` (preflight có `Access-Control-Allow-Private-Network`). Firefox, Cốc Cốc.
- [ ] Origin lạ: hộp thoại cho phép xuất hiện; Yes → `%APPDATA%\DMAGKySo\config.json` được ghi; No → 403.
- [ ] Ký liên tiếp nhiều tài liệu: có hỏi PIN lại mỗi lần không (tùy driver).
- [ ] `DMAGKySo.exe /uninstall`.
- [ ] `agent.log` không chứa thông tin nhạy cảm và xoay vòng ở ~1 MB.
