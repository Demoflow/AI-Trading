"""
Update Universe to 150 high-liquidity options stocks.
Selected by: daily options volume > 5,000 contracts,
tight bid-ask spreads, institutional interest.
"""

import os
import csv

UNIVERSE = [
    # ── MEGA CAP TECH (highest options volume) ──
    ("TSLA", "mega", "auto_tech"),
    ("AAPL", "mega", "mega_tech"),
    ("NVDA", "mega", "semis"),
    ("AMZN", "mega", "mega_tech"),
    ("AMD", "mega", "semis"),
    ("META", "mega", "mega_tech"),
    ("MSFT", "mega", "mega_tech"),
    ("GOOGL", "mega", "mega_tech"),
    ("NFLX", "mega", "streaming"),
    ("AVGO", "mega", "semis"),

    # ── HIGH GROWTH / MOMENTUM ──
    ("PLTR", "large", "saas"),
    ("COIN", "large", "crypto"),
    ("MARA", "large", "crypto"),
    ("RIOT", "large", "crypto"),
    ("HOOD", "large", "fintech"),
    ("SOFI", "large", "fintech"),
    ("RKLB", "large", "aerospace"),
    ("SNOW", "large", "saas"),
    ("CRWD", "large", "saas"),
    ("SHOP", "large", "saas"),
    ("PANW", "large", "saas"),
    ("ARM", "large", "semis"),
    ("SMCI", "large", "semis"),
    ("APP", "large", "adtech"),
    ("DKNG", "large", "gaming"),
    ("NET", "large", "saas"),
    ("ANET", "large", "networking"),
    ("MSTR", "large", "crypto"),
    ("TTD", "large", "adtech"),
    ("SNAP", "large", "social"),
    ("PINS", "large", "social"),
    ("U", "large", "gaming"),
    ("ROKU", "large", "streaming"),
    ("UPST", "large", "fintech"),
    ("FUBO", "large", "streaming"),
    ("IONQ", "large", "quantum"),
    ("RGTI", "large", "quantum"),
    ("HIMS", "large", "health_tech"),

    # ── SEMIS & CHIPS ──
    ("INTC", "large", "semis"),
    ("MRVL", "large", "semis"),
    ("MU", "large", "semis"),
    ("AMAT", "large", "semis"),
    ("LRCX", "large", "semis"),
    ("KLAC", "large", "semis"),
    ("QCOM", "large", "semis"),
    ("TSM", "mega", "semis"),
    ("ON", "large", "semis"),
    ("MCHP", "large", "semis"),

    # ── FINANCIALS ──
    ("JPM", "mega", "banks"),
    ("BAC", "mega", "banks"),
    ("GS", "mega", "banks"),
    ("MS", "mega", "banks"),
    ("C", "mega", "banks"),
    ("WFC", "mega", "banks"),
    ("SCHW", "mega", "banks"),
    ("V", "mega", "payments"),
    ("MA", "mega", "payments"),
    ("PYPL", "large", "payments"),
    ("XYZ", "large", "fintech"),
    ("BX", "large", "finance"),
    ("KKR", "large", "finance"),
    ("COIN", "large", "crypto"),

    # ── HEALTHCARE / BIOTECH ──
    ("JNJ", "mega", "pharma"),
    ("UNH", "mega", "health_ins"),
    ("LLY", "mega", "pharma"),
    ("ABBV", "mega", "pharma"),
    ("MRK", "mega", "pharma"),
    ("PFE", "large", "pharma"),
    ("BMY", "large", "pharma"),
    ("GILD", "large", "pharma"),
    ("MRNA", "large", "biotech"),
    ("BNTX", "large", "biotech"),
    ("ISRG", "mega", "medtech"),

    # ── ENERGY ──
    ("XOM", "mega", "energy"),
    ("CVX", "mega", "energy"),
    ("OXY", "large", "energy"),
    ("SLB", "large", "energy"),
    ("DVN", "large", "energy"),
    ("HAL", "large", "energy"),
    ("MPC", "large", "energy"),
    ("VLO", "large", "energy"),
    ("BP", "large", "energy"),

    # ── INDUSTRIALS / DEFENSE ──
    ("BA", "mega", "defense"),
    ("RTX", "mega", "defense"),
    ("LMT", "mega", "defense"),
    ("CAT", "mega", "industrial"),
    ("DE", "mega", "industrial"),
    ("GE", "mega", "industrial"),
    ("HON", "mega", "industrial"),
    ("UNP", "mega", "transport"),
    ("FDX", "large", "transport"),
    ("DAL", "large", "airlines"),
    ("UAL", "large", "airlines"),
    ("AAL", "large", "airlines"),

    # ── CONSUMER ──
    ("KO", "mega", "consumer"),
    ("PEP", "mega", "consumer"),
    ("WMT", "mega", "retail"),
    ("COST", "mega", "retail"),
    ("TGT", "large", "retail"),
    ("HD", "mega", "retail"),
    ("LOW", "mega", "retail"),
    ("NKE", "large", "consumer"),
    ("SBUX", "large", "consumer"),
    ("MCD", "mega", "consumer"),
    ("DIS", "mega", "media"),
    ("ABNB", "large", "travel"),
    ("BKNG", "mega", "travel"),
    ("UBER", "large", "rideshare"),
    ("LYFT", "large", "rideshare"),
    ("DASH", "large", "delivery"),
    ("CMG", "large", "restaurant"),
    ("PG", "mega", "consumer"),

    # ── TELECOM / UTILITIES ──
    ("T", "mega", "telecom"),
    ("VZ", "mega", "telecom"),
    ("NEE", "mega", "utilities"),

    # ── MATERIALS / MINING ──
    ("FCX", "large", "mining"),
    ("NEM", "large", "mining"),
    ("CLF", "large", "steel"),
    ("AA", "large", "aluminum"),
    ("VALE", "large", "mining"),
    ("NUE", "large", "steel"),

    # ── REAL ESTATE ──
    ("O", "large", "reit"),
    ("SPG", "large", "reit"),

    # ── CHINA / INTL ──
    ("BABA", "mega", "china_tech"),
    ("NIO", "large", "china_ev"),
    ("LI", "large", "china_ev"),
    ("XPEV", "large", "china_ev"),
    ("JD", "large", "china_tech"),
    ("PDD", "large", "china_tech"),

    # ── EV / CLEAN ENERGY ──
    ("RIVN", "large", "ev"),
    ("LCID", "large", "ev"),
    ("PLUG", "large", "clean_energy"),
    ("ENPH", "large", "solar"),
    ("FSLR", "large", "solar"),

    # ── SOFTWARE / CLOUD ──
    ("CRM", "mega", "saas"),
    ("ORCL", "mega", "enterprise"),
    ("NOW", "mega", "saas"),
    ("ADBE", "mega", "saas"),
    ("INTU", "mega", "saas"),
    ("DDOG", "large", "saas"),
    ("ZS", "large", "saas"),
    ("TEAM", "large", "saas"),
    ("MDB", "large", "saas"),
    ("DOCU", "large", "saas"),
    ("CSCO", "mega", "networking"),
    ("IBM", "mega", "enterprise"),
    ("TXN", "mega", "semis"),
    ("DELL", "large", "hardware"),

    # ── MISC HIGH OPTIONS VOLUME ──
    ("AMC", "large", "entertainment"),
    ("GME", "large", "retail"),
    ("BBBY", "large", "retail"),
    ("CCL", "large", "cruise"),
    ("NCLH", "large", "cruise"),
    ("RCL", "large", "cruise"),
    ("W", "large", "ecommerce"),
    ("BYND", "large", "food"),
    ("TLRY", "large", "cannabis"),
]

# Deduplicate
seen = set()
unique = []
for sym, cap, sector in UNIVERSE:
    if sym not in seen:
        seen.add(sym)
        unique.append((sym, cap, sector))

os.makedirs("config", exist_ok=True)
with open("config/universe.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["symbol", "market_cap_tier", "sector"])
    w.writeheader()
    for sym, cap, sector in unique:
        w.writerow({
            "symbol": sym,
            "market_cap_tier": cap,
            "sector": sector,
        })

print(f"Universe updated: {len(unique)} symbols")
print()

# Count by sector
sectors = {}
for _, _, s in unique:
    sectors[s] = sectors.get(s, 0) + 1
for s in sorted(sectors, key=sectors.get, reverse=True):
    print(f"  {s:16s} {sectors[s]:3d}")
print()
print("Run: python scripts/backfill.py  (to download price data)")
print("Then: python scripts/aggressive_scan.py")
