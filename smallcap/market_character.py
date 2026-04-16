"""
Market Character Analyzer.

Runs once at startup (pre-market or at open) to assess the overall session
character and return an adjusted aggression profile for the trading session.

Inputs:
  - SPY pre-market % change (overall market direction)
  - VIX spot level (fear / implied volatility regime)
  - Time of day (morning open is highest-probability window)

Output: MarketCharacter dataclass with:
  - regime      : "hot" | "normal" | "cold" | "avoid"
  - ofe_threshold: adjusted OFE composite score gate (default 65)
  - note        : Claude's one-sentence reasoning for the day

Regime → OFE threshold mapping:
  hot    → 55  (momentum is working, accept slightly weaker setups)
  normal → 65  (default)
  cold   → 72  (market choppy, only take the cleanest setups)
  avoid  → 80  (effectively blocks all entries — session not tradeable)

Usage:
    from smallcap.market_character import analyze_market_character
    market = analyze_market_character(schwab_client)
    # market.ofe_threshold, market.regime, market.note
"""

import os
import json
from dataclasses import dataclass
from datetime import datetime
from loguru import logger

# Lazy import — anthropic is optional; falls back to rule-based regime if absent
_anthropic = None


def _get_anthropic():
    global _anthropic
    if _anthropic is None:
        try:
            import anthropic as _ant
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if api_key:
                _anthropic = _ant.Anthropic(api_key=api_key)
        except ImportError:
            pass
    return _anthropic


@dataclass
class MarketCharacter:
    regime:        str    # "hot" | "normal" | "cold" | "avoid"
    ofe_threshold: int    # adjusted composite score gate
    spy_chg_pct:   float  # SPY pre-market or open change %
    vix:           float  # VIX level
    note:          str    # Claude's one-sentence session note

    def __str__(self) -> str:
        return (
            f"[{self.regime.upper()}] OFE≥{self.ofe_threshold} | "
            f"SPY{self.spy_chg_pct:+.2f}% VIX={self.vix:.1f} | {self.note}"
        )


# VIX → default regime (used when Claude is unavailable)
_VIX_RULES = [
    (12,  "cold"),    # Very low VIX — small caps don't move, setups fake out
    (20,  "hot"),     # Sweet spot for small cap momentum
    (30,  "normal"),  # Elevated but manageable
    (45,  "cold"),    # High fear — many false signals
    (999, "avoid"),   # Extreme fear — circuit breakers, erratic fills
]


def analyze_market_character(client) -> MarketCharacter:
    """
    Fetch market indicators and return an aggression profile for the session.
    Falls back gracefully if Schwab quotes or Claude are unavailable.
    """
    spy_chg, vix = _fetch_indicators(client)

    # Rule-based fallback regime (used even when Claude IS available as sanity check)
    rule_regime = _rule_regime(spy_chg, vix)

    # Claude-enhanced analysis (if API key is set)
    regime, note = _claude_regime(spy_chg, vix, rule_regime)

    thresholds = {"hot": 55, "normal": 65, "cold": 72, "avoid": 80}
    ofe_threshold = thresholds.get(regime, 65)

    mc = MarketCharacter(
        regime=regime,
        ofe_threshold=ofe_threshold,
        spy_chg_pct=spy_chg,
        vix=vix,
        note=note,
    )

    logger.info(f"Market character: {mc}")
    return mc


# ── PRIVATE ───────────────────────────────────────────────────────────────────

