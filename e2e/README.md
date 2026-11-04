# Chạy thử đầu-cuối (trình duyệt thật)

Kịch bản đã chạy khi phát triển (Odoo 17 trong Docker, Chromium headless qua Playwright):

1. `docker compose up -d` ở gốc repo, chờ http://localhost:8069 lên. DB `dmag_cks_demo` có dữ liệu demo.
2. Gửi 2 hồ sơ demo và cấp chứng thư "USB token giả lập":
   ```bash
   docker compose exec -T odoo mkdir -p /tmp/e2e
   docker compose cp e2e/odoo_shell_setup.py odoo:/tmp/e2e/setup.py
   docker compose exec -T odoo bash -c "odoo shell -d dmag_cks_demo --db_host db --db_user odoo --db_password odoo --no-http < /tmp/e2e/setup.py" | grep E2E_JSON
   for f in customer_cert customer_key dmag_cert dmag_key; do docker compose cp odoo:/tmp/e2e/$f.pem .; done
   ```
3. Chạy phần mềm ký ở chế độ thử (bản Linux, dùng file PEM thay cho USB token):
   ```bash
   (cd agent && ./build.sh)
   # full_demo.js tự chạy phần mềm này, lần lượt với chứng thư khách và DMAG
   ```
4. `npm i playwright@1.56.1`, rồi chạy toàn bộ luồng và chụp màn hình từng bước. Script tự bật phần mềm ký với chứng thư của khách, sau đó với chứng thư của DMAG:
   ```bash
   node e2e/full_demo.js urls.json <thư mục chứa *_cert.pem/*_key.pem> agent/dist/dmag-kyso-linux-amd64 shots/
   ```
   `urls.json` là dòng `E2E_JSON=...` ở bước 2.

## Ký USB token ngay trong Odoo Sign (`sign_flow.js`)

`fake_addons/sign` là module **Sign giả lập** theo cấu trúc Odoo 17: cùng tên model và trường, có trang ký vẽ PDF bằng pdf.js, hộp chọn chữ ký dùng `NameAndSignature` của web. Chỉ để phát triển và test. **Không cài lên hệ thống thật.**

```bash
odoo --addons-path=addons,e2e/fake_addons -d sign_demo -i dmag_esign_cks,sign,dmag_esign_cks_sign   # DB có dữ liệu demo
odoo shell -d sign_demo < e2e/sign_setup.py      # tạo mẫu ký, yêu cầu ký, 2 chứng thư giả lập → in E2E_JSON
node e2e/sign_flow.js sign_urls.json <thư mục PEM> agent/dist/dmag-kyso-linux-amd64 shots/ http://localhost:8069
```

Script làm lần lượt:
1. Khách mở link ký, bấm ô chữ ký, chọn tab USB token, chọn chứng thư, điền họ tên, bấm "Ký và hoàn tất".
2. DMAG làm tương tự với token công ty.
3. Mở lại trang để xem bản hoàn tất có 2 chữ ký số.
