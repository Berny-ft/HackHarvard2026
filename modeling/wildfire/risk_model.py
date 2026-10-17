"""10-year burn-likelihood model.

Each land cell is a sample. Features are terrain, fuel and fire history known
at the end of a "history" year; the label is whether the cell burned in the
following decade. We train on one decade and test on the next, so the score
is an honest out-of-time estimate.
"""
import json

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, roc_auc_score

from . import features as F
from .config import (DATA, OUT, TEST_HISTORY_END, TEST_LABEL_END,
                     TRAIN_HISTORY_END, TRAIN_LABEL_END)
from .export import save_overlay

FEATURES = [
    "elevation", "slope", "northness", "eastness", "tpi", "fuel_group",
    "canopy_cover", "dist_urban_km", "dist_coast_km", "times_burned",
    "years_since_burn",
]


def _matrix(static, years, stack, history_end, mask):
    count, since = F.history_features(years, stack, history_end)
    cols = {**static, "times_burned": count, "years_since_burn": since}
    return np.column_stack([cols[k][mask].astype(np.float32) for k in FEATURES])


def _top_capture(y, p, frac=0.1):
    """Share of burned cells that fall in the top `frac` riskiest cells."""
    cut = np.quantile(p, 1 - frac)
    return float(y[p >= cut].sum() / max(1, y.sum()))


def _model():
    cat = [FEATURES.index("fuel_group")]
    return HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.06, max_leaf_nodes=48,
        min_samples_leaf=200, l2_regularization=1.0,
        categorical_features=cat, random_state=0,
    )


def run():
    OUT.mkdir(exist_ok=True)
    static, land, prof = F.static_features("region")
    burnable = land & (static["fbfm40"] != 98)  # drop open water
    perims = F.load_perimeters()
    years, stack = F.burn_history(perims, prof)
    np.savez_compressed(DATA / "region_burn_history.npz", years=years, stack=stack)

    Xtr = _matrix(static, years, stack, TRAIN_HISTORY_END, burnable)
    ytr = F.burned_between(years, stack, TRAIN_HISTORY_END, TRAIN_LABEL_END)[burnable]
    Xte = _matrix(static, years, stack, TEST_HISTORY_END, burnable)
    yte = F.burned_between(years, stack, TEST_HISTORY_END, TEST_LABEL_END)[burnable]
    print(f"[risk] {burnable.sum():,} cells | train burn rate {ytr.mean():.3f} | test burn rate {yte.mean():.3f}")

    model = _model().fit(Xtr, ytr)
    p = model.predict_proba(Xte)[:, 1]
    # Baseline: "it burns where it burned before" (times burned, then recency).
    count, since = F.history_features(years, stack, TEST_HISTORY_END)
    base = (count * 100 - since)[burnable].astype(float)
    metrics = {
        "train": f"history<= {TRAIN_HISTORY_END}, label {TRAIN_HISTORY_END + 1}-{TRAIN_LABEL_END}",
        "test": f"history<= {TEST_HISTORY_END}, label {TEST_HISTORY_END + 1}-{TEST_LABEL_END}",
        "cells": int(burnable.sum()),
        "test_burn_rate": float(yte.mean()),
        "auc": float(roc_auc_score(yte, p)),
        "avg_precision": float(average_precision_score(yte, p)),
        "top10pct_capture": _top_capture(yte, p),
        "baseline_history_auc": float(roc_auc_score(yte, base)),
        "baseline_history_top10pct_capture": _top_capture(yte, base),
    }
    rng = np.random.default_rng(0)
    idx = rng.choice(len(yte), size=min(60000, len(yte)), replace=False)
    imp = permutation_importance(model, Xte[idx], yte[idx], scoring="roc_auc",
                                 n_repeats=3, random_state=0)
    metrics["importance_auc_drop"] = dict(sorted(
        ((k, round(float(v), 4)) for k, v in zip(FEATURES, imp.importances_mean)),
        key=lambda kv: -kv[1]))
    print(json.dumps(metrics, indent=2))

    # Forward-looking map: fit on both decades, score with history through 2025.
    final = _model().fit(np.vstack([Xtr, Xte]), np.concatenate([ytr, yte]))
    Xnow = _matrix(static, years, stack, TEST_LABEL_END, burnable)
    risk = np.full(burnable.shape, np.nan, dtype=np.float32)
    risk[burnable] = final.predict_proba(Xnow)[:, 1]
    save_overlay(risk, prof, "burn_probability", vmin=0, vmax=float(np.nanquantile(risk, 0.995)),
                 cmap="fire")
    count, since = F.history_features(years, stack, TEST_LABEL_END)
    save_overlay(np.where(land, count, np.nan).astype(np.float32), prof, "times_burned",
                 vmin=0, vmax=6, cmap="fire")
    (OUT / "risk_model_metrics.json").write_text(json.dumps(metrics, indent=2))
