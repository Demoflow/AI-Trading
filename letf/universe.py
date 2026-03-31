"""
Leveraged ETF Universe.
Maps sectors to bull/bear ETFs and their underlying indices.
"""

SECTORS = {
    "nasdaq": {
        "bull": "TQQQ", "bear": "SQQQ", "underlying": "QQQ",
        "leverage": 3, "name": "Nasdaq 100",
    },
    "sp500": {
        "bull": "SPXL", "bear": "SPXS", "underlying": "SPY",
        "leverage": 3, "name": "S&P 500",
    },
    "semis": {
        "bull": "SOXL", "bear": "SOXS", "underlying": "SMH",
        "leverage": 3, "name": "Semiconductors",
    },
    "fang": {
        "bull": "FNGU", "bear": "FNGD", "underlying": "META",
        "leverage": 3, "name": "FANG+ Tech",
    },
    "biotech": {
        "bull": "LABU", "bear": "LABD", "underlying": "XBI",
        "leverage": 3, "name": "Biotech",
    },
    "financials": {
        "bull": "FAS", "bear": "FAZ", "underlying": "XLF",
        "leverage": 3, "name": "Financials",
    },
    "energy": {
        "bull": "ERX", "bear": "ERY", "underlying": "XLE",
        "leverage": 2, "name": "Energy",
    },
    "gold": {
        "bull": "NUGT", "bear": "DUST", "underlying": "GDX",
        "leverage": 2, "name": "Gold Miners",
    },
    "smallcap": {
        "bull": "TNA", "bear": "TZA", "underlying": "IWM",
        "leverage": 3, "name": "Russell 2000",
    },
    "china": {
        "bull": "YINN", "bear": "YANG", "underlying": "FXI",
        "leverage": 3, "name": "China",
    },
    "realestate": {
        "bull": "DRN", "bear": "DRV", "underlying": "XLRE",
        "leverage": 3, "name": "Real Estate",
    },
    # Single-stock LETFs (stricter rules: 90+ conviction, 7% max, 5-day hold)
    "nvidia": {
        "bull": "NVDL", "bear": "NVDS", "underlying": "NVDA",
        "leverage": 2, "name": "NVIDIA", "single_stock": True,
        "max_position_pct": 0.07, "min_conviction": 90, "max_hold_days": 5,
    },
    "tesla": {
        "bull": "TSLL", "bear": "TSLS", "underlying": "TSLA",
        "leverage": 2, "name": "Tesla", "single_stock": True,
        "max_position_pct": 0.07, "min_conviction": 90, "max_hold_days": 5,
    },
}

# All underlyings to scan
UNDERLYINGS = list(set(s["underlying"] for s in SECTORS.values()))

# All tradeable ETFs
ALL_ETFS = []
for s in SECTORS.values():
    ALL_ETFS.append(s["bull"])
    ALL_ETFS.append(s["bear"])

def get_sector(etf_symbol):
    for name, s in SECTORS.items():
        if etf_symbol in (s["bull"], s["bear"]):
            return name
    return None

def get_direction(etf_symbol):
    for s in SECTORS.values():
        if etf_symbol == s["bull"]:
            return "BULL"
        if etf_symbol == s["bear"]:
            return "BEAR"
    return None
