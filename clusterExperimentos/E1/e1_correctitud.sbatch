#!/usr/bin/env bash
#SBATCH --job-name=e1_correctitud
#SBATCH --array=0-1                 # 0=W1 NimbusLab, 1=W2 red corporativa
#SBATCH --partition=gold            # confirmar con `sinfo`
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --exclusive                 # E1 mide correctitud, no tiempo; se deja por uniformidad con el resto
#SBATCH --time=01:00:00             #  5 semillas x 10,000
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

set -euo pipefail

# ---------------- Parametros del experimento -------------------
SEEDS="${SEEDS:-1 2 3 4 5}"
N_REQUESTS="${N_REQUESTS:-10000}"
EXTRA_ARGS="${EXTRA_ARGS:-}"       # p. ej. EXTRA_ARGS="--demo-semantics" para replicar el demo tal cual
# Cargas: indice del arreglo -> nombre | archivo de politicas (relativo a DEVAC_DIR)
WORKLOAD_NAMES=(W1 W2)
WORKLOAD_FILES=("experiments/nimbuslab_policies.json" "experiments/redcorporativa_policies.json")
# ------------------------------------------------------------------------------

# Cuando se manda con sbatch, el script se copia a /var/spool: usar la ruta real
source "${CLUSTER_DIR:-$HOME/devac_cluster}/common.sh"
check_env

IDX="${SLURM_ARRAY_TASK_ID:-0}"
WL="${WORKLOAD_NAMES[$IDX]}"
POL="$DEVAC_DIR/${WORKLOAD_FILES[$IDX]}"

# Pilotaje: PILOT=1 sbatch e1_correctitud.sbatch  ->  corrida corta para validar el flujo
if [ "${PILOT:-0}" = "1" ]; then SEEDS="1"; N_REQUESTS=500; fi

OUT="$RESULTS_DIR/e1/${SLURM_JOB_ID:-local}"   # un directorio por envio
mkdir -p "$OUT" "$AGG_DIR/e1"

log "E1 carga=$WL politicas=$POL semillas=[$SEEDS] n=$N_REQUESTS nodo=$(hostname) commit=$DEVAC_COMMIT"

python3 "$CLUSTER_DIR/run_e1.py" \
  --workload "$WL" \
  --policies "$POL" \
  --seeds $SEEDS \
  --n-requests "$N_REQUESTS" \
  --out-dir "$OUT" \
  $EXTRA_ARGS

# Copia los agregados a HOME; los crudos quedan en SCRATCH
cp "$OUT/e1_${WL}.csv" "$OUT/e1_${WL}_meta.json" "$AGG_DIR/e1/"
log "E1 $WL terminado -> $OUT"
