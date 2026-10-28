# datos del entorno que van en cada meta.json del paper 1: nodo, cpu, paquetes, commit y
# si los tiempos sirven para el paper (solo D2 corre con TIMING=1)

import os
import platform
import socket
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS")
TIMING_NOT_REPORTED = "timing_not_reported"


def _run(cmd: List[str], cwd: Optional[Path] = None) -> Optional[str]:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def cpu_model() -> str:
    out = _run(["lscpu"])
    for line in (out or "").splitlines():
        if line.lower().startswith("model name"):
            return line.split(":", 1)[1].strip()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def pip_freeze() -> List[str]:
    out = _run([sys.executable, "-m", "pip", "freeze"])
    if out is not None:
        return out.splitlines()
    # venv sin pip (p. ej. poetry): lo mismo desde los metadatos instalados
    from importlib import metadata
    return sorted(f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions())


def git_state() -> dict:
    # en el cluster puede no haber repo: common.sh exporta DEVAC_COMMIT.
    # los archivos sin seguimiento no cuentan (otras corridas escriben resultados mientras tanto)
    commit = _run(["git", "rev-parse", "HEAD"], ROOT) or os.environ.get("DEVAC_COMMIT")
    status = _run(["git", "status", "--porcelain", "--untracked-files=no"], ROOT)
    return {"commit": commit, "dirty": bool(status) if status is not None else None}


def timing_reported() -> bool:
    return os.environ.get("TIMING") == "1"


def environment() -> dict:
    try:
        affinity = sorted(os.sched_getaffinity(0))
    except AttributeError:
        affinity = None
    return {
        **git_state(),
        "node": socket.gethostname(),
        "cpu": cpu_model(),
        "cpu_affinity": affinity,  # nucleos donde puede correr el proceso (taskset o --cpu-bind)
        "threads": {k: os.environ.get(k) for k in THREAD_VARS},
        "timing": "reported" if timing_reported() else TIMING_NOT_REPORTED,
        "pilot": os.environ.get("PILOT") == "1",
        "python": platform.python_version(),
        "machine": platform.platform(),
        "pip_freeze": pip_freeze(),
        "slurm_job_id": os.environ.get("SLURM_ARRAY_JOB_ID") or os.environ.get("SLURM_JOB_ID"),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
        "slurm_partition": os.environ.get("SLURM_JOB_PARTITION"),
    }
