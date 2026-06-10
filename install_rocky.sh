#!/usr/bin/env bash
# Install Cisco Switch SSH Simulator on Rocky Linux
# Run as root: bash install_rocky.sh

set -euo pipefail

INSTALL_DIR="/opt/cisco-sim"
SERVICE_FILE="/etc/systemd/system/cisco-sim.service"

echo "==> Installing Cisco Switch SSH Simulator (Rocky Linux)"

# ── Dependencies ─────────────────────────────────────────────────────────────
dnf install -y python3 python3-pip

pip3 install asyncssh

# ── Deploy files ─────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cp simulator.py  "$INSTALL_DIR/"
cp tasks.py      "$INSTALL_DIR/"
cp endpoints.py  "$INSTALL_DIR/"

if [ ! -f "$INSTALL_DIR/state.json" ]; then
    cp state.json "$INSTALL_DIR/"
fi
cp state.json "$INSTALL_DIR/state_default.json"

chmod 755 "$INSTALL_DIR/simulator.py"

# ── Firewall: open port 2222 ──────────────────────────────────────────────────
if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port=2222/tcp
    firewall-cmd --reload
fi

# SELinux: allow python3 to bind port 2222
if command -v semanage &>/dev/null; then
    semanage port -a -t ssh_port_t -p tcp 2222 2>/dev/null || true
fi

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
