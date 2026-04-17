"""
PCRA RSI Oversold Bounce Scalper v2.0
Account: 16167026 (PCRA)

Strategy — trend-aligned oversold bounces:

  At session start, determine market direction via SPY vs its 20-day SMA:
    UPTREND   → watch TQQQ  — buy when TQQQ  RSI(14) < 20  (dip in uptrend)
    DOWNTREND → watch SQQQ  — buy when SQQQ  RSI(14) < 20  (dip in downtrend)

  Both cases use the same oversold-bounce logic. Trading with the trend means
  the underlying bias works FOR you while you wait for the bounce.

  ENTRY  — Instrument RSI first crosses below 20 AND
            bar volume >= 1.5x 10-bar average AND
            time is between 9:00 AM and 2:30 PM CT AND
            trend-direction gate passes (see below)

  Trend gate:
    TQQQ trade → SPY must not be falling hard right now (< -1% last 30 min)
    SQQQ trade → SPY must not be surging hard right now (> +1% last 30 min)

  EXIT   — First of:
            1. RSI crosses back above 30
            2. Price reaches VWAP
            3. Price drops -1.5% from entry (hard stop)
            4. 2:50 PM CT time stop

  One trade per day. Position size: 25% of equity.

Usage:
  python scripts/pcra_rsi_scalper.py              # paper, auto-detect direction
  python scripts/pcra_rsi_scalper.py --live        # live, auto-detect direction
  python scripts/pcra_rsi_scalper.py --live --force-ticker TQQQ  # override
"""

import os
import sys
import json
import time

from datetime import datetime, date

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _hour_ct() -> float:
    n = datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()
    return n.hour + n.minute / 60.0 + n.second / 3600.0

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
ACCOUNT_NUMBER  = "16167026"
SPY_TICKER      = "SPY"
BULL_TICKER     = "TQQQ"      # trade when market uptrend
BEAR_TICKER     = "SQQQ"      # trade when market downtrend

POSITION_PCT    = 0.25         # 25% of equity per trade
RSI_PERIOD      = 14
RSI_ENTRY       = 20           # Buy on first cross below this
RSI_EXIT        = 30           # Sell when RSI recovers above this
STOP_PCT        = 0.015        # Hard stop -1.5%
VOLUME_MULT     = 1.5          # Entry bar volume must be >= 1.5x 10-bar avg
SPY_SURGE_LIMIT = 0.01         # Block SQQQ entry if SPY surging > +1% last 30 min
SPY_DROP_LIMIT  = -0.01        # Block TQQQ entry if SPY dropping > -1% last 30 min
SMA_DAYS        = 20           # SPY SMA period for trend direction

BAR_MINUTES     = 5
POLL_SECONDS    = 30

# Central Time (market: 8:30 AM – 3:00 PM CT)
MARKET_OPEN_CT  = 8.5
ENTRY_START_CT  = 9.0          # 30-min warmup after open
ENTRY_STOP_CT   = 14.5
FORCE_EXIT_CT   = 14.833       # 2:50 PM CT
MARKET_CLOSE_CT = 15.0

STATE_FILE      = "config/pcra_scalper_state.json"


# ── BAR BUILDER ───────────────────────────────────────────────────────────────

class BarBuilder:
    """Aggregates 30-second polls into OHLCV bars.
    Tracks volume increments (Schwab returns cumulative daily volume).
    """

    def __init__(self, bar_minutes=5):
        self.bar_minutes   = bar_minutes
        self.completed     = []
        self._open_time    = None
        self._o = self._h = self._l = self._c = 0.0
        self._bar_vol      = 0
        self._last_day_vol = 0

    def _bucket(self, dt):
        m = (dt.minute // self.bar_minutes) * self.bar_minutes
        return dt.replace(minute=m, second=0, microsecond=0)

    def add(self, price, total_day_vol, dt=None):
        if price <= 0:
            return None
        dt     = dt or datetime.now()
        bucket = self._bucket(dt)
        incr   = max(0, total_day_vol - self._last_day_vol)
        self._last_day_vol = total_day_vol

        if self._open_time is None:
            self._open_time = bucket
            self._o = self._h = self._l = self._c = price
            self._bar_vol = incr
            return None

        if bucket > self._open_time:
            bar = {
                "time":   self._open_time.isoformat(),
                "open":   round(self._o, 4),
                "high":   round(self._h, 4),
                "low":    round(self._l, 4),
                "close":  round(self._c, 4),
                "volume": self._bar_vol,
            }
            self.completed.append(bar)
            self._open_time = bucket
            self._o = self._h = self._l = self._c = price
            self._bar_vol = incr
            return bar

        self._h = max(self._h, price)
        self._l = min(self._l, price)
        self._c = price
        self._bar_vol += incr
        return None

    def closes(self):  return [b["close"]  for b in self.completed]
    def volumes(self): return [b["volume"] for b in self.completed]


# ── RSI ───────────────────────────────────────────────────────────────────────

def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 2)


