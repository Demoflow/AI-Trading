"""
Stock Universe v1.0 — Static universe with dynamic screening for VWAP stock scalping.

Hardcoded universe of high-liquidity stocks with research-backed VWAP reliability
scores, implied daily moves, and institutional ownership percentages.

Position limits scale with volatility to keep dollar risk constant across names.
"""

from loguru import logger


# ── UNIVERSE DEFINITION ──────────────────────────────────────────────────────
# implied_move_pct: ~1 SD expected daily range from IV
# vwap_score: 0-100 historical VWAP respect (higher = more reliable)
# inst_own: institutional ownership fraction
# adv_shares: average daily volume in shares
# vwap_proxy: if not None, use this symbol's VWAP as signal (e.g., TQQQ uses QQQ)

UNIVERSE = {
    # Tier 1 — Primary (trade every day)
    "SPY":   {"tier": 1, "implied_move_pct": 0.85, "vwap_score": 95, "inst_own": 0.95, "adv_shares": 70_000_000, "vwap_proxy": None, "trending_only": False},
    "QQQ":   {"tier": 1, "implied_move_pct": 1.10, "vwap_score": 92, "inst_own": 0.90, "adv_shares": 45_000_000, "vwap_proxy": None, "trending_only": False},
    "NVDA":  {"tier": 1, "implied_move_pct": 3.50, "vwap_score": 82, "inst_own": 0.65, "adv_shares": 50_000_000, "vwap_proxy": None, "trending_only": False},
    "META":  {"tier": 1, "implied_move_pct": 2.20, "vwap_score": 88, "inst_own": 0.80, "adv_shares": 20_000_000, "vwap_proxy": None, "trending_only": False},
    "AAPL":  {"tier": 1, "implied_move_pct": 1.40, "vwap_score": 90, "inst_own": 0.60, "adv_shares": 60_000_000, "vwap_proxy": None, "trending_only": False},
    # Tier 2 — Secondary (trade on trending/volatile days)
    "MSFT":  {"tier": 2, "implied_move_pct": 1.20, "vwap_score": 88, "inst_own": 0.72, "adv_shares": 25_000_000, "vwap_proxy": None, "trending_only": False},
    "GOOGL": {"tier": 2, "implied_move_pct": 1.50, "vwap_score": 85, "inst_own": 0.60, "adv_shares": 25_000_000, "vwap_proxy": None, "trending_only": False},
    "AMZN":  {"tier": 2, "implied_move_pct": 1.80, "vwap_score": 83, "inst_own": 0.60, "adv_shares": 40_000_000, "vwap_proxy": None, "trending_only": False},
    "AMD":   {"tier": 2, "implied_move_pct": 3.00, "vwap_score": 78, "inst_own": 0.70, "adv_shares": 50_000_000, "vwap_proxy": None, "trending_only": False},
    "IWM":   {"tier": 2, "implied_move_pct": 1.50, "vwap_score": 88, "inst_own": 0.85, "adv_shares": 32_000_000, "vwap_proxy": None, "trending_only": False},
    "XLF":   {"tier": 2, "implied_move_pct": 1.20, "vwap_score": 88, "inst_own": 0.92, "adv_shares": 45_000_000, "vwap_proxy": None, "trending_only": False},
    # Tier 2 — Trending-only (directional conviction required)
    "XLE":   {"tier": 2, "implied_move_pct": 1.50, "vwap_score": 85, "inst_own": 0.90, "adv_shares": 28_000_000, "vwap_proxy": None, "trending_only": True},
    "AVGO":  {"tier": 2, "implied_move_pct": 2.50, "vwap_score": 80, "inst_own": 0.80, "adv_shares": 18_000_000, "vwap_proxy": None, "trending_only": True},
    # Tier 3 — High vol, low VWAP reliability
    "TSLA":  {"tier": 3, "implied_move_pct": 4.50, "vwap_score": 58, "inst_own": 0.44, "adv_shares": 80_000_000, "vwap_proxy": None, "trending_only": False},
    # ETF Volatility Plays (VIX > 20)
    "TQQQ":  {"tier": 2, "implied_move_pct": 3.30, "vwap_score": 75, "inst_own": 0.70, "adv_shares": 60_000_000, "vwap_proxy": "QQQ",  "trending_only": False},
    "SOXL":  {"tier": 2, "implied_move_pct": 5.00, "vwap_score": 70, "inst_own": 0.65, "adv_shares": 30_000_000, "vwap_proxy": "SMH",  "trending_only": False},
}


