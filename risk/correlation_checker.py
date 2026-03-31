"""
Cross-Position Correlation Checker.
"""

from loguru import logger


class CorrelationChecker:

    GROUPS = {
        "mega_tech": {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA"},
        "semis": {"NVDA", "AMD", "INTC", "AVGO", "TXN", "AMAT", "SOXL", "SOXS"},
        "nasdaq": {"TQQQ", "SQQQ", "QQQ"},
        "sp500": {"UPRO", "SPXU", "SPY"},
        "financials": {"JPM", "V", "MA", "SPGI"},
    }

    def check_new(self, new_sym, existing):
        if not existing:
            return True, "", 0.0
        for gname, gsyms in self.GROUPS.items():
            if new_sym in gsyms:
                overlap = set(existing) & gsyms
                if len(overlap) >= 2:
                    return False, f"{new_sym} would add 3rd in {gname} (existing: {overlap})", 1.0
                elif len(overlap) == 1:
                    logger.info(f"Note: {new_sym} same group as {overlap} ({gname})")
        return True, "", 0.0
