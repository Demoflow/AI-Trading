"""
Adaptive Position Sizing.
Uses fractional Kelly criterion from real tracked data.
Falls back to conservative fixed sizing when data is insufficient.
"""

from loguru import logger


class AdaptiveSizer:

    # Conservative defaults before we have data
    DEFAULT_SIZE = 0.15  # 15% of equity per trade
    MIN_SIZE = 0.05      # Never less than 5%
    MAX_SIZE = 0.30      # Never more than 30%

    def __init__(self, signal_tracker):
        self.tracker = signal_tracker
        self._kelly = None
        self._refresh()

    def _refresh(self):
        self._kelly = self.tracker.get_kelly_inputs()
        if self._kelly:
            logger.info(
                f"Adaptive sizing: WR={self._kelly['win_rate']:.1%} "
                f"W/L={self._kelly['avg_win']:.1f}/{self._kelly['avg_loss']:.1f} "
                f"Kelly={self._kelly['full_kelly']:.1%} "
                f"Fractional={self._kelly['fractional_kelly']:.1%} "
                f"(n={self._kelly['sample_size']})"
            )

    def get_size(self, conviction_score, vix_modifier=1.0, iv_modifier=1.0):
        """
        Get position size as fraction of equity.
        Uses Kelly if available, else conservative default.
        """
        if self._kelly and self._kelly["sample_size"] >= 50:
            # Use fractional Kelly
            base = self._kelly["fractional_kelly"]

            # Scale by conviction
            if conviction_score >= 90:
                scale = 1.2
            elif conviction_score >= 85:
                scale = 1.0
            elif conviction_score >= 80:
                scale = 0.8
            else:
                scale = 0.6

            size = base * scale * vix_modifier * iv_modifier
        else:
            # Not enough data yet - use conservative default
            base = self.DEFAULT_SIZE
            if conviction_score >= 90:
                size = base * 1.2 * vix_modifier * iv_modifier
            elif conviction_score >= 85:
                size = base * 1.0 * vix_modifier * iv_modifier
            elif conviction_score >= 80:
                size = base * 0.8 * vix_modifier * iv_modifier
            else:
                size = base * 0.6 * vix_modifier * iv_modifier

        # Clamp
        size = max(self.MIN_SIZE, min(self.MAX_SIZE, size))
        return round(size, 4)
