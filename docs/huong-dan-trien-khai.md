# Hướng dẫn triển khai addon ký số `dmag_esign_cks` (Odoo 17)

> Gửi kèm cho Bizapps khi cài đặt. Phương án nghiệp vụ xem ở [phuong-an-chot-v2.md](phuong-an-chot-v2.md).

## 1. Addon làm gì

Addon chỉ **gắn thêm chữ ký số** vào tài liệu, **không sửa** module ký của Bizapps.

| Ai ký | Cách ký | Trạng thái |
|---|---|---|
| Nội bộ | Module ký sẵn có của Bizapps | Không đổi |
| Khách hàng không cần CKS | Ký trên module ký của Bizapps như hiện nay (vẽ chữ ký / OTP) | Không đổi. DMAG đóng CKS tổ chức lên bản cuối. |
| Khách có **USB token** (mọi nhà cung cấp) | Trang ký → "USB token" → chọn chứng thư → nhập PIN | ✅ Đã làm |
| Khách có **CKS ký từ xa** | Trang ký → "Ứng dụng trên điện thoại" → nhập tài khoản → xác nhận trên app | ✅ Khung xong, adapter VNPT SmartCA và Viettel MySign chờ tài khoản sandbox để đối chiếu |
| **DMAG** | **Mặc định: người của DMAG ký bằng USB token chứng thư tổ chức** (bấm "DMAG ký bằng USB token" trên hồ sơ). Odoo chặn nếu token không đúng MST của DMAG. Tùy chọn: ký tự động bằng file .p12 hoặc ký từ xa | ✅ USB token và .p12 đã làm. Ký từ xa chờ tài khoản |

Sau khi các bên ký xong:
- File PDF có các chữ ký số chuẩn **PAdES**, mở bằng Adobe/Foxit là thấy.
- Có thêm **trang xác nhận ký số** ghi mã SHA-256 của bản gốc.
- Odoo **tự kiểm tra** từng chữ ký: còn toàn vẹn, hợp lệ, chuỗi về Root CA tin cậy, và **MST/CCCD trong chứng thư khớp với khách hàng**.

Ảnh chụp từ lần chạy thử đầu-cuối (hai bên đều ký bằng USB token):

| 1. Khách chọn USB token | 2. Khách ký xong, chờ DMAG | 3. Quản trị: đến lượt DMAG |
|---|---|---|
| ![](images/03-khach-chon-usb-token.png) | ![](images/04-khach-ky-xong-cho-dmag.png) | ![](images/05-den-luot-dmag-ky.png) |

| 4. DMAG ký bằng USB token | 5. Hoàn tất | 6. Kiểm tra chữ ký |
|---|---|---|
| ![](images/06-dmag-ky-usb-token.png) | ![](images/07-hoan-tat.png) | ![](images/08-kiem-tra-chu-ky.png) |

| Trang xác nhận ký số trong PDF | Khách ký bằng app (giả lập) | Kết nối module ký | Trên điện thoại |
|---|---|---|---|
| ![](images/14-trang-xac-nhan-ky-so.png) | ![](images/09-khach-ky-bang-app.png) | ![](images/12-ket-noi-module-sign.png) | ![](images/13-dien-thoai.png) |

## 2. Cài đặt trên máy chủ Odoo 17

1. Cài thư viện Python (đã thử trên image chính thức `odoo:17.0`):
   ```bash
   pip3 install -r requirements.txt
   # pyHanko==0.25.3, pyOpenSSL==24.2.1, urllib3==1.26.20
   ```
   pyHanko cần `cryptography` bản mới. Vì vậy phải nâng `pyOpenSSL` và `urllib3` cùng lúc, nếu không Odoo
   sẽ lỗi khi import `ir_mail_server`. Bộ phiên bản trên đã chạy được với Odoo 17.
