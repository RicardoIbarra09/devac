#!/usr/bin/env bash
#SBATCH --job-name=e4_umbral
#SBATCH --array=0-3%1               # un umbral θ por tarea (ver THETAS), una a la vez
#SBATCH --partition=gold_noIB
#SBATCH --nodelist=node422          # mismo nodo que E3
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --exclusive                 # E4 mide latencia: nodo sin otros trabajos
#SBATCH --mem=32G                   # con θ=0.2 y 10,000 eventos hay muchas mas conexiones que con 0.3
#SBATCH --time=03:00:00
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
THETAS=(0.2 0.3 0.4 0.5)
N="${N:-10000}"
REPS="${REPS:-10}"
MODE="${MODE:-uniforme}"            # uniforme (W4-U) o estructurada (W4-E, con P_MIX)
P_MIX="${P_MIX:-0.1}"
POLICIES="${POLICIES:-}"            # JSON de otra carga (p. ej. W3); si se da, ignora N y MODE
LABEL="${LABEL:-}"
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E4_DIR="$HOME/devac/clusterExperimentos/E4"

IDX="${SLURM_ARRAY_TASK_ID:-0}"
THETA="${THETAS[$IDX]}"

# Pilotaje: PILOT=1 sbatch e4_umbral.sbatch  ->  2,000 eventos, 2 repeticiones
if [ "${PILOT:-0}" = "1" ]; then N=2000; REPS=2; fi

OUT="$RESULTS_DIR/e4/${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
mkdir -p "$OUT" "$AGG_DIR/e4"

if [ -n "$POLICIES" ]; then
  LOAD_ARGS=(--policies "$DEVAC_DIR/$POLICIES")
  [ -n "$LABEL" ] && LOAD_ARGS+=(--label "$LABEL")
else
  LOAD_ARGS=(--n "$N" --mode "$MODE" --p-mix "$P_MIX")
fi

log "E4 theta=$THETA carga=${POLICIES:-W4 $MODE p_mix=$P_MIX n=$N} reps=$REPS nodo=$(hostname) commit=$DEVAC_COMMIT pin='$PIN'"

$PIN python3 "$E4_DIR/run_e4.py" --theta "$THETA" --reps "$REPS" "${LOAD_ARGS[@]}" \
  --out-dir "$OUT" $EXTRA_ARGS

cp "$OUT"/e4_*_theta"${THETA}".csv "$OUT"/e4_*_theta"${THETA}"_meta.json "$AGG_DIR/e4/"
log "E4 theta=$THETA terminado -> $OUT"
