#!/usr/bin/env bash
# install_gateway.sh — one-time download + unzip of the IBKR Client Portal Gateway
# for LOCAL development. (In the container the gateway is baked into the image by
# the Dockerfile.) Idempotent: skips the download if it's already installed.
#
# Target dir lives next to the repo root in ./gateway (gitignored) so the Java
# process never gets packaged into the Python distribution.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GW_DIR="${ROOT}/gateway"
GW_ZIP="${ROOT}/gateway.zip"
URL="https://download2.interactivebrokers.com/portal/clientportal.gw.zip"

if [[ -x "${GW_DIR}/bin/run.sh" ]]; then
  echo "[install_gateway] Already installed at ${GW_DIR}. Skipping."
  exit 0
fi

echo "[install_gateway] Downloading IBKR Client Portal Gateway"
echo "[install_gateway]   from: ${URL}"
echo "[install_gateway]   into: ${GW_DIR}"
mkdir -p "${GW_DIR}"

if command -v curl >/dev/null 2>&1; then
  curl --fail --location --show-error --progress-bar "${URL}" --output "${GW_ZIP}"
elif command -v wget >/dev/null 2>&1; then
  wget --show-progress -O "${GW_ZIP}" "${URL}"
else
  echo "[install_gateway] ERROR: need curl or wget in PATH." >&2
  exit 1
fi

echo "[install_gateway] Unzipping..."
unzip -q -o "${GW_ZIP}" -d "${GW_DIR}"
rm -f "${GW_ZIP}"
[[ -f "${GW_DIR}/bin/run.sh" ]] && chmod +x "${GW_DIR}/bin/run.sh"

echo "[install_gateway] Done. Next: ./scripts/run_gateway.sh"
