#!/usr/bin/env bash
# Install Cisco Switch SSH Simulator on Ubuntu
# Run as root: bash install_ubuntu.sh

set -euo pipefail

INSTALL_DIR="/opt/cisco-sim"
SERVICE_FILE="/etc/systemd/system/cisco-sim.service"

echo "==> Installing Cisco Switch SSH Simulator (Ubuntu)"

# ── Dependencies ─────────────────────────────────────────────────────────────
apt-get update -q
apt-get install -y -q python3 python3-pip

pip3 install asyncssh

# ── Deploy files ──────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cp simulator.py  "$INSTALL_DIR/"
cp tasks.py      "$INSTALL_DIR/"
cp endpoints.py  "$INSTALL_DIR/"

# Only deploy state.json if not already present (preserve any existing state)
if [ ! -f "$INSTALL_DIR/state.json" ]; then
    cp state.json "$INSTALL_DIR/"
fi

# Keep a clean backup for resets
cp state.json "$INSTALL_DIR/state_default.json"

chmod 755 "$INSTALL_DIR/simulator.py"

# ── Install systemd service ───────────────────────────────────────────────────
cp cisco-sim.service "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable cisco-sim
systemctl start  cisco-sim

# ── Verify ────────────────────────────────────────────────────────────────────
sleep 2
systemctl status cisco-sim --no-pager

echo ""
echo "==> Done. Connect with:"
echo "    ssh -p 2222 root@$(hostname -I | awk '{print $1}')"
echo "    Password: cisco"
echo ""
echo "    Or with legacy KEX:"
echo "    ssh -p 2222 -oKexAlgorithms=+diffie-hellman-group14-sha1 root@$(hostname -I | awk '{print $1}')"
