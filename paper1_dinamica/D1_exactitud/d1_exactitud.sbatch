#!/usr/bin/env bash
#SBATCH --job-name=p1_d1_exactitud
#SBATCH --array=0-11%32              # 2 conjuntos x 2 escenarios x 3 repeticiones (ver el reparto abajo)
#SBATCH --partition=amd256           # node218 y node227: resultados de red, los tiempos no van al paper
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2            # un modulo completo del opteron (dos hilos comparten la FPU)
#SBATCH --mem=8G
#SBATCH --time=08:00:00              # mixto con 5,000 cambios y Q completa en cada uno: ~2-3 h en el opteron
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
DATASETS=(politicas_w4u_n2000_s1 politicas_un_evento)   # principal y secundario (sin auditoria tiene muchos eventos repetidos)
SCENARIOS=(deactivation mixed)
CHANGES=("${CHANGES_DEACT:-1500}" "${CHANGES_MIXED:-5000}")   # el test usa 1,000 operaciones
CHECK_EVERY="${CHECK_EVERY:-50}"     # cada cuantos cambios se compara contra un build completo
REPS="${REPS:-3}"                    # si cambia, ajusta --array (2 x 2 x REPS tareas)
SEED="${SEED:-1}"                    # la repeticion r usa SEED*1000 + r (cambios y louvain)
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export TIMING=0
source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
check_env

# Piloto: PILOT=1 REPS=1 sbatch --array=0-3 d1_exactitud.sbatch
if [ "${PILOT:-0}" = "1" ]; then CHANGES=(100 100); CHECK_EVERY=25; fi

IDX="${SLURM_ARRAY_TASK_ID:-0}"
check_index "$IDX" $(( 2 * 2 * REPS ))
D=$(( IDX / (2 * REPS) ))
S=$(( (IDX / REPS) % 2 ))
REP=$(( IDX % REPS + 1 ))
DATASET="${DATASETS[$D]}"
SCENARIO="${SCENARIOS[$S]}"

JOB="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
OUT="$RAW_DIR/d1/$JOB/$DATASET"
mkdir -p "$OUT"
log "D1 conjunto=$DATASET escenario=$SCENARIO cambios=${CHANGES[$S]} rep=$REP"

$RUN python3 "$P1_DIR/D1_exactitud/run_d1.py" \
  --policies "$DEVAC_DIR/experiments/$DATASET.json" \
  --scenario "$SCENARIO" --changes "${CHANGES[$S]}" --check-graph-every "$CHECK_EVERY" \
  --seed "$SEED" --rep "$REP" --out-dir "$OUT" $EXTRA_ARGS

log "D1 $DATASET $SCENARIO r$REP terminado -> $OUT"
