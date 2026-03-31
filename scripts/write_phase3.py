"""
Portfolio Analyst v2 - Full institutional upgrade.
8 improvements: cross-account, capital efficiency, portfolio risk,
adaptive thresholds, time-weighted urgency, replacement trades,
earnings proximity, win/loss streak awareness.
"""
import os, json

analyst_v2 = '''\
"""
Portfolio Analyst v2.
Institutional-grade position review engine with cross-account
intelligence, capital efficiency scoring, and adaptive thresholds.
"""
import os
import json
import time
from datetime import date, datetime, timedelta
from loguru import logger

ANALYST_STATE_PATH = "config/analyst_state.json"


class PortfolioAnalyst:

    def __init__(self, client):
        self.client = client
        self._load_state()

    def _load_state(self):
        try:
            self.state = json.load(open(ANALYST_STATE_PATH))
        except (FileNotFoundError, json.JSONDecodeError):
            self.state = {
                "consecutive_flags": {},  # symbol -> count of consecutive flagged runs
                "sell_history": [],       # list of {symbol, date, pnl_after}
                "last_run": "",
            }

    def _save_state(self):
        json.dump(self.state, open(ANALYST_STATE_PATH, "w"), indent=2)

    def _get_quote(self, symbol):
        try:
            time.sleep(0.08)
            r = self.client.get_quote(symbol)
            if r.status_code == 200:
                return r.json().get(symbol, {}).get("quote", {})
        except Exception:
            pass
        return {}

    def _get_vix(self):
        q = self._get_quote("$VIX")
        return q.get("lastPrice", 20) if q else 20

    # ══════════════════════════════════════════
    # IMPROVEMENT 4: Adaptive thresholds
    # ══════════════════════════════════════════
    def _get_sell_threshold(self):
        """
        Adaptive sell threshold based on VIX and win streak.
        High VIX = more aggressive (lower threshold).
        Good track record = more trust (lower threshold).
        """
        vix = self._get_vix()
        base = 3

        # VIX adjustment
        if vix > 35:
            base = 2  # Very aggressive - cut losers fast
        elif vix > 28:
            base = 2  # Aggressive
        elif vix < 15:
            base = 4  # Patient in low vol

        # Win streak adjustment (Improvement 8)
        history = self.state.get("sell_history", [])
        recent = history[-5:] if len(history) >= 5 else history
        if len(recent) >= 3:
            good_sells = sum(1 for s in recent if s.get("was_good_sell", True))
            if good_sells >= 3:
                base = max(2, base - 1)  # Trust the analyst more
            elif good_sells <= 1:
                base = min(4, base + 1)  # Less trust

        return base

    def _get_warn_threshold(self):
        return max(1, self._get_sell_threshold() - 1)

    # ══════════════════════════════════════════
    # IMPROVEMENT 5: Time-weighted urgency
    # ══════════════════════════════════════════
    def _update_consecutive_flags(self, symbol, num_flags, threshold):
        cons = self.state.get("consecutive_flags", {})
        if num_flags >= self._get_warn_threshold():
            cons[symbol] = cons.get(symbol, 0) + 1
        else:
            cons[symbol] = 0
        self.state["consecutive_flags"] = cons
        self._save_state()
        return cons.get(symbol, 0)

    # ══════════════════════════════════════════
    # IMPROVEMENT 8: Record sell outcomes
    # ══════════════════════════════════════════
    def record_sell(self, symbol, sell_price, was_good_sell=True):
        history = self.state.get("sell_history", [])
        history.append({
            "symbol": symbol,
            "date": date.today().isoformat(),
            "sell_price": sell_price,
            "was_good_sell": was_good_sell,
        })
        # Keep last 20
        self.state["sell_history"] = history[-20:]
        self._save_state()

    # ══════════════════════════════════════════
    # CORE: Analyze options position
    # ══════════════════════════════════════════
    def analyze_option_position(self, position, all_positions=None,
                                 cross_account_positions=None):
        sym = position.get("underlying", "")
        csym = position.get("symbol", position.get("contract", ""))
        direction = position.get("direction", "CALL")
        stype = position.get("strategy_type", "NAKED_LONG")
        entry_cost = position.get("entry_cost", 0)
        entry_date = position.get("entry_date", "")
        qty = position.get("qty", 1)

        flags = []
        reasons = []
        score = 8  # 8 checks now

        # ── CHECK 1: THESIS VALIDITY ──
        try:
            from aggressive.flow_persistence import FlowPersistence
            fp = FlowPersistence()
            persist = fp.get_persistence(sym, direction)
            if persist["consecutive_days"] == 0 and persist["total_days_5d"] == 0:
                flags.append("NO_FLOW")
                reasons.append(f"No recent flow supporting {direction}")
                score -= 1
            elif persist["consecutive_days"] >= 2:
                reasons.append(f"Flow CONFIRMED: {persist['consecutive_days']}d streak")
        except Exception:
            pass

        # ── CHECK 2: GREEKS HEALTH ──
        if csym and ("260" in csym or "C0" in csym or "P0" in csym):
            try:
                q = self._get_quote(csym)
                if q:
                    delta = abs(q.get("delta", 0.50))
                    theta = abs(q.get("theta", 0))
                    mid = q.get("mark", 0)
                    if mid == 0:
                        mid = (q.get("bidPrice", 0) + q.get("askPrice", 0)) / 2
                    dte = q.get("daysToExpiration", 30)

                    if delta < 0.15:
                        flags.append("DELTA_DEATH")
                        reasons.append(f"Delta {delta:.2f} - losing sensitivity")
                        score -= 1

                    if mid > 0 and theta > 0:
                        theta_pct = theta / mid
                        if theta_pct > 0.05:
                            flags.append("THETA_BURN")
                            reasons.append(f"Theta {theta_pct:.1%}/day")
                            score -= 1

                    if dte < 5:
                        flags.append("EXPIRY_IMMINENT")
                        reasons.append(f"{dte} DTE remaining")
                        score -= 1

                    bid = q.get("bidPrice", 0)
                    ask = q.get("askPrice", 0)
                    if bid > 0 and ask > 0 and mid > 0:
                        spread_pct = (ask - bid) / mid
                        if spread_pct > 0.20:
                            flags.append("WIDE_SPREAD")
                            reasons.append(f"Spread {spread_pct:.0%}")
                            score -= 1
            except Exception:
                pass

        # ── CHECK 3: CROSS-ACCOUNT CORRELATION (Improvement 1) ──
        if cross_account_positions:
            try:
                for cap in cross_account_positions:
                    cap_sym = cap.get("symbol", cap.get("underlying", ""))
                    cap_dir = cap.get("direction", "")
                    cap_sector = cap.get("sector", "")

                    # Check if same sector, different direction
                    from aggressive.sector_momentum import SYMBOL_SECTOR
                    my_sector = SYMBOL_SECTOR.get(sym, "")

                    # Direct conflict: same underlying, opposite direction
                    if cap_sym == sym:
                        if (direction in ("CALL", "BULL") and cap_dir in ("PUT", "BEAR")) or \
                           (direction in ("PUT", "BEAR") and cap_dir in ("CALL", "BULL")):
                            flags.append("CROSS_ACCOUNT_CONFLICT")
                            reasons.append(f"Conflicts with {cap_sym} ({cap_dir}) in another account")
                            score -= 1
                            break

                    # Sector conflict
                    if my_sector and cap_sector and my_sector == cap_sector:
                        my_bull = direction in ("CALL", "BULL")
                        cap_bull = cap_dir in ("CALL", "BULL")
                        if my_bull != cap_bull:
                            flags.append("CROSS_SECTOR_CONFLICT")
                            reasons.append(f"Sector {my_sector}: you're {direction} here but {cap_dir} on {cap_sym}")
                            score -= 1
                            break

                    # Energy specific: check ERX/ERY vs energy stock puts/calls
                    if cap_sym in ("ERX", "SOXL", "TQQQ", "FNGU", "FAS", "NUGT", "TNA", "YINN", "DRN", "LABU"):
                        # This is a BULL LETF
                        energy_map = {"ERX": "energy", "SOXL": "semis", "TQQQ": "nasdaq",
                                      "FNGU": "fang", "FAS": "financials", "NUGT": "gold",
                                      "TNA": "smallcap", "YINN": "china", "DRN": "realestate", "LABU": "biotech"}
                        letf_sector = energy_map.get(cap_sym, "")
                        if my_sector == letf_sector and direction in ("PUT", "BEAR"):
                            flags.append("CROSS_ACCOUNT_CONFLICT")
                            reasons.append(f"Bearish {sym} conflicts with bull {cap_sym} in PCRA")
                            score -= 1
                            break

                    if cap_sym in ("ERY", "SOXS", "SQQQ", "FNGD", "FAZ", "DUST", "TZA", "YANG", "DRV", "LABD", "NVDS", "TSLS"):
                        # This is a BEAR LETF
                        bear_map = {"ERY": "energy", "SOXS": "semis", "SQQQ": "nasdaq",
                                    "FNGD": "fang", "FAZ": "financials", "DUST": "gold",
                                    "TZA": "smallcap", "YANG": "china", "DRV": "realestate",
                                    "LABD": "biotech", "NVDS": "nvidia", "TSLS": "tesla"}
                        letf_sector = bear_map.get(cap_sym, "")
                        if my_sector == letf_sector and direction in ("CALL", "BULL"):
                            flags.append("CROSS_ACCOUNT_CONFLICT")
                            reasons.append(f"Bullish {sym} conflicts with bear {cap_sym} in PCRA")
                            score -= 1
                            break
            except Exception:
                pass

        # ── CHECK 4: SECTOR ALIGNMENT ──
        try:
            from aggressive.sector_momentum import SectorMomentum, SYMBOL_SECTOR
            sector = SYMBOL_SECTOR.get(sym, "")
            if sector:
                sm = SectorMomentum(self.client)
                sm.calculate()
                boost = sm.get_boost(sym, direction)
                if boost < 0:
                    flags.append("SECTOR_AGAINST")
                    reasons.append(f"Sector momentum against {direction}")
                    score -= 1
                elif boost > 0:
                    reasons.append(f"Sector momentum SUPPORTS position")
        except Exception:
            pass

        # ── CHECK 5: VOL REGIME ──
        try:
            vix = self._get_vix()
            from aggressive.vol_strategy import VolatilityStrategySelector
            regime = VolatilityStrategySelector.get_regime(vix)
            avoid = regime.get("avoid_strategies", [])
            if stype in avoid:
                flags.append("VOL_REGIME_MISMATCH")
                reasons.append(f"VIX {vix:.0f} regime avoids {stype}")
                score -= 1
        except Exception:
            pass

        # ── CHECK 6: P&L + TIME ──
        try:
            if csym:
                q = self._get_quote(csym)
                if q:
                    mark = q.get("mark", 0)
                    if mark == 0:
                        mark = (q.get("bidPrice", 0) + q.get("askPrice", 0)) / 2
                    per_contract = entry_cost / max(abs(qty), 1) / 100 if entry_cost > 0 else 0
                    if per_contract > 0 and mark > 0:
                        pnl_pct = (mark - per_contract) / per_contract
                        if entry_date:
                            try:
                                ed = date.fromisoformat(entry_date) if isinstance(entry_date, str) else entry_date
                                days = (date.today() - ed).days
                                if days > 10 and pnl_pct < -0.25:
                                    flags.append("STALE_LOSER")
                                    reasons.append(f"Held {days}d, down {pnl_pct:.0%}")
                                    score -= 1
                            except Exception:
                                pass
        except Exception:
            pass

        # ── CHECK 7: EARNINGS PROXIMITY (Improvement 7) ──
        try:
            from letf.earnings_cluster import EARNINGS_CALENDAR_2026
            today = date.today()
            if sym in EARNINGS_CALENDAR_2026:
                for d in EARNINGS_CALENDAR_2026[sym]:
                    try:
                        ed = date.fromisoformat(d)
                        days_until = (ed - today).days
                        if 0 <= days_until <= 3:
                            flags.append("EARNINGS_IMMINENT")
                            reasons.append(f"{sym} earnings in {days_until}d - high risk")
                            score -= 1
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # ── CHECK 8: CAPITAL EFFICIENCY (Improvement 2) ──
        try:
            if csym and entry_cost > 0:
                q = self._get_quote(csym)
                if q:
                    mark = q.get("mark", 0)
                    if mark == 0:
                        mark = (q.get("bidPrice", 0) + q.get("askPrice", 0)) / 2
                    current_value = mark * abs(qty) * 100
                    if entry_date:
                        ed = date.fromisoformat(entry_date) if isinstance(entry_date, str) else entry_date
                        days = max((date.today() - ed).days, 1)
                        daily_return = (current_value - entry_cost) / entry_cost / days
                        if daily_return < -0.02 and days > 5:
                            flags.append("CAPITAL_INEFFICIENT")
                            reasons.append(f"Losing {daily_return:.1%}/day for {days}d - capital better deployed elsewhere")
                            score -= 1
                        elif daily_return > 0.01:
                            reasons.append(f"Capital efficient: {daily_return:+.1%}/day")
        except Exception:
            pass

        # ── IMPROVEMENT 5: Time-weighted urgency ──
        num_flags = len(flags)
        consecutive = self._update_consecutive_flags(sym, num_flags, self._get_sell_threshold())

        # Escalate if flagged multiple consecutive runs
        if consecutive >= 3 and num_flags >= 2:
            flags.append("PERSISTENT_WARNING")
            reasons.append(f"Flagged {consecutive} consecutive analyst runs")
            num_flags += 1

        # ── DECISION with adaptive threshold ──
        sell_threshold = self._get_sell_threshold()
        warn_threshold = self._get_warn_threshold()

        if num_flags >= sell_threshold:
            action = "SELL"
        elif num_flags >= warn_threshold:
            action = "TRIM"
        else:
            action = "HOLD"

        return {
            "symbol": sym,
            "contract": csym,
            "action": action,
            "score": score,
            "flags": flags,
            "reasons": reasons,
            "num_checks_failed": num_flags,
            "consecutive_flags": consecutive,
            "sell_threshold": sell_threshold,
        }

    # ══════════════════════════════════════════
    # CORE: Analyze LETF position
    # ══════════════════════════════════════════
    def analyze_letf_position(self, position, all_positions=None,
                               cross_account_positions=None):
        sym = position.get("symbol", "")
        sector = position.get("sector", "")
        direction = position.get("direction", "BULL")
        entry_price = position.get("entry_price", 0)
        entry_date = position.get("entry_date", "")
        leverage = position.get("leverage", 3)

        flags = []
        reasons = []
        score = 7  # 7 checks for LETFs now

        quote = self._get_quote(sym)
        current_price = quote.get("lastPrice", 0) if quote else 0

        # ── CHECK 1: SECTOR TREND ──
        try:
            from letf.sector_analyzer import SectorAnalyzer
            from letf.universe import SECTORS
            if sector in SECTORS:
                sa = SectorAnalyzer(self.client)
                result = sa.analyze_sector(sector, SECTORS[sector])
                if direction == "BULL" and result["bear_score"] > result["bull_score"] + 15:
                    flags.append("SECTOR_REVERSED")
                    reasons.append(f"Sector favors BEAR now (bull={result['bull_score']} bear={result['bear_score']})")
                    score -= 1
                elif direction == "BEAR" and result["bull_score"] > result["bear_score"] + 15:
                    flags.append("SECTOR_REVERSED")
                    reasons.append(f"Sector favors BULL now")
                    score -= 1
                else:
                    reasons.append("Sector trend confirmed")
        except Exception:
            pass

        # ── CHECK 2: MOMENTUM ──
        try:
            from letf.universe import SECTORS
            if sector in SECTORS:
                uq = self._get_quote(SECTORS[sector]["underlying"])
                if uq:
                    change = uq.get("netPercentChangeInDouble", 0)
                    if direction == "BULL" and change < -2.0:
                        flags.append("MOMENTUM_REVERSAL")
                        reasons.append(f"Underlying down {change:.1f}% today")
                        score -= 1
                    elif direction == "BEAR" and change > 2.0:
                        flags.append("MOMENTUM_REVERSAL")
                        reasons.append(f"Underlying up {change:+.1f}% today")
                        score -= 1
        except Exception:
            pass

        # ── CHECK 3: CROSS-ACCOUNT CORRELATION (Improvement 1) ──
        if cross_account_positions:
            try:
                for cap in cross_account_positions:
                    cap_sym = cap.get("underlying", cap.get("symbol", ""))
                    cap_dir = cap.get("direction", "")
                    from aggressive.sector_momentum import SYMBOL_SECTOR
                    cap_sector = SYMBOL_SECTOR.get(cap_sym, "")

                    if cap_sector == sector:
                        cap_bull = cap_dir in ("CALL", "BULL")
                        my_bull = direction == "BULL"
                        if cap_bull != my_bull:
                            flags.append("CROSS_ACCOUNT_CONFLICT")
                            reasons.append(f"Conflicts with {cap_sym} ({cap_dir}) in brokerage")
                            score -= 1
                            break
            except Exception:
                pass

        # ── CHECK 4: LEVERAGE DECAY ──
        if entry_date and current_price > 0:
            try:
                ed = date.fromisoformat(entry_date)
                days = (date.today() - ed).days
                pnl_pct = (current_price - entry_price) / entry_price
                if days > 5 and abs(pnl_pct) < 0.015:
                    flags.append("LEVERAGE_DECAY")
                    reasons.append(f"Flat {pnl_pct:+.1%} after {days}d - decay eating value")
                    score -= 1
            except Exception:
                pass

        # ── CHECK 5: VIX REGIME ──
        try:
            vix = self._get_vix()
            if vix > 35 and direction == "BULL":
                flags.append("HIGH_VIX_LONG")
                reasons.append(f"VIX {vix:.0f} - risky for bull LETF")
                score -= 1
            elif vix < 15 and direction == "BEAR":
                flags.append("LOW_VIX_SHORT")
                reasons.append(f"VIX {vix:.0f} - risky for bear LETF")
                score -= 1
        except Exception:
            pass

        # ── CHECK 6: EARNINGS PROXIMITY (Improvement 7) ──
        try:
            from letf.earnings_cluster import SectorEarningsCluster
            sec = SectorEarningsCluster()
            boost, stocks = sec.get_cluster_boost(sector, lookahead=3)
            if stocks:
                names = [s["symbol"] for s in stocks]
                reasons.append(f"EARNINGS: {', '.join(names)} reporting within 3 days")
                # Earnings could go either way - flag if we're already losing
                if current_price > 0 and entry_price > 0:
                    pnl = (current_price - entry_price) / entry_price
                    if pnl < -0.02:
                        flags.append("EARNINGS_RISK")
                        reasons.append("Losing position with imminent sector earnings")
                        score -= 1
        except Exception:
            pass

        # ── CHECK 7: CAPITAL EFFICIENCY (Improvement 2) ──
        if entry_price > 0 and current_price > 0 and entry_date:
            try:
                ed = date.fromisoformat(entry_date)
                days = max((date.today() - ed).days, 1)
                pnl_pct = (current_price - entry_price) / entry_price
                daily_return = pnl_pct / days
                if daily_return < -0.01 and days > 3:
                    flags.append("CAPITAL_INEFFICIENT")
                    reasons.append(f"Losing {daily_return:.2%}/day for {days}d")
                    score -= 1
            except Exception:
                pass

        # Time-weighted urgency
        num_flags = len(flags)
        consecutive = self._update_consecutive_flags(sym, num_flags, self._get_sell_threshold())
        if consecutive >= 3 and num_flags >= 2:
            flags.append("PERSISTENT_WARNING")
            reasons.append(f"Flagged {consecutive} consecutive runs")
            num_flags += 1

        sell_threshold = self._get_sell_threshold()
        warn_threshold = self._get_warn_threshold()

        if num_flags >= sell_threshold:
            action = "SELL"
        elif num_flags >= warn_threshold:
            action = "TRIM"
        else:
            action = "HOLD"

        return {
            "symbol": sym,
            "sector": sector,
            "direction": direction,
            "action": action,
            "score": score,
            "flags": flags,
            "reasons": reasons,
            "num_checks_failed": num_flags,
            "consecutive_flags": consecutive,
            "sell_threshold": sell_threshold,
        }

    # ══════════════════════════════════════════
    # IMPROVEMENT 6: Replacement trade identification
    # ══════════════════════════════════════════
    def find_replacement(self, freed_capital, current_trades_file="config/aggressive_trades.json"):
        """Check if there are better trades available for freed capital."""
        try:
            if os.path.exists(current_trades_file):
                trades = json.load(open(current_trades_file))
                candidates = trades.get("trades", [])
                affordable = [t for t in candidates if t.get("strategy", {}).get("total_cost", 9999) <= freed_capital]
                if affordable:
                    best = max(affordable, key=lambda t: t.get("ev", {}).get("ev_dollar", 0))
                    return {
                        "symbol": best.get("symbol", "?"),
                        "strategy": best.get("strategy", {}).get("type", "?"),
                        "cost": best.get("strategy", {}).get("total_cost", 0),
                        "ev": best.get("ev", {}).get("ev_dollar", 0),
                    }
        except Exception:
            pass
        return None

    # ══════════════════════════════════════════
    # IMPROVEMENT 3: Portfolio-level risk metrics
    # ══════════════════════════════════════════
    def portfolio_risk_summary(self, options_positions=None, letf_positions=None):
        """Calculate portfolio-level risk metrics."""
        total_bullish = 0
        total_bearish = 0
        sectors_used = {}
        total_deployed = 0

        all_pos = (options_positions or []) + (letf_positions or [])
        for p in all_pos:
            cost = p.get("entry_cost", 0) or (p.get("entry_price", 0) * p.get("qty", 0))
            total_deployed += cost
            d = p.get("direction", "")
            if d in ("CALL", "BULL"):
                total_bullish += cost
            elif d in ("PUT", "BEAR"):
                total_bearish += cost

            sector = p.get("sector", "")
            if not sector:
                from aggressive.sector_momentum import SYMBOL_SECTOR
                sector = SYMBOL_SECTOR.get(p.get("underlying", p.get("symbol", "")), "other")
            sectors_used[sector] = sectors_used.get(sector, 0) + 1

        directional_bias = "NEUTRAL"
        if total_deployed > 0:
            bull_pct = total_bullish / total_deployed
            if bull_pct > 0.70:
                directional_bias = "HEAVY_BULL"
            elif bull_pct > 0.55:
                directional_bias = "LEAN_BULL"
            elif bull_pct < 0.30:
                directional_bias = "HEAVY_BEAR"
            elif bull_pct < 0.45:
                directional_bias = "LEAN_BEAR"

        max_sector = max(sectors_used.values()) if sectors_used else 0
        concentration = "HIGH" if max_sector >= 3 else ("MODERATE" if max_sector >= 2 else "LOW")

        return {
            "total_deployed": total_deployed,
            "bullish": total_bullish,
            "bearish": total_bearish,
            "directional_bias": directional_bias,
            "sectors": sectors_used,
            "sector_concentration": concentration,
            "num_positions": len(all_pos),
        }

    # ══════════════════════════════════════════
    # MAIN: Run full analysis
    # ══════════════════════════════════════════
    def run_full_analysis(self, options_positions=None, letf_positions=None):
        results = []

        # Build cross-account position list
        cross_opts = []
        cross_letf = []
        if options_positions:
            cross_opts = [{"underlying": p.get("underlying",""), "direction": p.get("direction",""),
                          "sector": ""} for p in options_positions]
        if letf_positions:
            cross_letf = [{"symbol": p.get("symbol",""), "direction": p.get("direction",""),
                          "sector": p.get("sector","")} for p in letf_positions]

        all_cross = cross_opts + cross_letf

        logger.info("=" * 60)
        logger.info("PORTFOLIO ANALYST v2 - Full Position Review")
        vix = self._get_vix()
        threshold = self._get_sell_threshold()
        logger.info(f"VIX: {vix:.1f} | Sell threshold: {threshold} flags | Adaptive: YES")
        logger.info("=" * 60)

        # Portfolio risk summary
        risk = self.portfolio_risk_summary(options_positions, letf_positions)
        logger.info(f"Portfolio: {risk['num_positions']} positions | "
                    f"Bull ${risk['bullish']:,.0f} / Bear ${risk['bearish']:,.0f} | "
                    f"Bias: {risk['directional_bias']} | "
                    f"Concentration: {risk['sector_concentration']}")

        if options_positions:
            logger.info(f"\\nOPTIONS ({len(options_positions)}):")
            for pos in options_positions:
                result = self.analyze_option_position(pos, options_positions, all_cross)
                results.append(result)
                sym = result["symbol"]
                action = result["action"]
                score = result["score"]
                flags = result["flags"]
                consec = result["consecutive_flags"]

                icon = "SELL" if action == "SELL" else ("WARN" if action == "TRIM" else " OK ")
                consec_str = f" (run {consec})" if consec > 1 else ""
                logger.info(f"  [{icon}] {sym} score={score}/8 flags={len(flags)}{consec_str}")
                for r in result["reasons"]:
                    logger.info(f"        {r}")

                # Replacement trade suggestion
                if action == "SELL":
                    freed = pos.get("entry_cost", 0)
                    replacement = self.find_replacement(freed)
                    if replacement:
                        logger.info(f"        REPLACE WITH: {replacement['symbol']} "
                                    f"{replacement['strategy']} ${replacement['cost']:.0f} "
                                    f"EV ${replacement['ev']:+.0f}")

        if letf_positions:
            logger.info(f"\\nLETF ({len(letf_positions)}):")
            for pos in letf_positions:
                result = self.analyze_letf_position(pos, letf_positions, all_cross)
                results.append(result)
                sym = result["symbol"]
                action = result["action"]
                score = result["score"]
                consec = result["consecutive_flags"]

                icon = "SELL" if action == "SELL" else ("WARN" if action == "TRIM" else " OK ")
                consec_str = f" (run {consec})" if consec > 1 else ""
                logger.info(f"  [{icon}] {sym} ({result['direction']}) score={score}/7 flags={len(result['flags'])}{consec_str}")
                for r in result["reasons"]:
                    logger.info(f"        {r}")

        sells = [r for r in results if r["action"] == "SELL"]
        trims = [r for r in results if r["action"] == "TRIM"]
        holds = [r for r in results if r["action"] == "HOLD"]
        logger.info(f"\\nSUMMARY: {len(holds)} HOLD | {len(trims)} TRIM | {len(sells)} SELL")

        self.state["last_run"] = datetime.now().isoformat()
        self._save_state()

        return results
'''

