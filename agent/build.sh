#!/usr/bin/env bash
# Build DMAG Ký số.
#   ./build.sh                 -> dist/DMAGKySo.exe (Windows amd64) + dist/dmag-kyso-linux-amd64
#   ORIGINS=https://a,https://b ./build.sh   -> override the built-in origin allowlist
#   VERSION=1.0.1 ./build.sh   -> override the version string
set -euo pipefail
cd "$(dirname "$0")"

GO="${GO:-$(command -v go || echo /usr/local/go/bin/go)}"
ORIGINS="${ORIGINS:-https://dmagroup.vn,https://www.dmagroup.vn,http://localhost:8069}"
VERSION="${VERSION:-1.0.0}"
export GOTOOLCHAIN="${GOTOOLCHAIN:-local}"

X="-X main.defaultOrigins=${ORIGINS} -X main.version=${VERSION}"
mkdir -p dist

echo "==> go vet (linux + windows)"
"$GO" vet ./...
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 "$GO" vet ./...

echo "==> go test"
"$GO" test ./...

echo "==> dist/DMAGKySo.exe"
GOOS=windows GOARCH=amd64 CGO_ENABLED=0 "$GO" build -trimpath \
  -ldflags "-H windowsgui -s -w ${X}" -o dist/DMAGKySo.exe .

echo "==> dist/dmag-kyso-linux-amd64"
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 "$GO" build -trimpath \
  -ldflags "-s -w ${X}" -o dist/dmag-kyso-linux-amd64 .

ls -l dist/
if command -v sha256sum >/dev/null; then (cd dist && sha256sum DMAGKySo.exe dmag-kyso-linux-amd64); fi

# Đặt bản Windows vào addon để trang ký có link "Tải DMAG Ký số"
# (/dmag_esign_cks/static/agent/DMAGKySo.exe). File .exe không đưa vào git.
ADDON_AGENT_DIR="../addons/dmag_esign_cks/static/agent"
if [ -d "../addons/dmag_esign_cks" ]; then
  mkdir -p "$ADDON_AGENT_DIR"
  cp dist/DMAGKySo.exe "$ADDON_AGENT_DIR/DMAGKySo.exe"
  echo "==> đã chép DMAGKySo.exe vào $ADDON_AGENT_DIR"
fi
