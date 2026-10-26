import io
import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
import joblib
import numpy as np
import pandas as pd

logger = logging.getLogger()
logger.setLevel(logging.INFO)
s3 = boto3.client("s3")

MODEL_KEY = os.environ.get("MODEL_KEY", "outputs/models/v1/best_churn_model.joblib")
MODEL_VERSION = os.environ.get("MODEL_VERSION", "v1")
HIGH = float(os.environ.get("HIGH", "0.40"))
MEDIUM = float(os.environ.get("MEDIUM", "0.20"))
TOP_REASONS = 3
SENIOR_MAP = {"0": "No", "1": "Yes", "0.0": "No", "1.0": "Yes"}
_cache = {}


def load_model(bucket):
    """Download and load the model once per warm container."""
    if "bundle" not in _cache:
        s3.download_file(bucket, MODEL_KEY, "/tmp/model.joblib")
        bundle = joblib.load("/tmp/model.joblib")
        pre = bundle["preprocess"]
        known = {}
        for name, _, cols in pre.transformers_:
            if name == "cat":
                for col, cats in zip(cols, pre.named_transformers_["cat"].categories_):
                    known[col] = set(map(str, cats))
        _cache.update(bundle=bundle, features=list(pre.feature_names_in_), known=known)
    return _cache["bundle"], _cache["features"], _cache["known"]


def validate(df, features, known):
    """Returns (errors, warnings). Any error rejects the whole file."""
    errors, warnings = [], []
    required = ["customerID"] + features
    missing = [c for c in required if c not in df.columns]
    if missing:
        return [f"Missing required column(s): {missing}"], warnings
    if len(df) == 0:
        return ["File has a header but no data rows"], warnings

    if "Churn" in df.columns:
        warnings.append("Churn column present; it is ignored for scoring")
    extra = [c for c in df.columns if c not in required and c != "Churn"]
    if extra:
        warnings.append(f"Ignored extra column(s): {extra}")

    ids = df["customerID"].astype(str).str.strip()
    if (ids == "").sum():
        errors.append(f"{int((ids == '').sum())} row(s) with blank customerID")
    dup = int(ids[ids != ""].duplicated().sum())
    if dup:
        errors.append(f"{dup} duplicated customerID value(s)")

    tenure = pd.to_numeric(df["tenure"], errors="coerce")
    bad = int((tenure.isna() | (tenure < 0) | (tenure % 1 != 0)).sum())
    if bad:
        errors.append(f"{bad} row(s) with invalid tenure (whole number >= 0 required)")

    monthly = pd.to_numeric(df["MonthlyCharges"], errors="coerce")
    bad = int((monthly.isna() | (monthly < 0)).sum())
    if bad:
        errors.append(f"{bad} row(s) with invalid MonthlyCharges (number >= 0 required)")

    raw = df["TotalCharges"]
    total = pd.to_numeric(raw, errors="coerce")
    raw_blank = raw.astype(str).str.strip() == ""
    junk = int((total.isna() & ~raw_blank).sum())
    if junk:
        errors.append(f"{junk} row(s) with non-numeric TotalCharges")
    if int((total < 0).sum()):
        errors.append("Negative TotalCharges found")
    orphan = int((raw_blank & (tenure != 0)).sum())
    if orphan:
        errors.append(f"{orphan} row(s) with blank TotalCharges but tenure above 0")
    ok_blank = int((raw_blank & (tenure == 0)).sum())
    if ok_blank:
        warnings.append(f"{ok_blank} blank TotalCharges value(s) treated as 0 (tenure = 0)")

    for col, allowed in known.items():
        vals = df[col].replace(SENIOR_MAP) if col == "SeniorCitizen" else df[col]
        bad_vals = vals[~vals.isin(allowed)]
        if len(bad_vals):
            errors.append(f"{len(bad_vals)} row(s) with unrecognised {col} value, "
                          f"e.g. {sorted(set(bad_vals))[:3]}")
    return errors, warnings


def pretty(feature, scaled_value):
    kind, name = feature.split("__", 1)
    if kind == "num":
        return f"{name} ({'high' if scaled_value > 0 else 'low'})"
    return name.replace("_", " = ", 1)


def band(p):
    return "High" if p >= HIGH else "Medium" if p >= MEDIUM else "Low"


def score(df, bundle, features, now):
    pre, model = bundle["preprocess"], bundle["model"]
    X = df[features].copy()
    X["SeniorCitizen"] = X["SeniorCitizen"].replace(SENIOR_MAP)
    X["tenure"] = pd.to_numeric(X["tenure"])
    X["MonthlyCharges"] = pd.to_numeric(X["MonthlyCharges"])
    X["TotalCharges"] = pd.to_numeric(X["TotalCharges"], errors="coerce").fillna(0.0)

    Xt = pre.transform(X)
    Xt = Xt.toarray() if hasattr(Xt, "toarray") else np.asarray(Xt)
    prob = model.predict_proba(Xt)[:, 1]

    out = pd.DataFrame({
        "customerID": df["customerID"].values,
        "churn_probability": prob.round(4),
        "risk_band": [band(p) for p in prob],
    })
    if hasattr(model, "coef_"):
        names = pre.get_feature_names_out()
        contrib = Xt * model.coef_[0]
        order = np.argsort(-contrib, axis=1)[:, :TOP_REASONS]
        for k in range(TOP_REASONS):
            out[f"reason_{k + 1}"] = [
                pretty(names[r[k]], Xt[i, r[k]]) if contrib[i, r[k]] > 0 else ""
                for i, r in enumerate(order)]
    else:
        for k in range(TOP_REASONS):
            out[f"reason_{k + 1}"] = ""
    out["model"] = bundle["name"]
    out["model_version"] = MODEL_VERSION
    out["scored_at"] = now.strftime("%Y-%m-%d")
    out["actual_churn"] = df["Churn"].values if "Churn" in df.columns else ""
    return out


def lambda_handler(event, context):
    rec = event["Records"][0]["s3"]
    bucket = rec["bucket"]["name"]
    key = unquote_plus(rec["object"]["key"])
    now = datetime.now(timezone.utc)
    name = key.split("/")[-1].rsplit(".", 1)[0]
    entry = {"file": key, "processed_at": now.isoformat(), "status": "SUCCESS",
             "rows_read": 0, "errors": [], "warnings": [], "output": None, "risk_bands": {}}
    try:
        if not key.lower().endswith(".csv"):
            raise ValueError("File is not a CSV")
        bundle, features, known = load_model(bucket)
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
        df = pd.read_csv(io.StringIO(body), dtype=str, keep_default_na=False)
        entry["rows_read"] = len(df)

        errors, warnings = validate(df, features, known)
        entry["warnings"] = warnings
        if errors:
            entry["status"], entry["errors"] = "FAILED", errors
        else:
            out = score(df, bundle, features, now)
            out_key = f"outputs/scores/{name}_scored.csv"
            s3.put_object(Bucket=bucket, Key=out_key,
                          Body=out.to_csv(index=False).encode("utf-8"),
                          ContentType="text/csv")
            entry["output"] = out_key
            entry["risk_bands"] = {k: int(v) for k, v in out["risk_band"].value_counts().items()}
    except Exception as e:
        entry["status"], entry["errors"] = "FAILED", [f"{type(e).__name__}: {e}"]

    (logger.info if entry["status"] == "SUCCESS" else logger.error)(json.dumps(entry))
    s3.put_object(Bucket=bucket, Key=f"logs/score_{now:%Y%m%dT%H%M%S}_{name}.json",
                  Body=json.dumps(entry, indent=2), ContentType="application/json")
    return entry