def _fetch_indicators(client) -> tuple[float, float]:
    """
    Fetch SPY % change and VIX from Schwab.
    Returns (spy_chg_pct, vix_level). Falls back to (0.0, 20.0) on any error.
    """
    spy_chg = 0.0
    vix     = 20.0

    try:
        import httpx
        resp = client.get_quotes(["SPY", "$VIX"])
        if resp.status_code == httpx.codes.OK:
            data = resp.json()

            spy = data.get("SPY", {})
            spy_quote = spy.get("quote", spy)
            spy_last  = float(spy_quote.get("lastPrice") or spy_quote.get("mark") or 0)
            spy_close = float(spy_quote.get("closePrice") or spy_quote.get("regularMarketPreviousClose") or 0)
            if spy_close > 0 and spy_last > 0:
                spy_chg = round((spy_last - spy_close) / spy_close * 100, 2)

            vix_data  = data.get("$VIX", {})
            vix_quote = vix_data.get("quote", vix_data)
            vix_val   = float(vix_quote.get("lastPrice") or vix_quote.get("mark") or 0)
            if vix_val > 0:
                vix = round(vix_val, 2)

            logger.info(f"Market indicators: SPY {spy_chg:+.2f}% | VIX {vix:.1f}")
    except Exception as e:
        logger.warning(f"Could not fetch market indicators: {e} — using defaults")

    return spy_chg, vix


def _rule_regime(spy_chg: float, vix: float) -> str:
    """Pure rule-based regime classification. No LLM required."""
    # Hard avoid: extreme VIX
    if vix > 45:
        return "avoid"

    # Determine VIX-based base regime
    base = "normal"
    for threshold, regime in _VIX_RULES:
        if vix < threshold:
            base = regime
            break

    # SPY direction modifier
    if spy_chg <= -1.5:
        # Market selling off hard — step down one level
        if base == "hot":
            return "normal"
        if base == "normal":
            return "cold"
        return "avoid"
    elif spy_chg >= 0.5:
        # Market up pre-market — risk-on, step up if we're cold
        if base == "cold":
            return "normal"

    return base


def _claude_regime(spy_chg: float, vix: float, rule_regime: str) -> tuple[str, str]:
    """
    Ask Claude to reason about session character.
    Returns (regime, note). Falls back to (rule_regime, rule-note) if unavailable.
    """
    client = _get_anthropic()
    if not client:
        note = _rule_note(spy_chg, vix, rule_regime)
        return rule_regime, note

    now_str = datetime.now().strftime("%A %B %d, %Y %H:%M CT")

    prompt = f"""You are the risk manager for a small cap momentum trading system.
Today is {now_str}.

Pre-market market conditions:
- SPY change: {spy_chg:+.2f}% vs prior close
- VIX (fear index): {vix:.1f}
- Rule-based classification: {rule_regime}

The system trades small cap stocks ($1–$20, float <20M) using breakout patterns
(Bull Flag, ABCD, ORB, VWAP Reclaim). It is most profitable when:
- VIX is 15–30 (volatility creates moves but not chaos)
- Market is up pre-market or flat (risk-on sentiment)
- NOT a major macro event day (FOMC, CPI, NFP) that could cause whipsaw

Based ONLY on the numbers above, classify this session and give one-sentence guidance.

Valid regimes:
- "hot"    → lower entry bar (OFE 55), momentum is likely strong
- "normal" → standard entry bar (OFE 65), trade the system as designed
- "cold"   → higher entry bar (OFE 72), only take A+ setups
- "avoid"  → block all entries (OFE 80), conditions unfavorable

Respond with JSON only: {{"regime": "...", "note": "one sentence"}}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if "{" in text:
            text = text[text.index("{"):text.rindex("}") + 1]
        result = json.loads(text)
        regime = result.get("regime", rule_regime)
        note   = result.get("note", "")
        if regime not in ("hot", "normal", "cold", "avoid"):
            regime = rule_regime
        return regime, note
    except Exception as e:
        logger.debug(f"Claude market character failed: {e}")
        return rule_regime, _rule_note(spy_chg, vix, rule_regime)


def _rule_note(spy_chg: float, vix: float, regime: str) -> str:
    notes = {
        "hot":    f"VIX {vix:.0f} in sweet spot, SPY {spy_chg:+.1f}% — momentum favored today.",
        "normal": f"VIX {vix:.0f} and SPY {spy_chg:+.1f}% — standard session, trade the system.",
        "cold":   f"VIX {vix:.0f} or SPY {spy_chg:+.1f}% — choppy conditions, A+ setups only.",
        "avoid":  f"VIX {vix:.0f} — extreme conditions, skipping entries today.",
    }
    return notes.get(regime, "Standard session.")
