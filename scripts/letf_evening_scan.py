"""
LETF Evening Scan - runs after market close.
Syncs equity, analyzes sectors, saves recommendations for tomorrow.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from loguru import logger
from utils.logging_setup import setup_logging
from data.broker.schwab_auth import get_schwab_client

from letf.universe import SECTORS
from letf.sector_analyzer import SectorAnalyzer
from letf.executor import LETFExecutor

setup_logging()
client = get_schwab_client()
logger.info("LETF Evening Scan")

# Sync equity
executor = LETFExecutor(client, live=True)
equity = executor.sync_equity()
if not equity:
    config = json.load(open("config/letf_config.json"))
    equity = config["equity"]

logger.info(f"Equity: ${equity:,.2f}")

# Scan sectors
analyzer = SectorAnalyzer(client)
results = []
for sector_name, sector_info in SECTORS.items():
    result = analyzer.analyze_sector(sector_name, sector_info)
    results.append(result)
    bull = result["bull_score"]
    bear = result["bear_score"]
    best = "BULL" if bull > bear else "BEAR"
    score = max(bull, bear)
    logger.info(f"  {sector_name:<12} {best} {score:>3} | bull={bull} bear={bear}")

# Save for tomorrow
scan_data = {
    "date": __import__("datetime").date.today().isoformat(),
    "equity": equity,
    "sectors": results,
}
json.dump(scan_data, open("config/letf_scan.json", "w"), indent=2)
logger.info(f"Saved {len(results)} sector analyses for tomorrow")

# Count high conviction
high = [r for r in results if max(r["bull_score"], r["bear_score"]) >= 80]
logger.info(f"High conviction sectors: {len(high)}")
for r in high:
    best = "BULL" if r["bull_score"] > r["bear_score"] else "BEAR"
    score = max(r["bull_score"], r["bear_score"])
    logger.info(f"  {r['sector']}: {best} {score}")
