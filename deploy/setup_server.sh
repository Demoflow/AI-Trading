#!/usr/bin/env bash
# =============================================================================
# Trading System — VPS Setup Script
# Run this on a fresh Ubuntu 24.04 server as root (or with sudo).
#
# Usage:
#   chmod +x setup_server.sh
#   sudo ./setup_server.sh
# =============================================================================

set -euo pipefail

echo "========================================"
echo "  Trading System — Server Setup"
echo "========================================"
echo

# ── 1. System packages ──────────────────────────────────────────────────────
echo "[1/6] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

echo "[2/6] Installing Python 3.12, pip, git, nginx..."
apt-get install -y -qq \
    python3.12 python3.12-venv python3.12-dev \
    python3-pip \
    git \
    nginx \
    curl \
    htop \
    unzip

# ── 2. Create dedicated user ────────────────────────────────────────────────
echo "[3/6] Creating 'trading' user..."
if id "trading" &>/dev/null; then
    echo "  User 'trading' already exists, skipping."
else
    useradd -m -s /bin/bash trading
    echo "  User 'trading' created."
fi

# ── 3. Create directory structure ────────────────────────────────────────────
echo "[4/6] Creating directory structure..."
TRADING_DIR="/home/trading/trading_system"
mkdir -p "$TRADING_DIR"/{config,logs,data,deploy}
chown -R trading:trading /home/trading

echo "  Directory: $TRADING_DIR"

# ── 4. Create placeholder .env ───────────────────────────────────────────────
echo "[5/6] Creating placeholder .env..."
ENV_FILE="$TRADING_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
# =============================================================================
# Trading System Environment Variables
# Fill in your actual values below.
# =============================================================================

# ── Database ──
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/trading_db

# ── Account ──
ACCOUNT_EQUITY=25000

# ── Schwab API ──
SCHWAB_API_KEY=YOUR_API_KEY_HERE
SCHWAB_APP_SECRET=YOUR_APP_SECRET_HERE
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
SCHWAB_TOKEN_PATH=config/schwab_token.json
SCHWAB_ACCOUNT_HASH=YOUR_ACCOUNT_HASH_HERE
SCHWAB_ACCOUNT_NUMBER=YOUR_ACCOUNT_NUMBER_HERE

# ── Anthropic (for LLM catalyst analysis) ──
ANTHROPIC_API_KEY=YOUR_ANTHROPIC_KEY_HERE

# ── Dashboard password (HTTP Basic Auth, username: admin) ──
# Set this to protect your dashboards when exposed to the internet.
DASHBOARD_PASSWORD=CHANGE_ME_TO_A_STRONG_PASSWORD

# ── Email alerts (optional) ──
GMAIL_APP_PASSWORD=YOUR_GMAIL_APP_PASSWORD_HERE
ENVEOF
    chown trading:trading "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "  Created $ENV_FILE (fill in your credentials)"
else
    echo "  .env already exists, skipping."
fi

# ── 5. Python venv + dependencies ────────────────────────────────────────────
echo "[6/6] Setting up Python venv..."
if [ -f "$TRADING_DIR/requirements.txt" ]; then
    sudo -u trading python3.12 -m venv "$TRADING_DIR/venv"
    sudo -u trading "$TRADING_DIR/venv/bin/pip" install --upgrade pip -q
    sudo -u trading "$TRADING_DIR/venv/bin/pip" install -r "$TRADING_DIR/requirements.txt" -q
    echo "  Python dependencies installed."
else
    echo "  WARNING: requirements.txt not found. Copy your code first, then run:"
    echo "    sudo -u trading python3.12 -m venv $TRADING_DIR/venv"
    echo "    sudo -u trading $TRADING_DIR/venv/bin/pip install -r $TRADING_DIR/requirements.txt"
fi

echo
echo "========================================"
echo "  Setup complete!"
echo "========================================"
echo
echo "Next steps:"
echo "  1. Copy your trading_system code to $TRADING_DIR"
echo "  2. Edit $ENV_FILE with your real credentials"
echo "  3. Run: sudo -u trading $TRADING_DIR/venv/bin/pip install -r $TRADING_DIR/requirements.txt"
echo "  4. Run: sudo -u trading $TRADING_DIR/venv/bin/python $TRADING_DIR/scripts/authenticate_manual.py"
echo "  5. Install systemd services (see deploy/README.md)"
echo
