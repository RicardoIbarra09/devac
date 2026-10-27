#!/usr/bin/env bash
#SBATCH --job-name=e3_construccion
#SBATCH --array=0-6                 # un tamano n por tarea (ver N_VALUES)
#SBATCH --partition=gold_IB
#SBATCH --nodelist=node419          # mismo nodo que E2
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --exclusive
#SBATCH --time=03:00:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
N_VALUES=(100 500 1000 2000 5000 10000 20000)
MODE="${MODE:-uniforme}"            # uniforme (W4-U) o estructurada (W4-E, con P_MIX)
P_MIX="${P_MIX:-0.1}"
THETA="${THETA:-0.3}"
NO_INDEX_MAX="${NO_INDEX_MAX:-5000}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E3_DIR="$HOME/devac/clusterExperimentos/E3"

IDX="${SLURM_ARRAY_TASK_ID:-0}"
N="${N_VALUES[$IDX]}"
# Repeticiones del protocolo: 30 hasta 2,000; 10 para 5,000 y 10,000; 5 para 20,000
if   [ "$N" -le 2000 ];  then REPS="${REPS:-30}"
elif [ "$N" -le 10000 ]; then REPS="${REPS:-10}"
else                          REPS="${REPS:-5}"; fi

# Pilotaje: PILOT=1 sbatch --array=0-3 e3_construccion.sbatch
if [ "${PILOT:-0}" = "1" ]; then REPS=2; fi

OUT="$RESULTS_DIR/e3/${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
mkdir -p "$OUT" "$AGG_DIR/e3"

log "E3 modo=$MODE p_mix=$P_MIX n=$N reps=$REPS theta=$THETA nodo=$(hostname) commit=$DEVAC_COMMIT pin='$PIN'"

$PIN python3 "$E3_DIR/run_e3.py" \
  --n "$N" --reps "$REPS" --mode "$MODE" --p-mix "$P_MIX" \
  --theta "$THETA" --no-index-max "$NO_INDEX_MAX" \
  --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT"/e3_*_n"${N}".csv "$OUT"/e3_*_n"${N}"_meta.json "$AGG_DIR/e3/"
log "E3 n=$N terminado -> $OUT"
