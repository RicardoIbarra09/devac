#!/usr/bin/env bash
#SBATCH --job-name=p1_d5_periodico
#SBATCH --array=0-359%32             # 2 conjuntos x 2 escenarios x 9 estrategias x 10 repeticiones
#SBATCH --partition=amd256           # node218 y node227: resultados de red, los tiempos no van al paper
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2            # un modulo completo del opteron (dos hilos comparten la FPU)
#SBATCH --mem=8G
#SBATCH --time=03:00:00              # el peor caso es k=25: 40 reconstrucciones en 1,000 cambios
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
DATASETS=(politicas_un_evento politicas_w4u_n2000_s1)
SCENARIOS=(deactivation mixed)
# every<k> = reconstruye cada k cambios; theta<t> = regla de estres (los de D4); none = nunca
STRATEGIES=(every25 every50 every100 every200 theta0.01 theta0.02 theta0.05 theta0.1 none)
REPS="${REPS:-10}"                   # como D3; si cambia, ajusta --array (2 x 2 x 9 x REPS tareas)
SEED="${SEED:-1}"                    # la repeticion r usa SEED*1000 + r: los mismos cambios que en D3
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export TIMING=0
source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
check_env

# Piloto: PILOT=1 REPS=1 sbatch --array=0-35 d5_periodico.sbatch
if [ "${PILOT:-0}" = "1" ]; then EXTRA_ARGS="--changes 100 $EXTRA_ARGS"; fi

NS=${#STRATEGIES[@]}
IDX="${SLURM_ARRAY_TASK_ID:-0}"
check_index "$IDX" $(( ${#DATASETS[@]} * ${#SCENARIOS[@]} * NS * REPS ))
D=$(( IDX / (${#SCENARIOS[@]} * NS * REPS) ))
C=$(( (IDX / (NS * REPS)) % ${#SCENARIOS[@]} ))
K=$(( (IDX / REPS) % NS ))
REP=$(( IDX % REPS + 1 ))
DATASET="${DATASETS[$D]}"
SCENARIO="${SCENARIOS[$C]}"
STRATEGY="${STRATEGIES[$K]}"

case "$STRATEGY" in
  every*) RULE="--stress-threshold 1e9 --rebuild-every ${STRATEGY#every}" ;;
  theta*) RULE="--stress-threshold ${STRATEGY#theta}" ;;
  none)   RULE="--stress-threshold 1e9" ;;
esac

JOB="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
OUT="$RAW_DIR/d5/$JOB/$DATASET"
mkdir -p "$OUT"
log "D5 conjunto=$DATASET escenario=$SCENARIO estrategia=$STRATEGY rep=$REP"

# sin Q completa: la exactitud ya la cubren D1 y D3
$RUN python3 "$P1_DIR/run_dynamics.py" \
  --policies "$DEVAC_DIR/experiments/$DATASET.json" \
  --scenario "$SCENARIO" $RULE --tag "$STRATEGY" --networkx-sample 0 \
  --seed "$SEED" --rep "$REP" --out-dir "$OUT" $EXTRA_ARGS

log "D5 $DATASET $SCENARIO $STRATEGY r$REP terminado -> $OUT"
