"""
Backtester for Aggressive Mode.
Simulates what would have happened over the past 3 months
using the same scoring criteria and exit rules.
"""

import os
import sys
import csv
import json
from datetime import datetime, date, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()


CORRELATED_GROUPS = {
    "crypto": {"MARA", "RIOT", "COIN"},
    "mega_tech": {"AAPL", "MSFT", "GOOGL", "AMZN", "META"},
    "semis": {"NVDA", "AMD", "INTC", "AVGO", "MRVL", "AMAT", "MU", "ARM", "SMCI"},
    "fintech": {"HOOD", "SOFI", "SQ"},
    "saas": {"SNOW", "CRWD", "PLTR", "PANW", "SHOP"},
}


def get_group(sym):
    for name, syms in CORRELATED_GROUPS.items():
        if sym in syms:
            return name
    return None


class BacktestEngine:

    def __init__(self, equity=8000, days_back=90):
        self.starting_equity = equity
        self.equity = equity
        self.cash = equity
        self.days_back = days_back
        self.positions = []
        self.closed_trades = []
        self.daily_equity = []
        self.signal_log = []

        # Exit rules matching live system
        self.STOP_PCT = 0.35
        self.T1_PCT = 0.50
        self.T2_PCT = 1.00
        self.MAX_HOLD = 30
        self.DELTA = 0.55
        self.MAX_POSITIONS = 5

    def load_data(self):
        """Load all price data from database."""
        from analysis.signals.signal_generator import SignalGenerator
        sig = SignalGenerator()

        symbols = []
        with open("config/universe.csv") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r["market_cap_tier"] in ("mega", "large"):
                    symbols.append(r["symbol"])

        self.symbols = symbols
        self.price_data = {}
        self.spy_data = None

        logger.info(f"Loading price data for {len(symbols)} symbols...")
        try:
            self.spy_data = sig.load_price_data("SPY")
            for sym in symbols:
                try:
                    df = sig.load_price_data(sym)
                    if df is not None and len(df) >= 50:
                        self.price_data[sym] = df
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"DB error: {e}")
            return False

        logger.info(f"Loaded {len(self.price_data)} symbols with data")
        return True

    def analyze_day(self, sym, df, idx, spy_df, spy_idx):
        """Score a stock on a given day using same logic as live system."""
        if idx < 50 or idx >= len(df):
            return None

        row = df.iloc[idx]
        price = row["close"]
        scores = {}

        # TREND
        sma50 = df["close"].iloc[max(0,idx-49):idx+1].mean()
        sma200 = df["close"].iloc[max(0,idx-199):idx+1].mean() if idx >= 200 else sma50
        a50 = price > sma50
        a200 = price > sma200
        golden = sma50 > sma200

        if a50 and a200 and golden:
            scores["trend"] = 90
        elif a200 and golden:
            scores["trend"] = 70
        elif a200:
            scores["trend"] = 55
        elif not a50 and not a200:
            scores["trend"] = 15
        else:
            scores["trend"] = 35

        # PULLBACK
        high_20 = df["high"].iloc[max(0,idx-19):idx+1].max()
        pullback = (high_20 - price) / high_20 if high_20 > 0 else 0
        rsi = row.get("rsi_14", 50)

        if a200 and 0.03 <= pullback <= 0.12 and rsi < 45:
            scores["pullback"] = 90
        elif a200 and 0.02 <= pullback <= 0.10:
            scores["pullback"] = 75
        elif pullback < 0.02:
            scores["pullback"] = 45
        elif pullback > 0.15:
            scores["pullback"] = 20
        else:
            scores["pullback"] = 55

        # VOLUME
        vol_20d = df["volume"].iloc[max(0,idx-19):idx+1].mean()
        up_days = 0
        for i in range(max(0, idx-4), idx+1):
            r = df.iloc[i]
            if r["close"] > r["open"] and r["volume"] > vol_20d:
                up_days += 1
        scores["volume"] = 85 if up_days >= 3 else (65 if up_days >= 2 else 40)

        # RELATIVE STRENGTH
        if spy_df is not None and spy_idx >= 20 and idx >= 20:
            stk_ret = (price / df.iloc[idx-20]["close"]) - 1
            spy_ret = (spy_df.iloc[spy_idx]["close"] / spy_df.iloc[spy_idx-20]["close"]) - 1
            rs = stk_ret - spy_ret
            if rs > 0.05:
                scores["rel_strength"] = 90
            elif rs > 0.02:
                scores["rel_strength"] = 75
            elif rs > 0:
                scores["rel_strength"] = 60
            elif rs > -0.03:
                scores["rel_strength"] = 40
            else:
                scores["rel_strength"] = 20
        else:
            scores["rel_strength"] = 50

        # R:R
        atr = row.get("atr_14", price * 0.02)
        low_60 = df["low"].iloc[max(0,idx-59):idx+1].min()
        support = max(sma50, low_60)
        resistance = df["high"].iloc[max(0,idx-59):idx+1].max()
        stop_dist = price - support
        upside = resistance - price
        rr = upside / stop_dist if stop_dist > 0 else 1

        if rr > 3:
            scores["rr"] = 90
        elif rr > 2:
            scores["rr"] = 75
        elif rr > 1.5:
            scores["rr"] = 60
        elif rr > 1:
            scores["rr"] = 45
        else:
            scores["rr"] = 25

        # Simulated flow score (based on volume spike as proxy)
        vol_ratio = df["volume"].iloc[idx] / vol_20d if vol_20d > 0 else 1
        if vol_ratio > 3:
            scores["flow"] = 85
        elif vol_ratio > 2:
            scores["flow"] = 70
        elif vol_ratio > 1.5:
            scores["flow"] = 55
        else:
            scores["flow"] = 40

        # COMPOSITE
        w = {
            "trend": 0.15,
            "pullback": 0.15,
            "volume": 0.10,
            "flow": 0.30,
            "rel_strength": 0.10,
            "rr": 0.20,
        }
        composite = min(sum(scores[k] * w[k] for k in w), 100)

        # Direction: bearish if below both SMAs
        direction = "CALL"
        if not a50 and not a200:
            direction = "PUT"

        # Market regime penalty
        if spy_idx >= 50 and spy_df is not None:
            spy_price = spy_df.iloc[spy_idx]["close"]
            spy_sma20 = spy_df["close"].iloc[max(0,spy_idx-19):spy_idx+1].mean()
            spy_sma50 = spy_df["close"].iloc[max(0,spy_idx-49):spy_idx+1].mean()
            if spy_price < spy_sma20 and spy_price < spy_sma50:
                if direction == "CALL":
                    composite *= 0.85
                else:
                    composite = min(composite * 1.10, 100)

        composite = min(composite, 100)

        if composite >= 72:
            conviction = "HIGH"
        elif composite >= 64:
            conviction = "MEDIUM"
        else:
            conviction = None

        if conviction is None:
            return None

        return {
            "symbol": sym,
            "price": price,
            "composite": round(composite, 1),
            "conviction": conviction,
            "direction": direction,
            "scores": scores,
            "rsi": round(rsi, 1),
            "rr": round(rr, 2),
            "support": round(support, 2),
            "resistance": round(resistance, 2),
        }

    def estimate_option_return(self, entry_price, exit_price, direction, delta=0.55):
        """Estimate option P&L from underlying move."""
        pct_move = (exit_price - entry_price) / entry_price
        if direction == "PUT":
            pct_move = -pct_move

        # Option return ≈ delta * leverage * underlying move
        # ATM options have roughly 3-5x leverage on the underlying
        leverage = 3.5
        option_return = pct_move * leverage

        # Cap at -100% (can't lose more than premium)
        return max(option_return, -1.0)

    def simulate_position(self, signal, df, entry_idx):
        """Simulate holding a position with exit rules."""
        entry_price = signal["price"]
        direction = signal["direction"]
        # Estimated option cost (ATM, ~30 DTE)
        option_cost = entry_price * 0.05  # ~5% of underlying
        qty = max(1, int((self.equity * 0.06) / (option_cost * 100)))
        total_cost = qty * option_cost * 100

        if total_cost > self.cash:
            qty = max(1, int(self.cash / (option_cost * 100)))
            total_cost = qty * option_cost * 100

        if total_cost > self.cash or total_cost < 50:
            return None

        best_return = 0
        t1_hit = False
        exit_idx = None
        exit_reason = None
        exit_price = entry_price

        for day in range(1, min(self.MAX_HOLD + 1, len(df) - entry_idx)):
            idx = entry_idx + day
            if idx >= len(df):
                break

            current_price = df.iloc[idx]["close"]
            opt_ret = self.estimate_option_return(
                entry_price, current_price, direction
            )
            best_return = max(best_return, opt_ret)

            # Stop loss
            if opt_ret <= -self.STOP_PCT:
                exit_idx = idx
                exit_price = current_price
                exit_reason = "STOP"
                break

            # T1: scale out half at +50%
            if not t1_hit and opt_ret >= self.T1_PCT:
                t1_hit = True

            # T2: close all at +100%
            if opt_ret >= self.T2_PCT:
                exit_idx = idx
                exit_price = current_price
                exit_reason = "TARGET"
                break

            # Trailing stop after T1
            if t1_hit and opt_ret < best_return - 0.25:
                exit_idx = idx
                exit_price = current_price
                exit_reason = "TRAIL"
                break

        # Time stop
        if exit_idx is None:
            end = min(entry_idx + self.MAX_HOLD, len(df) - 1)
            exit_price = df.iloc[end]["close"]
            exit_reason = "TIME"
            exit_idx = end

        final_return = self.estimate_option_return(
            entry_price, exit_price, direction
        )

        # If T1 was hit, we sold half at +50%
        if t1_hit and exit_reason != "TARGET":
            half_profit = total_cost * 0.5 * 0.50
            other_half = (total_cost * 0.5) * (1 + final_return)
            pnl = half_profit + other_half - total_cost
        else:
            pnl = total_cost * final_return

        entry_date = str(df.iloc[entry_idx].get("date", ""))
        exit_date = str(df.iloc[exit_idx].get("date", ""))
        hold_days = exit_idx - entry_idx

        return {
            "symbol": signal["symbol"],
            "direction": direction,
            "conviction": signal["conviction"],
            "composite": signal["composite"],
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "entry_date": entry_date,
            "exit_date": exit_date,
            "hold_days": hold_days,
            "option_cost": round(total_cost, 2),
            "pnl": round(pnl, 2),
            "return_pct": round(final_return * 100, 1),
            "exit_reason": exit_reason,
            "t1_hit": t1_hit,
            "rsi": signal["rsi"],
            "rr": signal["rr"],
        }

    def run(self):
        logger.info("=" * 60)
        logger.info("BACKTEST - AGGRESSIVE MODE")
        logger.info(f"Period: {self.days_back} days")
        logger.info(f"Starting equity: ${self.starting_equity:,.2f}")
        logger.info("=" * 60)

        if not self.load_data():
            return

        spy_df = self.spy_data
        if spy_df is None or len(spy_df) < 100:
            logger.error("Need SPY data")
            return

        # Find date range
        total_bars = len(spy_df)
        start_idx = max(50, total_bars - self.days_back)

        logger.info(f"Simulating from bar {start_idx} to {total_bars}")
        logger.info(f"Scanning {len(self.price_data)} symbols each day")
        logger.info("")

        # Track active positions by symbol
        active_symbols = set()
        used_groups = set()

        for day_idx in range(start_idx, total_bars - 5):
            spy_date = spy_df.iloc[day_idx].get("date", "")

            # Check exits first
            for pos in list(self.positions):
                sym = pos["symbol"]
                if sym not in self.price_data:
                    continue
                df = self.price_data[sym]
                # Find matching index
                entry_bar = pos.get("entry_bar", 0)
                days_held = day_idx - entry_bar
                if days_held <= 0:
                    continue

                current_idx = min(entry_bar + days_held, len(df) - 1)
                if current_idx >= len(df):
                    continue

                current_price = df.iloc[current_idx]["close"]
                opt_ret = self.estimate_option_return(
                    pos["entry_price"], current_price, pos["direction"]
                )

                should_exit = False
                reason = ""

                if opt_ret <= -self.STOP_PCT:
                    should_exit = True
                    reason = "STOP"
                elif opt_ret >= self.T2_PCT:
                    should_exit = True
                    reason = "TARGET"
                elif pos.get("t1_hit") and opt_ret < pos.get("best_ret", 0) - 0.25:
                    should_exit = True
                    reason = "TRAIL"
                elif days_held >= self.MAX_HOLD:
                    should_exit = True
                    reason = "TIME"

                if not pos.get("t1_hit") and opt_ret >= self.T1_PCT:
                    pos["t1_hit"] = True

                pos["best_ret"] = max(pos.get("best_ret", 0), opt_ret)

                if should_exit:
                    if pos.get("t1_hit") and reason != "TARGET":
                        half_profit = pos["cost"] * 0.5 * 0.50
                        other_half = (pos["cost"] * 0.5) * (1 + opt_ret)
                        pnl = half_profit + other_half - pos["cost"]
                    else:
                        pnl = pos["cost"] * opt_ret

                    self.cash += pos["cost"] + pnl
                    active_symbols.discard(sym)
                    g = get_group(sym)
                    if g:
                        used_groups.discard(g)

                    trade = {
                        "symbol": sym,
                        "direction": pos["direction"],
                        "conviction": pos["conviction"],
                        "entry_price": pos["entry_price"],
                        "exit_price": round(current_price, 2),
                        "entry_date": pos["entry_date"],
                        "exit_date": str(spy_date),
                        "hold_days": days_held,
                        "cost": pos["cost"],
                        "pnl": round(pnl, 2),
                        "return_pct": round(opt_ret * 100, 1),
                        "exit_reason": reason,
                    }
                    self.closed_trades.append(trade)
                    self.positions.remove(pos)

            # Scan for new entries (once per week-ish to be realistic)
            if day_idx % 1 != 0:
                continue
            if len(self.positions) >= self.MAX_POSITIONS:
                continue

            # Find spy index
            spy_idx = day_idx

            # Score all symbols
            signals = []
            for sym, df in self.price_data.items():
                if sym in active_symbols:
                    continue
                g = get_group(sym)
                if g and g in used_groups:
                    continue

                # Find the bar in this stock's data closest to spy_idx
                sym_idx = min(day_idx, len(df) - 6)
                if sym_idx < 50:
                    continue

                result = self.analyze_day(sym, df, sym_idx, spy_df, spy_idx)
                if result:
                    signals.append((result, sym_idx))

            # Sort by score, take top ones
            signals.sort(key=lambda x: x[0]["composite"], reverse=True)

            for signal, sym_idx in signals[:3]:
                if len(self.positions) >= self.MAX_POSITIONS:
                    break
                sym = signal["symbol"]
                if sym in active_symbols:
                    continue
                g = get_group(sym)
                if g and g in used_groups:
                    continue

                entry_price = signal["price"]
                option_cost = entry_price * 0.05
                qty = max(1, int((self.equity * 0.06) / max(option_cost * 100, 1)))
                total_cost = qty * option_cost * 100

                if total_cost > self.cash or total_cost < 50:
                    continue

                self.cash -= total_cost
                pos = {
                    "symbol": sym,
                    "direction": signal["direction"],
                    "conviction": signal["conviction"],
                    "entry_price": entry_price,
                    "entry_date": str(spy_date),
                    "entry_bar": day_idx,
                    "cost": round(total_cost, 2),
                    "t1_hit": False,
                    "best_ret": 0,
                }
                self.positions.append(pos)
                active_symbols.add(sym)
                if g:
                    used_groups.add(g)

                self.signal_log.append({
                    "date": str(spy_date),
                    "symbol": sym,
                    "direction": signal["direction"],
                    "conviction": signal["conviction"],
                    "score": signal["composite"],
                    "price": round(entry_price, 2),
                    "rsi": signal["rsi"],
                    "rr": signal["rr"],
                })

            # Track daily equity
            deployed = sum(p["cost"] for p in self.positions)
            self.daily_equity.append({
                "date": str(spy_date),
                "cash": round(self.cash, 2),
                "deployed": round(deployed, 2),
                "total": round(self.cash + deployed, 2),
                "positions": len(self.positions),
            })

        # Force close remaining positions
        for pos in list(self.positions):
            sym = pos["symbol"]
            if sym in self.price_data:
                df = self.price_data[sym]
                last_price = df.iloc[-1]["close"]
                opt_ret = self.estimate_option_return(
                    pos["entry_price"], last_price, pos["direction"]
                )
                pnl = pos["cost"] * opt_ret
                self.cash += pos["cost"] + pnl
                self.closed_trades.append({
                    "symbol": sym,
                    "direction": pos["direction"],
                    "conviction": pos["conviction"],
                    "entry_price": pos["entry_price"],
                    "exit_price": round(last_price, 2),
                    "entry_date": pos["entry_date"],
                    "exit_date": "END",
                    "hold_days": 0,
                    "cost": pos["cost"],
                    "pnl": round(pnl, 2),
                    "return_pct": round(opt_ret * 100, 1),
                    "exit_reason": "END",
                })

        self.positions = []
        self.equity = self.cash
        self._print_results()
        self._save_results()

    def _print_results(self):
        total_trades = len(self.closed_trades)
        if total_trades == 0:
            logger.info("No trades generated.")
            return

        wins = [t for t in self.closed_trades if t["pnl"] > 0]
        losses = [t for t in self.closed_trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in self.closed_trades)

        win_rate = len(wins) / total_trades if total_trades > 0 else 0
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        profit_factor = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 999

        best = max(self.closed_trades, key=lambda t: t["pnl"])
        worst = min(self.closed_trades, key=lambda t: t["pnl"])

        # Max drawdown
        peak = self.starting_equity
        max_dd = 0
        for d in self.daily_equity:
            peak = max(peak, d["total"])
            dd = (peak - d["total"]) / peak
            max_dd = max(max_dd, dd)

        final_equity = self.cash
        total_return = (final_equity - self.starting_equity) / self.starting_equity

        print()
        print("=" * 58)
        print("  BACKTEST RESULTS")
        print("=" * 58)
        print(f"  Period:          {self.days_back} days")
        print(f"  Starting Equity: ${self.starting_equity:>10,.2f}")
        print(f"  Final Equity:    ${final_equity:>10,.2f}")
        print(f"  Total Return:    {total_return:>+10.1%}")
        print(f"  Total P&L:       ${total_pnl:>+10,.2f}")
        print()
        print(f"  Total Trades:    {total_trades:>10}")
        print(f"  Wins:            {len(wins):>10}")
        print(f"  Losses:          {len(losses):>10}")
        print(f"  Win Rate:        {win_rate:>10.0%}")
        print(f"  Avg Win:         ${avg_win:>+10,.2f}")
        print(f"  Avg Loss:        ${avg_loss:>+10,.2f}")
        print(f"  Profit Factor:   {profit_factor:>10.2f}")
        print(f"  Max Drawdown:    {max_dd:>10.1%}")
        print()
        print(f"  Best Trade:      {best['symbol']} ${best['pnl']:+,.2f} ({best['return_pct']:+.0f}%)")
        print(f"  Worst Trade:     {worst['symbol']} ${worst['pnl']:+,.2f} ({worst['return_pct']:+.0f}%)")
        print()

        # Exit reasons
        reasons = defaultdict(int)
        for t in self.closed_trades:
            reasons[t["exit_reason"]] += 1
        print("  Exit Reasons:")
        for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {r:10s}: {c}")

        # By conviction
        print()
        print("  By Conviction:")
        for conv in ["HIGH", "MEDIUM"]:
            ct = [t for t in self.closed_trades if t["conviction"] == conv]
            if ct:
                w = len([t for t in ct if t["pnl"] > 0])
                pnl = sum(t["pnl"] for t in ct)
                wr = w / len(ct)
                print(f"    {conv:8s}: {len(ct)} trades | WR: {wr:.0%} | P&L: ${pnl:+,.2f}")

        # Signal log
        print()
        print("  SIGNAL HISTORY (last 20):")
        print(f"  {'Date':12s} {'Dir':4s} {'Sym':6s} {'Score':>6s} {'Price':>8s} {'Conv':8s}")
        print(f"  {'-'*50}")
        for s in self.signal_log[-20:]:
            print(f"  {s['date']:12s} {s['direction']:4s} {s['symbol']:6s} {s['score']:>6.1f} ${s['price']:>7.2f} [{s['conviction']}]")

        # Trade log
        print()
        print("  TRADE LOG (all):")
        print(f"  {'Sym':6s} {'Dir':4s} {'Entry':>8s} {'Exit':>8s} {'Days':>5s} {'P&L':>10s} {'Ret':>7s} {'Reason':8s}")
        print(f"  {'-'*60}")
        for t in self.closed_trades:
            print(
                f"  {t['symbol']:6s} {t['direction']:4s} "
                f"${t['entry_price']:>7.2f} ${t['exit_price']:>7.2f} "
                f"{t['hold_days']:>5} ${t['pnl']:>+9,.2f} "
                f"{t['return_pct']:>+6.1f}% {t['exit_reason']:8s}"
            )

        print()
        print("=" * 58)

    def _save_results(self):
        results = {
            "period_days": self.days_back,
            "starting_equity": self.starting_equity,
            "final_equity": round(self.cash, 2),
            "total_pnl": round(self.cash - self.starting_equity, 2),
            "total_return_pct": round((self.cash - self.starting_equity) / self.starting_equity * 100, 2),
            "total_trades": len(self.closed_trades),
            "trades": self.closed_trades,
            "signals": self.signal_log,
            "daily_equity": self.daily_equity,
        }
        os.makedirs("config", exist_ok=True)
        with open("config/backtest_results.json", "w") as f:
            json.dump(results, f, indent=2, default=str)
        logger.info("Results saved to config/backtest_results.json")


if __name__ == "__main__":
    from utils.logging_setup import setup_logging
    setup_logging()

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--equity", type=float, default=8000)
    args = parser.parse_args()

    bt = BacktestEngine(equity=args.equity, days_back=args.days)
    bt.run()
