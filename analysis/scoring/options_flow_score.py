"""
Options Flow Scoring Module (25% weight).
"""

from loguru import logger


class OptionsFlowScorer:

    def score(self, chain_data, direction="CALL"):
        scores = {}
        try:
            call_map = chain_data.get("callExpDateMap", {})
            put_map = chain_data.get("putExpDateMap", {})
            cv, coi = self._agg(call_map, 14, 45)
            pv, poi = self._agg(put_map, 14, 45)
            tv = cv + pv
            toi = coi + poi
            if tv == 0 or toi == 0:
                return {"total_score": 50, "details": {"note": "insufficient data"}}
            cp = cv / max(pv, 1)
            if direction == "CALL":
                if cp > 3.0:
                    scores["cp_ratio"] = 90
                elif cp > 2.0:
                    scores["cp_ratio"] = 75
                elif cp > 1.2:
                    scores["cp_ratio"] = 60
                elif cp < 0.5:
                    scores["cp_ratio"] = 20
                else:
                    scores["cp_ratio"] = 45
            else:
                pc = pv / max(cv, 1)
                if pc > 2.5:
                    scores["cp_ratio"] = 85
                elif pc > 1.8:
                    scores["cp_ratio"] = 70
                elif pc > 1.2:
                    scores["cp_ratio"] = 55
                else:
                    scores["cp_ratio"] = 35
            t_vol = cv if direction == "CALL" else pv
            t_oi = coi if direction == "CALL" else poi
            voi = t_vol / max(t_oi, 1)
            if voi > 2.0:
                scores["vol_oi_ratio"] = 90
            elif voi > 1.0:
                scores["vol_oi_ratio"] = 75
            elif voi > 0.5:
                scores["vol_oi_ratio"] = 55
            else:
                scores["vol_oi_ratio"] = 40
            if tv > toi * 1.5:
                scores["activity_level"] = 80
            elif tv > toi * 0.8:
                scores["activity_level"] = 65
            elif tv > toi * 0.3:
                scores["activity_level"] = 50
            else:
                scores["activity_level"] = 35
            atm_v, otm_v = self._split(
                call_map if direction == "CALL" else put_map,
                chain_data.get("underlyingPrice", 0)
            )
            tdv = atm_v + otm_v
            ar = atm_v / max(tdv, 1)
            if ar > 0.6:
                scores["atm_concentration"] = 80
            elif ar > 0.4:
                scores["atm_concentration"] = 60
            else:
                scores["atm_concentration"] = 40
            w = {"cp_ratio": 0.30, "vol_oi_ratio": 0.30, "activity_level": 0.20, "atm_concentration": 0.20}
            total = sum(scores[k] * w[k] for k in w)
        except Exception as e:
            logger.error(f"Options flow scoring error: {e}")
            return {"total_score": 50, "details": {"error": str(e)}}
        return {"total_score": round(total, 1), "details": {k: round(v, 1) for k, v in scores.items()}, "raw": {"call_volume": cv, "put_volume": pv, "call_oi": coi, "put_oi": poi}}

    def _agg(self, exp_map, min_dte, max_dte):
        tv = 0
        toi = 0
        for ek, strikes in exp_map.items():
            try:
                dte = int(ek.split(":")[1]) if ":" in ek else 30
            except (IndexError, ValueError):
                dte = 30
            if min_dte <= dte <= max_dte:
                for sk, contracts in strikes.items():
                    for c in (contracts if isinstance(contracts, list) else [contracts]):
                        tv += c.get("totalVolume", 0)
                        toi += c.get("openInterest", 0)
        return tv, toi

    def _split(self, exp_map, price):
        atm = 0
        otm = 0
        if price <= 0:
            return 0, 0
        for ek, strikes in exp_map.items():
            for sk, contracts in strikes.items():
                try:
                    strike = float(sk)
                except ValueError:
                    continue
                dist = abs(strike - price) / price
                for c in (contracts if isinstance(contracts, list) else [contracts]):
                    v = c.get("totalVolume", 0)
                    if dist <= 0.05:
                        atm += v
                    else:
                        otm += v
        return atm, otm

    def score_without_api(self, symbol=None):
        return {"total_score": 50, "details": {"note": "no live chain data"}}
