# Cloud Deployment Guide — Trading System on VPS

Complete step-by-step guide to migrate the trading system from a Windows desktop to a Linux VPS running 24/7.

---

## 1. Provision the VPS

**Recommended: DigitalOcean Droplet**

| Setting   | Value                          |
|-----------|--------------------------------|
| OS        | Ubuntu 24.04 LTS               |
| Plan      | Basic (Shared CPU)             |
| Size      | **$12/mo** — 2 vCPU, 2 GB RAM, 60 GB SSD |
| Region    | **NYC1** or **NYC3** (lowest latency to US markets) |
| Auth      | SSH key (recommended) or password |
| Hostname  | `trading-vps`                  |

After creation, note the server's **IP address** (e.g., `164.90.xxx.xxx`).

### Firewall Rules

In the DigitalOcean dashboard (Networking > Firewalls), create a firewall:

| Type    | Port  | Source       | Purpose              |
|---------|-------|--------------|----------------------|
| SSH     | 22    | Your IP only | SSH access           |
| Custom  | 8888  | Your IP only | Scalper dashboard    |
| Custom  | 8889  | Your IP only | Small cap dashboard  |

Attach the firewall to your droplet.

---

## 2. Initial Server Setup

SSH into your server:

```bash
ssh root@YOUR_SERVER_IP
```

Upload and run the setup script:

```bash
# From your local machine (Git Bash on Windows):
scp -r C:/Users/User/Desktop/trading_system/deploy/setup_server.sh root@YOUR_SERVER_IP:/root/

# On the server:
chmod +x /root/setup_server.sh
sudo /root/setup_server.sh
```

This installs Python 3.12, creates the `trading` user, and sets up the directory structure.

---

## 3. Transfer Code

From your Windows machine (in Git Bash):

```bash
# Copy the entire trading system to the server
scp -r /c/Users/User/Desktop/trading_system/* root@YOUR_SERVER_IP:/home/trading/trading_system/

# Also copy hidden files (.env)
scp /c/Users/User/Desktop/trading_system/.env root@YOUR_SERVER_IP:/home/trading/trading_system/

# Fix ownership on the server
ssh root@YOUR_SERVER_IP "chown -R trading:trading /home/trading/trading_system"
```

**Alternative: Use git**

If you push your repo to GitHub first:

```bash
ssh root@YOUR_SERVER_IP
sudo -u trading bash
cd /home/trading/trading_system
git clone https://github.com/YOUR_USERNAME/trading_system.git .
```

Then copy over `.env` and `config/schwab_token.json` separately (these are gitignored).

---

## 4. Configure .env

SSH into the server and edit the .env:

```bash
ssh root@YOUR_SERVER_IP
sudo -u trading nano /home/trading/trading_system/.env
```

Required variables:

| Variable              | Description                                    |
|-----------------------|------------------------------------------------|
| `DATABASE_URL`        | PostgreSQL connection string                   |
| `SCHWAB_API_KEY`      | Your Schwab API key                            |
| `SCHWAB_APP_SECRET`   | Your Schwab app secret                         |
| `SCHWAB_CALLBACK_URL` | `https://127.0.0.1:8182` (keep as-is)          |
| `SCHWAB_TOKEN_PATH`   | `config/schwab_token.json` (keep as-is)        |
| `SCHWAB_ACCOUNT_HASH` | Your account hash                              |
| `SCHWAB_ACCOUNT_NUMBER` | Your account number                          |
| `ANTHROPIC_API_KEY`   | For LLM catalyst analysis                      |
| `DASHBOARD_PASSWORD`  | Strong password for dashboard HTTP Basic Auth  |
| `GMAIL_APP_PASSWORD`  | For email alerts (optional)                    |
| `ACCOUNT_EQUITY`      | Current account equity value                   |

Make sure permissions are locked down:

```bash
chmod 600 /home/trading/trading_system/.env
```

---

## 5. Install Python Dependencies

```bash
ssh root@YOUR_SERVER_IP
sudo -u trading bash
cd /home/trading/trading_system
python3.12 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

You also need `uvicorn` and `fastapi` for the dashboards (add them if not in requirements.txt):

```bash
./venv/bin/pip install uvicorn fastapi schwab-py
```

---

## 6. Initial Schwab Authentication

This is the critical step. You need to authenticate with Schwab using the manual flow since the server has no browser.

```bash
sudo -u trading bash
cd /home/trading/trading_system
./venv/bin/python scripts/authenticate_manual.py
```

The script will:
1. Print a URL
2. Open that URL **on your phone or laptop browser**
3. Log in to Schwab and authorize
4. You will be redirected to a URL that won't load (that's normal)
5. Copy the **full URL** from the address bar
6. Paste it into the SSH terminal

You should see "Authentication successful!" with token expiry info.

---

## 7. Install systemd Services

```bash
# Copy service files
sudo cp /home/trading/trading_system/deploy/scalper.service /etc/systemd/system/
sudo cp /home/trading/trading_system/deploy/smallcap.service /etc/systemd/system/
sudo cp /home/trading/trading_system/deploy/scalper_dashboard.service /etc/systemd/system/
sudo cp /home/trading/trading_system/deploy/smallcap_dashboard.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload
```

---

## 8. Start Everything

```bash
# Enable services (auto-start on boot)
sudo systemctl enable scalper
sudo systemctl enable smallcap
sudo systemctl enable scalper_dashboard
sudo systemctl enable smallcap_dashboard

