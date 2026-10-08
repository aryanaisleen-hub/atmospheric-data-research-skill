"""统计与相关分析。"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _clean(x: pd.Series, y: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    frame = pd.DataFrame({"x": x, "y": y}).apply(pd.to_numeric, errors="coerce").dropna()
    return frame["x"].to_numpy(dtype=float), frame["y"].to_numpy(dtype=float)


def pearson(x: pd.Series, y: pd.Series) -> dict[str, Any]:
    from scipy import stats

    xs, ys = _clean(x, y)
    if xs.size < 3 or np.std(xs) == 0 or np.std(ys) == 0:
        return {"r": float("nan"), "p": float("nan"), "n": int(xs.size)}
    r, p = stats.pearsonr(xs, ys)
    return {"r": float(r), "p": float(p), "n": int(xs.size)}


def spearman(x: pd.Series, y: pd.Series) -> dict[str, Any]:
    from scipy import stats

    xs, ys = _clean(x, y)
    if xs.size < 3:
        return {"rho": float("nan"), "p": float("nan"), "n": int(xs.size)}
    rho, p = stats.spearmanr(xs, ys)
    return {"rho": float(rho), "p": float(p), "n": int(xs.size)}


def linear_fit(x: pd.Series, y: pd.Series) -> dict[str, Any]:
    from scipy import stats

    xs, ys = _clean(x, y)
    if xs.size < 3 or np.std(xs) == 0:
        return {"slope": float("nan"), "intercept": float("nan"), "r": float("nan"), "p": float("nan"), "n": int(xs.size)}
    result = stats.linregress(xs, ys)
    return {
        "slope": float(result.slope),
        "intercept": float(result.intercept),
        "r": float(result.rvalue),
        "p": float(result.pvalue),
        "n": int(xs.size),
    }


def lag_correlation(x: pd.Series, y: pd.Series, max_lag: int = 7) -> pd.DataFrame:
    """超前-滞后相关。

    lag > 0 表示 x 超前 y（x 变化在前，y 响应在后）。
    返回列：lag, r, p, n。
    """
    frame = pd.DataFrame({"x": pd.to_numeric(x, errors="coerce"), "y": pd.to_numeric(y, errors="coerce")}).dropna()
    rows: list[dict[str, Any]] = []
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a = frame["x"].iloc[: len(frame) - lag] if lag else frame["x"]
            b = frame["y"].iloc[lag:] if lag else frame["y"]
        else:
            a = frame["x"].iloc[-lag:]
            b = frame["y"].iloc[: len(frame) + lag]
        stats = pearson(a.reset_index(drop=True), b.reset_index(drop=True))
        rows.append({"lag": lag, **stats})
    return pd.DataFrame(rows)


def best_lag(lag_frame: pd.DataFrame) -> dict[str, Any]:
    """取 |r| 最大的滞后。"""
    valid = lag_frame.dropna(subset=["r"])
    if valid.empty:
        return {"lag": None, "r": float("nan")}
    index = valid["r"].abs().idxmax()
    return {"lag": int(valid.loc[index, "lag"]), "r": float(valid.loc[index, "r"])}
