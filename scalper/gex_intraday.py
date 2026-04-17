"""
Intraday GEX Analyzer v2 — Level Interaction Tracking.

Calculates dealer gamma exposure for SPY/QQQ from live option chain data.
v2 additions:
  - Per-level interaction log: tracks each time price tests a GEX wall
  - Approach detection: entering/exiting wall proximity (±0.3% of level)
  - Outcome scoring: rejected (snapped back) vs absorbed (broke through)
  - get_level_score(symbol, level): returns confidence modifier for fading or
    momentum trading at that wall based on its interaction history
"""

import httpx
from collections import defaultdict, deque
from datetime import datetime
from loguru import logger

try:
    from zoneinfo import ZoneInfo
    _CT_TZ = ZoneInfo("America/Chicago")
except ImportError:
    _CT_TZ = None


def _now_ct():
    return datetime.now(tz=_CT_TZ) if _CT_TZ else datetime.now()

APPROACH_THRESHOLD = 0.003  # Within 0.3% of a level = "approaching"
ABSORBED_THRESHOLD = 0.004  # Price moved 0.4%+ through the level = absorbed


class IntradayGEX:

    def __init__(self, schwab_client):
        self.client = schwab_client
        self.cache = {}
        # Interaction log: {(symbol, level): deque of {"time", "price", "result"}}
        self._interactions = defaultdict(lambda: deque(maxlen=10))
        # Approach state: {symbol: {"level": float, "approach_price": float, "time": dt}}
        self._in_approach = {}

    # ── CORE GEX ANALYSIS (unchanged logic) ───────────────────────────────────

    def analyze(self, symbol):
        try:
            from schwab.client import Client
            resp = self.client.get_option_chain(
                symbol,
                contract_type=Client.Options.ContractType.ALL,
                strike_count=20,
                include_underlying_quote=True,
                strategy=Client.Options.Strategy.SINGLE,
            )
            if resp.status_code != httpx.codes.OK:
                return None
            chain = resp.json()
        except Exception:
            return None

        price = chain.get("underlyingPrice", 0)
        if price <= 0:
            return None

        call_map = chain.get("callExpDateMap", {})
        put_map  = chain.get("putExpDateMap",  {})

        gex_by_strike = {}
        total_gex     = 0
        zero_dte_gex  = 0

        for exp_key, strikes in call_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 5:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    oi    = c.get("openInterest", 0)
                    gamma = c.get("gamma", 0)
                    if oi <= 0 or gamma <= 0:
                        continue
                    gex = gamma * oi * 100 * price / 1e6
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    gex_by_strike[strike] = gex_by_strike.get(strike, 0) + gex
                    total_gex    += gex
                    zero_dte_gex += gex if dte <= 1 else 0

        for exp_key, strikes in put_map.items():
            try:
                dte = int(exp_key.split(":")[1])
            except (IndexError, ValueError):
                continue
            if dte > 5:
                continue
            for sk, contracts in strikes.items():
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    oi    = c.get("openInterest", 0)
                    gamma = c.get("gamma", 0)
                    if oi <= 0 or gamma <= 0:
                        continue
                    gex = -gamma * oi * 100 * price / 1e6
                    try:
                        strike = float(sk)
                    except ValueError:
                        continue
                    gex_by_strike[strike] = gex_by_strike.get(strike, 0) + gex
                    total_gex    += gex
                    zero_dte_gex += gex if dte <= 1 else 0

        if not gex_by_strike:
            return None

        max_gex_strike = max(gex_by_strike, key=gex_by_strike.get)

        # Nearest significant wall: closest strike above/below price whose
        # absolute GEX exceeds 10% of the total absolute GEX (avoids tiny
        # strikes drowning out the dominant nearby wall).
        total_abs_gex = sum(abs(g) for g in gex_by_strike.values()) or 1
        min_wall_gex  = total_abs_gex * 0.10

        call_wall = None
        put_wall  = None
        for s in sorted(gex_by_strike.keys()):
            if s > price and gex_by_strike[s] > 0 and gex_by_strike[s] >= min_wall_gex:
                if call_wall is None:   # First qualifying strike above price = nearest
                    call_wall = s
            if s < price and gex_by_strike[s] < 0 and abs(gex_by_strike[s]) >= min_wall_gex:
                put_wall = s            # Last qualifying strike below price = nearest

        flip_strike = None
        prev_sign   = None
        for strike, gex in sorted(gex_by_strike.items(), key=lambda x: x[0]):
            cur_sign = 1 if gex >= 0 else -1
            if prev_sign is not None and cur_sign != prev_sign:
                if abs(strike - price) < price * 0.03:
                    flip_strike = strike
            prev_sign = cur_sign

        regime = "POSITIVE" if total_gex > 0 else "NEGATIVE"

        near = {s: abs(g) for s, g in gex_by_strike.items() if abs(s - price) < price * 0.02}
        pin_level = max(near, key=near.get) if near else price

        profile = {
            "net_gex":       round(total_gex, 2),
            "zero_dte_gex":  round(zero_dte_gex, 2),
            "regime":        regime,
            "pin_level":     round(pin_level, 2),
            "call_wall":     round(call_wall, 2)   if call_wall  else round(price * 1.005, 2),
            "put_wall":      round(put_wall, 2)    if put_wall   else round(price * 0.995, 2),
            "flip_strike":   round(flip_strike, 2) if flip_strike else None,
            "max_oi_strike": round(max_gex_strike, 2),
            "strategy_bias": "SELL_PREMIUM" if regime == "POSITIVE" else "BUY_DIRECTION",
            "gex_by_strike": gex_by_strike,  # Needed for interaction tracking
        }

        self.cache[symbol] = profile
        return profile

    # ── INTERACTION TRACKING ──────────────────────────────────────────────────

    def record_price_interaction(self, symbol, current_price, gex_profile):
        """
        Call each poll cycle with the current price.
        Detects when price approaches a GEX wall and whether it was
        rejected (snapped back) or absorbed (broke through).

        State machine per symbol:
          IDLE       → price comes within APPROACH_THRESHOLD of a wall → APPROACHING
          APPROACHING → price moves ABSORBED_THRESHOLD through wall → record "absorbed"
          APPROACHING → price retreats back from wall   → record "rejected"
          APPROACHING → new approach to different level → resolve old, start new
        """
        if not gex_profile or current_price <= 0:
            return

        call_wall = gex_profile.get("call_wall", 0)
        put_wall  = gex_profile.get("put_wall",  0)
        walls     = [w for w in (call_wall, put_wall) if w > 0]

        state = self._in_approach.get(symbol)

        if state:
            tracked_level   = state["level"]
            approach_price  = state["approach_price"]
            dist_from_wall  = abs(current_price - tracked_level) / tracked_level

            # Absorbed: price moved cleanly through the wall
            if tracked_level > approach_price:  # Was testing CALL wall (upside)
                if current_price > tracked_level * (1 + ABSORBED_THRESHOLD):
                    self._record_result(symbol, tracked_level, current_price, "absorbed")
                    del self._in_approach[symbol]
                    return
                elif current_price < approach_price * (1 - ABSORBED_THRESHOLD):
                    self._record_result(symbol, tracked_level, current_price, "rejected")
                    del self._in_approach[symbol]
                    return
            else:  # Was testing PUT wall (downside)
                if current_price < tracked_level * (1 - ABSORBED_THRESHOLD):
                    self._record_result(symbol, tracked_level, current_price, "absorbed")
                    del self._in_approach[symbol]
                    return
                elif current_price > approach_price * (1 + ABSORBED_THRESHOLD):
                    self._record_result(symbol, tracked_level, current_price, "rejected")
                    del self._in_approach[symbol]
                    return

            # Still in approach zone — check if it has shifted to a different wall
            if dist_from_wall > APPROACH_THRESHOLD * 3:
                # Price drifted away without a decisive resolution — treat as rejected
                self._record_result(symbol, tracked_level, current_price, "rejected")
                del self._in_approach[symbol]

        # Detect new approach
        for wall in walls:
            if abs(current_price - wall) / wall <= APPROACH_THRESHOLD:
                if symbol not in self._in_approach:
                    self._in_approach[symbol] = {
                        "level":          wall,
                        "approach_price": current_price,
                        "time":           _now_ct(),
                    }
                    logger.debug(
                        f"GEX approach: {symbol} price ${current_price:.2f} "
                        f"→ wall ${wall:.2f}"
                    )
                break

    def _record_result(self, symbol, level, price, result):
        key = (symbol, round(level, 2))
        self._interactions[key].append({
            "time":   _now_ct(),
            "price":  round(price, 2),
            "result": result,
        })
        logger.debug(
            f"GEX interaction: {symbol} ${level:.2f} → {result} "
            f"(total {len(self._interactions[key])} tests)"
        )

    def get_level_score(self, symbol, level):
        """
        Score a GEX wall based on its interaction history.

        Returns a dict:
          score         - integer -100 to +100
                         Positive: wall is confirmed (many rejections) → fade confidence +
                         Negative: wall was absorbed → momentum confidence +
          tests         - total interaction count
          rejections    - count of rejected tests
          absorptions   - count of absorbed tests
          recommendation- "FADE" / "MOMENTUM" / "NEUTRAL"
        """
        key = (symbol, round(level, 2))
        history = list(self._interactions.get(key, []))

        if not history:
            return {"score": 0, "tests": 0, "rejections": 0,
                    "absorptions": 0, "recommendation": "NEUTRAL"}

        rejections  = sum(1 for h in history if h["result"] == "rejected")
        absorptions = sum(1 for h in history if h["result"] == "absorbed")
        total       = len(history)

        # Score: +100 = wall always rejects, -100 = wall always absorbed
        score = round((rejections - absorptions) / total * 100)

        if score >= 40:
            recommendation = "FADE"        # Confirmed wall: fade third test
        elif score <= -40:
            recommendation = "MOMENTUM"    # Dealers are absorbing: follow through
        else:
            recommendation = "NEUTRAL"

        return {
            "score":          score,
            "tests":          total,
            "rejections":     rejections,
            "absorptions":    absorptions,
            "recommendation": recommendation,
        }

    def get_wall_context(self, symbol, price, gex_profile):
        """
        Convenience method: returns interaction scores for both walls
        and whether price is approaching either.
        """
        if not gex_profile:
            return None

        call_wall  = gex_profile.get("call_wall", 0)
        put_wall   = gex_profile.get("put_wall",  0)
        call_score = self.get_level_score(symbol, call_wall) if call_wall else None
        put_score  = self.get_level_score(symbol, put_wall)  if put_wall  else None

        approaching = self._in_approach.get(symbol, {}).get("level")

        return {
            "call_wall":        call_wall,
            "put_wall":         put_wall,
            "call_wall_score":  call_score,
            "put_wall_score":   put_score,
            "approaching_wall": approaching,
        }