# Start services now
sudo systemctl start scalper
sudo systemctl start smallcap
sudo systemctl start scalper_dashboard
sudo systemctl start smallcap_dashboard
```

---

## 9. Verify It's Working

### Check service status

```bash
sudo systemctl status scalper
sudo systemctl status smallcap
sudo systemctl status scalper_dashboard
sudo systemctl status smallcap_dashboard
```

All four should show `active (running)`.

### Check logs

```bash
# Scalper scheduler logs
sudo journalctl -u scalper -f --no-pager

# Small cap scheduler logs
sudo journalctl -u smallcap -f --no-pager

# Dashboard logs
sudo journalctl -u scalper_dashboard -f --no-pager
sudo journalctl -u smallcap_dashboard -f --no-pager

# Application-level logs
sudo -u trading tail -f /home/trading/trading_system/logs/scalper_scheduler.log
sudo -u trading tail -f /home/trading/trading_system/logs/smallcap_scheduler.log
```

### Verify dashboards

From your phone or laptop browser:

- Scalper dashboard: `http://YOUR_SERVER_IP:8888`
- Small cap dashboard: `http://YOUR_SERVER_IP:8889`

You will be prompted for credentials:
- **Username:** `admin`
- **Password:** whatever you set as `DASHBOARD_PASSWORD` in `.env`

---

## 10. Access Dashboards from Phone

Bookmark these URLs on your phone:

```
http://YOUR_SERVER_IP:8888   (scalper)
http://YOUR_SERVER_IP:8889   (small cap)
```

Replace `YOUR_SERVER_IP` with your droplet's actual IP address.

Your phone's browser will remember the credentials after the first login, so you only need to enter admin/password once.

The dashboards auto-update via WebSocket every 2 seconds - just leave the tab open.

---

## 11. Weekly Token Refresh (Schwab Re-Authentication)

Schwab refresh tokens expire every **7 days**. You must re-authenticate before they expire, or trading will stop.

**Recommended: Re-authenticate every Sunday evening.**

### From your phone (via SSH app like Termius):

```bash
ssh trading@YOUR_SERVER_IP
cd /home/trading/trading_system
./venv/bin/python scripts/authenticate_manual.py
```

1. The script prints a URL
2. Open the URL in your phone's browser
3. Log in to Schwab, authorize
4. Copy the redirect URL from the address bar
5. Paste it into the SSH session
6. Done - token refreshed for another 7 days

### Set a weekly phone reminder

Create a recurring reminder for **Sunday 7:00 PM CT**: "Re-authenticate Schwab on VPS"

---

## 12. Useful SSH Commands

```bash
# ── Service management ──
sudo systemctl restart scalper           # Restart scalper
sudo systemctl restart smallcap          # Restart small cap
sudo systemctl stop scalper              # Stop scalper
sudo systemctl start scalper             # Start scalper

# ── View logs (live) ──
sudo journalctl -u scalper -f            # Follow scalper logs
sudo journalctl -u smallcap -f           # Follow small cap logs
sudo journalctl -u scalper -n 100        # Last 100 lines

# ── Application logs ──
ls -la /home/trading/trading_system/logs/
tail -50 /home/trading/trading_system/logs/scalper_scheduler.log
tail -50 /home/trading/trading_system/logs/smallcap_scheduler.log

# ── Check token age ──
python3 -c "
import json
from datetime import datetime, timezone
with open('/home/trading/trading_system/config/schwab_token.json') as f:
    t = json.load(f)
age = (datetime.now(timezone.utc).timestamp() - t['creation_timestamp']) / 86400
print(f'Token age: {age:.1f} days | Expires in: {7-age:.1f} days')
"

# ── System health ──
htop                                     # CPU/memory usage
df -h                                    # Disk space
uptime                                   # Server uptime

# ── Update code from git ──
sudo -u trading bash -c 'cd /home/trading/trading_system && git pull'
sudo systemctl restart scalper smallcap scalper_dashboard smallcap_dashboard
```

---

## 13. Rollback Plan

If something goes wrong on the VPS and you need to fall back to the desktop:

### Step 1: Stop VPS services (prevent duplicate orders)

```bash
sudo systemctl stop scalper smallcap scalper_dashboard smallcap_dashboard
sudo systemctl disable scalper smallcap scalper_dashboard smallcap_dashboard
```

### Step 2: On your Windows desktop

1. Make sure your desktop `.env` and `config/schwab_token.json` are current
2. Re-authenticate locally if needed: `python scripts/authenticate_schwab.py`
3. Start the schedulers via the BAT files or Task Scheduler as before

### Step 3: Verify

- Check that trades are only executing from one location
- Never run both VPS and desktop simultaneously with live trading

### When ready to return to VPS

1. Stop desktop schedulers
2. SSH to VPS
3. Re-authenticate: `./venv/bin/python scripts/authenticate_manual.py`
4. Re-enable services:
   ```bash
   sudo systemctl enable --now scalper smallcap scalper_dashboard smallcap_dashboard
   ```

---

## Troubleshooting

### "Token expired" errors
Run `scripts/authenticate_manual.py` to get a fresh token.

### Dashboard not loading
```bash
sudo systemctl status scalper_dashboard
sudo journalctl -u scalper_dashboard -n 50
```
Check that port 8888/8889 is open in your firewall.

### Service keeps restarting
```bash
sudo journalctl -u scalper -n 200 --no-pager
```
Look for Python import errors or missing dependencies.

### Can't connect via SSH
Use the DigitalOcean web console (Droplets > your droplet > Access > Launch Console).
