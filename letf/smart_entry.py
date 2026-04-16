"""
LETF Smart Entry Timing Engine.

Waits for optimal intraday conditions before entering swing trades.

Key principles:
  BULL LETFs — buy underlying pullbacks / low-of-range (don't chase rips)
  BEAR LETFs — buy underlying bounces / high-of-range (don't chase flushes)
  Volume confirmation — entry on above-avg volume adds conviction
  VIX stability   — don't enter bull into a VIX spike; don't enter bear into a VIX collapse
  Gap-and-hold    — gap that holds near its extreme is valid entry
  Spread gate     — LETF bid/ask spread checked; logged if wide
  Patience system — after PATIENCE_ATTEMPTS loops (~4 min), enter regardless
  Time gates      — no entries before 9:00 AM CT (opening noise) or after 2:00 PM CT
"""
import time
from datetime import datetime
from loguru import logger


class LETFSmartEntry:

    ENTRY_OPEN_CT   = 9.0    # No entries before 9:00 AM CT
    ENTRY_CLOSE_CT  = 14.0   # No new entries after 2:00 PM CT
    MAX_SPREAD_PCT  = 0.015  # 1.5% LETF bid/ask spread warning threshold
    PATIENCE_ATTEMPTS = 8    # ~4 min at 30 s loop cadence

    def __init__(self, client):
        self.client = client
        self.trackers = {}   # etf_symbol -> tracker dict

    def reset(self, symbol):
        """Clear tracker so the symbol can be re-evaluated fresh."""
        self.trackers.pop(symbol, None)

    def should_enter(self, etf_symbol, underlying_symbol, direction):
        """
        Returns (should_enter: bool, reason: str).

        etf_symbol        — e.g. "TQQQ", "SQQQ"
        underlying_symbol — e.g. "QQQ", "SPY"
        direction         — "BULL" or "BEAR"
        """
        now = datetime.now()
        hour_ct = now.hour + now.minute / 60.0

        # ── TIME GATES ──
        if hour_ct < self.ENTRY_OPEN_CT:
            return False, f"too_early_{hour_ct:.2f}CT"
        if hour_ct > self.ENTRY_CLOSE_CT:
            return False, "past_entry_window"

        # ── UNDERLYING QUOTE ──
        und = self._get_quote(underlying_symbol)
        if not und:
            return False, "no_underlying_quote"
        und_price  = und.get("lastPrice", 0)
        und_high   = und.get("highPrice", und_price)
        und_low    = und.get("lowPrice", und_price)
        und_volume = und.get("totalVolume", 0)
        und_avg    = und.get("averageVolume", und_volume or 1)
        und_chg    = und.get("netPercentChangeInDouble", 0)
        if und_price <= 0:
            return False, "no_underlying_price"

        # ── LETF QUOTE (spread check) ──
        etf = self._get_quote(etf_symbol)
        if not etf:
            return False, "no_etf_quote"
        etf_bid   = etf.get("bidPrice", 0)
        etf_ask   = etf.get("askPrice", 0)
        etf_price = etf.get("lastPrice", (etf_bid + etf_ask) / 2 if etf_bid and etf_ask else 0)
        if etf_price <= 0:
            return False, "no_etf_price"
        if etf_bid > 0 and etf_ask > 0:
            spread_pct = (etf_ask - etf_bid) / etf_price
            if spread_pct > self.MAX_SPREAD_PCT:
                logger.debug(f"    {etf_symbol} spread {spread_pct:.2%} (wide)")

        # ── TRACKER ──
        if etf_symbol not in self.trackers:
            self.trackers[etf_symbol] = {
                "session_high": max(und_high, und_price),
                "session_low":  min(und_low,  und_price),
                "attempts":     0,
                "start_time":   now,
            }
        t = self.trackers[etf_symbol]
        t["attempts"]    += 1
        t["session_high"] = max(t["session_high"], und_high, und_price)
        t["session_low"]  = min(t["session_low"],  und_low,  und_price)

        intraday_range = t["session_high"] - t["session_low"]
        vol_ratio = und_volume / max(und_avg, 1)

        # Dead market — relax early in session
        if vol_ratio < 0.10 and t["attempts"] < 4:
            return False, f"volume_too_low_{vol_ratio:.2f}"

        # ── VIX STABILITY CHECK ──
        vix_q = self._get_quote("$VIX")
        if vix_q:
            vix_chg = vix_q.get("netPercentChangeInDouble", 0)
            # Bull position into a VIX explosion — wait for it to stabilise
            if direction == "BULL" and vix_chg > 8 and t["attempts"] < 6:
                return False, f"vix_spike_{vix_chg:.1f}pct"
            # Bear position into a VIX collapse — market is rallying hard
            if direction == "BEAR" and vix_chg < -8 and t["attempts"] < 6:
                return False, f"vix_collapse_{vix_chg:.1f}pct"

        # ── BULL ENTRY CONDITIONS ──
        if direction == "BULL":

            # A: Price in lower third of session range — ideal dip entry
            if intraday_range > 0:
                range_pos = (und_price - t["session_low"]) / intraday_range
                if range_pos <= 0.33:
                    return True, f"bull_dip_range_{range_pos:.2f}"

            # B: Meaningful pullback from session high (≥0.3%)
            if t["session_high"] > 0:
                pullback = (t["session_high"] - und_price) / t["session_high"]
                if pullback >= 0.003:
                    return True, f"bull_pullback_{pullback:.3f}"

            # C: Volume surge on up move — conviction entry
            if vol_ratio >= 1.5 and und_chg > 0:
                return True, f"bull_vol_surge_{vol_ratio:.1f}x"

            # D: Gap-and-hold — gapped up ≥1% and still holding upper 60% of range
            if und_chg > 1.0 and intraday_range > 0:
                range_pos = (und_price - t["session_low"]) / intraday_range
                if range_pos >= 0.60:
                    return True, f"bull_gap_hold_{und_chg:.1f}pct"

        # ── BEAR ENTRY CONDITIONS ──
        elif direction == "BEAR":

            # A: Price in upper third of session range — ideal bounce-to-short entry
            if intraday_range > 0:
                range_pos = (und_price - t["session_low"]) / intraday_range
                if range_pos >= 0.67:
                    return True, f"bear_rip_range_{range_pos:.2f}"

            # B: Meaningful bounce from session low (≥0.3%)
            if t["session_low"] > 0:
                bounce = (und_price - t["session_low"]) / t["session_low"]
                if bounce >= 0.003:
                    return True, f"bear_bounce_{bounce:.3f}"

            # C: Volume surge on down move — conviction entry
            if vol_ratio >= 1.5 and und_chg < 0:
                return True, f"bear_vol_surge_{vol_ratio:.1f}x"

            # D: Gap-and-hold — gapped down ≥1% and still holding lower 40% of range
            if und_chg < -1.0 and intraday_range > 0:
                range_pos = (und_price - t["session_low"]) / intraday_range
                if range_pos <= 0.40:
                    return True, f"bear_gap_hold_{und_chg:.1f}pct"

        # ── PATIENCE — force entry after enough attempts ──
        if t["attempts"] >= self.PATIENCE_ATTEMPTS:
            return True, f"patience_{t['attempts']}attempts"

        return False, f"waiting_attempt_{t['attempts']}"

    def _get_quote(self, symbol):
        try:
            time.sleep(0.05)
            r = self.client.get_quote(symbol)
            if r.status_code == 200:
                return r.json().get(symbol, {}).get("quote", {})
        except Exception:
            pass
        return None
