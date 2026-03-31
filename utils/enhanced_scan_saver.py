"""
Enhanced scan history saver.
Saves top 20 scores (not just actionable) for ML training.
"""

import json
import os
from datetime import date
from loguru import logger


def save_enhanced_scan(all_results, path=None):
    """Save top 20 scores per direction for ML negative examples."""
    if path is None:
        d = f"config/scan_history"
        os.makedirs(d, exist_ok=True)
        path = f"{d}/{date.today().isoformat()}_full.json"

    bull = [
        r for r in all_results
        if r.get("direction") == "BULLISH"
    ]
    bear = [
        r for r in all_results
        if r.get("direction") == "BEARISH"
    ]

    bull.sort(
        key=lambda x: x["composite_score"],
        reverse=True,
    )
    bear.sort(
        key=lambda x: x["composite_score"],
        reverse=True,
    )

    data = {
        "date": date.today().isoformat(),
        "total_scanned": len(all_results) // 2,
        "top_bullish": [
            {
                "symbol": r["symbol"],
                "score": r["composite_score"],
                "action": r["action"],
                "sub_scores": r["sub_scores"],
                "trade_params": r["trade_params"],
                "sector": r.get("sector", ""),
            }
            for r in bull[:20]
        ],
        "top_bearish": [
            {
                "symbol": r["symbol"],
                "score": r["composite_score"],
                "action": r["action"],
                "sub_scores": r["sub_scores"],
            }
            for r in bear[:20]
        ],
    }

    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

    logger.info(f"Enhanced scan saved: {path}")
    return data
