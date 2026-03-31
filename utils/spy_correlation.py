"""
#15 - Intraday SPY Correlation Monitor.
Adjusts SPY breaker sensitivity per-stock.
"""

import numpy as np
from loguru import logger


class SPYCorrelationMonitor:

    def __init__(self):
        self.price_history = {}
        self.spy_history = []

    def update(self, prices, spy_price):
        self.spy_history.append(spy_price)
        if len(self.spy_history) > 60:
            self.spy_history = self.spy_history[-60:]
        for sym, price in prices.items():
            if sym == "SPY":
                continue
            if sym not in self.price_history:
                self.price_history[sym] = []
            self.price_history[sym].append(price)
            if len(self.price_history[sym]) > 60:
                self.price_history[sym] = (
                    self.price_history[sym][-60:]
                )

    def get_correlation(self, symbol, lookback=20):
        """
        Calculate rolling correlation with SPY.
        Returns -1 to 1.
        """
        sh = self.spy_history[-lookback:]
        ph = self.price_history.get(symbol, [])[-lookback:]
        if len(sh) < 10 or len(ph) < 10:
            return 0.5
        min_len = min(len(sh), len(ph))
        sh = sh[-min_len:]
        ph = ph[-min_len:]
        if min_len < 5:
            return 0.5
        spy_ret = np.diff(sh) / sh[:-1]
        stk_ret = np.diff(ph) / ph[:-1]
        if len(spy_ret) < 3:
            return 0.5
        corr = np.corrcoef(spy_ret, stk_ret)[0, 1]
        if np.isnan(corr):
            return 0.5
        return round(corr, 3)

    def should_trigger_spy_breaker(self, symbol,
                                    spy_change):
        """
        Smart SPY breaker: only trigger if stock
        is actually correlated with SPY today.
        """
        corr = self.get_correlation(symbol)

        if spy_change <= -0.03:
            return True, f"SPY -3%+ (corr={corr:.2f})"

        if spy_change <= -0.02:
            if corr > 0.5:
                return True, (
                    f"SPY -2%+ high corr ({corr:.2f})"
                )
            else:
                return False, (
                    f"SPY -2% but low corr ({corr:.2f})"
                )

        return False, "SPY within limits"
