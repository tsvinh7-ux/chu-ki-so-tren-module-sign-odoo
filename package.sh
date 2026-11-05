#!/usr/bin/env bash
# Đóng gói cho Bizapps: 3 module + thư viện Python + hướng dẫn cài + DMAGKySo.exe (nếu đã build).
#   ./package.sh            -> dist/dmag-ky-so-odoo17-<ngày>.zip
set -euo pipefail
cd "$(dirname "$0")"
OUT="dist/dmag-ky-so-odoo17-$(date +%Y%m%d)"
rm -rf "$OUT" "$OUT.zip"
mkdir -p "$OUT/addons"
for mod in dmag_esign_cks dmag_esign_cks_sign dmag_sign_probe; do
  cp -r "addons/$mod" "$OUT/addons/"
done
find "$OUT" -name '__pycache__' -type d -prune -exec rm -rf {} +
if [ -f agent/dist/DMAGKySo.exe ]; then
  mkdir -p "$OUT/addons/dmag_esign_cks/static/agent"
  cp agent/dist/DMAGKySo.exe "$OUT/addons/dmag_esign_cks/static/agent/DMAGKySo.exe"
fi
cp requirements.txt "$OUT/"
cp docs/cai-dat-cho-bizapps.md "$OUT/HUONG-DAN-CAI-DAT.md"
(cd dist && zip -qr "$(basename "$OUT").zip" "$(basename "$OUT")")
rm -rf "$OUT"
ls -l "$OUT.zip"
