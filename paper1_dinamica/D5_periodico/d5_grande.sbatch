#!/usr/bin/env bash
#SBATCH --job-name=p1_d5_grande
#SBATCH --array=0-26%14              # 9 estrategias x 3 repeticiones, W4-U de 20,000 eventos
#SBATCH --partition=amd256           # node218 y node227: resultados de red, los tiempos no van al paper
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2            # un modulo completo del opteron (dos hilos comparten la FPU)
#SBATCH --mem=16G                    # pico estimado ~6 GB (en local: 1.5 GB con 10,000, crece como n^1.9)
#SBATCH --time=10:00:00              # peor caso k=25: 41 reconstrucciones de ~2 min en local, ~6 min en el opteron: ~4-5 h
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
N=20000                              # eventos; la red sale de gen_w4.py como en D2
W4_SEED=1
SCENARIO=deactivation                # 1,000 desactivaciones
STRATEGIES=(every25 every50 every100 every200 theta0.01 theta0.02 theta0.05 theta0.1 none)
REPS="${REPS:-3}"                    # si cambia, ajusta --array (9 x REPS tareas)
SEED="${SEED:-1}"                    # la repeticion r usa SEED*1000 + r, como en D3 y D5
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export TIMING=0
source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
check_env

# Piloto: PILOT=1 REPS=1 sbatch --array=0-8 d5_grande.sbatch
if [ "${PILOT:-0}" = "1" ]; then EXTRA_ARGS="--changes 100 $EXTRA_ARGS"; fi

NS=${#STRATEGIES[@]}
IDX="${SLURM_ARRAY_TASK_ID:-0}"
check_index "$IDX" $(( NS * REPS ))
STRATEGY="${STRATEGIES[$(( IDX / REPS ))]}"
REP=$(( IDX % REPS + 1 ))

case "$STRATEGY" in
  every*) RULE="--stress-threshold 1e9 --rebuild-every ${STRATEGY#every}" ;;
  theta*) RULE="--stress-threshold ${STRATEGY#theta}" ;;
  none)   RULE="--stress-threshold 1e9" ;;
esac

JOB="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
DATASET="w4u_n${N}_s${W4_SEED}"
OUT="$RAW_DIR/d5_grande/$JOB/$DATASET"
DATA="$RAW_DIR/d5_grande/$JOB/redes/$DATASET.json"
mkdir -p "$OUT" "$(dirname "$DATA")"
# es determinista: cada tarea la vuelve a generar y el sha256 queda en el meta.json
python3 "$DEVAC_DIR/clusterExperimentos/E2/gen_w4.py" --n "$N" --seed "$W4_SEED" --out "$DATA.$IDX.tmp"
mv "$DATA.$IDX.tmp" "$DATA"
log "D5 grande n=$N escenario=$SCENARIO estrategia=$STRATEGY rep=$REP"

# sin Q completa: la exactitud la cubre D1
$RUN python3 "$P1_DIR/run_dynamics.py" \
  --policies "$DATA" --scenario "$SCENARIO" $RULE --tag "$STRATEGY" --networkx-sample 0 \
  --seed "$SEED" --rep "$REP" --out-dir "$OUT" $EXTRA_ARGS

log "D5 grande $STRATEGY r$REP terminado -> $OUT"
