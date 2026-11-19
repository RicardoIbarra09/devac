#!/usr/bin/env bash
#SBATCH --job-name=e7_replicas
#SBATCH --array=0-1%1               # 0 = 2,000 eventos, 1 = 10,000 eventos; una tarea a la vez
#SBATCH --partition=gold_IB,gold_noIB
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=96          # todo el nodo: 48 nucleos fisicos, 96 logicos
#SBATCH --exclusive
#SBATCH --mem=0                     # toda la memoria del nodo
#SBATCH --time=24:00:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err
# Nodo completo: hasta 32 lectores, cada uno (y el primario) en su propio nucleo fisico.

set -euo pipefail

# ---------------- Parametros del experimento -------------------
N_VALUES=(2000 10000)
REPS="${REPS:-10}"
WORKERS="${WORKERS:-1 2 4 8 16 32}"    # procesos lectores
WARMUP="${WARMUP:-10}"              # segundos de calentamiento
WINDOW="${WINDOW:-30}"              # segundos de cada ventana de medicion
RATE="${RATE:-20}"                  # cambios por segundo en la fase 2
OP="${OP:-toggle}"                  # toggle no desordena las comunidades; update si (como E5 y E6)
THETA="${THETA:-0.3}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export PIN_CORE=0                   # los procesos se fijan desde Python
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E7_DIR="$HOME/devac/clusterExperimentos/E7"

IDX="${SLURM_ARRAY_TASK_ID:-0}"
N="${N_VALUES[$IDX]}"

# Piloto: PILOT=1 sbatch --array=0 e7_replicas.sbatch  ->  1 repeticion, K = 1 8 32, ventanas cortas
if [ "${PILOT:-0}" = "1" ]; then REPS=1; WORKERS="1 8 32"; WARMUP=3; WINDOW=10; fi

OUT="$RESULTS_DIR/e7/${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
mkdir -p "$OUT" "$AGG_DIR/e7"

log "E7 n=$N reps=$REPS lectores=[$WORKERS] calentamiento=${WARMUP}s ventana=${WINDOW}s ritmo=$RATE/s op=$OP nodo=$(hostname) cpus=$(nproc) commit=$DEVAC_COMMIT"

python3 "$E7_DIR/run_e7.py" --n "$N" --reps "$REPS" --workers $WORKERS --warmup "$WARMUP" --window "$WINDOW" \
  --rate "$RATE" --op "$OP" --theta "$THETA" --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT"/e7_*_n"${N}"_* "$AGG_DIR/e7/" 2>/dev/null || true
log "E7 n=$N terminado -> $OUT"
