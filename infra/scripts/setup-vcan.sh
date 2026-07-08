#!/usr/bin/env bash
# infra/scripts/setup-vcan.sh
# Brings up the vcan0 virtual CAN interface for SOVDpilot local development.
# Works natively in WSL2 (kernel >= 5.15) and Linux hosts with vcan kernel module.
#
# Usage:
#   sudo ./infra/scripts/setup-vcan.sh
#
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

IFACE="${VCAN_IFACE:-vcan0}"

log()  { echo "[setup-vcan] $*"; }
err()  { echo "[setup-vcan] ERROR: $*" >&2; exit 1; }

# ── 1. Root check ────────────────────────────────────────────────────────────
if [[ "${EUID}" -ne 0 ]]; then
    err "This script must be run as root (sudo)."
fi

# ── 2. Load kernel module ─────────────────────────────────────────────────────
if ! lsmod | grep -q "^vcan "; then
    log "Loading vcan kernel module..."
    modprobe vcan || err "modprobe vcan failed. Is the vcan module available in this kernel?"
fi
log "vcan module: loaded"

# ── 3. Skip if interface already up ──────────────────────────────────────────
if ip link show "${IFACE}" &>/dev/null; then
    STATE=$(ip link show "${IFACE}" | awk '/state/{print $9}')
    log "Interface ${IFACE} already exists (state: ${STATE}). Nothing to do."
    exit 0
fi

# ── 4. Create and bring up the interface ─────────────────────────────────────
log "Creating ${IFACE}..."
ip link add dev "${IFACE}" type vcan
ip link set up "${IFACE}"
log "Interface ${IFACE} is UP."

# ── 5. Smoke test ─────────────────────────────────────────────────────────────
if ! ip link show "${IFACE}" | grep -q "UP"; then
    err "Interface ${IFACE} was created but is not UP. Check ip link output."
fi

log "Smoke test: sending a test UDS frame (7DF#02 01 00 ...)"
if command -v cansend &>/dev/null; then
    cansend "${IFACE}" 7DF#0201000000000000 && log "cansend OK"
else
    log "cansend not found — skipping smoke test (install can-utils for full verification)."
fi

log "Done. Virtual CAN interface ${IFACE} is ready."
log "Tip: use 'candump ${IFACE}' in another terminal to monitor traffic."
