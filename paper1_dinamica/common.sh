#!/usr/bin/env bash
# common.sh -- entorno compartido por los sbatch del paper 1 (dinamica de la red de eventos).
# Se carga con:  source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
#
# TIMING=1  mediciones de tiempo (solo D2): el proceso queda fijo en un nucleo con taskset.
# TIMING=0  resultados de red (D1, D3, D4): sin taskset, srun --cpu-bind=cores sobre los nucleos
#           que asigno slurm. Sus columnas de tiempo se guardan, pero el meta.json las marca
#           como timing_not_reported.

# --- Rutas ------------------------------------------------------------------
export DEVAC_DIR="${DEVAC_DIR:-$HOME/devac}"
export P1_DIR="$DEVAC_DIR/paper1_dinamica"
# resultados crudos: dentro del repo pero fuera de git (ver .gitignore)
export RAW_DIR="${RAW_DIR:-$P1_DIR/resultados/crudo}"

# --- Python -----------------------------------------------------------------
# [AJUSTAR] venv con pydantic, networkx y numpy (o el module load que aplique)
source "${DEVAC_VENV:-$HOME/venvs/devac}/bin/activate"
export PYTHONPATH="$DEVAC_DIR:${PYTHONPATH:-}"
export PYTHONHASHSEED=0          # mismo orden de conjuntos y diccionarios entre corridas
export PYTHONUNBUFFERED=1        # log en tiempo real en el .out de slurm

# un solo hilo para las bibliotecas numericas en los dos modos: en amd256 corren hasta 32 tareas
# a la vez y no deben pelearse por los nucleos
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

# --- Trazabilidad -----------------------------------------------------------
if [ -z "${DEVAC_COMMIT:-}" ]; then
  DEVAC_COMMIT="$(git -C "$DEVAC_DIR" rev-parse HEAD 2>/dev/null || echo desconocido)"
fi
export DEVAC_COMMIT

# --- Modo de medicion -------------------------------------------------------
export TIMING="${TIMING:-0}"
if [ "$TIMING" = "1" ]; then
  # primer nucleo que el job tiene permitido (con --exclusive son todos)
  TIMING_CORE="${TIMING_CORE:-$(python3 -c 'import os; print(min(os.sched_getaffinity(0)))')}"
  RUN="taskset -c $TIMING_CORE"
elif [ -n "${SLURM_JOB_ID:-}" ] && command -v srun >/dev/null 2>&1; then
  # slurm 22.05: srun ya no hereda --cpus-per-task del sbatch, hay que pasarlo
  RUN="srun --ntasks=1 --cpus-per-task=${SLURM_CPUS_PER_TASK:-1} --cpu-bind=cores"
else
  RUN=""
fi
export RUN

# --- Utilidades -------------------------------------------------------------
log() { echo "[$(date -u +%FT%TZ)] $*"; }

check_env() {
  [ -d "$DEVAC_DIR/devac" ] || { echo "ERROR: no existe $DEVAC_DIR/devac (ajusta DEVAC_DIR)"; exit 2; }
  python3 -c "import pydantic, networkx, numpy" 2>/dev/null \
    || { echo "ERROR: faltan dependencias (pydantic, networkx, numpy) en el python activo"; exit 2; }
  log "nodo=$(hostname) cpu='$(lscpu | sed -n 's/^Model name: *//p')' commit=$DEVAC_COMMIT TIMING=$TIMING RUN='$RUN'"
}

# tarea fuera del array declarado: error claro en vez de un indice fuera de rango
check_index() {
  local idx="$1" total="$2"
  if [ "$idx" -ge "$total" ]; then
    echo "ERROR: tarea $idx fuera de rango; este job tiene $total tareas (--array=0-$((total - 1)))"
    exit 2
  fi
}
