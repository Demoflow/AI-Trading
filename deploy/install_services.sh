#!/usr/bin/env bash
# ============================================================
#  Install (or reinstall) all systemd services.
#  Run as root on the VPS after copying code to the server.
#  Usage: sudo bash /home/trading/trading_system/deploy/install_services.sh
# ============================================================
set -euo pipefail

DEPLOY_DIR="/home/trading/trading_system/deploy"
SYSTEMD_DIR="/etc/systemd/system"

echo "Installing trading system systemd services..."

for svc in scalper smallcap scalper_dashboard smallcap_dashboard; do
    src="$DEPLOY_DIR/$svc.service"
    dst="$SYSTEMD_DIR/$svc.service"
    if [ ! -f "$src" ]; then
        echo "  ERROR: $src not found — is the code copied to the server?"
        exit 1
    fi
    cp "$src" "$dst"
    echo "  Installed: $dst"
done

systemctl daemon-reload

for svc in scalper smallcap scalper_dashboard smallcap_dashboard; do
    systemctl enable "$svc"
    systemctl restart "$svc"
    echo "  Started:   $svc"
done

echo ""
echo "All services installed and running. Status:"
echo ""
for svc in scalper smallcap scalper_dashboard smallcap_dashboard; do
    STATUS=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
    echo "  [$STATUS]  $svc"
done

echo ""
echo "View logs with: sudo journalctl -u scalper -f"
echo "Dashboards at:  http://$(curl -s ifconfig.me 2>/dev/null || echo YOUR_SERVER_IP):8888"
echo "                http://$(curl -s ifconfig.me 2>/dev/null || echo YOUR_SERVER_IP):8889"
