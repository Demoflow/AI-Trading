"""
Aggressive Scanner v6 - GEX + EV + Vol Regime.
"""

import os
import sys
import json
import csv
from datetime import datetime, date
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CORRELATED_GROUPS = {
    "crypto": {"MARA", "RIOT", "COIN", "MSTR"},
    "mega_tech": {"AAPL", "MSFT", "GOOGL", "AMZN", "META"},
    "semis": {"NVDA", "AMD", "INTC", "AVGO", "MRVL", "AMAT", "MU", "ARM", "SMCI", "TSM", "QCOM", "LRCX", "KLAC", "ON", "MCHP", "TXN"},
    "fintech": {"HOOD", "SOFI", "XYZ", "UPST"},
    "saas": {"SNOW", "CRWD", "PLTR", "PANW", "SHOP", "NET", "DDOG", "ZS", "CRM", "NOW"},
    "defense": {"RTX", "BA", "LMT"},
    "energy": {"XOM", "CVX", "OXY", "SLB", "DVN", "HAL", "MPC", "VLO", "BP"},
    "consumer": {"KO", "PEP", "WMT", "COST", "TGT", "PG"},
    "banks": {"JPM", "BAC", "GS", "MS", "C", "WFC", "SCHW"},
    "pharma": {"JNJ", "LLY", "ABBV", "MRK", "PFE", "BMY", "GILD"},
    "ev": {"TSLA", "RIVN", "LCID", "NIO", "LI", "XPEV"},
    "airlines": {"DAL", "UAL", "AAL"},
    "cruise": {"CCL", "NCLH", "RCL"},
    "china_tech": {"BABA", "JD", "PDD"},
    "solar": {"ENPH", "FSLR"},
    "streaming": {"NFLX", "ROKU", "FUBO"},
    "social": {"SNAP", "PINS"},
    "mining": {"FCX", "NEM", "VALE", "CLF", "AA"},
    "quantum": {"IONQ", "RGTI"},
}


def get_group(symbol):
    for name, syms in CORRELATED_GROUPS.items():
        if symbol in syms:
            return name
    return None


