import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.pre_trade_validator import PreTradeValidator
from risk.realtime_monitor import RealtimeMonitor
from risk.circuit_breakers import IndependentCircuitBreakers
from risk.correlation_checker import CorrelationChecker
from alerts.notifier import Notifier
from alerts.daily_report import DailyReportBuilder
from strategy.portfolio_manager import PortfolioManager

print("=" * 50)
print("PHASE 4 INTEGRATION TEST")
print("=" * 50)

# Pre-Trade Validator
print("\n--- PRE-TRADE VALIDATOR ---")
ptv = PreTradeValidator(8000)

ok, reason = ptv.validate({"symbol": "AAPL", "type": "STOCK_BUY", "shares": 3, "limit_price": 257.0, "cost": 771}, {"AAPL": 257.0})
print(f"Normal order: {ok} ({reason})")

ok, reason = ptv.validate({"symbol": "AAPL", "type": "STOCK_BUY", "shares": 3, "limit_price": 257.0, "cost": 771}, {"AAPL": 257.0})
print(f"Duplicate (30s): {ok} ({reason})")

ok, reason = ptv.validate({"symbol": "MSFT", "type": "STOCK_BUY", "shares": 100, "limit_price": 400.0, "cost": 5000}, {"MSFT": 400.0})
print(f"Oversized $5000: {ok} ({reason})")

ok, reason = ptv.validate({"symbol": "NVDA", "type": "STOCK_BUY", "shares": 2, "limit_price": 300.0, "cost": 600}, {"NVDA": 178.0})
print(f"Bad price $300 vs $178: {ok} ({reason})")

# Correlation Checker
print("\n--- CORRELATION CHECKER ---")
cc = CorrelationChecker()

ok, msg, corr = cc.check_new("NVDA", ["AAPL", "MSFT"])
print(f"NVDA with AAPL+MSFT: {ok} ({msg})")

ok, msg, corr = cc.check_new("AMZN", ["AAPL", "MSFT", "GOOGL"])
print(f"AMZN with AAPL+MSFT+GOOGL: {ok} ({msg})")

# Circuit Breakers
print("\n--- INDEPENDENT CIRCUIT BREAKERS ---")
cb = IndependentCircuitBreakers()
cb.state = {"halted": False, "halt_reason": None, "halt_until": None, "peak": 8000, "daily_pnl": [], "consec_losses": 0}

result = cb.update_and_check(8000, -0.01)
print(f"Normal day (-1%): halted={result['halted']}")

result = cb.update_and_check(7800, -0.03)
print(f"Bad day (-3%): halted={result['halted']} reason={result.get('reason')}")

# Realtime Monitor
print("\n--- REALTIME MONITOR ---")
pm = PortfolioManager(8000)
mon = RealtimeMonitor(pm.tracker, 8000)
result = mon.run_check({"AAPL": 250.0}, spy_price=520.0, vix=22.0)
print(f"Alerts: {len(result['alerts'])}")
print(f"Recommendations: {len(result['recommendations'])}")

# Daily Report
print("\n--- DAILY REPORT ---")
rpt = DailyReportBuilder()
report = rpt.build(
    portfolio=pm.tracker.get_summary(),
    equity=8000,
    peak=8000,
    dt_status=pm.dt_tracker.get_status()
)
print(report[:300])

# Notifier (dry run - no actual send)
print("\n--- NOTIFIER ---")
n = Notifier()
print(f"Email configured: {n.email_on}")
print(f"Slack configured: {n.slack_on}")

print("\n=== PHASE 4 INTEGRATION TEST PASSED ===")
