#!/usr/bin/env python3
"""
Monitor Agent — 30-second heartbeat for scalper + small cap bot.

Alerts sent to austinbult@gmail.com via Gmail SMTP.
Requires GMAIL_APP_PASSWORD in .env (generate at myaccount.google.com/apppasswords).

Run:  python scripts/monitor_agent.py
"""

import json
import os
import re
import smtplib
import sys
import time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# ── Bootstrap ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")  # Explicit path so Task Scheduler (no cwd) still finds it

# ── Paths ──────────────────────────────────────────────────────────────────────
PORTFOLIO_ROSS   = BASE_DIR / "config" / "smallcap_portfolio.json"
PORTFOLIO_DUX    = BASE_DIR / "config" / "dux_portfolio.json"
PORTFOLIO_SCALP  = BASE_DIR / "config" / "paper_scalp.json"
OVERRIDES_FILE   = BASE_DIR / "config" / "agent_overrides.json"
COMMANDS_FILE    = BASE_DIR / "config" / "agent_commands.txt"
TOKEN_FILE       = BASE_DIR / "config" / "schwab_token.json"
LOGS_DIR         = BASE_DIR / "logs"

# ── Config ─────────────────────────────────────────────────────────────────────
ALERT_EMAIL       = "austinbult@gmail.com"
GMAIL_USER        = os.getenv("GMAIL_USER", ALERT_EMAIL)
GMAIL_APP_PASS    = os.getenv("GMAIL_APP_PASSWORD", "")

HEARTBEAT_SEC     = 30

# Market hours in CT
MARKET_OPEN_CT    = 8.50
MARKET_CLOSE_CT   = 15.0
MORNING_BRIEF_CT  = 6.50   # 6:30 AM CT
EOD_SUMMARY_CT    = 14.92  # 2:55 PM CT
EOD_REVIEW_CT     = 15.08  # 3:05 PM CT — after market close

# Alert thresholds
LOSS_WARN_STREAK  = 2
LOSS_HALT_STREAK  = 3
LIMIT_WARN_PCT    = 75
LIMIT_CRIT_PCT    = 90
LOG_STALE_MIN     = 5      # minutes with no log activity before crash alert
TOKEN_WARN_DAYS   = 3      # days before expiry to warn

# ── Alert cooldowns (seconds) — prevent repeated spam ─────────────────────────
COOLDOWN = {
    "trade_open":   120,
    "trade_close":  120,
    "loss_streak":  600,
    "daily_halt":   1800,
    "crash":        600,
    "token":        7200,
    "morning":      86400,
    "eod":          86400,
}

# ── Log patterns ───────────────────────────────────────────────────────────────
_RE_SCALP_OPEN  = re.compile(r"SCALP OPEN: (\w+) (\w+) \$")
_RE_SCALP_WIN   = re.compile(r"SCALP WIN: (\w+) (\w+) \$([+\-\d,.]+)")
_RE_SCALP_LOSS  = re.compile(r"SCALP LOSS: (\w+) (\w+) \$([+\-\d,.]+)")
_RE_SC_ENTRY    = re.compile(r"ENTRY: (\w+) \| (\d+) shares @ \$([0-9.]+)")
_RE_DUX_ENTRY   = re.compile(r"\[DuxExec\] ENTRY: (\w+) (\w+) (\d+) shares")
_RE_SELL        = re.compile(r"SELL \(([^)]+)\): (\w+) (\d+) shares @ \$([0-9.]+)")
_RE_DAILY_HALT  = re.compile(r"DAILY LOSS LIMIT(?:\s+HIT|\s+REACHED)", re.IGNORECASE)
_RE_3STRIKE     = re.compile(r"3-strike circuit breaker \((\d+) consecutive losses\)")
_RE_CONSEC_LOSS = re.compile(r"CONSECUTIVE LOSS LIMIT: (\d+) straight")
_RE_STREAK_WARN = re.compile(r"consecutive losses=(\d+)")
_RE_MARKET_CHAR = re.compile(r"Market character: \[(\w+)\]")


# ──────────────────────────────────────────────────────────────────────────────
# State
# ──────────────────────────────────────────────────────────────────────────────
class MonitorState:
    def __init__(self):
        self.log_offset: int = 0
        self.log_date: str = ""
        self.last_log_mtime: float = 0.0
        self.alerted: dict[str, float] = {}   # alert_key → epoch sent
        self.morning_sent: str = ""            # date string
        self.eod_sent: str = ""
        self.eod_review_sent: str = ""         # date of last EOD review
        self.last_streak: int = 0


_state = MonitorState()


# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────
def _hour_ct() -> float:
    now = datetime.now()
    return now.hour + now.minute / 60.0


