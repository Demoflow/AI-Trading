"""
LightGBM signal enhancement model.
"""

import os
import json
import glob
import numpy as np
import pandas as pd
from pathlib import Path
from loguru import logger

try:
    import lightgbm as lgb
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import accuracy_score
    import joblib
    ML_OK = True
except ImportError:
    ML_OK = False


class SignalModel:

    MODEL_PATH = "config/models/signal_model.pkl"

    def __init__(self):
        self.model = None
        if ML_OK and Path(self.MODEL_PATH).exists():
            self.model = joblib.load(self.MODEL_PATH)
            logger.info("Loaded trained model")

    def prepare_data(self, scan_dir="config/scan_history", tlog="config/trade_log.json"):
        if not ML_OK:
            return pd.DataFrame()
        scans = sorted(glob.glob(f"{scan_dir}/*.json"))
        if len(scans) < 30:
            return pd.DataFrame()
        if not Path(tlog).exists():
            return pd.DataFrame()
        with open(tlog) as f:
            trades = json.load(f)
        rows = []
        for t in trades:
            sym = t["symbol"]
            ed = t.get("entry_date", "")
            win = 1 if t.get("pnl", 0) > 0 else 0
            sp = f"{scan_dir}/{ed}.json"
            if Path(sp).exists():
                with open(sp) as f:
                    wl = json.load(f)
                for cat in ["options", "stocks", "leveraged_etfs"]:
                    for pick in wl.get(cat, []):
                        if pick["symbol"] == sym:
                            row = self._ext(pick)
                            row["label"] = win
                            rows.append(row)
        return pd.DataFrame(rows) if rows else pd.DataFrame()

    def _ext(self, pick):
        sub = pick.get("sub_scores", {})
        return {
            "composite": pick.get("score", 50),
            "tech": sub.get("technical", 50),
            "flow": sub.get("options_flow", 50),
            "fund": sub.get("fundamental", 50),
            "mkt": sub.get("market_context", 50),
            "rr": sub.get("risk_reward", 50),
        }

    def train(self, df):
        if not ML_OK or df.empty or len(df) < 60:
            return None
        fcols = [c for c in df.columns if c != "label"]
        X = df[fcols].fillna(50)
        y = df["label"]
        tscv = TimeSeriesSplit(n_splits=5)
        scores = []
        for ti, vi in tscv.split(X):
            m = lgb.LGBMClassifier(
                n_estimators=100, max_depth=4,
                learning_rate=0.05, num_leaves=15,
                min_child_samples=10, verbose=-1
            )
            m.fit(X.iloc[ti], y.iloc[ti])
            p = m.predict(X.iloc[vi])
            scores.append(accuracy_score(y.iloc[vi], p))
        avg = np.mean(scores)
        logger.info(f"Walk-forward accuracy: {avg:.3f}")
        self.model = lgb.LGBMClassifier(
            n_estimators=100, max_depth=4,
            learning_rate=0.05, num_leaves=15,
            min_child_samples=10, verbose=-1
        )
        self.model.fit(X, y)
        Path(self.MODEL_PATH).parent.mkdir(
            parents=True, exist_ok=True
        )
        joblib.dump(self.model, self.MODEL_PATH)
        logger.info(f"Model saved")
        return avg

    def predict_confidence(self, pick):
        if self.model is None:
            return 1.0
        feats = self._ext(pick)
        X = pd.DataFrame([feats]).fillna(50)
        try:
            prob = self.model.predict_proba(X)[0][1]
            return round(0.5 + prob, 2)
        except Exception:
            return 1.0
