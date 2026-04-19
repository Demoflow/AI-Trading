#!/usr/bin/env python3
"""
EOD Review Agent — runs at 3:05 PM CT after market close.

1. Reads today's full trading log + both portfolio JSONs
2. Reads key source code sections (thresholds, risk limits)
3. Sends everything to Claude Sonnet for deep analysis
4. Generates: narrative + specific proposed code changes
5. Emails summary to austinbult@gmail.com and asks for approval
6. Saves proposed changes to config/pending_changes.json
7. Awaits approval via agent_commands.txt (approve_changes / reject_changes)

Can be run standalone:  python scripts/eod_review.py
Or triggered by monitor_agent.py at EOD.
"""

import json
import os
import re
import smtplib
import sys
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

BASE_DIR           = Path(__file__).parent.parent
LOGS_DIR           = BASE_DIR / "logs"
PORTFOLIO_ROSS     = BASE_DIR / "config" / "smallcap_portfolio.json"
PORTFOLIO_DUX      = BASE_DIR / "config" / "dux_portfolio.json"
PORTFOLIO_SCALP    = BASE_DIR / "config" / "paper_scalp.json"
PENDING_CHANGES    = BASE_DIR / "config" / "pending_changes.json"
CANDIDATES_FILE    = BASE_DIR / "config" / "smallcap_candidates.json"

ALERT_EMAIL  = "austinbult@gmail.com"
GMAIL_USER   = os.getenv("GMAIL_USER", ALERT_EMAIL)
GMAIL_PASS   = os.getenv("GMAIL_APP_PASSWORD", "")