2. Chép thư mục `addons/dmag_esign_cks` vào addons path, cập nhật danh sách ứng dụng, cài **"DMAG – Chữ ký số cho hợp đồng"**.
   Addon chỉ phụ thuộc `mail` và `portal`.
3. Chạy test: `odoo -d <db_test> -i dmag_esign_cks --test-enable --test-tags=/dmag_esign_cks --stop-after-init`
   (kết quả lúc phát triển: **17/17 test đạt**, cộng 4 test phần nối module ký chạy với module Sign giả lập trong `e2e/fake_addons`).

Chạy thử nhanh bằng Docker: `docker compose up -d` rồi mở http://localhost:8069 (admin/admin, DB có dữ liệu demo).

## 3. Cấu hình (menu **Ký số › Cấu hình**)

1. **Nhà cung cấp chữ ký số**
   - **CKS của DMAG:** cài xong đã có sẵn bản ghi **"CKS tổ chức DMAG (USB token)"**, là cách mặc định. Kiểm tra công ty đã nhập **Mã số thuế**, vì Odoo dùng nó để chặn token không phải của DMAG.
     Khi đến lượt, người phụ trách hồ sơ nhận việc cần làm "DMAG ký số bằng USB token". Người đó mở hồ sơ, bấm **DMAG ký bằng USB token**, cắm token và nhập PIN.
     Muốn ký tự động thì tạo bản ghi khác, đánh dấu "Dùng để DMAG ký" và đặt lên đầu danh sách.
     - Loại *Chứng thư trên máy chủ (.p12/.pfx)*: tải file lên và nhập mật khẩu.
     - Loại *VNPT SmartCA / Viettel MySign*: nhập API, Client ID/secret và "Tài khoản ký của DMAG".
     - Có thể nhập *TSA URL* để gắn dấu thời gian.
   - **Cho khách ký từ xa:** bản ghi *VNPT SmartCA* và *Viettel MySign* đã tạo sẵn ở trạng thái lưu trữ. Điền thông tin tài khoản đối tác rồi bật lên.
     Nếu tài liệu API của nhà cung cấp khác mặc định, sửa ở ô *Endpoint tùy chỉnh (JSON)*, không cần sửa code.
   - Bấm **Kiểm tra kết nối / chứng thư** để thử.
2. **Root CA tin cậy:** thêm Root CA quốc gia và CA gốc của các nhà cung cấp (VNPT, Viettel, BKAV, FPT, MISA…).
   Thiếu Root CA thì cột "Chuỗi tin cậy" sẽ báo đỏ dù chữ ký vẫn toàn vẹn.
3. **Khách hàng:** ô *Mã số thuế* có sẵn. Addon thêm **Số CCCD (ký số)** cho cá nhân và **Luôn yêu cầu ký số**.

## 4. Phần mềm "DMAG Ký số" cho USB token

- Mã nguồn ở `agent/` (Go), build bằng `agent/build.sh`. Script tạo `DMAGKySo.exe` và tự chép vào addon,
  để trang ký có link tải.
- Khách **tải về, bấm đúp một lần**: phần mềm tự cài cho người dùng hiện tại (không cần quyền admin) và tự chạy khi bật máy.
- Phần mềm đọc kho chứng thư Windows, nên **dùng được token của mọi nhà cung cấp**, vì driver token khách đã cài để khai thuế đưa chứng thư vào đó.
- **Bảo mật:**
  - Phần mềm chỉ nghe trên `127.0.0.1`.
  - Chỉ trang trong danh sách cho phép mới gọi được. Mặc định gồm `dmagroup.vn` và `localhost:8069`. Gặp tên miền lạ, phần mềm hỏi người dùng.
  - Trước mỗi lần ký, phần mềm hiện hộp thoại xác nhận, sau đó driver token hỏi PIN.
  - Phần mềm chỉ nhận **mã băm**, không nhận nội dung hợp đồng.