# ── VWAP ──────────────────────────────────────────────────────────────────────

class VWAP:
    def __init__(self):
        self._pv = 0.0; self._v = 0; self._day = None

    def update(self, price, volume):
        today = date.today()
        if self._day != today:
            self._pv = self._v = 0; self._day = today
        self._pv += price * volume; self._v += volume
        return self._pv / self._v if self._v > 0 else price


# ── MARKET DIRECTION ─────────────────────────────────────────────────────────

def detect_market_trend(client):
    """
    Compare SPY's current price to its 20-day SMA using daily history.
    Returns 'UP', 'DOWN', or 'NEUTRAL'.
    Also returns (spy_price, sma20, pct_from_sma) for logging.
    """
    try:
        from schwab.client import Client as SC
        # Fetch last 25 daily candles for SPY
        resp = client.get_price_history(
            SPY_TICKER,
            period_type=SC.PriceHistory.PeriodType.MONTH,
            period=SC.PriceHistory.Period.ONE_MONTH,
            frequency_type=SC.PriceHistory.FrequencyType.DAILY,
            frequency=SC.PriceHistory.Frequency.DAILY,
        )
        if resp.status_code == 200:
            candles = resp.json().get("candles", [])
            closes  = [c["close"] for c in candles if "close" in c]  # noqa: E741
            if len(closes) >= SMA_DAYS:
                sma20     = sum(closes[-SMA_DAYS:]) / SMA_DAYS
                spy_price = closes[-1]
                pct       = (spy_price - sma20) / sma20
                if spy_price > sma20 * 1.005:     # > 0.5% above SMA = uptrend
                    return "UP", spy_price, sma20, pct
                elif spy_price < sma20 * 0.995:   # > 0.5% below SMA = downtrend
                    return "DOWN", spy_price, sma20, pct
                else:
                    return "NEUTRAL", spy_price, sma20, pct
    except Exception as e:
        logger.warning(f"Trend detection failed: {e}")

    # Fallback: use recent intraday SPY direction
    return "NEUTRAL", 0, 0, 0


def detect_trend_from_spy_bars(spy_bars):
    """
    Intraday fallback: compare current SPY price to open-of-session price.
    Returns 'UP', 'DOWN', or 'NEUTRAL'.
    """
    closes = spy_bars.closes()
    if len(closes) < 6:
        return "NEUTRAL"
    open_price = closes[0]
    cur_price  = closes[-1]
    chg = (cur_price - open_price) / open_price
    if chg > 0.003:   return "UP"
    if chg < -0.003:  return "DOWN"
    return "NEUTRAL"


# ── SCHWAB HELPERS ────────────────────────────────────────────────────────────

def fetch_quote(client, symbol):
    try:
        r = client.get_quote(symbol)
        if r.status_code == 200:
            q     = r.json().get(symbol, {}).get("quote", {})
            price = (q.get("lastPrice") or q.get("mark") or
                     (q.get("bidPrice", 0) + q.get("askPrice", 0)) / 2)
            vol   = q.get("totalVolume", 0)
            return float(price), int(vol)
    except Exception as e:
        logger.debug(f"Quote {symbol}: {e}")
    return None, None


def fetch_equity(client, account_hash):
    try:
        from schwab.client import Client
        r   = client.get_account(account_hash, fields=[Client.Account.Fields.POSITIONS])
        bal = r.json().get("securitiesAccount", {}).get("currentBalances", {})
        return bal.get("equity", bal.get("liquidationValue", 0))
    except Exception:
        return 0


def get_account_hash(client):
    try:
        for a in client.get_account_numbers().json():
            if a["accountNumber"] == ACCOUNT_NUMBER:
                return a["hashValue"]
    except Exception:
        pass
    return None