# Key source files to include in analysis context
SOURCE_FILES = {
    "scalper/signal_engine.py":  (1,  120),   # signal thresholds
    "scalper/risk_manager.py":   (1,  120),   # risk limits
    "smallcap/config.py":        (1,  80),    # smallcap config
    "smallcap/risk_manager.py":  (1,  80),    # smallcap risk
    "smallcap/dux_risk_manager.py": (1, 80),  # dux risk
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> dict | list:
    try:
        if path.exists():
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _read_source_section(rel_path: str, start: int, end: int) -> str:
    full = BASE_DIR / rel_path
    try:
        lines = full.read_text(encoding="utf-8").splitlines()
        snippet = lines[start - 1 : end]
        return "\n".join(snippet)
    except Exception:
        return f"(could not read {rel_path})"


def _read_today_log() -> str:
    today = date.today().isoformat()
    path  = LOGS_DIR / f"trading_{today}.log"
    if not path.exists():
        return "(no log file found for today)"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        # Strip DEBUG lines to save tokens — keep INFO, WARNING, ERROR
        filtered = [
            ln for ln in text.splitlines()
            if "| DEBUG   |" not in ln
        ]
        return "\n".join(filtered)
    except Exception as e:
        return f"(log read error: {e})"


def _get_today_trades(portfolio: dict | list, today: str) -> list[dict]:
    """Extract today's closed trades from a portfolio dict."""
    if not isinstance(portfolio, dict):
        return []
    history = portfolio.get("history", [])
    return [
        t for t in history
        if isinstance(t, dict) and t.get("exit_time", "")[:10] == today
    ]


def _summarize_trades(trades: list[dict], label: str) -> str:
    if not trades:
        return f"{label}: No trades today\n"

    lines = [f"{label}: {len(trades)} trades"]
    total_pnl = 0.0
    wins = losses = 0
    by_pattern: dict[str, list[float]] = {}

    for t in trades:
        pnl = t.get("pnl", 0.0)
        total_pnl += pnl
        pattern = t.get("signal_type") or t.get("pattern") or "unknown"
        by_pattern.setdefault(pattern, []).append(pnl)

        r = "WIN" if pnl >= 0 else "LOSS"
        if pnl >= 0:
            wins += 1
        else:
            losses += 1

        entry = t.get("entry_time", "")[:16]
        exit_ = t.get("exit_time", "")[:16]
        sym   = t.get("symbol", "?")
        lines.append(
            f"  {entry} → {exit_}  {sym:6s}  {r}  ${pnl:+.2f}  "
            f"pattern={pattern}  "
            f"entry=${t.get('entry_price', 0):.2f}  "
            f"exit=${t.get('exit_price', 0):.2f}"
        )

    wr = wins / len(trades) * 100 if trades else 0
    lines.append(
        f"  TOTAL: ${total_pnl:+.2f}  W:{wins} L:{losses}  WR:{wr:.0f}%"
    )
    lines.append("")
    lines.append("  By pattern:")
    for pat, pnls in sorted(by_pattern.items()):
        pat_wr = sum(1 for p in pnls if p >= 0) / len(pnls) * 100
        lines.append(
            f"    {pat:30s}  {len(pnls):2d} trades  "
            f"P&L:${sum(pnls):+.2f}  WR:{pat_wr:.0f}%"
        )
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# LLM analysis
# ──────────────────────────────────────────────────────────────────────────────

_REVIEW_SYSTEM = """You are an expert algorithmic trading analyst reviewing an automated day-trading system.
The system runs two sub-systems:
1. SCALPER — trades large-cap stocks and ETFs (SPY, QQQ, NVDA, META, AAPL, MSFT, GOOGL, AMZN, TSLA, TQQQ, SOXL) using VWAP-based signals. Signal types: RECLAIM (price crosses above VWAP with volume), REJECTION (price fails at VWAP from above), RETEST (reclaim → pullback → bounce). 8-level exit priority: VWAP break → hard stop → SD2 target → SD1 partial → breakeven lock → trailing stop → time stop → 3:30 PM CT hard close.
2. SMALL CAP — trades small-cap equities (Ross Cameron: long breakouts; Steven Dux: short exhaustion patterns including FRD, Spike Short, H&S, Dip Panic).

Your job:
- Analyze today's trading activity rigorously and honestly
- Identify specific, fixable root causes for losses and missed opportunities
- Propose concrete code changes with exact before/after values
- Be surgical: one clear problem = one focused fix. Do NOT suggest adding complexity.
- Do NOT propose removing risk controls, safety stops, or circuit breakers.
- Format your response EXACTLY as specified — it will be parsed programmatically."""

_REVIEW_PROMPT_TEMPLATE = """
TODAY: {today}
MARKET REGIME: {regime}

════════════════════════════════════════
TRADE RESULTS
════════════════════════════════════════
{trade_summary}

════════════════════════════════════════
TODAY'S TRADING LOG (INFO/WARNING/ERROR only)
════════════════════════════════════════
{log_excerpt}

════════════════════════════════════════
CURRENT SYSTEM PARAMETERS (relevant source code)
════════════════════════════════════════
{source_code}

════════════════════════════════════════
INSTRUCTIONS
════════════════════════════════════════
Analyze today's trading. Then respond in EXACTLY this format:

---NARRATIVE---
[3-5 paragraphs: what happened today, what worked, what didn't, root causes of losses/underperformance, key observations. Be direct and specific — reference actual trade times, symbols, patterns, and log lines.]

---ASSESSMENT---
SCALPER: [GOOD / ACCEPTABLE / NEEDS WORK] — [one sentence reason]
SMALL_CAP_ROSS: [GOOD / ACCEPTABLE / NEEDS WORK] — [one sentence reason]
SMALL_CAP_DUX: [GOOD / ACCEPTABLE / NEEDS WORK] — [one sentence reason]
OVERALL: [GOOD / ACCEPTABLE / NEEDS WORK] — [one sentence reason]

---PROPOSED_CHANGES---
[List ONLY changes that are clearly supported by today's evidence. If no changes are warranted, write NONE.]

Each change in this format:
CHANGE_N:
  FILE: [relative path from trading_system/]
  WHAT: [one sentence description of the change]
  WHY: [one sentence — what evidence from today supports this change]
  RISK: [LOW / MEDIUM — LOW = parameter tweak, MEDIUM = logic change]
  BEFORE: [exact current code, 1-5 lines]
  AFTER: [exact replacement code, 1-5 lines]

---END---
"""


def run_analysis(today: str, trade_summary: str, log_text: str, source_code: str, regime: str) -> str:
    """Call Claude Sonnet for deep analysis. Returns raw response text."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

        # Truncate log to fit context — keep most recent 300 lines
        log_lines = log_text.splitlines()
        if len(log_lines) > 300:
            log_excerpt = (
                f"[... {len(log_lines) - 300} earlier lines omitted ...]\n"
                + "\n".join(log_lines[-300:])
            )
        else:
            log_excerpt = log_text

        prompt = _REVIEW_PROMPT_TEMPLATE.format(
            today=today,
            regime=regime,
            trade_summary=trade_summary,
            log_excerpt=log_excerpt,
            source_code=source_code,
        )

        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            system=_REVIEW_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text

    except Exception as e:
        logger.error(f"LLM analysis failed: {e}")
        return f"---NARRATIVE---\nAnalysis unavailable: {e}\n---ASSESSMENT---\nOVERALL: NEEDS WORK — LLM error\n---PROPOSED_CHANGES---\nNONE\n---END---"


# ──────────────────────────────────────────────────────────────────────────────
# Parse LLM response
# ──────────────────────────────────────────────────────────────────────────────

def parse_analysis(raw: str) -> dict:
    """Parse the structured LLM response into components."""
    result = {
        "narrative":  "",
        "assessment": {},
        "changes":    [],
        "raw":        raw,
    }

    # Narrative
    m = re.search(r"---NARRATIVE---\s*(.*?)\s*---ASSESSMENT---", raw, re.DOTALL)
    if m:
        result["narrative"] = m.group(1).strip()

    # Assessment
    m = re.search(r"---ASSESSMENT---\s*(.*?)\s*---PROPOSED_CHANGES---", raw, re.DOTALL)
    if m:
        for line in m.group(1).strip().splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                result["assessment"][key.strip()] = val.strip()

    # Proposed changes
    m = re.search(r"---PROPOSED_CHANGES---\s*(.*?)\s*---END---", raw, re.DOTALL)
    if m:
        block = m.group(1).strip()
        if block.upper() == "NONE" or not block:
            result["changes"] = []
        else:
            # Split into individual CHANGE_N blocks
            chunks = re.split(r"CHANGE_\d+:", block)
            for chunk in chunks:
                chunk = chunk.strip()
                if not chunk:
                    continue
                change = {}
                for field in ("FILE", "WHAT", "WHY", "RISK", "BEFORE", "AFTER"):
                    fm = re.search(
                        rf"{field}:\s*(.*?)(?=(?:FILE|WHAT|WHY|RISK|BEFORE|AFTER):|$)",
                        chunk, re.DOTALL
                    )
                    if fm:
                        change[field.lower()] = fm.group(1).strip()
                if "file" in change and "before" in change and "after" in change:
                    result["changes"].append(change)

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Email
# ──────────────────────────────────────────────────────────────────────────────

def send_review_email(today: str, parsed: dict, trade_summary: str) -> bool:
    """Send EOD review email with analysis and proposed changes."""
    if not GMAIL_PASS:
        logger.warning("Cannot send EOD review — GMAIL_APP_PASSWORD not set")
        return False

    changes = parsed["changes"]
    assessment = parsed["assessment"]
    overall = assessment.get("OVERALL", "UNKNOWN")

    subject = f"EOD Review — {today} — {overall}"

    # Build plain text body
    lines = [
        f"EOD REVIEW — {today}",
        "=" * 60,
        "",
        "ASSESSMENT",
        "-" * 40,
    ]
    for k, v in assessment.items():
        lines.append(f"  {k}: {v}")

    lines += [
        "",
        "ANALYSIS",
        "-" * 40,
        parsed["narrative"],
        "",
        "TRADE SUMMARY",
        "-" * 40,
        trade_summary,
    ]

    if changes:
        lines += [
            "",
            f"PROPOSED CODE CHANGES ({len(changes)} change{'s' if len(changes) != 1 else ''})",
            "-" * 40,
            "These changes will NOT be applied automatically.",
            "Review them below, then reply with your decision.",
            "",
        ]
        for i, c in enumerate(changes, 1):
            lines += [
                f"CHANGE {i} — {c.get('risk','?')} RISK",
                f"  File:  {c.get('file','')}",
                f"  What:  {c.get('what','')}",
                f"  Why:   {c.get('why','')}",
                f"  Before:",
                *[f"    {ln}" for ln in c.get("before","").splitlines()],
                f"  After:",
                *[f"    {ln}" for ln in c.get("after","").splitlines()],
                "",
            ]

        lines += [
            "─" * 60,
            "TO APPROVE ALL CHANGES:",
            '  Add this line to config/agent_commands.txt:',
            "    approve_changes",
            "",
            "TO REJECT (no changes applied):",
            '  Add this line to config/agent_commands.txt:',
            "    reject_changes",
            "",
            "TO APPROVE SPECIFIC CHANGES ONLY:",
            '  e.g. to approve only changes 1 and 3:',
            "    approve_changes 1,3",
            "─" * 60,
        ]
    else:
        lines += [
            "",
            "PROPOSED CODE CHANGES",
            "-" * 40,
            "No code changes recommended for today.",
            "Both systems appear to be operating within expected parameters.",
        ]

    body = "\n".join(lines)

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Trading Bot] {subject}"
        msg["From"]    = GMAIL_USER
        msg["To"]      = ALERT_EMAIL
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(GMAIL_USER, GMAIL_PASS)
            server.sendmail(GMAIL_USER, ALERT_EMAIL, msg.as_string())

        logger.info(f"[EOD Review] Email sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"[EOD Review] Email failed: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Save pending changes
# ──────────────────────────────────────────────────────────────────────────────

def save_pending_changes(today: str, parsed: dict):
    """Save proposed changes to config/pending_changes.json."""
    data = {
        "date":       today,
        "generated":  datetime.now().isoformat(),
        "approved":   False,
        "rejected":   False,
        "approved_indices": [],   # empty = all; [1,3] = specific changes
        "assessment": parsed["assessment"],
        "narrative":  parsed["narrative"],
        "changes":    parsed["changes"],
    }
    try:
        with open(PENDING_CHANGES, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[EOD Review] Pending changes saved ({len(parsed['changes'])} change(s))")
    except Exception as e:
        logger.error(f"[EOD Review] Failed to save pending changes: {e}")


# ──────────────────────────────────────────────────────────────────────────────
# Main entry
# ──────────────────────────────────────────────────────────────────────────────

def run(today: str | None = None):
    today = today or date.today().isoformat()
    logger.info(f"[EOD Review] Starting review for {today}")

    # ── 1. Gather trade data ──────────────────────────────────────────────────
    ross_port  = _load_json(PORTFOLIO_ROSS)
    dux_port   = _load_json(PORTFOLIO_DUX)
    scalp_port = _load_json(PORTFOLIO_SCALP)

    ross_trades  = _get_today_trades(ross_port,  today)
    dux_trades   = _get_today_trades(dux_port,   today)
    scalp_trades = _get_today_trades(scalp_port, today)

    trade_summary = "\n".join([
        _summarize_trades(ross_trades,  "ROSS (Long Breakouts)"),
        _summarize_trades(dux_trades,   "DUX (Short Exhaustion)"),
        _summarize_trades(scalp_trades, "SCALPER (0DTE Options)"),
    ])

    # ── 2. Read log ───────────────────────────────────────────────────────────
    log_text = _read_today_log()

    # Extract market regime from log
    regime = "UNKNOWN"
    m = re.search(r"Market character: \[(\w+)\]", log_text)
    if m:
        regime = m.group(1)
    # Also grab day classification
    m2 = re.search(r"Day classified: (\w+)", log_text)
    if m2:
        regime += f" | Day: {m2.group(1)}"

    # ── 3. Read source code sections ─────────────────────────────────────────
    source_parts = []
    for rel_path, (start, end) in SOURCE_FILES.items():
        snippet = _read_source_section(rel_path, start, end)
        source_parts.append(f"# {rel_path} (lines {start}-{end})\n{snippet}")
    source_code = "\n\n".join(source_parts)

    # ── 4. Run LLM analysis ───────────────────────────────────────────────────
    logger.info("[EOD Review] Sending to Claude Sonnet for analysis...")
    raw_response = run_analysis(today, trade_summary, log_text, source_code, regime)

    # ── 5. Parse ──────────────────────────────────────────────────────────────
    parsed = parse_analysis(raw_response)
    n_changes = len(parsed["changes"])
    logger.info(
        f"[EOD Review] Analysis complete — "
        f"overall={parsed['assessment'].get('OVERALL','?')} "
        f"proposed_changes={n_changes}"
    )

    # ── 6. Save pending changes ───────────────────────────────────────────────
    save_pending_changes(today, parsed)

    # ── 7. Email ──────────────────────────────────────────────────────────────
    sent = send_review_email(today, parsed, trade_summary)
    if not sent:
        # Fallback: print to stdout so monitor agent can log it
        logger.info("[EOD Review] Email failed — printing summary to log:")
        logger.info(parsed["narrative"][:500])

    return parsed


if __name__ == "__main__":
    import sys
    today_arg = sys.argv[1] if len(sys.argv) > 1 else None
    result = run(today_arg)
    if result["changes"]:
        print(f"\n{len(result['changes'])} change(s) proposed. "
              f"Review email sent to {ALERT_EMAIL}.")
        print("Approve by adding 'approve_changes' to config/agent_commands.txt")
    else:
        print("No code changes recommended.")
