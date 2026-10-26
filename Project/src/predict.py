import json
import sys
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

MODEL_PATH = Path("outputs/best_churn_model.joblib")
HIGH, MEDIUM = 0.40, 0.20          # risk-band cut-offs (from the cost analysis)
ACTION = {"High": "Proactive retention offer",
          "Medium": "Low-cost check-in (email or call)",
          "Low": "No action"}


@lru_cache(maxsize=None)
def _load(path):
    """Load the model bundle once and remember it."""
    bundle = joblib.load(path)
    pre, model = bundle["preprocess"], bundle["model"]
    known = {}
    for name, _, cols in pre.transformers_:
        if name == "cat":
            for col, cats in zip(cols, pre.named_transformers_["cat"].categories_):
                known[col] = set(map(str, cats))
    return pre, model, list(pre.feature_names_in_), known


def band(p):
    return "High" if p >= HIGH else "Medium" if p >= MEDIUM else "Low"


def _pretty(feature, scaled_value):
    kind, name = feature.split("__", 1)
    if kind == "num":
        return f"{name} ({'high' if scaled_value > 0 else 'low'})"
    return name.replace("_", " = ", 1)


def predict_customer(customer: dict, model_path=MODEL_PATH) -> dict:
    pre, model, features, known = _load(Path(model_path))
    row = dict(customer)

    # Same TotalCharges rule as training: blank is allowed only when tenure is 0
    tc = pd.to_numeric(row.get("TotalCharges"), errors="coerce")
    if pd.isna(tc):
        if row.get("tenure") == 0:
            tc = 0.0
        else:
            raise ValueError("TotalCharges is blank for a customer with tenure > 0")
    row["TotalCharges"] = float(tc)

    # SeniorCitizen: accept 0/1 or "No"/"Yes"
    if row.get("SeniorCitizen") in (0, 1):
        row["SeniorCitizen"] = "Yes" if row["SeniorCitizen"] == 1 else "No"

    missing = [f for f in features if f not in row]
    if missing:
        raise ValueError(f"Missing field(s): {missing}")
    bad = {f: row[f] for f in known if str(row[f]) not in known[f]}
    if bad:
        raise ValueError(f"Unrecognised value(s): {bad}. Allowed: " +
                         "; ".join(f"{k}={sorted(known[k])}" for k in bad))

    X = pd.DataFrame([row])[features]
    Xt = pre.transform(X)
    Xt = Xt.toarray() if hasattr(Xt, "toarray") else np.asarray(Xt)
    p = float(model.predict_proba(Xt)[0, 1])

    reasons = []
    if hasattr(model, "coef_"):
        names = pre.get_feature_names_out()
        contrib = Xt[0] * model.coef_[0]
        for j in np.argsort(-contrib)[:3]:
            if contrib[j] > 0:
                reasons.append(_pretty(names[j], Xt[0, j]))

    return {"risk_score": round(p, 3), "risk_band": band(p),
            "action": ACTION[band(p)], "reasons": reasons}


if __name__ == "__main__":
    customer = json.load(open(sys.argv[1]))
    print(json.dumps(predict_customer(customer), indent=2))