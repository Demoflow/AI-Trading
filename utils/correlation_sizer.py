"""
#7 - Correlation-Aware Position Sizing.
Reduces size when adding correlated positions.
"""

from loguru import logger


class CorrelationSizer:

    GROUPS = {
        "mega_tech": {
            "AAPL", "MSFT", "GOOGL", "AMZN",
            "META", "NVDA",
        },
        "semis": {
            "NVDA", "AMD", "INTC", "AVGO",
            "TXN", "AMAT", "SOXL", "SOXS",
        },
        "nasdaq_lev": {"TQQQ", "SQQQ", "QQQ"},
        "sp500_lev": {"UPRO", "SPXU", "SPY"},
        "financials": {"JPM", "V", "MA", "SPGI"},
    }

    def get_size_modifier(self, new_symbol, existing_syms):
        """
        Returns modifier 0.5-1.0 based on correlation.
        1.0 = no overlap, full size
        0.75 = one overlap in same group
        0.5 = two overlaps (should rarely happen)
        """
        if not existing_syms:
            return 1.0

        max_overlap = 0
        for gname, gsyms in self.GROUPS.items():
            if new_symbol in gsyms:
                overlap = len(set(existing_syms) & gsyms)
                max_overlap = max(max_overlap, overlap)

        if max_overlap >= 2:
            return 0.5
        elif max_overlap == 1:
            return 0.75
        return 1.0

    def adjust_size(self, shares, modifier):
        return max(1, int(shares * modifier))