- Nếu Odoo chạy ở tên miền khác (ví dụ `erp.dmagroup.vn`), build lại với `ORIGINS=https://erp.dmagroup.vn ./build.sh`, hoặc để khách bấm "Có" lần đầu.
- **Trước khi phát hành:** nên ký số file .exe (Authenticode) để Windows SmartScreen và phần mềm diệt virus không cảnh báo.

## 5. Nối với module ký (bản Sign của Bizapps)

**Không cần sửa module ký và không cần viết module cầu nối.** Vào **Ký số › Cấu hình › Kết nối module ký**, mở bản ghi
"Module Sign (kiểu Odoo Sign)" rồi bấm **Kích hoạt / kiểm tra**.

- Các ô điền sẵn theo Odoo Sign: model `sign.request`, trạng thái `state = signed`, file `completed_document`,
  tên `reference`, người ký `request_item_ids.partner_id`. Bizapps làm "giống Odoo Sign" nên nhiều khả năng giữ
  nguyên các tên này. Nếu khác, chỉ cần sửa các ô tương ứng. Nút Kích hoạt sẽ báo nếu model hoặc trường không tồn tại.
- **Tự động:** cứ 5 phút, yêu cầu ký nào đã hoàn tất trên module Sign sẽ được tạo hồ sơ ký số. Khách nhận email
  mời ký bằng USB token hoặc app, sau đó DMAG ký bằng USB token.
- **Thủ công:** module Sign có thêm nút **Gửi ký số (CKS)** trong menu Thao tác.
- **Khách hàng** là người ký đầu tiên không phải người của công ty mình.
- Ô **"Khách ký số"** chọn một trong ba: luôn ký, theo cài đặt từng khách, hoặc chỉ DMAG ký.
- Ký xong, file PDF đã ký số được gửi ngược vào phần trao đổi (chatter) của chính yêu cầu ký.

Model khác (ví dụ hợp đồng `dmag.contract`) dùng mixin `dmag.cks.mixin` và hàm `_dmag_cks_on_done(envelope)` để
nhận file đã ký và chuyển trạng thái.

## 6. Đã kiểm chứng và chưa kiểm chứng

| Hạng mục | Kết quả |
|---|---|
| Ký PAdES nhiều chữ ký nối tiếp (RSA, ECDSA), chữ ký sai bị từ chối, sửa 1 byte thì mọi chữ ký báo hỏng | ✅ Test tự động |
| Luồng Odoo: khách ký USB, **DMAG ký USB (sai MST bị chặn, link của khách không ký thay DMAG được)**, cá nhân ký vào ô tổ chức bị chặn, ký từ xa (giả lập) có chờ xác nhận, DMAG tự ký bằng .p12 | ✅ 17/17 test Odoo |
| Nối module ký kiểu Odoo Sign: tự tạo hồ sơ, không trùng, không nhận người nội bộ làm khách, file đã ký quay về yêu cầu ký | ✅ 4 test với module Sign giả lập. ⏳ Cần chạy trên bản Sign thật của Bizapps |
| Trình duyệt thật: khách ký USB, DMAG ký USB, ký từ xa giả lập (`e2e/full_demo.js`) | ✅ Playwright + Chromium |
| PDF kiểm tra bằng công cụ độc lập (poppler `pdfsig`) | ✅ "Signature is Valid" |
| **Phần mềm DMAG Ký số trên Windows với USB token thật** | ⏳ Code đã biên dịch, **chưa chạy trên máy Windows**. Danh sách kiểm thử ở `agent/README.md` |
| **API VNPT SmartCA / Viettel MySign thật** | ⏳ Code theo tài liệu công khai, **cần tài khoản sandbox để đối chiếu** endpoint và tên trường |
| Dấu thời gian (TSA) của CA thật, kiểm tra thu hồi (OCSP/CRL) trực tuyến | ⏳ Có trong code, chưa thử với dịch vụ thật |
| Mở PDF trong Adobe Reader trên Windows | ⏳ Cần cài Root CA Việt Nam vào Adobe để hiện "hợp lệ" |
