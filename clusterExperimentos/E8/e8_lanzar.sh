#!/usr/bin/env bash
# e8_lanzar.sh -- lo llaman e8_piloto.sbatch y e8_distribuido.sbatch (trabajo heterogeneo de Slurm).
# Grupo 0 (nodos intel): el primero es el principal; los demas, una copia de DEvAC por nodo.
# Grupo 1 (nodos amd): los clientes que piden, CLIENTES_POR_NODO procesos en cada nodo.
set -euo pipefail
export PIN_CORE=0 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export E8_IFACE="${E8_IFACE:-ib0}"      # red InfiniBand; si el nodo no la tiene, se usa el nombre del nodo
source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
E8_DIR="$HOME/devac/clusterExperimentos/E8"
S="$E8_DIR/run_e8.py"

G0="${SLURM_JOB_NODELIST_HET_GROUP_0:-${SLURM_NODELIST_HET_GROUP_0:-}}"
G1="${SLURM_JOB_NODELIST_HET_GROUP_1:-${SLURM_NODELIST_HET_GROUP_1:-}}"
[ -n "$G0" ] && [ -n "$G1" ] || { echo "ERROR: no es un trabajo heterogeneo (faltan los dos grupos de nodos)"; exit 2; }
INTEL=($(scontrol show hostnames "$G0"))
AMD=($(scontrol show hostnames "$G1"))
PRIM="${INTEL[0]}"
COPIAS=("${INTEL[@]:1}")
NCOP=${#COPIAS[@]}
NCLI=$(( ${#AMD[@]} * CLIENTES_POR_NODO ))
COPIAS_CSV=$(IFS=,; echo "${COPIAS[*]}")

OUT="$RESULTS_DIR/e8/${SLURM_JOB_ID}"
RDV="$OUT/rdv"
mkdir -p "$OUT" "$RDV" "$AGG_DIR/e8"
log "E8 n=$N reps=$REPS copias=[$REPLICAS] principal=$PRIM copias_nodos=${COPIAS[*]} clientes=${AMD[*]} x$CLIENTES_POR_NODO commit=$DEVAC_COMMIT"
for h in "$PRIM" "${COPIAS[0]}" "${AMD[0]}"; do
  srun --het-group=$([ "$h" = "${AMD[0]}" ] && echo 1 || echo 0) --nodes=1 --ntasks=1 -w "$h" --overlap \
    bash -c 'echo "$(hostname): $(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2) | $(nproc) CPUs | ib0: $(ip -4 -o addr show ib0 2>/dev/null | awk "{print \$4}")"'
done

srun --het-group=0 --nodes=1 --ntasks=1 -w "$PRIM" --overlap --cpu-bind=none \
  python3 "$S" principal --rendezvous "$RDV" --out-dir "$OUT" --n "$N" --reps "$REPS" --replicas $REPLICAS \
  --clients "$NCLI" --step "$STEP" --step-warmup "$STEP_WARMUP" --max-steps "$MAX_STEPS" --rate0 "$RATE0" \
  --window "$WINDOW" --wrate "$WRATE" --op "$OP" $EXTRA_ARGS > "$OUT/principal.log" 2>&1 &
PPID_PRIM=$!
srun --het-group=0 --nodes="$NCOP" --ntasks="$NCOP" --ntasks-per-node=1 -w "$COPIAS_CSV" --overlap \
  python3 "$S" copia --rendezvous "$RDV" > "$OUT/copias.log" 2>&1 &
PID_COP=$!
srun --het-group=1 --nodes="${#AMD[@]}" --ntasks="$NCLI" --ntasks-per-node="$CLIENTES_POR_NODO" --overlap \
  python3 "$S" cliente --rendezvous "$RDV" > "$OUT/clientes.log" 2>&1 &
PID_CLI=$!

tail -f "$OUT/principal.log" --pid=$PPID_PRIM &
wait $PPID_PRIM || log "el principal termino con error"
sleep 10
kill $PID_COP $PID_CLI 2>/dev/null || true
cp "$OUT"/e8_*_n"${N}"_* "$AGG_DIR/e8/" 2>/dev/null || true
log "E8 n=$N terminado -> $OUT"
