#!/usr/bin/env bash
#SBATCH --job-name=p1_d3_dinamica
#SBATCH --array=0-79%32              # 2 conjuntos x 4 configuraciones x 10 repeticiones (ver el reparto abajo)
#SBATCH --partition=amd256           # node218 y node227: resultados de red, los tiempos no van al paper
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2            # un modulo completo del opteron (dos hilos comparten la FPU)
#SBATCH --mem=8G
#SBATCH --time=04:00:00              # 1,000 cambios con Q completa en cada uno + reconstrucciones
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
DATASETS=(politicas_un_evento politicas_w4u_n2000_s1)
# umbral de estres por conjunto; con 0.05, W4-U solo reconstruye 1 o 2 veces por cada 1,000 cambios
THRESHOLDS=("${THRESHOLD_UN:-0.05}" "${THRESHOLD_W4U:-0.05}")
CONFIGS=(deactivation:rebuild deactivation:norebuild mixed:rebuild mixed:norebuild)
REPS="${REPS:-10}"                   # si cambia, ajusta tambien --array (2 x 4 x REPS tareas)
SEED="${SEED:-1}"                    # la repeticion r usa SEED*1000 + r (cambios y louvain)
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export TIMING=0
source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
check_env

# Piloto: PILOT=1 REPS=2 sbatch --array=0-15 d3_dinamica.sbatch
if [ "${PILOT:-0}" = "1" ]; then EXTRA_ARGS="--changes 100 $EXTRA_ARGS"; fi

# tarea -> conjunto, configuracion y repeticion
IDX="${SLURM_ARRAY_TASK_ID:-0}"
check_index "$IDX" $(( 2 * 4 * REPS ))
D=$(( IDX / (4 * REPS) ))
C=$(( (IDX / REPS) % 4 ))
REP=$(( IDX % REPS + 1 ))
DATASET="${DATASETS[$D]}"
SCENARIO="${CONFIGS[$C]%%:*}"
MODE="${CONFIGS[$C]##*:}"
if [ "$MODE" = "rebuild" ]; then THRESHOLD="${THRESHOLDS[$D]}"; else THRESHOLD=1e9; fi

JOB="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
OUT="$RAW_DIR/${D3_SUBDIR:-d3}/$JOB/$DATASET"
mkdir -p "$OUT"
log "D3 conjunto=$DATASET escenario=$SCENARIO $MODE umbral=$THRESHOLD rep=$REP"

$RUN python3 "$P1_DIR/run_dynamics.py" \
  --policies "$DEVAC_DIR/experiments/$DATASET.json" \
  --scenario "$SCENARIO" --stress-threshold "$THRESHOLD" --tag "$MODE" \
  --seed "$SEED" --rep "$REP" --out-dir "$OUT" $EXTRA_ARGS

log "D3 $DATASET $SCENARIO $MODE r$REP terminado -> $OUT"
