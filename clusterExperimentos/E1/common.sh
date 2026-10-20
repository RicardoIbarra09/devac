#!/usr/bin/env bash
# common.sh -- entorno compartido por todos los sbatch de la bateria DEvAC.
# Se carga con:  source "$(dirname "$0")/common.sh"   (o ruta absoluta desde el sbatch)


# --- Rutas ------------------------------------------------------------------
# [AJUSTAR] donde esta el demo (la carpeta que contiene el paquete "devac/" y "experiments/")
export DEVAC_DIR="${DEVAC_DIR:-$HOME/devac}"
# [AJUSTAR] datos crudos de corridas (SSD, rapido) y agregados (HOME, respaldado)
export SCRATCH_BASE="${SCRATCH_BASE:-$HOME}"
export RESULTS_DIR="${RESULTS_DIR:-$SCRATCH_BASE/devac_resultados}"
export AGG_DIR="${AGG_DIR:-$HOME/devac_agregados}"
# [AJUSTAR] directorio con estos scripts (run_e1.py, ...)
export CLUSTER_DIR="${CLUSTER_DIR:-$HOME/devac/clusterExperimentos/E1}"

# --- Python -----------------------------------------------------------------
# [AJUSTAR] modulo o venv. Descomenta lo que aplique (ver `module avail python`).
# module purge && module load python/3.12
source "$HOME/venvs/devac/bin/activate"
export PYTHONPATH="$DEVAC_DIR:${PYTHONPATH:-}"
export PYTHONHASHSEED=0          # reproducibilidad del orden de conjuntos/diccionarios entre corridas
export PYTHONUNBUFFERED=1        # log en tiempo real en el .out de Slurm

# --- Trazabilidad -----------------------------------------------------------
# Hash del codigo del demo (si es repo git). Queda en el meta.json de cada corrida.
if [ -z "${DEVAC_COMMIT:-}" ]; then
  DEVAC_COMMIT="$(git -C "$DEVAC_DIR" rev-parse --short HEAD 2>/dev/null || echo desconocido)"
fi
export DEVAC_COMMIT

# --- Afinidad ---------------------------------------------------------------
# Fija el proceso a un solo nucleo (mediciones de tiempo: E2-E7). En E1 no hace falta.
# Uso:  $PIN python3 script.py ...
if command -v taskset >/dev/null 2>&1 && [ "${PIN_CORE:-1}" = "1" ]; then
  PIN="taskset -c 0"
else
  PIN=""
fi
export PIN

# --- Utilidades -------------------------------------------------------------
log() { echo "[$(date -u +%FT%TZ)] $*"; }

check_env() {
  [ -d "$DEVAC_DIR/devac" ] || { echo "ERROR: no existe $DEVAC_DIR/devac (ajusta DEVAC_DIR en common.sh)"; exit 2; }
  python3 -c "import pydantic, networkx, numpy" 2>/dev/null || { echo "ERROR: faltan dependencias (pydantic, networkx, numpy) en el python activo"; exit 2; }
}
