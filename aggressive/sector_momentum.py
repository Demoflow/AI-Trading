"""
Sector Momentum Scorer.
Weights trades toward sectors showing relative strength/weakness.
"""
from loguru import logger


SECTOR_ETFS = {
    "tech": "XLK", "semis": "SOXX", "energy": "XLE", "banks": "XLF",
    "pharma": "XLV", "defense": "ITA", "airlines": "JETS",
    "mining": "GDX", "retail": "XRT", "saas": "IGV", "reits": "XLRE",
    "telecom": "IYZ",  # Telecom ETF
}

# Map symbols to sectors
SYMBOL_SECTOR = {
    "AAPL": "tech", "MSFT": "tech", "NVDA": "semis", "AMD": "semis",
    "MRVL": "semis", "ARM": "semis", "MU": "semis", "AVGO": "semis",
    "PLTR": "saas", "CRM": "saas", "NOW": "saas", "SNOW": "saas",
    "XOM": "energy", "CVX": "energy", "BP": "energy", "SLB": "energy",
    "HAL": "energy", "OXY": "energy",
    "JPM": "banks", "BAC": "banks", "WFC": "banks", "MS": "banks",
    "GS": "banks", "C": "banks",
    "PFE": "pharma", "BMY": "pharma", "MRNA": "pharma", "LLY": "pharma",
    "LMT": "defense", "RTX": "defense", "GE": "defense", "BA": "defense",
    "DAL": "airlines", "UAL": "airlines", "AAL": "airlines",
    "NEM": "mining", "FCX": "mining",
    "ABNB": "retail", "DIS": "retail",
    "DELL": "tech", "ANET": "tech", "NFLX": "tech",
    "T": "telecom", "VZ": "telecom",
    "FDX": "retail",
}


class SectorMomentum:

    def __init__(self, client):
        self.client = client
        self.sector_scores = {}

    def calculate(self):
        """Calculate 5-day momentum for each sector ETF."""
        for sector, etf in SECTOR_ETFS.items():
            try:
                resp = self.client.get_quote(etf)
                if resp.status_code == 200:
                    q = resp.json().get(etf, {}).get("quote", {})
                    change_5d = q.get("netPercentChangeInDouble", 0)
                    self.sector_scores[sector] = round(change_5d, 2)
            except Exception:
                self.sector_scores[sector] = 0
        return self.sector_scores

    def get_boost(self, symbol, direction):
        """
        Returns confidence boost based on sector alignment.
        +10 if sector momentum aligns with trade direction.
        -10 if it conflicts.
        0 if neutral.
        """
        sector = SYMBOL_SECTOR.get(symbol)
        if not sector or sector not in self.sector_scores:
            return 0

        momentum = self.sector_scores.get(sector, 0)

        if direction == "CALL" and momentum > 1.0:
            return 10  # Bullish trade + sector momentum up
        elif direction == "PUT" and momentum < -1.0:
            return 10  # Bearish trade + sector momentum down
        elif direction == "CALL" and momentum < -2.0:
            return -10  # Bullish trade against strong sector downtrend
        elif direction == "PUT" and momentum > 2.0:
            return -10  # Bearish trade against strong sector uptrend
        return 0
