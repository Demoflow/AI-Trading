"""
Position Correlation Checker.
Prevents doubling down on the same sector risk.
"""
from loguru import logger

# Correlation groups - symbols that move together
CORR_GROUPS = {
    "pharma": ["PFE", "BMY", "MRNA", "LLY", "JNJ", "ABBV"],
    "semis": ["NVDA", "AMD", "MRVL", "ARM", "MU", "AVGO", "QCOM"],
    "banks": ["JPM", "BAC", "WFC", "MS", "GS", "C"],
    "energy": ["XOM", "CVX", "BP", "SLB", "HAL", "OXY"],
    "defense": ["LMT", "RTX", "GE", "BA", "NOC"],
    "airlines": ["DAL", "UAL", "AAL"],
    "big_tech": ["AAPL", "MSFT", "GOOGL", "META", "AMZN"],
    "saas": ["PLTR", "CRM", "NOW", "SNOW"],
}


class PositionCorrelation:

    def __init__(self):
        self.open_symbols = set()
        self.open_sectors = set()

    def update_positions(self, symbols):
        """Update with currently held symbols."""
        self.open_symbols = set(symbols)
        self.open_sectors = set()
        for group_name, group_symbols in CORR_GROUPS.items():
            for sym in symbols:
                if sym in group_symbols:
                    self.open_sectors.add(group_name)

    def check(self, symbol, direction):
        """
        Returns (allowed, reason).
        Blocks if same sector already has 2+ positions.
        Warns if same sector has 1 position.
        """
        if symbol in self.open_symbols:
            return False, f"already_holding_{symbol}"

        symbol_sector = None
        for group_name, group_symbols in CORR_GROUPS.items():
            if symbol in group_symbols:
                symbol_sector = group_name
                break

        if not symbol_sector:
            return True, "no_correlation"

        # Count positions in same sector
        sector_count = 0
        for sym in self.open_symbols:
            if sym in CORR_GROUPS.get(symbol_sector, []):
                sector_count += 1

        if sector_count >= 2:
            return False, f"sector_{symbol_sector}_full_{sector_count}"
        elif sector_count == 1:
            return True, f"sector_{symbol_sector}_1_ok"
        return True, "sector_clear"
