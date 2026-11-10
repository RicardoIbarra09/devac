#!/usr/bin/env bash
#SBATCH --job-name=e5_dinamica
#SBATCH --array=0-1%1               # 0 = 2,000 eventos, 1 = 10,000 eventos; una tarea a la vez
#SBATCH --partition=gold_noIB
#SBATCH --nodelist=node422          # mismo nodo que E3 y E4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2           # un nucleo para el hilo escritor y otro para el lector
#SBATCH --exclusive
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
N_VALUES=(2000 10000)
REPS="${REPS:-10}"
N_OPS="${N_OPS:-1000}"
MODE="${MODE:-uniforme}"            # uniforme (W4-U) o estructurada (W4-E, con P_MIX)
P_MIX="${P_MIX:-0.1}"
THETA="${THETA:-0.3}"               # umbral de similitud del motor (el valor actual)
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

# E5 fija sus dos hilos a nucleos distintos desde Python; no se usa el taskset de common.sh
export PIN_CORE=0
source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E5_DIR="$HOME/devac/clusterExperimentos/E5"

IDX="${SLURM_ARRAY_TASK_ID:-0}"
N="${N_VALUES[$IDX]}"

# Pilotaje: PILOT=1 sbatch e5_dinamica.sbatch  ->  1 repeticion, 100 operaciones por tipo
# Control del GIL: REPS=3 EXTRA_ARGS="--switch-interval 0.0005" sbatch --array=1 e5_dinamica.sbatch
if [ "${PILOT:-0}" = "1" ]; then REPS=1; N_OPS=100; fi

OUT="$RESULTS_DIR/e5/${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
mkdir -p "$OUT" "$AGG_DIR/e5"

log "E5 n=$N reps=$REPS ops=$N_OPS modo=$MODE theta=$THETA nodo=$(hostname) commit=$DEVAC_COMMIT"

python3 "$E5_DIR/run_e5.py" --n "$N" --reps "$REPS" --n-ops "$N_OPS" \
  --mode "$MODE" --p-mix "$P_MIX" --theta "$THETA" --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT"/e5_*_n"${N}"{,_gil*}.csv "$OUT"/e5_*_n"${N}"{,_gil*}_meta.json "$AGG_DIR/e5/" 2>/dev/null || true
log "E5 n=$N terminado -> $OUT"
