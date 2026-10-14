# Chữ ký số trên module ký của Odoo (DMAG)

Addon Odoo 17 **`dmag_esign_cks`** gắn **chữ ký số công cộng của CA Việt Nam** (chuẩn PAdES) vào tài liệu đã ký
trên module ký của Odoo, **không sửa module ký**. Mục tiêu: hợp đồng có giá trị pháp lý đầy đủ theo Luật Giao dịch
điện tử 2023 và Nghị định 23/2025/NĐ-CP.

- Khách ký bằng **USB token của bất kỳ nhà cung cấp nào**, qua phần mềm **DMAG Ký số** (cài 1 lần).
- Khách ký bằng **app ký số từ xa** (VNPT SmartCA, Viettel MySign; adapter cấu hình được).
- **DMAG tự động ký** bằng chứng thư số tổ chức, kèm dấu thời gian.
- Odoo **tự kiểm tra** chữ ký: toàn vẹn, chuỗi tin cậy, **MST/CCCD khớp khách hàng**.

| Thư mục | Nội dung |
|---|---|
| `addons/dmag_esign_cks/` | Addon Odoo 17 (models, trang ký của khách, adapter nhà cung cấp, thư viện PAdES, test) |
| `agent/` | Phần mềm **DMAG Ký số** (Go): ký bằng USB token qua kho chứng thư Windows |
| `docs/` | Phương án pháp lý, phương án chốt, **hướng dẫn triển khai** |
| `e2e/` | Kịch bản chạy thử bằng trình duyệt thật |

**Bắt đầu:** `docker compose up -d` rồi mở http://localhost:8069 (admin/admin). Menu **Ký số** có 2 hồ sơ demo.

Tài liệu:
- [Hướng dẫn triển khai (cho Bizapps)](docs/huong-dan-trien-khai.md)
- [Phương án chốt v2](docs/phuong-an-chot-v2.md)
- [Đề xuất v1 và căn cứ pháp lý](docs/de-xuat-chu-ky-so-odoo-sign.md)
