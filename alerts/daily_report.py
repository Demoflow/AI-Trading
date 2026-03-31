"""
End-of-Day Report Builder.
"""

import json
from datetime import datetime, date, timedelta
from pathlib import Path
from loguru import logger


class DailyReportBuilder:

    def build(self, portfolio, watchlist=None, equity=0, peak=0, dt_status=None, breaker=None):
        today = date.today().strftime("%A, %B %d, %Y")
        lines = []
        lines.append("=" * 55)
        lines.append(f"  DAILY TRADING REPORT - {today}")
        lines.append("=" * 55)
        lines.append(f"  Equity:      ${equity:>12,.2f}")
        if peak > 0:
            dd = (peak - equity) / peak
            lines.append(f"  Peak:        ${peak:>12,.2f}")
            lines.append(f"  Drawdown:    {dd:>12.1%}")
        dep = portfolio.get("total_deployed", 0)
        cash = equity - dep
        if equity > 0:
            lines.append(f"  Deployed:    ${dep:>12,.2f} ({dep/equity:.0%})")
            lines.append(f"  Cash:        ${cash:>12,.2f} ({cash/equity:.0%})")
        upnl = portfolio.get("total_unrealized_pnl", 0)
        rpnl = portfolio.get("total_realized_pnl", 0)
        lines.append(f"  Unrealized:  ${upnl:>+12,.2f}")
        lines.append(f"  Realized:    ${rpnl:>+12,.2f}")
        positions = portfolio.get("positions", {})
        lines.append(f"\n  Open Positions: {len(positions)}")
        for k, v in positions.items():
            lines.append(f"    {v['symbol']:5s} {v['instrument']:5s} {v['quantity']:>3} @ ${v['entry']:.2f} stop ${v['stop']:.2f} day {v['days_held']}")
        if dt_status:
            lines.append(f"\n  Day Trades: {dt_status['remaining']}/3 remaining")
        if breaker and breaker.get("halted"):
            lines.append(f"\n  *** TRADING HALTED: {breaker['halt_reason']} ***")
        if watchlist:
            s = watchlist.get("summary", {})
            lines.append(f"\n  Tomorrow: {s.get('options_picks', 0)} opts, {s.get('stock_picks', 0)} stk, {s.get('etf_picks', 0)} etf")
            lines.append(f"  Planned: ${s.get('total_planned_deployment', 0):,.0f} ({s.get('deployment_pct', 0):.0f}%)")
        lines.append("=" * 55)
        return "\n".join(lines)

    def build_and_send(self, notifier, **kwargs):
        report = self.build(**kwargs)
        today = date.today().strftime("%Y-%m-%d")
        notifier.send("DAILY_REPORT", today, report)
        logger.info("Daily report sent")
        return report
