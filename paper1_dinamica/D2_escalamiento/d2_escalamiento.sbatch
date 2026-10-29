#!/usr/bin/env bash
#SBATCH --job-name=p1_d2_escalamiento
#SBATCH --array=0-32%1               # 6 tamanos x 5 repeticiones + 50,000 x 3, una tarea a la vez
#SBATCH --partition=gold_noIB
#SBATCH --nodelist=node422           # Xeon Gold 6342: los tiempos del paper salen solo de aqui
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --exclusive
#SBATCH --mem=96G                    # pico estimado sin auditoria: 20,000 ~6 GB, 50,000 ~30-37 GB (medido 1.5 GB con 10,000)
#SBATCH --time=04:00:00              # por tarea; 50,000 estimado ~65 min en local (50 Q completas de ~45 s + 2 reconstrucciones de ~13 min)
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
SIZES=(500 1000 2000 5000 10000 20000 50000)   # eventos (W4-U tiene un evento por politica)
CHANGES="${CHANGES:-500}"            # escenario mixto, sin reconstrucciones
NX_SAMPLE_FROM="${NX_SAMPLE_FROM:-10000}"   # desde este tamano la Q completa se mide solo en una muestra
NX_SAMPLE="${NX_SAMPLE:-50}"
REPS_BY_SIZE=(5 5 5 5 5 5 3)         # 50,000 solo 3, para no ocupar node422 un dia entero
# REPS=n pone n repeticiones a todos los tamanos (el piloto usa REPS=1); ajusta --array al total
SEED="${SEED:-1}"                    # la repeticion r usa SEED*1000 + r (cambios y louvain)
W4_SEED=1                            # la red: misma configuracion de gen_w4.py, solo cambia el tamano
EXTRA_ARGS="${EXTRA_ARGS:-}"
# ------------------------------------------------------------------------------

export TIMING=1
source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
check_env

# Piloto: PILOT=1 REPS=1 sbatch --array=0-6%1 d2_escalamiento.sbatch
# Solo 20,000 (para revisar memoria y tiempo antes de la completa): PILOT=1 REPS=1 sbatch --array=5 d2_escalamiento.sbatch
if [ "${PILOT:-0}" = "1" ]; then CHANGES=30; NX_SAMPLE=10; fi

# tarea -> tamano y repeticion, en orden de tamano
TASK_N=(); TASK_REP=()
for i in "${!SIZES[@]}"; do
  for (( r = 1; r <= ${REPS:-${REPS_BY_SIZE[$i]}}; r++ )); do TASK_N+=("${SIZES[$i]}"); TASK_REP+=("$r"); done
done
IDX="${SLURM_ARRAY_TASK_ID:-0}"
check_index "$IDX" ${#TASK_N[@]}
N="${TASK_N[$IDX]}"
REP="${TASK_REP[$IDX]}"

JOB="${SLURM_ARRAY_JOB_ID:-${SLURM_JOB_ID:-local}}"
OUT="$RAW_DIR/d2/$JOB/n$N"
DATA="$RAW_DIR/d2/$JOB/redes/w4u_n${N}_s${W4_SEED}.json"
mkdir -p "$OUT" "$(dirname "$DATA")"
# es determinista: cada tarea la vuelve a generar y el sha256 queda en el meta.json
python3 "$DEVAC_DIR/clusterExperimentos/E2/gen_w4.py" --n "$N" --seed "$W4_SEED" --out "$DATA.$REP.tmp"
mv "$DATA.$REP.tmp" "$DATA"

SAMPLE_ARGS=""
if [ "$N" -ge "$NX_SAMPLE_FROM" ]; then SAMPLE_ARGS="--networkx-sample $NX_SAMPLE"; fi
log "D2 n=$N rep=$REP cambios=$CHANGES muestra_networkx='${SAMPLE_ARGS:-todos}'"

$RUN python3 "$P1_DIR/run_dynamics.py" \
  --policies "$DATA" --scenario mixed --changes "$CHANGES" \
  --stress-threshold 1e9 --tag norebuild --final-rebuild \
  --seed "$SEED" --rep "$REP" --out-dir "$OUT" $SAMPLE_ARGS $EXTRA_ARGS

log "D2 n=$N r$REP terminado -> $OUT"