def _today() -> str:
    return date.today().isoformat()


def _can_alert(key: str) -> bool:
    cooldown = COOLDOWN.get(key.split(":")[0], 300)
    last = _state.alerted.get(key, 0)
    return (time.time() - last) > cooldown


def _mark_alerted(key: str):
    _state.alerted[key] = time.time()


def _load_json(path: Path) -> dict:
    try:
        if path.exists():
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_overrides(overrides: dict):
    overrides["last_updated"] = datetime.now().isoformat()
    try:
        with open(OVERRIDES_FILE, "w") as f:
            json.dump(overrides, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save overrides: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Email
# ──────────────────────────────────────────────────────────────────────────────
def _send_email(subject: str, body: str, html_body: str | None = None) -> bool:
    """Send email via Gmail SMTP. Returns True on success."""
    if not GMAIL_APP_PASS:
        logger.warning(f"[Monitor] EMAIL SKIPPED (no GMAIL_APP_PASSWORD): {subject}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Trading Bot] {subject}"
        msg["From"]    = GMAIL_USER
        msg["To"]      = ALERT_EMAIL

        msg.attach(MIMEText(body, "plain"))
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())

        logger.info(f"[Monitor] Email sent: {subject}")
        return True

    except Exception as e:
        logger.error(f"[Monitor] Email failed: {e}")
        return False


def _alert(key: str, subject: str, body: str, html: str | None = None):
    """Send alert only if not in cooldown."""
    if not _can_alert(key):
        return
    if _send_email(subject, body, html):
        _mark_alerted(key)


# ──────────────────────────────────────────────────────────────────────────────
# Portfolio helpers
# ──────────────────────────────────────────────────────────────────────────────
def _get_scalper_stats() -> dict:
    p = _load_json(PORTFOLIO_SCALP)
    today = _today()
    ds = p.get("daily_stats", {}).get(today, {})
    return {
        "trades": ds.get("trades", 0),
        "wins":   ds.get("wins", 0),
        "losses": ds.get("losses", 0),
        "pnl":    ds.get("pnl", 0.0),
        "equity": p.get("equity", 0.0),
        "open":   len([x for x in p.get("positions", []) if x.get("status") == "OPEN"]),
    }


def _get_smallcap_stats() -> dict:
    """Returns merged Ross + Dux stats for today."""
    today = _today()
    result = {"ross": {}, "dux": {}, "combined_pnl": 0.0}

    for key, path in [("ross", PORTFOLIO_ROSS), ("dux", PORTFOLIO_DUX)]:
        p = _load_json(path)
        ds = p.get("daily_stats", {}).get(today, {})
        result[key] = {
            "trades": ds.get("trades", 0),
            "wins":   ds.get("wins", 0),
            "losses": ds.get("losses", 0),
            "pnl":    ds.get("pnl", 0.0),
        }

    result["combined_pnl"] = (
        result["ross"].get("pnl", 0.0) + result["dux"].get("pnl", 0.0)
    )
    return result


def _get_daily_limit_pct() -> float:
    """Returns 0–100 usage of smallcap daily loss limit ($500)."""
    sc = _get_smallcap_stats()
    worst_pnl = min(sc["combined_pnl"], 0.0)
    return min(abs(worst_pnl) / 500.0 * 100, 100)


def _get_scalper_limit_pct() -> float:
    """Returns scalper daily loss limit usage (against 3×$250 = $750)."""
    s = _get_scalper_stats()
    worst = min(s["pnl"], 0.0)
    return min(abs(worst) / 750.0 * 100, 100)


# ──────────────────────────────────────────────────────────────────────────────
# Token check
# ──────────────────────────────────────────────────────────────────────────────
def _check_token_expiry():
    token = _load_json(TOKEN_FILE)
    exp_str = token.get("access_token_issued_at") or token.get("expires_at", "")
    if not exp_str:
        return

    try:
        # Schwab tokens expire in 30 minutes; refresh tokens expire in 7 days
        # Check refresh_token expiry if present
        rt_exp = token.get("refresh_token_expires_in")  # seconds
        issued = token.get("access_token_issued_at")
        if rt_exp and issued:
            issued_dt = datetime.fromisoformat(issued.replace("Z", "+00:00"))
            expires_dt = issued_dt + timedelta(seconds=int(rt_exp))
            days_left = (expires_dt - datetime.now(expires_dt.tzinfo)).days
            if days_left <= TOKEN_WARN_DAYS:
                _alert(
                    "token",
                    f"Token Expiry Warning — {days_left}d left",
                    f"Schwab refresh token expires in {days_left} day(s).\n"
                    f"Run: python scripts/refresh_token.py\n"
                    f"Or visit your Schwab API dashboard to re-authenticate.",
                )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Log parsing
# ──────────────────────────────────────────────────────────────────────────────
def _get_log_path() -> Path | None:
    today = _today()
    path = LOGS_DIR / f"trading_{today}.log"
    return path if path.exists() else None


def _read_new_log_lines() -> list[str]:
    """Read only lines added since last check. Resets offset on date rollover."""
    today = _today()
    if today != _state.log_date:
        _state.log_date = today
        _state.log_offset = 0

    path = _get_log_path()
    if not path:
        return []

    try:
        mtime = path.stat().st_mtime
        _state.last_log_mtime = mtime

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(_state.log_offset)
            new_text = f.read()
            _state.log_offset = f.tell()

        return new_text.splitlines()
    except Exception as e:
        logger.debug(f"Log read error: {e}")
        return []


def _process_log_lines(lines: list[str]):
    """Parse new log lines and fire appropriate alerts."""
    for line in lines:
        # ── Scalper trade opened ──
        m = _RE_SCALP_OPEN.search(line)
        if m:
            direction, sym = m.group(1), m.group(2)
            _alert(
                f"trade_open:scalp:{sym}",
                f"Scalper Entry — {direction} {sym}",
                f"Scalper opened a {direction} position on {sym}\n\n{line}",
            )
            continue

        # ── Scalper trade closed ──
        m = _RE_SCALP_WIN.search(line) or _RE_SCALP_LOSS.search(line)
        if m:
            outcome = "WIN" if "WIN" in line else "LOSS"
            direction, sym, pnl = m.group(1), m.group(2), m.group(3)
            emoji = "+" if outcome == "WIN" else "-"
            _alert(
                f"trade_close:scalp:{sym}",
                f"Scalper {outcome} — {sym} {emoji}${pnl.strip('+-,')}",
                f"Scalper closed {direction} {sym}: {outcome} ${pnl}\n\n{line}",
            )
            continue

        # ── Small cap (Ross) entry ──
        m = _RE_SC_ENTRY.search(line)
        if m:
            sym, shares, price = m.group(1), m.group(2), m.group(3)
            _alert(
                f"trade_open:ross:{sym}",
                f"Ross Entry — {sym} {shares} shares @ ${price}",
                f"Ross Cameron strategy entered {sym}\n{shares} shares @ ${price}\n\n{line}",
            )
            continue

        # ── Dux entry ──
        m = _RE_DUX_ENTRY.search(line)
        if m:
            sym, direction, shares = m.group(1), m.group(2), m.group(3)
            _alert(
                f"trade_open:dux:{sym}",
                f"Dux Entry — {direction} {sym} ({shares} shares)",
                f"Steven Dux strategy entered {sym} {direction}\n{shares} shares\n\n{line}",
            )
            continue

        # ── Small cap close ──
        m = _RE_SELL.search(line)
        if m:
            reason, sym, shares, price = m.group(1), m.group(2), m.group(3), m.group(4)
            sc = _get_smallcap_stats()
            pnl_str = f"  Combined P&L today: ${sc['combined_pnl']:+,.2f}"
            _alert(
                f"trade_close:sc:{sym}",
                f"SmallCap Closed — {sym} ({reason})",
                f"Position closed: {sym} {shares} shares @ ${price}\nReason: {reason}\n{pnl_str}\n\n{line}",
            )
            continue

        # ── Daily halt — small cap ──
        if _RE_DAILY_HALT.search(line):
            sc = _get_smallcap_stats()
            _alert(
                "daily_halt:smallcap",
                "HALT — SmallCap Daily Loss Limit Hit",
                f"Small cap system has hit its daily loss limit and is HALTED for today.\n"
                f"P&L: ${sc['combined_pnl']:+,.2f}\n"
                f"Ross: ${sc['ross'].get('pnl', 0):+,.2f} | "
                f"Dux: ${sc['dux'].get('pnl', 0):+,.2f}\n\n{line}",
            )
            continue

        # ── 3-strike circuit breaker ──
        m = _RE_3STRIKE.search(line)
        if m:
            n = m.group(1)
            _alert(
                "daily_halt:3strike",
                f"HALT — 3-Strike Circuit Breaker ({n} consecutive losses)",
                f"Small cap trading halted: {n} consecutive losses triggered 3-strike rule.\n\n{line}",
            )
            continue

        # ── Scalper consecutive loss limit ──
        m = _RE_CONSEC_LOSS.search(line)
        if m:
            n = m.group(1)
            _alert(
                "daily_halt:scalper_consec",
                f"HALT — Scalper {n} Consecutive Losses",
                f"Scalper session shutdown: {n} straight losses hit the circuit breaker.\n\n{line}",
            )
            continue

        # ── Loss streak warning ──
        m = _RE_STREAK_WARN.search(line)
        if m:
            n = int(m.group(1))
            if n >= LOSS_WARN_STREAK and n != _state.last_streak:
                _state.last_streak = n
                _alert(
                    f"loss_streak:{n}",
                    f"Loss Streak Warning — {n} Consecutive Losses",
                    f"Warning: {n} consecutive losses detected.\n"
                    f"At {LOSS_HALT_STREAK} losses, trading halts automatically.\n\n{line}",
                )
            continue


# ──────────────────────────────────────────────────────────────────────────────
# Crash detection
# ──────────────────────────────────────────────────────────────────────────────
def _check_system_crash():
    """Alert if no log activity for >5 min during market hours."""
    h = _hour_ct()
    if not (MARKET_OPEN_CT <= h <= MARKET_CLOSE_CT):
        return

    path = _get_log_path()
    if not path:
        stale_min = LOG_STALE_MIN + 1  # no log file at all
    else:
        try:
            mtime = path.stat().st_mtime
            stale_min = (time.time() - mtime) / 60
        except Exception:
            return

    if stale_min >= LOG_STALE_MIN:
        _alert(
            "crash:no_log",
            f"ALERT — No Log Activity ({stale_min:.0f} min)",
            f"No new log entries for {stale_min:.0f} minutes during market hours "
            f"({datetime.now().strftime('%H:%M CT')})\n\n"
            f"One or both bots may have crashed. Check immediately.",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Daily limit checks (polling portfolio)
# ──────────────────────────────────────────────────────────────────────────────
def _check_daily_limits():
    # Small cap
    sc_pct = _get_daily_limit_pct()
    if sc_pct >= LIMIT_CRIT_PCT:
        sc = _get_smallcap_stats()
        _alert(
            "daily_limit:sc_critical",
            f"SmallCap Daily Limit {sc_pct:.0f}% Used",
            f"Small cap system has used {sc_pct:.0f}% of daily loss limit.\n"
            f"Combined P&L: ${sc['combined_pnl']:+,.2f}\n"
            f"Ross: ${sc['ross'].get('pnl', 0):+,.2f} | "
            f"Dux: ${sc['dux'].get('pnl', 0):+,.2f}",
        )
    elif sc_pct >= LIMIT_WARN_PCT:
        sc = _get_smallcap_stats()
        _alert(
            "daily_limit:sc_warn",
            f"SmallCap Daily Limit {sc_pct:.0f}% Used",
            f"Small cap system has used {sc_pct:.0f}% of daily loss limit.\n"
            f"Combined P&L: ${sc['combined_pnl']:+,.2f}",
        )

    # Scalper
    scalp_pct = _get_scalper_limit_pct()
    if scalp_pct >= LIMIT_CRIT_PCT:
        s = _get_scalper_stats()
        _alert(
            "daily_limit:scalp_critical",
            f"Scalper Daily Limit {scalp_pct:.0f}% Used",
            f"Scalper has used {scalp_pct:.0f}% of daily loss budget.\n"
            f"P&L: ${s['pnl']:+,.2f} | Trades: {s['trades']} ({s['wins']}W/{s['losses']}L)",
        )
    elif scalp_pct >= LIMIT_WARN_PCT:
        s = _get_scalper_stats()
        _alert(
            "daily_limit:scalp_warn",
            f"Scalper Daily Limit {scalp_pct:.0f}% Used",
            f"Scalper has used {scalp_pct:.0f}% of daily loss budget.\n"
            f"P&L: ${s['pnl']:+,.2f}",
        )


# ──────────────────────────────────────────────────────────────────────────────
# LLM-generated briefings
# ──────────────────────────────────────────────────────────────────────────────
def _generate_briefing(prompt: str) -> str:
    """Call Claude Haiku to generate a briefing narrative."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as e:
        logger.warning(f"LLM briefing failed: {e}")
        return "(LLM unavailable)"


def _morning_briefing():
    today = _today()
    if _state.morning_sent == today:
        return

    h = _hour_ct()
    if h < MORNING_BRIEF_CT:
        return

    sc = _get_smallcap_stats()
    s  = _get_scalper_stats()

    # Peek at recent log for market character
    regime = "UNKNOWN"
    path = _get_log_path()
    if path and path.exists():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            m = _RE_MARKET_CHAR.search(text)
            if m:
                regime = m.group(1)
        except Exception:
            pass

    # Read candidates for context
    cands = _load_json(BASE_DIR / "config" / "smallcap_candidates.json")
    cand_names = [c.get("symbol", "") for c in (cands if isinstance(cands, list) else [])][:5]

    prompt = (
        f"You are the AI trading monitor for an automated day-trading system.\n"
        f"Today is {today}. Market opens in ~2 hours (9:30 ET / 8:30 CT).\n\n"
        f"Pre-market regime: {regime}\n"
        f"Gap candidates: {', '.join(cand_names) if cand_names else 'none yet'}\n\n"
        f"Yesterday's performance:\n"
        f"  Small cap (Ross+Dux): ${sc['combined_pnl']:+,.2f} | "
        f"  Ross: {sc['ross'].get('trades',0)} trades | "
        f"  Dux: {sc['dux'].get('trades',0)} trades\n"
        f"  Scalper: ${s['pnl']:+,.2f} | {s['trades']} trades ({s['wins']}W/{s['losses']}L)\n\n"
        f"Write a concise 3-paragraph morning briefing for the trader:\n"
        f"1. Today's market setup and what to watch for\n"
        f"2. Key risk reminders based on yesterday's performance\n"
        f"3. One sentence focus for today\n"
        f"Keep it punchy and actionable. No emojis."
    )

    narrative = _generate_briefing(prompt)

    body = (
        f"MORNING BRIEFING — {today}\n"
        f"{'='*50}\n\n"
        f"MARKET REGIME: {regime}\n"
        f"GAP CANDIDATES: {', '.join(cand_names) if cand_names else 'None yet'}\n\n"
        f"SCALPER YESTERDAY: ${s['pnl']:+,.2f} | "
        f"{s['trades']} trades ({s['wins']}W/{s['losses']}L)\n"
        f"SMALL CAP YESTERDAY: ${sc['combined_pnl']:+,.2f} | "
        f"Ross {sc['ross'].get('trades',0)} trades | "
        f"Dux {sc['dux'].get('trades',0)} trades\n\n"
        f"{'─'*50}\n\n"
        f"{narrative}\n\n"
        f"{'─'*50}\n"
        f"Both systems will auto-launch. Monitor at http://localhost:8889\n"
    )

    _state.morning_sent = today  # Always mark sent to prevent retry spam
    if _send_email(f"Morning Briefing — {today}", body):
        logger.info("[Monitor] Morning briefing sent")
    else:
        logger.warning("[Monitor] Morning briefing email failed (Gmail config?)")


def _eod_summary():
    today = _today()
    if _state.eod_sent == today:
        return

    h = _hour_ct()
    if h < EOD_SUMMARY_CT:
        return

    sc = _get_smallcap_stats()
    s  = _get_scalper_stats()

    combined_total = sc["combined_pnl"] + s["pnl"]
    sc_trades  = sc["ross"].get("trades", 0) + sc["dux"].get("trades", 0)
    sc_wins    = sc["ross"].get("wins",   0) + sc["dux"].get("wins",   0)
    sc_losses  = sc["ross"].get("losses", 0) + sc["dux"].get("losses", 0)
    sc_wr      = (sc_wins / sc_trades * 100) if sc_trades > 0 else 0

    prompt = (
        f"You are the AI trading monitor for an automated day-trading system.\n"
        f"Today is {today} — market is closing.\n\n"
        f"Today's final results:\n"
        f"  Small cap system:\n"
        f"    Ross: ${sc['ross'].get('pnl',0):+,.2f} | "
        f"{sc['ross'].get('trades',0)} trades "
        f"({sc['ross'].get('wins',0)}W/{sc['ross'].get('losses',0)}L)\n"
        f"    Dux:  ${sc['dux'].get('pnl',0):+,.2f} | "
        f"{sc['dux'].get('trades',0)} trades "
        f"({sc['dux'].get('wins',0)}W/{sc['dux'].get('losses',0)}L)\n"
        f"    Combined: ${sc['combined_pnl']:+,.2f} | WR: {sc_wr:.0f}%\n"
        f"  Scalper: ${s['pnl']:+,.2f} | "
        f"{s['trades']} trades ({s['wins']}W/{s['losses']}L)\n"
        f"  Total P&L: ${combined_total:+,.2f}\n\n"
        f"Write a concise EOD summary (3-4 paragraphs):\n"
        f"1. Overall performance assessment\n"
        f"2. What worked and what didn't\n"
        f"3. Key takeaways for tomorrow\n"
        f"Be direct and analytical. No fluff. No emojis."
    )

    narrative = _generate_briefing(prompt)

    result_icon = "PROFITABLE" if combined_total >= 0 else "LOSS DAY"
    body = (
        f"EOD SUMMARY — {today} — {result_icon}\n"
        f"{'='*50}\n\n"
        f"ROSS:    ${sc['ross'].get('pnl',0):+,.2f} | "
        f"{sc['ross'].get('trades',0)} trades "
        f"({sc['ross'].get('wins',0)}W/{sc['ross'].get('losses',0)}L)\n"
        f"DUX:     ${sc['dux'].get('pnl',0):+,.2f} | "
        f"{sc['dux'].get('trades',0)} trades "
        f"({sc['dux'].get('wins',0)}W/{sc['dux'].get('losses',0)}L)\n"
        f"SCALPER: ${s['pnl']:+,.2f} | "
        f"{s['trades']} trades ({s['wins']}W/{s['losses']}L)\n"
        f"{'─'*30}\n"
        f"TOTAL:   ${combined_total:+,.2f}\n\n"
        f"{'─'*50}\n\n"
        f"{narrative}\n"
    )

    _state.eod_sent = today  # Always mark sent to prevent retry spam
    if _send_email(f"EOD Summary — {today} — ${combined_total:+,.2f}", body):
        logger.info("[Monitor] EOD summary sent")
    else:
        logger.warning("[Monitor] EOD summary email failed (Gmail config?)")


# ──────────────────────────────────────────────────────────────────────────────
# EOD Review (runs at 3:05 PM CT)
# ──────────────────────────────────────────────────────────────────────────────
def _eod_review():
    """Trigger the deep EOD review via eod_review.py."""
    today = _today()
    if _state.eod_review_sent == today:
        return

    h = _hour_ct()
    if h < EOD_REVIEW_CT:
        return

    logger.info("[Monitor] Starting EOD review...")
    try:
        # Import and run directly (same process — avoids subprocess PATH issues)
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        import eod_review
        eod_review.run(today)
        _state.eod_review_sent = today
        logger.info("[Monitor] EOD review complete")
    except Exception as e:
        logger.error(f"[Monitor] EOD review failed: {e}")
        _send_email(
            f"EOD Review Failed — {today}",
            f"The automated EOD review encountered an error:\n\n{e}\n\n"
            f"Run manually: python scripts/eod_review.py",
        )


def _apply_approved_changes(indices: list[int] | None = None):
    """Apply approved pending changes via apply_changes.py."""
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        import apply_changes
        applied, failed = apply_changes.run(dry_run=False, indices=indices)

        pending_file = BASE_DIR / "config" / "pending_changes.json"
        pending = {}
        try:
            with open(pending_file) as f:
                pending = json.load(f)
        except Exception:
            pass

        today = _today()
        result_body = (
            f"CODE CHANGES APPLIED — {today}\n"
            f"{'='*50}\n\n"
            f"Applied: {applied}\n"
            f"Failed:  {failed}\n\n"
        )

        if failed > 0:
            result_body += (
                "Some changes could not be applied automatically.\n"
                "Check logs/monitor_*.log for details, then apply manually.\n\n"
            )

        changes = pending.get("changes", [])
        for i, c in enumerate(changes, 1):
            if indices and i not in indices:
                continue
            result_body += (
                f"Change {i}: {c.get('what','?')}\n"
                f"  File: {c.get('file','')}\n"
            )

        result_body += (
            f"\nAll applied changes are backed up in config/code_backups/\n"
            f"Full audit log: config/applied_changes_log.json\n"
        )

        _send_email(
            f"Changes Applied — {applied} OK, {failed} Failed",
            result_body,
        )

    except Exception as e:
        logger.error(f"[Monitor] apply_changes failed: {e}")
        _send_email(
            "Change Application Error",
            f"Error while applying approved changes:\n\n{e}\n\n"
            f"Run manually: python scripts/apply_changes.py",
        )


# ──────────────────────────────────────────────────────────────────────────────
# Command interface
# ──────────────────────────────────────────────────────────────────────────────
def _process_commands():
    """Read agent_commands.txt, execute, clear file."""
    if not COMMANDS_FILE.exists():
        return

    try:
        text = COMMANDS_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return

    if not text:
        return

    overrides = _load_json(OVERRIDES_FILE)
    responses = []

    for raw_line in text.splitlines():
        line = raw_line.strip().lower()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        cmd = parts[0] if parts else ""

        if cmd == "pause_smallcap":
            overrides["smallcap_paused"] = True
            responses.append("OK: small cap trading paused")
            logger.info("[Monitor] CMD: small cap paused")

        elif cmd == "resume_smallcap":
            overrides["smallcap_paused"] = False
            responses.append("OK: small cap trading resumed")
            logger.info("[Monitor] CMD: small cap resumed")

        elif cmd == "pause_scalper":
            overrides["scalper_paused"] = True
            responses.append("OK: scalper trading paused")
            logger.info("[Monitor] CMD: scalper paused")

        elif cmd == "resume_scalper":
            overrides["scalper_paused"] = False
            responses.append("OK: scalper trading resumed")
            logger.info("[Monitor] CMD: scalper resumed")

        elif cmd == "set_ofe" and len(parts) >= 2:
            try:
                val = int(parts[1])
                overrides["ofe_override"] = val
                responses.append(f"OK: OFE threshold set to {val}")
                logger.info(f"[Monitor] CMD: OFE override → {val}")
            except ValueError:
                responses.append(f"ERR: invalid OFE value '{parts[1]}'")

        elif cmd == "clear_ofe":
            overrides["ofe_override"] = None
            responses.append("OK: OFE override cleared (using auto)")
            logger.info("[Monitor] CMD: OFE override cleared")

        elif cmd == "block" and len(parts) >= 2:
            sym = parts[1].upper()
            blocked = overrides.get("blocked_symbols", [])
            if sym not in blocked:
                blocked.append(sym)
                overrides["blocked_symbols"] = blocked
            responses.append(f"OK: {sym} blocked from trading")
            logger.info(f"[Monitor] CMD: blocked {sym}")

        elif cmd == "unblock" and len(parts) >= 2:
            sym = parts[1].upper()
            blocked = overrides.get("blocked_symbols", [])
            overrides["blocked_symbols"] = [s for s in blocked if s != sym]
            responses.append(f"OK: {sym} unblocked")
            logger.info(f"[Monitor] CMD: unblocked {sym}")

        elif cmd == "flatten":
            overrides["flatten_all"] = True
            responses.append("OK: FLATTEN ALL triggered — positions will close at next tick")
            logger.warning("[Monitor] CMD: FLATTEN ALL triggered")

        elif cmd == "status":
            sc = _get_smallcap_stats()
            s  = _get_scalper_stats()
            pending_file = BASE_DIR / "config" / "pending_changes.json"
            pending_status = "none"
            try:
                with open(pending_file) as pf:
                    pd = json.load(pf)
                    n = len(pd.get("changes", []))
                    if pd.get("approved"):
                        pending_status = f"approved ({n} changes)"
                    elif pd.get("rejected"):
                        pending_status = "rejected"
                    elif n > 0:
                        pending_status = f"awaiting approval ({n} changes from {pd.get('date','?')})"
            except Exception:
                pass

            status_text = (
                f"STATUS REPORT — {datetime.now().strftime('%H:%M CT')}\n"
                f"Small cap paused: {overrides.get('smallcap_paused', False)}\n"
                f"Scalper paused:   {overrides.get('scalper_paused', False)}\n"
                f"OFE override:     {overrides.get('ofe_override', 'auto')}\n"
                f"Blocked symbols:  {overrides.get('blocked_symbols', [])}\n"
                f"Pending changes:  {pending_status}\n\n"
                f"Ross P&L:  ${sc['ross'].get('pnl',0):+,.2f} | {sc['ross'].get('trades',0)} trades\n"
                f"Dux P&L:   ${sc['dux'].get('pnl',0):+,.2f} | {sc['dux'].get('trades',0)} trades\n"
                f"Scalper:   ${s['pnl']:+,.2f} | {s['trades']} trades ({s['wins']}W/{s['losses']}L)\n"
            )
            _send_email("Status Report", status_text)
            responses.append("OK: status email sent")

        elif cmd == "approve_changes":
            # Optional: approve_changes 1,3  → apply only changes 1 and 3
            indices = None
            if len(parts) >= 2:
                try:
                    indices = [int(x) for x in parts[1].split(",")]
                except ValueError:
                    responses.append(f"ERR: invalid indices '{parts[1]}' — use approve_changes 1,2,3")
                    continue

            pending_file = BASE_DIR / "config" / "pending_changes.json"
            try:
                with open(pending_file) as pf:
                    pd = json.load(pf)
                pd["approved"] = True
                pd["rejected"] = False
                pd["approved_indices"] = indices or []
                pd["approved_at"] = datetime.now().isoformat()
                with open(pending_file, "w") as pf:
                    json.dump(pd, pf, indent=2)

                n = len(pd.get("changes", []))
                idx_str = f" (changes {indices})" if indices else f" (all {n} changes)"
                responses.append(f"OK: changes approved{idx_str} — applying now")
                logger.info(f"[Monitor] CMD: approve_changes{idx_str}")
                _apply_approved_changes(indices)
            except Exception as e:
                responses.append(f"ERR: approve_changes failed: {e}")
                logger.error(f"[Monitor] approve_changes error: {e}")

        elif cmd == "reject_changes":
            pending_file = BASE_DIR / "config" / "pending_changes.json"
            try:
                with open(pending_file) as pf:
                    pd = json.load(pf)
                pd["approved"] = False
                pd["rejected"] = True
                pd["rejected_at"] = datetime.now().isoformat()
                with open(pending_file, "w") as pf:
                    json.dump(pd, pf, indent=2)
                responses.append("OK: changes rejected — no code modifications made")
                logger.info("[Monitor] CMD: reject_changes")
                _send_email(
                    "Changes Rejected",
                    f"EOD review changes for {pd.get('date','?')} were rejected.\n"
                    f"No code modifications were made.\n"
                    f"The system will continue running with current parameters.",
                )
            except Exception as e:
                responses.append(f"ERR: reject_changes failed: {e}")

        elif cmd == "run_review":
            # Manually trigger an EOD review for today (or specific date)
            review_date = parts[1] if len(parts) >= 2 else _today()
            responses.append(f"OK: running EOD review for {review_date}...")
            logger.info(f"[Monitor] CMD: run_review {review_date}")
            _state.eod_review_sent = ""  # reset so it can run again
            try:
                sys.path.insert(0, str(BASE_DIR / "scripts"))
                import eod_review
                import importlib
                importlib.reload(eod_review)
                eod_review.run(review_date)
                responses[-1] = f"OK: review complete for {review_date} — check email"
            except Exception as e:
                responses.append(f"ERR: review failed: {e}")
                logger.error(f"[Monitor] run_review error: {e}")

        else:
            responses.append(f"ERR: unknown command '{raw_line.strip()}'")
            logger.warning(f"[Monitor] Unknown command: {raw_line.strip()}")

    # Save overrides and write response back to file
    _save_overrides(overrides)

    try:
        result_text = "\n".join(f"# {r}" for r in responses)
        COMMANDS_FILE.write_text(result_text, encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to write command responses: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────────
def main():
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format="{time:HH:mm:ss} | {level:7s} | {message}",
    )

    LOGS_DIR.mkdir(exist_ok=True)
    logger.add(
        str(LOGS_DIR / "monitor_{time:YYYY-MM-DD}.log"),
        rotation="00:00",
        retention="30 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:7s} | {message}",
    )

    logger.info("=" * 60)
    logger.info("MONITOR AGENT started")
    logger.info(f"  Heartbeat: {HEARTBEAT_SEC}s")
    logger.info(f"  Alert email: {ALERT_EMAIL}")
    logger.info(f"  Gmail configured: {'YES' if GMAIL_APP_PASS else 'NO — set GMAIL_APP_PASSWORD in .env'}")
    logger.info("=" * 60)

    if not GMAIL_APP_PASS:
        logger.warning(
            "GMAIL_APP_PASSWORD not set. Alerts will be logged but NOT emailed.\n"
            "  1. Go to myaccount.google.com/apppasswords\n"
            "  2. Create app password for 'Mail'\n"
            "  3. Add to .env:  GMAIL_APP_PASSWORD=your_16_char_password"
        )

    # Ensure commands file exists
    if not COMMANDS_FILE.exists():
        COMMANDS_FILE.write_text(
            "# Monitor Agent Command Interface\n"
            "# Write one command per line, save the file.\n"
            "# Commands: pause_smallcap, resume_smallcap, pause_scalper, resume_scalper,\n"
            "#           set_ofe <value>, clear_ofe, block <SYM>, unblock <SYM>,\n"
            "#           flatten, status\n",
            encoding="utf-8",
        )

    tick = 0
    while True:
        try:
            tick += 1

            # ── Process new log lines ──
            new_lines = _read_new_log_lines()
            if new_lines:
                _process_log_lines(new_lines)

            # ── Every tick: crash check + limit check ──
            _check_system_crash()
            if tick % 2 == 0:  # every ~60s
                _check_daily_limits()

            # ── Every 10 ticks (~5 min): token check ──
            if tick % 10 == 0:
                _check_token_expiry()

            # ── Commands (every tick) ──
            _process_commands()

            # ── Time-based briefings ──
            h = _hour_ct()
            if h >= MORNING_BRIEF_CT:
                _morning_briefing()
            if h >= EOD_SUMMARY_CT:
                _eod_summary()
            if h >= EOD_REVIEW_CT:
                _eod_review()

            if tick % 20 == 0:
                sc = _get_smallcap_stats()
                s  = _get_scalper_stats()
                logger.info(
                    f"Heartbeat #{tick} | "
                    f"SC: ${sc['combined_pnl']:+,.2f} "
                    f"(R:{sc['ross'].get('trades',0)} D:{sc['dux'].get('trades',0)}) | "
                    f"Scalper: ${s['pnl']:+,.2f} ({s['trades']} trades)"
                )

        except KeyboardInterrupt:
            logger.info("Monitor agent stopped by user")
            break
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

        time.sleep(HEARTBEAT_SEC)


if __name__ == "__main__":
    main()
