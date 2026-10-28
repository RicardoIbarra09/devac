#!/usr/bin/env bash
#SBATCH --job-name=p1_d4_sensibilidad
#SBATCH --array=0-39%32              # 2 conjuntos x 4 umbrales x 5 repeticiones (ver el reparto abajo)
#SBATCH --partition=amd256           # node218 y node227: resultados de red, los tiempos no van al paper
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2            # un modulo completo del opteron (dos hilos comparten la FPU)
#SBATCH --mem=8G
#SBATCH --time=03:00:00              # con 0.01, politicas_un_evento reconstruye ~10 veces en 1,000 cambios
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
DATASETS=(politicas_un_evento politicas_w4u_n2000_s1)
THETAS=(0.01 0.02 0.05 0.1)
REPS="${REPS:-5}"                    # si cambia, ajusta --array (2 x 4 x REPS tareas)
SEED="${SEED:-1}"                    # la repeticion r usa SEED*1000 + r: mismos cambios para todos los umbrales
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export TIMING=0
source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
check_env

# Piloto: PILOT=1 REPS=1 sbatch --array=0-7 d4_sensibilidad.sbatch
if [ "${PILOT:-0}" = "1" ]; then EXTRA_ARGS="--changes 100 $EXTRA_ARGS"; fi

IDX="${SLURM_ARRAY_TASK_ID:-0}"
check_index "$IDX" $(( 2 * ${#THETAS[@]} * REPS ))
D=$(( IDX / (${#THETAS[@]} * REPS) ))
T=$(( (IDX / REPS) % ${#THETAS[@]} ))
REP=$(( IDX % REPS + 1 ))
DATASET="${DATASETS[$D]}"
THETA="${THETAS[$T]}"

JOB="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
OUT="$RAW_DIR/d4/$JOB/$DATASET"
mkdir -p "$OUT"
log "D4 conjunto=$DATASET umbral=$THETA rep=$REP"

# sin Q completa: la exactitud ya la cubren D1 y D3
$RUN python3 "$P1_DIR/run_dynamics.py" \
  --policies "$DEVAC_DIR/experiments/$DATASET.json" \
  --scenario deactivation --stress-threshold "$THETA" --tag "theta$THETA" --networkx-sample 0 \
  --seed "$SEED" --rep "$REP" --out-dir "$OUT" $EXTRA_ARGS

log "D4 $DATASET umbral=$THETA r$REP terminado -> $OUT"
