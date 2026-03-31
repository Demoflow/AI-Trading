"""
Signal Tracking Database.
Records every signal the system generates.
Tracks outcomes at 1, 3, 7, 14, 30 days.
After 50+ signals, calculates real win rates.
After 200+, feeds adaptive Kelly sizing.
"""

import os
import json
from datetime import datetime, date, timedelta
from loguru import logger


class SignalTracker:

    def __init__(self):
        self.path = "config/signal_history.json"
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"signals": [], "stats": {}}

    def _save(self):
        os.makedirs("config", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.data, f, indent=2, default=str)

    def record_signal(self, symbol, direction, strategy_type,
                      conviction, composite_score, flow_strength,
                      gex_regime, iv_rank, ev_grade, entry_price,
                      option_price, traded=False):
        """Record every signal, whether traded or not."""
        sig = {
            "id": len(self.data["signals"]) + 1,
            "date": date.today().isoformat(),
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "direction": direction,
            "strategy_type": strategy_type,
            "conviction": conviction,
            "composite_score": composite_score,
            "flow_strength": flow_strength,
            "gex_regime": gex_regime,
            "iv_rank": iv_rank,
            "ev_grade": ev_grade,
            "entry_price": round(entry_price, 2),
            "option_price": round(option_price, 2),
            "traded": traded,
            "outcomes": {},
        }
        self.data["signals"].append(sig)
        self._save()
        return sig["id"]

    def update_outcomes(self, schwab_client):
        """
        Check past signals and record what happened.
        Called during evening scan.
        """
        import httpx
        updated = 0
        for sig in self.data["signals"]:
            if not sig.get("entry_price"):
                continue

            sig_date = date.fromisoformat(sig["date"])
            days_ago = (date.today() - sig_date).days

            checkpoints = [1, 3, 7, 14, 30]
            for cp in checkpoints:
                key = f"day_{cp}"
                if key in sig.get("outcomes", {}):
                    continue
                if days_ago < cp:
                    continue

                # Get current price
                try:
                    resp = schwab_client.get_quote(sig["symbol"])
                    if resp.status_code == httpx.codes.OK:
                        data = resp.json()
                        q = data.get(sig["symbol"], {}).get("quote", {})
                        current = q.get("lastPrice", 0)
                        if current > 0:
                            entry = sig["entry_price"]
                            if sig["direction"] == "CALL":
                                pnl_pct = (current - entry) / entry * 100
                            else:
                                pnl_pct = (entry - current) / entry * 100
                            if "outcomes" not in sig:
                                sig["outcomes"] = {}
                            sig["outcomes"][key] = {
                                "price": round(current, 2),
                                "pnl_pct": round(pnl_pct, 2),
                                "win": pnl_pct > 0,
                            }
                            updated += 1
                except Exception:
                    pass

        if updated > 0:
            self._save()
            logger.info(f"Signal tracker: updated {updated} outcomes")

    def get_real_stats(self):
        """
        Calculate actual win rates from tracked outcomes.
        Returns stats by strategy type, flow strength, etc.
        """
        signals = self.data["signals"]
        if len(signals) < 10:
            return None

        stats = {
            "total_signals": len(signals),
            "traded_signals": len([s for s in signals if s.get("traded")]),
            "by_strategy": {},
            "by_flow_strength": {},
            "by_gex_regime": {},
            "by_ev_grade": {},
            "overall_win_rate": 0,
            "overall_avg_win": 0,
            "overall_avg_loss": 0,
        }

        wins = 0
        losses = 0
        win_sum = 0
        loss_sum = 0

        for sig in signals:
            outcomes = sig.get("outcomes", {})
            # Use 7-day outcome as primary metric
            result = outcomes.get("day_7", outcomes.get("day_3", outcomes.get("day_1")))
            if not result:
                continue

            is_win = result["win"]
            pnl = result["pnl_pct"]

            if is_win:
                wins += 1
                win_sum += pnl
            else:
                losses += 1
                loss_sum += abs(pnl)

            # By strategy
            st = sig.get("strategy_type", "UNKNOWN")
            if st not in stats["by_strategy"]:
                stats["by_strategy"][st] = {"wins": 0, "losses": 0, "total_pnl": 0}
            if is_win:
                stats["by_strategy"][st]["wins"] += 1
            else:
                stats["by_strategy"][st]["losses"] += 1
            stats["by_strategy"][st]["total_pnl"] += pnl

            # By flow strength
            fs = str(sig.get("flow_strength", 0))
            if fs not in stats["by_flow_strength"]:
                stats["by_flow_strength"][fs] = {"wins": 0, "losses": 0}
            if is_win:
                stats["by_flow_strength"][fs]["wins"] += 1
            else:
                stats["by_flow_strength"][fs]["losses"] += 1

            # By GEX regime
            gex = sig.get("gex_regime", "UNKNOWN")
            if gex not in stats["by_gex_regime"]:
                stats["by_gex_regime"][gex] = {"wins": 0, "losses": 0}
            if is_win:
                stats["by_gex_regime"][gex]["wins"] += 1
            else:
                stats["by_gex_regime"][gex]["losses"] += 1

            # By EV grade
            ev = sig.get("ev_grade", "?")
            if ev not in stats["by_ev_grade"]:
                stats["by_ev_grade"][ev] = {"wins": 0, "losses": 0}
            if is_win:
                stats["by_ev_grade"][ev]["wins"] += 1
            else:
                stats["by_ev_grade"][ev]["losses"] += 1

        total = wins + losses
        if total > 0:
            stats["overall_win_rate"] = round(wins / total, 3)
            stats["overall_avg_win"] = round(win_sum / max(wins, 1), 2)
            stats["overall_avg_loss"] = round(loss_sum / max(losses, 1), 2)

        # Calculate win rates per bucket
        for bucket_name in ["by_strategy", "by_flow_strength", "by_gex_regime", "by_ev_grade"]:
            for key, vals in stats[bucket_name].items():
                t = vals["wins"] + vals["losses"]
                vals["win_rate"] = round(vals["wins"] / t, 3) if t > 0 else 0
                vals["total"] = t

        self.data["stats"] = stats
        self._save()
        return stats

    def get_kelly_inputs(self):
        """
        Get real win rate and win/loss ratio for Kelly sizing.
        Returns None if insufficient data.
        """
        stats = self.get_real_stats()
        if not stats or stats["total_signals"] < 50:
            return None

        wr = stats["overall_win_rate"]
        avg_win = stats["overall_avg_win"]
        avg_loss = stats["overall_avg_loss"]

        if avg_loss <= 0 or wr <= 0:
            return None

        b = avg_win / avg_loss  # Win/loss ratio
        kelly = (b * wr - (1 - wr)) / b
        # Use 1/3 Kelly (fractional)
        fractional = max(0, min(0.25, kelly / 3))

        return {
            "win_rate": wr,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "full_kelly": round(kelly, 4),
            "fractional_kelly": round(fractional, 4),
            "sample_size": stats["total_signals"],
        }
