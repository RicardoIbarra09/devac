import math
from collections import Counter
from typing import Dict, List, Optional

import numpy as np

from devac_sim.des import SimResult

# t de student al 95% (dos colas) por grados de libertad
_T95 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228,
    11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093,
    20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048,
    29: 2.045, 30: 2.042,
}


def t95(df: int) -> float:
    if df in _T95:
        return _T95[df]
    if df <= 40:
        return 2.021
    if df <= 60:
        return 2.000
    if df <= 120:
        return 1.980
    return 1.960


def _pcts(values: List[float]) -> dict:
    if not values:
        return {}
    arr = np.asarray(values, dtype=float)
    return {
        "mean": float(arr.mean()),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(arr.max()),
    }


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def summarize(result: SimResult, warmup: float = 0.0) -> dict:
    rows = [r for r in result.rows if r["t_arrival"] >= warmup]
    horizon = max((r["t_end"] for r in result.rows), default=warmup)
    span = horizon - warmup
    controls = result.controls

    # promedios en el tiempo sobre [warmup, horizonte]
    area_l = sum(_overlap(r["t_arrival"], r["t_end"], warmup, horizon) for r in result.rows)
    area_lq = sum(_overlap(r["t_arrival"], r["t_start"], warmup, horizon) for r in result.rows)
    area_busy = sum(_overlap(r["t_start"], r["t_end"], warmup, horizon) for r in result.rows)

    throughput = len(rows) / span if span > 0 else 0.0
    response = [r["response_us"] for r in rows]
    return {
        "requests": len(rows),
        "warmup_s": warmup,
        "horizon_s": horizon,
        "throughput_rps": throughput,
        "utilization": area_busy / (span * result.servers) if span > 0 else 0.0,
        "mean_in_system": area_l / span if span > 0 else 0.0,
        "mean_in_queue": area_lq / span if span > 0 else 0.0,
        # ley de little: debe parecerse a mean_in_system
        "little_l": throughput * (np.mean(response) / 1e6) if response else 0.0,
        "wait_us": _pcts([r["wait_us"] for r in rows]),
        "service_us": _pcts([r["service_us"] for r in rows]),
        "response_us": _pcts(response),
        "steps": _pcts([float(r["steps"]) for r in rows if r["steps"] is not None]),
        "decisions": dict(Counter(r["decision"] for r in rows)),
        "incorrect": sum(1 for r in rows if not r["correct"]),
        "stale": sum(1 for r in rows if r["stale"]),
        "controls": dict(Counter(c["kind"] for c in controls)),
        "control_errors": sum(1 for c in controls if c["error"]),
        "control_cost_us": _pcts([c["real_cost_us"] for c in controls]),
        "rebuilds": result.rebuilds,
        "oracle_errors": len(result.oracle_errors),
    }


def time_series(result: SimResult, bin_s: float) -> List[dict]:
    if bin_s <= 0:
        raise ValueError("bin debe ser > 0")
    horizon = max((r["t_end"] for r in result.rows), default=0.0)
    n_bins = max(1, math.ceil(horizon / bin_s))
    bins = [{"bin": i, "t0": i * bin_s, "t1": (i + 1) * bin_s, "arrivals": 0, "completions": 0,
             "_resp": [], "_l": 0.0, "_lq": 0.0, "_busy": 0.0} for i in range(n_bins)]

    def spread(t0: float, t1: float, key: str) -> None:
        first, last = int(t0 // bin_s), min(int(t1 // bin_s), n_bins - 1)
        for i in range(first, last + 1):
            bins[i][key] += _overlap(t0, t1, bins[i]["t0"], bins[i]["t1"])

    for r in result.rows:
        bins[min(int(r["t_arrival"] // bin_s), n_bins - 1)]["arrivals"] += 1
        done = bins[min(int(r["t_end"] // bin_s), n_bins - 1)]
        done["completions"] += 1
        done["_resp"].append(r["response_us"])
        spread(r["t_arrival"], r["t_end"], "_l")
        spread(r["t_arrival"], r["t_start"], "_lq")
        spread(r["t_start"], r["t_end"], "_busy")

    versions = sorted((c["t"], c["version"]) for c in result.controls)
    out = []
    for b in bins:
        resp = b.pop("_resp")
        version = 0
        for t, v in versions:
            if t >= b["t1"]:
                break
            version = v
        out.append({
            "bin": b["bin"], "t0": b["t0"], "t1": b["t1"],
            "arrivals": b["arrivals"], "completions": b["completions"],
            "throughput_rps": b["completions"] / bin_s,
            "mean_response_us": float(np.mean(resp)) if resp else None,
            "p95_response_us": float(np.percentile(resp, 95)) if resp else None,
            "mean_in_system": b.pop("_l") / bin_s,
            "mean_in_queue": b.pop("_lq") / bin_s,
            "utilization": b.pop("_busy") / (bin_s * result.servers),
            "policy_version": version,
        })
    return out


# metricas escalares que se resumen entre replicas
REPLICATION_METRICS = {
    "throughput_rps": lambda s: s["throughput_rps"],
    "utilization": lambda s: s["utilization"],
    "mean_in_system": lambda s: s["mean_in_system"],
    "mean_in_queue": lambda s: s["mean_in_queue"],
    "wait_mean_us": lambda s: s["wait_us"].get("mean"),
    "response_mean_us": lambda s: s["response_us"].get("mean"),
    "response_p95_us": lambda s: s["response_us"].get("p95"),
    "response_p99_us": lambda s: s["response_us"].get("p99"),
    "service_mean_us": lambda s: s["service_us"].get("mean"),
    "steps_mean": lambda s: s["steps"].get("mean"),
    "incorrect": lambda s: s["incorrect"],
    "stale": lambda s: s["stale"],
}


def confidence_interval(values: List[float]) -> Dict[str, Optional[float]]:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "sd": None, "ci95": None}
    mean = float(np.mean(values))
    if n == 1:
        return {"n": 1, "mean": mean, "sd": None, "ci95": None}
    sd = float(np.std(values, ddof=1))
    return {"n": n, "mean": mean, "sd": sd, "ci95": t95(n - 1) * sd / math.sqrt(n)}


def across_replications(summaries: List[dict]) -> Dict[str, dict]:
    out = {}
    for name, get in REPLICATION_METRICS.items():
        values = [v for v in (get(s) for s in summaries) if v is not None]
        out[name] = confidence_interval(values)
    return out
