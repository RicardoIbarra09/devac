#!/usr/bin/env bash
#SBATCH --job-name=e4_dinamica
#SBATCH --array=0-79                # 2 conjuntos x 4 configuraciones x 10 repeticiones (ver el reparto abajo)
#SBATCH --partition=gold_IB
#SBATCH --nodelist=node419          # mismo nodo que E2 y E3
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --exclusive
#SBATCH --time=01:30:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
DATASETS=(politicas_un_evento politicas_w4u_n2000_s1)
# umbral de estres por conjunto; con 0.05, W4-U solo reconstruye 1 o 2 veces por cada 1,000 cambios
THRESHOLDS=("${THRESHOLD_UN:-0.05}" "${THRESHOLD_W4U:-0.05}")
CONFIGS=(deactivation:rebuild deactivation:norebuild mixed:rebuild mixed:norebuild)
REPS="${REPS:-10}"                  # si cambia, ajusta tambien --array (2 x 4 x REPS tareas)
SEED="${SEED:-1}"                   # fija la secuencia de cambios; louvain usa SEED*1000 + repeticion
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env

# Pilotaje: PILOT=1 REPS=2 sbatch --array=0-15 e4_dinamica.sbatch
if [ "${PILOT:-0}" = "1" ]; then EXTRA_ARGS="--changes 100 $EXTRA_ARGS"; fi

# tarea -> conjunto, configuracion y repeticion
IDX="${SLURM_ARRAY_TASK_ID:-0}"
D=$(( IDX / (4 * REPS) ))
C=$(( (IDX / REPS) % 4 ))
REP=$(( IDX % REPS + 1 ))
DATASET="${DATASETS[$D]}"
SCENARIO="${CONFIGS[$C]%%:*}"
MODE="${CONFIGS[$C]##*:}"
if [ "$MODE" = "rebuild" ]; then THRESHOLD="${THRESHOLDS[$D]}"; else THRESHOLD=1e9; fi

JOB="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
OUT="$RESULTS_DIR/e4/$JOB/$DATASET"
mkdir -p "$OUT" "$AGG_DIR/e4/$JOB/$DATASET"

log "E4 conjunto=$DATASET escenario=$SCENARIO $MODE umbral=$THRESHOLD rep=$REP nodo=$(hostname) commit=$DEVAC_COMMIT pin='$PIN'"

$PIN python3 "$DEVAC_DIR/experiments/run_dynamics.py" \
  --policies "$DEVAC_DIR/experiments/$DATASET.json" \
  --scenario "$SCENARIO" --stress-threshold "$THRESHOLD" --tag "$MODE" \
  --seed "$SEED" --rep "$REP" --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT/${SCENARIO}_${MODE}_r$(printf %02d "$REP").json" "$OUT/${SCENARIO}_${MODE}_r$(printf %02d "$REP")_meta.json" \
   "$AGG_DIR/e4/$JOB/$DATASET/"
log "E4 $DATASET $SCENARIO $MODE r$REP terminado -> $OUT"
