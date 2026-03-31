"""
Weekly model retraining script.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger


def train():
    from analysis.ml.model import SignalModel
    logger.info("Starting weekly model training...")
    model = SignalModel()
    df = model.prepare_data()
    if df.empty:
        logger.info("Not enough data yet for training.")
        return
    logger.info(f"Training on {len(df)} records...")
    acc = model.train(df)
    if acc:
        logger.info(f"Training complete. Accuracy: {acc:.3f}")


if __name__ == "__main__":
    train()
