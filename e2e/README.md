# Chạy thử đầu-cuối (trình duyệt thật)

Kịch bản đã chạy khi phát triển (Odoo 17 trong Docker, Chromium headless qua Playwright):

1. `docker compose up -d` ở gốc repo, chờ http://localhost:8069 lên. DB `dmag_cks_demo` có dữ liệu demo.
2. Gửi 2 hồ sơ demo và cấp chứng thư "USB token giả lập":
   ```bash
   docker compose exec -T odoo mkdir -p /tmp/e2e
   docker compose cp e2e/odoo_shell_setup.py odoo:/tmp/e2e/setup.py
   docker compose exec -T odoo bash -c "odoo shell -d dmag_cks_demo --db_host db --db_user odoo --db_password odoo --no-http < /tmp/e2e/setup.py" | grep E2E_JSON
   docker compose cp odoo:/tmp/e2e/token_cert.pem . && docker compose cp odoo:/tmp/e2e/token_key.pem .
   ```
3. Chạy phần mềm ký ở chế độ thử (bản Linux, dùng file PEM thay cho USB token):
   ```bash
   (cd agent && ./build.sh)
   DMAG_AGENT_PEM_CERT=token_cert.pem DMAG_AGENT_PEM_KEY=token_key.pem agent/dist/dmag-kyso-linux-amd64 &
   ```
4. `npm i playwright@1.56.1 && node e2e/run.js "<url hồ sơ 1>" "<url hồ sơ 2>"`. Hồ sơ 1 ký bằng USB token,
   hồ sơ 2 ký bằng app (giả lập). Script chụp màn hình từng bước.
