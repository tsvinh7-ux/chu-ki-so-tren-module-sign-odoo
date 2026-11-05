# Hướng dẫn cài đặt: Ký số USB token / Viettel MySign trong module Sign (Odoo 17)

Gói gồm 3 module. Không module nào sửa mã nguồn module Sign của Bizapps:

| Module | Vai trò | Cài khi nào |
|---|---|---|
| `dmag_sign_probe` | **Chỉ đọc.** Kiểm tra module Sign có khớp cấu trúc Odoo Sign 17 không. Không đọc mã nguồn, không sửa dữ liệu | Bước 2. Gỡ sau khi kiểm tra |
| `dmag_esign_cks` | Lõi ký số PAdES: chữ ký số USB token, ký từ xa (Viettel MySign, VNPT SmartCA), kiểm tra chữ ký | Bước 3 |
| `dmag_esign_cks_sign` | Thêm tab "USB token" và "App ký số" vào hộp chọn chữ ký trên trang ký của module Sign | Bước 3 |

Nên cài lên **bản sao DB thử** trước. Nếu không có bản sao, cài `dmag_sign_probe` thẳng lên DB thật vẫn an toàn vì module chỉ đọc. Hai module còn lại nên cài vào giờ ít người dùng.

## Bước 1. Thư viện Python (trên máy chủ Odoo)

```bash
pip3 install -r requirements.txt
# pyHanko==0.25.3, pyOpenSSL==24.2.1, urllib3==1.26.20
```

pyHanko cần `cryptography` bản mới, nên phải nâng `pyOpenSSL` và `urllib3` cùng lúc. Nếu không, Odoo 17 sẽ lỗi khi import `ir_mail_server`. Bộ phiên bản trên đã chạy được trên image `odoo:17.0`.

Sau khi cài, khởi động lại Odoo.

## Bước 2. Kiểm tra tương thích (`dmag_sign_probe`)

1. Chép thư mục `addons/dmag_sign_probe` vào addons path, cập nhật danh sách ứng dụng rồi cài.
2. Vào **Thiết lập › Kiểm tra Sign (DMAG)** và bấm **Chạy kiểm tra**.
3. Bấm **Tải báo cáo (gửi cho DMAG)** và gửi file `kiem-tra-sign-dmag.json` cho DMAG.
4. Gỡ module.

Báo cáo chỉ gồm: phiên bản Odoo, danh sách module có chữ "sign", tên và kiểu trường của các model có chữ "sign", có hay không có một số hàm, các đường dẫn có chữ "sign", phiên bản thư viện Python.

## Bước 3. Cài 2 module ký số

1. Chép `addons/dmag_esign_cks` và `addons/dmag_esign_cks_sign` vào addons path, rồi cài **dmag_esign_cks_sign**. Module này tự cài kèm `dmag_esign_cks`.
2. Vào **Ký số › Cấu hình › Ký số trong module Sign**, mở bản ghi có sẵn và bấm **Kiểm tra**.
   - Các tên điền sẵn theo Odoo Sign 17. Mục nào báo "Không khớp" thì sửa tên trong tab *Tên model và trường*.
   - Có thể dựa vào báo cáo ở Bước 2, hoặc gửi báo cáo cho DMAG để DMAG điền giúp.
3. Bấm **Bật**. Từ lúc này, hộp chọn chữ ký trên trang ký có thêm lời nhắc, tab **USB token** và tab **App ký số**.
4. Nếu trang ký của Sign dùng bundle tài nguyên riêng, khác `web.assets_frontend`, mà tab không hiện: báo DMAG tên bundle (có trong báo cáo ở Bước 2) để bổ sung.

**Tắt:** bấm **Tắt** trên cùng màn hình. Module Sign chạy lại như cũ ngay lập tức. Gỡ cài đặt cũng không ảnh hưởng dữ liệu của module Sign.

## Bước 4. Cấu hình chữ ký số của DMAG và khách

Vào **Ký số › Cấu hình**:

- **Nhà cung cấp chữ ký số › Viettel MySign:**
  - Bật bản ghi (đang ở trạng thái lưu trữ), rồi nhập:
    - **Địa chỉ API**, **Client ID**, **Client secret**, **Profile ID**: thông tin tài khoản *đối tác tích hợp API MySign* do Viettel-CA cấp. Đây không phải tài khoản MySign cá nhân dùng để ký.
    - **Tài khoản ký của DMAG**: mã số thuế hoặc tài khoản MySign của công ty.
  - Đánh dấu **Dùng để DMAG ký**. Đánh dấu thêm **Cho khách ký từ xa** nếu muốn khách dùng MySign của họ.
  - Bấm **Kiểm tra kết nối / chứng thư**.
- **Ký số trong module Sign › Vai trò bắt buộc ký số:** ví dụ `DMAG`. Người ký ở vai trò này chỉ được ký bằng USB token hoặc app ký số.
- **Root CA tin cậy:** nhập Root CA quốc gia và CA gốc của Viettel-CA, VNPT-CA, BKAV… để mục "Chuỗi tin cậy" khi kiểm tra chữ ký báo xanh.
- **Công ty:** phải có **Mã số thuế**. Odoo dùng nó để chặn token hoặc tài khoản không phải của DMAG.

## Bước 5. Phần mềm DMAG Ký số (cho người ký bằng USB token)

- File `addons/dmag_esign_cks/static/agent/DMAGKySo.exe` có sẵn trong gói. Trang ký tự hiện link tải khi máy người ký chưa cài.
- Phần mềm mặc định cho phép trang `https://dmagroup.vn` và `https://www.dmagroup.vn`. Nếu Odoo chạy ở tên miền khác, lần đầu người ký sẽ được hỏi "Cho phép trang … dùng DMAG Ký số?".

## Kiểm tra nhanh sau khi cài

1. Tạo một yêu cầu ký trên module Sign: 2 người ký, một khách và một nhân viên DMAG.
2. Mở link ký của khách, bấm ô chữ ký. Hộp ký phải hiện lời nhắc màu vàng, tab **USB token** và tab **App ký số**.
3. Ký bằng USB token, hoặc bằng MySign trên điện thoại, rồi bấm hoàn tất.
4. Vào **Ký số › Tài liệu ký trong Sign** để xem "Ký số: x/y bên" và kết quả kiểm tra chữ ký.
