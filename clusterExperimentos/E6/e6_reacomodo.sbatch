#!/usr/bin/env bash
#SBATCH --job-name=e6_reacomodo
#SBATCH --array=0-1%1               # 0 = 2,000 eventos, 1 = 10,000 eventos; una tarea a la vez
#SBATCH --partition=gold_IB
#SBATCH --nodelist=node419          # mismo nodo que la corrida definitiva de E5
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2           # un nucleo para el escritor (y el reacomodo) y otro para el lector
#SBATCH --exclusive
#SBATCH --mem=32G
#SBATCH --time=12:00:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
N_VALUES=(2000 10000)
REPS="${REPS:-10}"
N_OPS="${N_OPS:-1000}"
TAUS="${TAUS:-0.02 0.05 0.1 inf}"   # umbrales de estres; inf = reacomodo apagado
MODE="${MODE:-uniforme}"
P_MIX="${P_MIX:-0.1}"
THETA="${THETA:-0.3}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export PIN_CORE=0                   # los hilos se fijan desde Python, como en E5
source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E6_DIR="$HOME/devac/clusterExperimentos/E6"

IDX="${SLURM_ARRAY_TASK_ID:-0}"
N="${N_VALUES[$IDX]}"

# Piloto: PILOT=1 sbatch --array=0 e6_reacomodo.sbatch  ->  1 repeticion, 300 modificaciones
if [ "${PILOT:-0}" = "1" ]; then REPS=1; N_OPS=300; fi

OUT="$RESULTS_DIR/e6/${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
mkdir -p "$OUT" "$AGG_DIR/e6"

log "E6 n=$N reps=$REPS ops=$N_OPS taus=[$TAUS] theta=$THETA nodo=$(hostname) commit=$DEVAC_COMMIT"

python3 "$E6_DIR/run_e6.py" --n "$N" --reps "$REPS" --n-ops "$N_OPS" --taus $TAUS \
  --mode "$MODE" --p-mix "$P_MIX" --theta "$THETA" --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT"/e6_*_n"${N}"_* "$AGG_DIR/e6/" 2>/dev/null || true
log "E6 n=$N terminado -> $OUT"