def buy_market(client, ah, symbol, shares):
    try:
        from schwab.orders.equities import equity_buy_market
        r = client.place_order(ah, equity_buy_market(symbol, shares))
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"BUY error: {e}"); return False


def sell_market(client, ah, symbol, shares):
    try:
        from schwab.orders.equities import equity_sell_market
        r = client.place_order(ah, equity_sell_market(symbol, shares))
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"SELL error: {e}"); return False


# ── STATE ─────────────────────────────────────────────────────────────────────

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"State file load failed, starting fresh: {e}")
    return {"last_trade_date": "", "trades_today": 0, "history": []}


def save_state(state):
    from pathlib import Path
    path = Path(STATE_FILE)
    path.parent.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(path)


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run(paper=True, force_ticker=None):
    from utils.logging_setup import setup_logging
    setup_logging()

    from data.broker.schwab_auth import get_schwab_client
    client = get_schwab_client()
    ah     = get_account_hash(client)
    if not ah:
        logger.error(f"Could not resolve account hash for {ACCOUNT_NUMBER}"); return

    # ── DETECT MARKET DIRECTION ───────────────────────────────────────────────
    if force_ticker:
        ticker = force_ticker
        trend  = "UP" if ticker == BULL_TICKER else "DOWN"
        logger.info(f"Ticker forced to {ticker} (trend={trend})")
    else:
        trend, spy_px, sma20, pct = detect_market_trend(client)
        if trend == "NEUTRAL":
            # Intraday fallback will refine after bars accumulate; default UP
            ticker = BULL_TICKER
            logger.info(
                f"Trend NEUTRAL (SPY ${spy_px:.2f} vs SMA20 ${sma20:.2f} "
                f"{pct:+.2%}) — defaulting to {ticker}, will reassess intraday"
            )
        elif trend == "DOWN":
            ticker = BEAR_TICKER
            logger.info(
                f"Trend DOWN (SPY {pct:+.2%} below SMA20) — watching {ticker}"
            )
        else:
            ticker = BULL_TICKER
            logger.info(
                f"Trend UP (SPY {pct:+.2%} above SMA20) — watching {ticker}"
            )

    logger.info("=" * 60)
    logger.info(f"PCRA RSI SCALPER v2  {'PAPER' if paper else '*** LIVE ***'}")
    logger.info(f"Instrument : {ticker}  ({'BULL' if ticker==BULL_TICKER else 'BEAR'} side)")
    logger.info(f"Account    : {ACCOUNT_NUMBER}")
    logger.info(f"RSI        : entry<{RSI_ENTRY}  exit>{RSI_EXIT}")
    logger.info(f"Stop       : -{STOP_PCT:.1%}    Size: {POSITION_PCT:.0%} of equity")
    logger.info(f"Hours      : {ENTRY_START_CT}–{ENTRY_STOP_CT} CT")
    logger.info("=" * 60)

    state = load_state()
    today = date.today().isoformat()
    if state.get("last_trade_date") != today:
        state.update(last_trade_date=today, trades_today=0)
        save_state(state)

    bars     = BarBuilder(BAR_MINUTES)
    spy_bars = BarBuilder(BAR_MINUTES)
    vwap     = VWAP()

    in_position   = False
    shares_held   = 0
    entry_price   = 0.0
    entry_time    = None
    rsi_triggered = False
    prev_rsi      = None
    cycle         = 0
    trend_locked  = (force_ticker is not None or trend != "NEUTRAL")

    logger.info(f"Collecting bars — RSI ready after {RSI_PERIOD+1} bars "
                f"(~{(RSI_PERIOD+1)*BAR_MINUTES} min from open)")

    while True:
        now     = datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()
        hour_ct = _hour_ct()
        cycle  += 1

        # Session end
        if now.weekday() >= 5 or hour_ct >= MARKET_CLOSE_CT + 0.1:
            if in_position:
                logger.warning("SESSION END — closing position")
                if not paper:
                    sell_market(client, ah, ticker, shares_held)
                last_px, _ = fetch_quote(client, ticker)
                pnl = ((last_px or entry_price) - entry_price) / entry_price
                logger.info(f"CLOSED at session end: {shares_held}sh PnL≈{pnl:.2%}")
            logger.info("Session complete.")
            break

        if hour_ct < MARKET_OPEN_CT:
            if cycle % 20 == 1:
                logger.info(f"Pre-market — waiting for 8:30 AM CT")
            time.sleep(60)
            continue

        # ── FETCH ─────────────────────────────────────────────────────────────
        price,   day_vol  = fetch_quote(client, ticker)
        spy_price, spy_vol = fetch_quote(client, SPY_TICKER)

        if not price or price <= 0:
            time.sleep(POLL_SECONDS); continue

        current_vwap = vwap.update(price, day_vol or 1)
        new_bar      = bars.add(price, day_vol or 0)
        if spy_price:
            spy_bars.add(spy_price, spy_vol or 0)

        closes = bars.closes()
        rsi    = compute_rsi(closes, RSI_PERIOD)

        # Re-assess trend from intraday bars once we have enough data
        # (only if trend was NEUTRAL at session start and no trade yet)
        if (not trend_locked and not in_position
                and state["trades_today"] == 0
                and len(spy_bars.closes()) >= 10):
            intraday_trend = detect_trend_from_spy_bars(spy_bars)
            if intraday_trend != "NEUTRAL":
                new_ticker = BULL_TICKER if intraday_trend == "UP" else BEAR_TICKER
                if new_ticker != ticker:
                    logger.info(
                        f"TREND UPDATE: intraday SPY {intraday_trend} "
                        f"→ switching from {ticker} to {new_ticker}"
                    )
                    ticker       = new_ticker
                    bars         = BarBuilder(BAR_MINUTES)
                    vwap         = VWAP()
                    rsi_triggered = False
                    prev_rsi     = None
                trend_locked = True

        # ── STATUS LOG (every new bar) ─────────────────────────────────────────
        if new_bar:
            pos_str = (f"HELD {shares_held}sh@${entry_price:.2f} "
                       f"PnL:{((price-entry_price)/entry_price):+.2%}"
                       if in_position else "flat")
            rsi_str = f"{rsi:.1f}" if rsi is not None else "warming"
            spy_str = f"SPY=${spy_price:.2f}" if spy_price else ""
            logger.info(
                f"[{now.strftime('%H:%M')}] {ticker}=${price:.2f} "
                f"VWAP=${current_vwap:.2f}  RSI={rsi_str}  "
                f"bars={len(closes)}  {spy_str}  {pos_str}"
            )

        # ── EXIT ──────────────────────────────────────────────────────────────
        if in_position:
            pnl_pct  = (price - entry_price) / entry_price
            exit_tag = None

            if pnl_pct <= -STOP_PCT:
                exit_tag = ("STOP", f"stop loss {pnl_pct:.2%}")
            elif hour_ct >= FORCE_EXIT_CT:
                exit_tag = ("TIME", "2:50 PM CT time stop")
            elif rsi is not None and rsi > RSI_EXIT:
                exit_tag = ("RSI", f"RSI recovered {rsi:.1f} > {RSI_EXIT}")
            elif price >= current_vwap and pnl_pct > 0:
                exit_tag = ("VWAP", f"VWAP target ${current_vwap:.2f}")

            if exit_tag:
                label, reason = exit_tag
                logger.info(f"EXIT [{label}]: {reason}")
                if not paper:
                    sell_market(client, ah, ticker, shares_held)
                dollar_pnl = shares_held * (price - entry_price)
                logger.info(
                    f"  SOLD {shares_held}sh {ticker} "
                    f"entry=${entry_price:.2f} exit=${price:.2f} "
                    f"PnL=${dollar_pnl:+,.2f} ({pnl_pct:+.2%})"
                )
                state["history"].append({
                    "date": today, "ticker": ticker,
                    "trend": "UP" if ticker == BULL_TICKER else "DOWN",
                    "entry": entry_price, "exit": price,
                    "shares": shares_held,
                    "pnl_pct": round(pnl_pct, 4),
                    "pnl_dollar": round(dollar_pnl, 2),
                    "exit_reason": label,
                })
                save_state(state)
                in_position = False; shares_held = 0
                entry_price = 0.0;   rsi_triggered = False
                prev_rsi = rsi
                time.sleep(POLL_SECONDS); continue

        # ── ENTRY ─────────────────────────────────────────────────────────────
        if (not in_position
                and state["trades_today"] == 0
                and rsi is not None
                and ENTRY_START_CT <= hour_ct <= ENTRY_STOP_CT):

            # Detect first RSI crossover below RSI_ENTRY
            if rsi < RSI_ENTRY and (prev_rsi is None or prev_rsi >= RSI_ENTRY):
                rsi_triggered = True
                logger.info(
                    f"RSI OVERSOLD: {ticker} RSI={rsi:.1f} < {RSI_ENTRY} "
                    f"at ${price:.2f}"
                )

            if rsi_triggered:
                # Volume confirmation
                vols   = bars.volumes()
                vol_ok = True
                if len(vols) >= 10:
                    avg_v  = sum(vols[-10:]) / 10
                    vol_ok = bars._bar_vol >= avg_v * VOLUME_MULT if avg_v > 0 else True
                    if not vol_ok:
                        logger.debug(f"Vol blocked: {bars._bar_vol:,} < "
                                     f"{avg_v*VOLUME_MULT:,.0f} needed")

                # Trend gate: block if current SPY momentum opposes the trade
                trend_ok = True
                spy_cls  = spy_bars.closes()
                if len(spy_cls) >= 6:
                    spy_30m = (spy_cls[-1] - spy_cls[-6]) / spy_cls[-6]
                    if ticker == BULL_TICKER and spy_30m < SPY_DROP_LIMIT:
                        trend_ok = False
                        logger.warning(
                            f"Trend gate: SPY {spy_30m:+.2%} last 30 min "
                            f"— too weak for TQQQ entry"
                        )
                    elif ticker == BEAR_TICKER and spy_30m > SPY_SURGE_LIMIT:
                        trend_ok = False
                        logger.warning(
                            f"Trend gate: SPY {spy_30m:+.2%} last 30 min "
                            f"— too strong for SQQQ entry"
                        )

                if vol_ok and trend_ok:
                    equity      = fetch_equity(client, ah) if not paper else 47378.0
                    equity      = equity or 47378.0
                    dollar_size = equity * POSITION_PCT
                    shares      = int(dollar_size / price)

                    if shares >= 1:
                        side = "BULL (TQQQ)" if ticker == BULL_TICKER else "BEAR (SQQQ)"
                        logger.info("=" * 55)
                        logger.info(f"  *** ENTRY SIGNAL — {side} ***")
                        logger.info(f"  Ticker  : {ticker}")
                        logger.info(f"  RSI     : {rsi:.1f}  (oversold <{RSI_ENTRY})")
                        logger.info(f"  Price   : ${price:.2f}")
                        logger.info(f"  VWAP    : ${current_vwap:.2f}  "
                                    f"({(current_vwap-price)/price:+.2%} away)")
                        logger.info(f"  Shares  : {shares}  "
                                    f"Cost: ${shares*price:,.0f}")
                        logger.info(f"  Stop    : ${price*(1-STOP_PCT):.2f}  "
                                    f"risk ${shares*price*STOP_PCT:,.0f}")
                        logger.info(f"  Target  : VWAP ${current_vwap:.2f} "
                                    f"or RSI > {RSI_EXIT}")
                        logger.info("=" * 55)

                        filled = True
                        if not paper:
                            filled = buy_market(client, ah, ticker, shares)

                        if filled:
                            in_position           = True
                            shares_held           = shares
                            entry_price           = price
                            entry_time            = now
                            state["trades_today"] += 1
                            save_state(state)
                            logger.info(f"ENTERED: {shares}sh {ticker} @ ${price:.2f} "
                                        f"({'PAPER' if paper else 'LIVE'})")
                        else:
                            logger.error("Order rejected — will retry next signal")
                            rsi_triggered = False

        prev_rsi = rsi
        time.sleep(POLL_SECONDS)


# ── ENTRY POINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="PCRA RSI Oversold Bounce Scalper v2")
    p.add_argument("--live",         action="store_true",
                   help="Live trading (default: paper)")
    p.add_argument("--force-ticker", default=None,
                   help="Force TQQQ or SQQQ (skip auto-detect)")
    args = p.parse_args()

    if args.live:
        logger.warning("=" * 60)
        logger.warning("  LIVE MODE — REAL MONEY — PCRA ACCOUNT 16167026")
        logger.warning("=" * 60)

    run(paper=not args.live, force_ticker=args.force_ticker)