class AggressiveScanner:

    MAX_TRADES = 8

    def __init__(self, schwab_client, equity):
        self.client = schwab_client
        self.equity = equity
        from aggressive.flow_scanner import FlowScanner
        from aggressive.flow_persistence import FlowPersistence
        from aggressive.vol_strategy import VolatilityStrategySelector
        from aggressive.advanced_strategies import AdvancedStrategies
        from aggressive.skew_analyzer import SkewAnalyzer
        from aggressive.sector_momentum import SectorMomentum
        from aggressive.position_correlation import PositionCorrelation
        from aggressive.account_manager import AccountManager
        from aggressive.iv_percentile import IVPercentile
        from aggressive.deep_analyzer import DeepAnalyzer
        from aggressive.strategy_engine import StrategyEngine
        from aggressive.gex_analyzer import GEXAnalyzer
        from aggressive.ev_calculator import EVCalculator
        from aggressive.vol_regime import VolRegime
        self.flow = FlowScanner(schwab_client)
        self.flow_persist = FlowPersistence()
        self.adv_strat = AdvancedStrategies()
        self.analyzer = DeepAnalyzer()
        self.strategy = StrategyEngine(schwab_client)
        self.gex = GEXAnalyzer()
        self.ev_calc = EVCalculator()
        self.vol = VolRegime()

    def _get_existing_positions(self):
        syms = []
        for path in ["config/paper_options.json", "config/aggressive_positions.json"]:
            if os.path.exists(path):
                try:
                    with open(path) as f:
                        data = json.load(f)
                    positions = data if isinstance(data, list) else data.get("positions", [])
                    for p in positions:
                        if isinstance(p, dict) and p.get("status") == "OPEN":
                            syms.append(p.get("underlying", p.get("symbol", "")))
                except Exception:
                    pass
        return syms

    def _get_vix(self):
        try:
            import httpx
            resp = self.client.get_quote("$VIX")
            if resp.status_code == httpx.codes.OK:
                data = resp.json()
                return data.get("$VIX", {}).get("quote", {}).get("lastPrice", 20)
        except Exception:
            pass
        return 20

    def run(self):
        logger.info("=" * 60)
        logger.info("ELITE v6 - GEX + EV")
        logger.info(f"Equity: ${self.equity:,.2f}")
        logger.info("=" * 60)

        symbols = []
        with open("config/universe.csv") as f:
            for r in csv.DictReader(f):
                symbols.append(r["symbol"])

        logger.info(f"Universe: {len(symbols)} symbols")

        existing = self._get_existing_positions()
        if existing:
            logger.info(f"Holding: {existing}")

        vix = self._get_vix()
        logger.info(f"VIX: {vix:.1f}")

        # Volatility regime
        vol_regime = self.vol.classify(vix)

        # Flow scan
        logger.info("Step 1: Flow scan...")
        flow_results = self.flow.scan_universe(symbols)

        if not flow_results:
            logger.warning("No flow signals. Check API connection.")
            self._save_empty(vix, vol_regime, len(symbols))
            return []

        # Price data
        logger.info("Step 2: Loading data...")
        sig = None
        spy_df = None
        price_cache = {}
        try:
            from analysis.signals.signal_generator import SignalGenerator
            sig = SignalGenerator()
            spy_df = sig.load_price_data("SPY")
            for flow in flow_results:
                sym = flow["symbol"]
                if sym not in price_cache:
                    try:
                        df = sig.load_price_data(sym)
                        if df is not None and len(df) > 0:
                            price_cache[sym] = df
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"DB: {e}")

        self.analyzer.set_context(
            existing_positions=existing, spy_df=spy_df,
            vix=vix, schwab_client=self.client,
            price_data=price_cache,
        )

        # Analysis + Strategy + GEX + EV
        logger.info("Step 3: Analysis + GEX + EV...")
        trades = []
        used_groups = set()
        skipped = {"existing": 0, "corr": 0, "filter": 0, "low_score": 0, "no_strategy": 0, "negative_ev": 0}

        for sym in existing:
            g = get_group(sym)
            if g:
                used_groups.add(g)

        for flow in flow_results:
            sym = flow["symbol"]

            if sym in existing:
                skipped["existing"] += 1
                continue
            g = get_group(sym)
            if g and g in used_groups:
                skipped["corr"] += 1
                continue

            try:
                df = price_cache.get(sym)
                analysis = self.analyzer.analyze(sym, df, spy_df, flow, flow.get("chain_data"))
                if not analysis:
                    skipped["filter"] += 1
                    continue
                if analysis["conviction"] in ("SKIP", "LOW", "MEDIUM") or analysis["composite"] < 85:
                    skipped["low_score"] += 1
                    continue

                # Priority 6: Multi-day flow persistence check
                try:
                    persist = self.flow_persist.get_persistence(sym, flow["direction"])
                    persistence_boost = persist["persistence_boost"]
                    if persist["is_persistent"]:
                        logger.info(f"    PERSISTENT FLOW: {sym} {persist['consecutive_days']}d streak +{persistence_boost}pts")
                except Exception:
                    persistence_boost = 0

                # v7 Edge 2: Skew analysis
                try:
                    chain = flow.get("chain_data")
                    skew_result = self.skew.analyze(chain, analysis["price"])
                    skew_boost = 0
                    if skew_result["direction_bias"] == flow["direction"]:
                        skew_boost = skew_result["confidence_boost"]
                    elif skew_result["direction_bias"] != "NEUTRAL" and skew_result["direction_bias"] != flow["direction"]:
                        skew_boost = -skew_result["confidence_boost"]
                except Exception:
                    skew_boost = 0

                # v7 Edge 4: Sector momentum
                try:
                    sector_boost = self.sector_mom.get_boost(sym, flow["direction"])
                except Exception:
                    sector_boost = 0

                # v7 Edge 5: Correlation check with existing positions
                try:
                    corr_ok, corr_reason = self.pos_corr.check(sym, flow["direction"])
                    if not corr_ok:
                        skipped["corr"] += 1
                        continue
                except Exception:
                    pass

                # Edge 1: Earnings buffer - skip if earnings within 7 days
                earnings_buffer = False
                try:
                    from aggressive.econ_calendar import EconCalendar
                    # Check if symbol has earnings in next 7 days
                    import datetime
                    today = datetime.date.today()
                    for days_ahead in range(7):
                        check_date = today + datetime.timedelta(days=days_ahead)
                        # The flow scanner already checks earnings, but we add buffer
                    if flow.get("days_to_earnings", 999) < 7:
                        earnings_buffer = True
                except Exception:
                    pass
                if earnings_buffer:
                    skipped["filter"] += 1
                    continue


                    skipped["low_score"] += 1
                    continue

                iv_rank = 50
                if self.analyzer.iv_analyzer:
                    iv_rank = self.analyzer.iv_analyzer.get_iv_rank(sym)

                days_to_cat = 999
                try:
                    from utils.earnings_calendar import EarningsCalendar
                    ecal = EarningsCalendar()
                    days_to_cat = ecal.days_to_earnings(sym)
                    if days_to_cat < 0:
                        days_to_cat = 999
                except Exception:
                    pass

                # GEX analysis
                chain = flow.get("chain_data")
                gex_profile = self.gex.analyze(sym, chain, analysis["price"])
                gex_regime = gex_profile["regime"] if gex_profile else None

                max_cost = min(self.equity * analysis["size_pct"], self.equity * 0.10)  # Cap at 10%% of equity

                # Apply vol regime sizing
                max_cost *= vol_regime["size_modifier"]

                strategy = self.strategy.select_strategy(
                    symbol=sym, direction=analysis["direction"],
                    chain_data=chain, price=analysis["price"],
                    conviction_score=analysis["composite"],
                    iv_rank=iv_rank,
                    support=analysis["levels"]["support"],
                    resistance=analysis["levels"]["resistance"],
                    days_to_catalyst=days_to_cat,
                    max_cost=max_cost, equity=self.equity,
                )

                if not strategy:
                    skipped["no_strategy"] += 1
                    continue

                # EV calculation
                ev_result = self.ev_calc.calculate_ev(
                    strategy=strategy,
                    conviction=analysis["composite"],
                    flow_strength=flow["signal_strength"],
                    iv_rank=iv_rank,
                    direction=analysis["direction"],
                    gex_regime=gex_regime,
                )

                ev_ok, ev_reason = self.ev_calc.should_trade(ev_result)
                if not ev_ok:
                    skipped["negative_ev"] += 1
                    logger.debug(f"  Skip {sym}: {ev_reason}")
                    continue

                trade = {
                    "symbol": sym,
                    "direction": analysis["direction"],
                    "conviction": analysis["conviction"],
                    "composite": analysis["composite"],
                    "strategy": strategy,
                    "analysis": analysis,
                    "iv_rank": round(iv_rank, 1),
                    "gex": gex_profile,
                    "ev": ev_result,
                    "vol_regime": vol_regime,
                    "flow_summary": {
                        "strength": flow["signal_strength"],
                        "cp_ratio": flow["cp_ratio"],
                        "total_premium": flow["total_premium"],
                        "opening_pct": flow.get("opening_pct", 0),
                    },
                    "entry_price": analysis["price"],
                    "max_hold_days": 30,
                }
                trades.append(trade)

                if g:
                    used_groups.add(g)
                existing.append(sym)

                s = strategy
                ev = ev_result
                gex_str = f"GEX:{gex_regime}" if gex_regime else ""
                logger.info(
                    f"  {analysis['direction']:4s} {sym:5s} "
                    f"Score:{analysis['composite']:5.1f} "
                    f"EV:${ev['ev_dollar']:+.0f}({ev['grade']}) "
                    f"P:{ev['prob_profit']:.0%} "
                    f"{gex_str} "
                    f"| {s['type']} ${s['total_cost']:,.0f}" + (f" prem=${s.get('premium',0):,.0f}" if s.get('premium') else "")
                )
                logger.info(f"       {s['description']}")

            except Exception as e:
                logger.debug(f"Error {sym}: {e}")

        # Sort by EV, not just conviction
        trades.sort(key=lambda x: x["ev"]["ev_dollar"], reverse=True)
        trades = trades[:self.MAX_TRADES]

        total_cost = sum(t["strategy"]["total_cost"] for t in trades)

        rec = {
            "date": date.today().isoformat(),
            "equity": self.equity,
            "mode": "ELITE_v6_GEX_EV",
            "vix": vix,
            "vol_regime": vol_regime,
            "regime": self.analyzer.market_regime,
            "trades": trades,
            "total_cost": total_cost,
            "deployment_pct": round(total_cost / self.equity * 100, 1),
            "skipped": skipped,
            "universe_size": len(symbols),
            "flow_signals": len(flow_results),
        }

        os.makedirs("config", exist_ok=True)
        with open("config/aggressive_trades.json", "w") as f:
            json.dump(rec, f, indent=2, default=str)

        hdir = "config/aggressive_history"
        os.makedirs(hdir, exist_ok=True)
        with open(f"{hdir}/{date.today().isoformat()}.json", "w") as f:
            json.dump(rec, f, indent=2, default=str)

        # Print results
        logger.info("=" * 60)
        logger.info("RECOMMENDATIONS")
        logger.info(f"Regime: {self.analyzer.market_regime} | VIX: {vix:.1f} {vol_regime['signal']}")
        logger.info(f"Universe: {len(symbols)} | Flow: {len(flow_results)}")
        logger.info(f"Groups: {used_groups}")
        logger.info(
            f"Filtered: {skipped['existing']} held, "
            f"{skipped['corr']} corr, {skipped['filter']} IV/earn, "
            f"{skipped['low_score']} score, {skipped['negative_ev']} neg EV"
        )
        logger.info("=" * 60)

        for t in trades:
            s = t["strategy"]
            a = t["analysis"]
            ev = t["ev"]
            fs = t["flow_summary"]
            gp = t.get("gex")
            logger.info("")
            logger.info(f"  {t['direction']} {t['symbol']} - {t['conviction']} | Score: {a['composite']}")
            logger.info(f"  Strategy: {s['type']}")
            logger.info(f"  {s['description']}")
            logger.info(
                f"  Cost: ${s['total_cost']:,.2f} | "
                f"Max Loss: ${s.get('max_loss', 'N/A')} | "
                f"Max Profit: ${s.get('max_profit', 'N/A')}"
            )
            logger.info(
                f"  EV: ${ev['ev_dollar']:+,.2f} ({ev['grade']}) | "
                f"Prob: {ev['prob_profit']:.0%} | "
                f"R:R: {ev['risk_reward']}x | "
                f"Kelly: {ev['kelly_fraction']:.1%}"
            )
            if gp:
                logger.info(
                    f"  GEX: {gp['regime']} | "
                    f"Pin: ${gp['max_gex_strike']} | "
                    f"Call Wall: ${gp['call_wall']} | "
                    f"Put Wall: ${gp['put_wall']}"
                )
            logger.info(
                f"  Flow: str={fs['strength']} cp={fs['cp_ratio']} "
                f"open={fs['opening_pct']}% prem=${fs['total_premium']:,.0f}"
            )
            if s.get("contracts"):
                for c in s["contracts"]:
                    logger.info(
                        f"    {c.get('leg','?'):5s} "
                        f"{c.get('desc', c.get('symbol', ''))} "
                        f"d={c.get('delta', 0)} ${c.get('mid', 0)}"
                    )

        if not trades:
            logger.info("")
            logger.info("  No trades passed all filters.")

        logger.info("")
        logger.info(f"Deploy: ${total_cost:,.2f} ({rec['deployment_pct']}%)")
        logger.info(f"Cash: ${self.equity - total_cost:,.2f}")
        logger.info("=" * 60)
        return trades

    def _save_empty(self, vix, vol_regime, universe_size):
        rec = {
            "date": date.today().isoformat(),
            "equity": self.equity,
            "mode": "ELITE_v6_GEX_EV",
            "vix": vix, "vol_regime": vol_regime,
            "regime": "UNKNOWN", "trades": [],
            "total_cost": 0, "deployment_pct": 0,
            "skipped": {}, "universe_size": universe_size,
            "flow_signals": 0,
        }
        os.makedirs("config", exist_ok=True)
        with open("config/aggressive_trades.json", "w") as f:
            json.dump(rec, f, indent=2, default=str)
