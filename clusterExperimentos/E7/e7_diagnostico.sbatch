#!/usr/bin/env bash
#SBATCH --job-name=e7_diagnostico
#SBATCH --array=0-3%1               # las 4 combinaciones, una a la vez (ver COMBOS)
#SBATCH --partition=gold_IB,gold_noIB
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=96          # todo el nodo: 2 procesadores de 24 nucleos (48 fisicos, 96 logicos)
#SBATCH --exclusive
#SBATCH --mem=0
#SBATCH --time=8:00:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

# Diagnostico de la perdida de rendimiento de E7 con muchos lectores:
#   colocacion compact = llena un procesador antes de pasar al otro (como la corrida 1748710)
#   colocacion spread  = alterna los lectores entre los dos procesadores
#   replicas fork = heredan la memoria del primario (copia en escritura, como la corrida 1748710)
#   replicas own  = proceso nuevo que arma su propia copia, sin compartir memoria
set -euo pipefail

COMBOS=("compact fork" "spread fork" "compact own" "spread own")
N="${N:-10000}"
REPS="${REPS:-3}"
WORKERS="${WORKERS:-16}"
WARMUP="${WARMUP:-10}"
WINDOW="${WINDOW:-30}"
RATE="${RATE:-20}"
OP="${OP:-toggle}"
EXTRA_ARGS="${EXTRA_ARGS:-}"

export PIN_CORE=0
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E7_DIR="$HOME/devac/clusterExperimentos/E7"

read -r PLACEMENT REPLICA <<< "${COMBOS[${SLURM_ARRAY_TASK_ID:-0}]}"
OUT="$RESULTS_DIR/e7diag/${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
mkdir -p "$OUT" "$AGG_DIR/e7diag"

log "E7 diagnostico n=$N K=[$WORKERS] colocacion=$PLACEMENT replicas=$REPLICA reps=$REPS nodo=$(hostname) commit=$DEVAC_COMMIT"
lscpu | grep -i -E "^CPU\(s\)|socket|numa|core\(s\)" || true

python3 "$E7_DIR/run_e7.py" --n "$N" --reps "$REPS" --workers $WORKERS --warmup "$WARMUP" --window "$WINDOW" \
  --rate "$RATE" --op "$OP" --placement "$PLACEMENT" --replica "$REPLICA" --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT"/e7_*_n"${N}"_* "$AGG_DIR/e7diag/" 2>/dev/null || true
log "E7 diagnostico $PLACEMENT/$REPLICA terminado -> $OUT"
