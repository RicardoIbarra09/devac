#!/usr/bin/env bash
#SBATCH --job-name=e2_latencia
#SBATCH --array=0-6                 # un tamano n por tarea (ver N_VALUES)
#SBATCH --partition=gold_noIB
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --exclusive                 # E2 mide tiempo: nodo sin otros trabajos
#SBATCH --time=02:00:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
N_VALUES=(100 500 1000 2000 5000 10000 20000)
SEED="${SEED:-1}"
N_WARMUP="${N_WARMUP:-1000}"
N_MEASURE="${N_MEASURE:-2000}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E2_DIR="$HOME/devac/clusterExperimentos/E2"

IDX="${SLURM_ARRAY_TASK_ID:-0}"
N="${N_VALUES[$IDX]}"
# 30 repeticiones hasta n = 5,000; 10 para tamanos mayores (protocolo)
if [ "$N" -le 5000 ]; then REPS="${REPS:-30}"; else REPS="${REPS:-10}"; fi

# Pilotaje: PILOT=1 sbatch --array=0-3 e2_latencia.sbatch  ->  corrida corta
if [ "${PILOT:-0}" = "1" ]; then REPS=3; N_WARMUP=200; N_MEASURE=300; fi

OUT="$RESULTS_DIR/e2/${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
mkdir -p "$OUT" "$AGG_DIR/e2"

log "E2 n=$N semilla=$SEED reps=$REPS medidas=$N_MEASURE nodo=$(hostname) commit=$DEVAC_COMMIT pin='$PIN'"

$PIN python3 "$E2_DIR/run_e2.py" \
  --n "$N" --seed "$SEED" --reps "$REPS" \
  --n-warmup "$N_WARMUP" --n-measure "$N_MEASURE" \
  --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT/e2_n${N}_s${SEED}.csv" "$OUT/e2_n${N}_s${SEED}_meta.json" "$AGG_DIR/e2/"
log "E2 n=$N terminado -> $OUT"
