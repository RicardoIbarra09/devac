# lectura de lo que escribe run_dynamics.py (y run_d1.py), para los resumen_dN.py y make_figures.py.
# una carpeta puede traer un meta por repeticion (tareas de slurm con --rep) o uno con todas
# (corrida local con --reps); se juntan por configuracion <escenario>_<etiqueta>

import csv
import json
import math
import re
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "resultados"
RAW = RESULTS / "crudo"

META = re.compile(r"^(?P<config>.+?)(_r(?P<rep>\d+))?_meta\.json$")
# deben coincidir entre repeticiones para poder juntarlas
SAME = ("script", "commit", "dirty", "policies_sha256", "timing")
SAME_PARAMS = ("policies", "scenario", "changes", "stress_threshold", "similarity_threshold", "no_audit",
               "seed", "tag", "networkx_sample", "rebuild_every", "final_rebuild")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_runs(folder: Path, with_rows: bool = True) -> Dict[str, dict]:
    """configuracion -> {"metas": [...], "reps": [{"summary", "rows", "meta"}...]} ordenadas por repeticion"""
    configs: Dict[str, dict] = {}
    seen = set()
    for path in sorted(Path(folder).glob("*_meta.json")):
        match = META.match(path.name)
        if not match:
            continue
        meta = _read(path)
        entry = configs.setdefault(match["config"], {"metas": [], "reps": []})
        entry["metas"].append(meta)
        for name, summary in zip(meta["files"], meta["replications"]):
            if name in seen:
                continue
            seen.add(name)
            rows = _read(Path(folder) / name) if with_rows else None
            entry["reps"].append({"summary": summary, "rows": rows, "meta": meta, "file": name})
    for config, entry in configs.items():
        entry["reps"].sort(key=lambda r: r["summary"].get("rep") or 0)
        check_same(Path(folder) / config, entry["metas"])
    return configs


def check_same(where: Path, metas: List[dict]) -> None:
    first = metas[0]
    for meta in metas[1:]:
        for key in SAME:
            if meta.get(key) != first.get(key):
                sys.exit(f"{where}: hay repeticiones con otro {key} ({meta.get(key)} vs {first.get(key)})")
        for key in SAME_PARAMS:
            if meta["params"].get(key) != first["params"].get(key):
                sys.exit(f"{where}: hay repeticiones con otro parametro {key}")


def _job_key(path: Path):
    return (int(path.name), "") if path.name.isdigit() else (-1, path.name)


def _is_pilot(job_dir: Path) -> bool:
    for meta in job_dir.rglob("*_meta.json"):
        return bool(_read(meta).get("pilot"))
    return True


def latest_job(experiment: str, raw: Path = RAW) -> Optional[Path]:
    """la carpeta de job mas reciente de crudo/<experimento>/, prefiriendo las que no son piloto"""
    base = raw / experiment
    if not base.is_dir():
        return None
    jobs = sorted((p for p in base.iterdir() if p.is_dir()), key=_job_key)
    full = [p for p in jobs if not _is_pilot(p)]
    if full:
        return full[-1]
    if jobs:
        print(f"aviso: {experiment} solo tiene corridas piloto; se usa {jobs[-1].name}")
        return jobs[-1]
    return None


def subfolders(job_dir: Path) -> List[Path]:
    return sorted(p for p in job_dir.iterdir() if p.is_dir())


def job_dir_from_argv(argv: List[str], experiment: str) -> Path:
    job_dir = Path(argv[0]) if argv else latest_job(experiment)
    if job_dir is None or not job_dir.is_dir():
        sys.exit(f"no hay resultados de {experiment.upper()} (resultados/crudo/{experiment}/<JOB>)")
    return job_dir


def write_csv(path: Path, rows: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
       10: 2.228, 15: 2.131, 20: 2.086, 30: 2.042}


def mean(values) -> float:
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else float("nan")


# media +- intervalo de confianza del 95 % (t de student) entre repeticiones
def ci95(values) -> float:
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return 0.0
    df = len(values) - 1
    t = T95.get(df) or T95[max(k for k in T95 if k <= df)]
    return t * statistics.stdev(values) / math.sqrt(len(values))


# ajuste t = c * x^b por minimos cuadrados en log-log; None si no hay dos puntos distintos
def fit_power(points):
    pts = [(math.log(x), math.log(y)) for x, y in points if x and y and x > 0 and y > 0]
    if len({x for x, _ in pts}) < 2:
        return None
    mx, my = statistics.fmean(x for x, _ in pts), statistics.fmean(y for _, y in pts)
    b = sum((x - mx) * (y - my) for x, y in pts) / sum((x - mx) ** 2 for x, _ in pts)
    return math.exp(my - b * mx), b


def _num(value):
    return float(value) if value not in (None, "") else None


# modelo de costo con los tiempos de D2 (node422): una reconstruccion completa segun los nodos + aristas
# del grafo reconstruido, y un cambio incremental (grafo + Q) segun el tamano medio del grafo.
# lo usan resumen_d5.py y make_figures.py; None si todavia no hay resultados/d2_escalamiento.csv
def cost_model(d2_csv: Path = RESULTS / "d2_escalamiento.csv") -> Optional[dict]:
    if not Path(d2_csv).is_file():
        return None
    with open(d2_csv, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rebuild, update = [], []
    for r in rows:
        # el build inicial es el mismo camino que una reconstruccion
        rebuild.append((_num(r["initial_nodes"]) + _num(r["initial_edges"]), _num(r["initial_build_s"])))
        if _num(r.get("rebuild_s")) is not None:
            rebuild.append((_num(r["rebuild_nodes"]) + _num(r["rebuild_edges"]), _num(r["rebuild_s"])))
        if _num(r.get("mean_size")) is not None:
            update.append((_num(r["mean_size"]), _num(r["mean_t_graph_s"]) + _num(r["mean_t_incremental_s"])))
    model = {"rebuild": fit_power(rebuild), "update": fit_power(update), "source": str(d2_csv),
             "timing": sorted({r["timing"] for r in rows}),
             "sizes": (min(x for x, _ in rebuild), max(x for x, _ in rebuild)) if rebuild else None}
    if model["rebuild"] is None or model["update"] is None:
        return None
    return model


def estimate_cost(model: dict, rebuild_sizes: List[float], changes: int, mean_size: float) -> dict:
    (cr, br), (cu, bu) = model["rebuild"], model["update"]
    rebuild = sum(cr * x ** br for x in rebuild_sizes)
    update = changes * cu * mean_size ** bu
    return {"est_rebuild_s": rebuild, "est_update_s": update, "est_cost_s": rebuild + update}
