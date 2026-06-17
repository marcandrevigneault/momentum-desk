#!/usr/bin/env bash
# run_gateway.sh — start the IBKR Client Portal Gateway in the foreground (LOCAL).
#
# The gateway listens on https://localhost:5000 with a self-signed cert. Once it
# is up, open that URL, log in with your IBKR credentials and approve the IBKR
# Mobile push (IB Key 2FA) on your phone. Leave this process running the whole
# time you want the desk connected. (In the cloud, ibeam does the login for you.)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GW_DIR="${ROOT}/gateway"
GW_RUN="${GW_DIR}/bin/run.sh"
GW_CONF="${GW_DIR}/root/conf.yaml"

if [[ ! -x "${GW_RUN}" ]]; then
  echo "[run_gateway] Gateway not installed. Run: ./scripts/install_gateway.sh" >&2
  exit 1
fi
if [[ ! -f "${GW_CONF}" ]]; then
  echo "[run_gateway] Missing config ${GW_CONF}" >&2
  exit 1
fi

echo "[run_gateway] Starting IBKR Client Portal Gateway at https://localhost:5000"
echo "[run_gateway] Open it, log in, and approve the push on your phone. Keep this running."
cd "${GW_DIR}"
exec "${GW_RUN}" root/conf.yaml