class StockUniverse:
    """
    Manages the stock universe, dynamic symbol selection, position limits,
    and stop distances.
    """

    def __init__(self):
        self.universe = dict(UNIVERSE)

    def get_all_symbols(self):
        """Return all symbols in the universe."""
        return list(self.universe.keys())

    def get_proxy_symbols(self):
        """Return set of proxy symbols needed for VWAP calculation (e.g., SMH for SOXL)."""
        proxies = set()
        for info in self.universe.values():
            if info["vwap_proxy"]:
                proxies.add(info["vwap_proxy"])
        return proxies

    def get_all_tracked_symbols(self):
        """Return all symbols including proxy symbols needed for VWAP tracking."""
        syms = set(self.universe.keys())
        syms.update(self.get_proxy_symbols())
        return sorted(syms)

    def get_info(self, symbol):
        """Return universe info dict for a symbol, or None."""
        return self.universe.get(symbol)

    def get_vwap_proxy(self, symbol):
        """Return the VWAP proxy symbol, or the symbol itself if no proxy."""
        info = self.universe.get(symbol)
        if info and info["vwap_proxy"]:
            return info["vwap_proxy"]
        return symbol

    # ── ACTIVE SYMBOL SELECTION ──────────────────────────────────────────────

    def get_active_symbols(self, day_type="UNKNOWN", gex_regime="NEUTRAL", vix_level=15):
        """
        Return list of symbols to actively scan for signals.

        Args:
            day_type: TRENDING, CHOPPY, QUIET, VOLATILE, RANGE_BOUND, UNKNOWN
            gex_regime: POSITIVE, NEGATIVE, NEUTRAL
            vix_level: current VIX value

        Returns:
            List of symbol strings, priority-ordered.
        """
        active = []

        # Tier 1 always included
        for sym, info in self.universe.items():
            if info["tier"] == 1:
                active.append(sym)

        # Tier 2 on TRENDING or VOLATILE days
        if day_type in ("TRENDING", "VOLATILE", "UNKNOWN"):
            for sym, info in self.universe.items():
                if info["tier"] == 2 and sym not in ("TQQQ", "SOXL"):
                    # Trending-only symbols require explicit directional day
                    if info.get("trending_only") and day_type != "TRENDING":
                        continue
                    if sym not in active:
                        active.append(sym)

        # TSLA only on VOLATILE days with VIX > 20
        if day_type == "VOLATILE" and vix_level > 20:
            if "TSLA" not in active:
                active.append("TSLA")

        # Leveraged ETFs only when VIX > 20
        if vix_level > 20:
            for sym in ("TQQQ", "SOXL"):
                if sym not in active:
                    active.append(sym)

        # On QUIET/CHOPPY days: Tier 1 only, prioritize SPY and QQQ
        if day_type in ("QUIET", "CHOPPY", "RANGE_BOUND"):
            active = [s for s in active if self.universe.get(s, {}).get("tier") == 1]
            # Ensure SPY and QQQ are first
            for prioritize in ("QQQ", "SPY"):
                if prioritize in active:
                    active.remove(prioritize)
                    active.insert(0, prioritize)

        return active

    # ── POSITION LIMITS ──────────────────────────────────────────────────────

    def get_position_limit(self, symbol, equity):
        """
        Max dollar notional for a single position in this symbol.
        Scales inversely with implied volatility to keep risk constant.

        Args:
            symbol: ticker string
            equity: current account equity

        Returns:
            Maximum dollar notional for the position.
        """
        info = self.universe.get(symbol)
        if not info:
            return equity * 0.10  # Conservative fallback

        tier = info["tier"]
        imp = info["implied_move_pct"]

        if tier == 1:
            # Up to 4x equity, but capped by volatility-adjusted risk
            leverage_cap = equity * 4
            risk_cap = equity * 0.40 / imp * 100 if imp > 0 else leverage_cap
            return min(leverage_cap, risk_cap)
        elif tier == 2:
            leverage_cap = equity * 3
            risk_cap = equity * 0.30 / imp * 100 if imp > 0 else leverage_cap
            return min(leverage_cap, risk_cap)
        else:  # tier 3 (TSLA)
            leverage_cap = equity * 2
            risk_cap = equity * 0.20 / imp * 100 if imp > 0 else leverage_cap
            return min(leverage_cap, risk_cap)

    # ── STOP DISTANCE ────────────────────────────────────────────────────────

    def get_stop_distance_pct(self, symbol):
        """
        Returns stop distance as a percentage of price.
        Tier 1: implied_move × 0.10, clamped [0.08%, 0.40%]
        Tier 2-3: implied_move × 0.12, clamped [0.10%, 0.50%]

        The higher multiplier for Tier 2-3 prevents premature stop-outs on
        volatile names (NVDA at Tier 1 excluded by design; AMD/TSLA/SOXL need
        wider stops to absorb intraday noise without exiting winning trades).
        """
        info = self.universe.get(symbol)
        if not info:
            return 0.15  # Conservative default

        tier = info["tier"]
        if tier == 1:
            raw = info["implied_move_pct"] * 0.10
            return max(0.08, min(raw, 0.40))
        else:
            raw = info["implied_move_pct"] * 0.12
            return max(0.10, min(raw, 0.50))

    # ── SYMBOL SCORING ───────────────────────────────────────────────────────

    def score_symbol(self, symbol, day_type="", gex_regime=""):
        """
        Composite score for prioritizing which signals to act on first
        when multiple fire simultaneously.

        Higher score = higher priority.
        """
        info = self.universe.get(symbol)
        if not info:
            return 0

        score = 0.0

        # Base score from VWAP reliability
        score += info["vwap_score"]

        # Tier bonus
        if info["tier"] == 1:
            score += 20
        elif info["tier"] == 2:
            score += 10

        # Liquidity bonus (higher ADV = easier fills)
        if info["adv_shares"] >= 50_000_000:
            score += 10
        elif info["adv_shares"] >= 25_000_000:
            score += 5

        # Day type alignment
        if day_type == "TRENDING" and info["implied_move_pct"] > 2.0:
            score += 10  # Higher vol names benefit more from trends
        if day_type in ("QUIET", "RANGE_BOUND") and info["vwap_score"] >= 90:
            score += 10  # High VWAP reliability on quiet days

        # GEX alignment
        if gex_regime == "NEGATIVE" and info["implied_move_pct"] > 2.0:
            score += 5  # Negative GEX amplifies moves in high-beta names

        # Institutional ownership bonus
        if info["inst_own"] >= 0.80:
            score += 5

        return score

    # ── MINIMUM CONFIDENCE THRESHOLDS ────────────────────────────────────────

    def get_min_confidence(self, symbol):
        """Return minimum signal confidence threshold for this symbol."""
        info = self.universe.get(symbol)
        if not info:
            return 80

        tier = info["tier"]
        if tier == 1:
            return 65
        elif tier == 2:
            return 72
        else:  # tier 3
            return 80