with open("aggressive/portfolio_analyst.py", "w", encoding="utf-8") as f:
    f.write(analyst_v2)
print("Portfolio Analyst v2 - CREATED (8 improvements)")

# Initialize state file
state = {
    "consecutive_flags": {},
    "sell_history": [],
    "last_run": "",
}
json.dump(state, open("config/analyst_state.json", "w"), indent=2)
print("Created: config/analyst_state.json")

# Verify
import py_compile
for path in ["aggressive/portfolio_analyst.py", "scripts/aggressive_live.py",
             "scripts/letf_live.py", "scripts/letf_roth_live.py"]:
    if os.path.exists(path):
        try:
            py_compile.compile(path, doraise=True)
            print(f"  COMPILE: {path} OK")
        except py_compile.PyCompileError as e:
            print(f"  ERROR: {path} - {e}")

print()
print("=" * 60)
print("  PORTFOLIO ANALYST v2 - ALL 8 IMPROVEMENTS")
print("=" * 60)
print()
print("  1. Cross-account correlation (options <-> LETFs)")
print("  2. Capital efficiency scoring (daily return rate)")
print("  3. Portfolio-level risk metrics (bias, concentration)")
print("  4. Adaptive thresholds (VIX-based, 2-4 flags)")
print("  5. Time-weighted urgency (escalates over consecutive runs)")
print("  6. Replacement trade identification (suggests better use)")
print("  7. Earnings proximity check (3-day warning)")
print("  8. Win/loss streak awareness (adjusts trust level)")