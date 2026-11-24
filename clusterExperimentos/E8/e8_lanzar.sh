#!/usr/bin/env bash
# e8_lanzar.sh intel|amd -- corre dentro de cada trabajo de Slurm (lo llama e8_nodo.sbatch).
set -euo pipefail
ROLE="$1"
export PIN_CORE=0 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
export E8_IFACE="${E8_IFACE:-ib0}"      # red InfiniBand; si el nodo no la tiene, se usa el nombre del nodo
source "$HOME/devac/clusterExperimentos/E1/common.sh"
check_env
S="$HOME/devac/clusterExperimentos/E8/run_e8.py"
OUT="$RESULTS_DIR/e8/$RUN"
RDV="$OUT/rdv"
mkdir -p "$OUT" "$RDV" "$AGG_DIR/e8"
NODES=($(scontrol show hostnames "$SLURM_JOB_NODELIST"))
for h in "${NODES[@]}"; do
  srun --nodes=1 --ntasks=1 -w "$h" --overlap \
    bash -c 'echo "$(hostname): $(grep -m1 "model name" /proc/cpuinfo | cut -d: -f2) | $(nproc) CPUs | ib0: $(ip -4 -o addr show ib0 2>/dev/null | awk "{print \$4}")"'
done

if [ "$ROLE" = "intel" ]; then
  PRIM="${NODES[0]}"
  COPIAS=("${NODES[@]:1}")
  NCOP=${#COPIAS[@]}
  log "E8 $RUN n=$N reps=$REPS copias=[$REPLICAS] principal=$PRIM copias_nodos=${COPIAS[*]} clientes_esperados=$NCLI commit=$DEVAC_COMMIT"
  srun --nodes=1 --ntasks=1 -w "$PRIM" --overlap --cpu-bind=none \
    python3 "$S" principal --rendezvous "$RDV" --out-dir "$OUT" --n "$N" --reps "$REPS" --replicas $REPLICAS \
    --clients "$NCLI" --step "$STEP" --step-warmup "$STEP_WARMUP" --max-steps "$MAX_STEPS" --rate0 "$RATE0" \
    --window "$WINDOW" --wrate "$WRATE" --op "$OP" $EXTRA_ARGS > "$OUT/principal.log" 2>&1 &
  PID_PRIM=$!
  srun --nodes="$NCOP" --ntasks="$NCOP" --ntasks-per-node=1 -w "$(IFS=,; echo "${COPIAS[*]}")" --overlap \
    python3 "$S" copia --rendezvous "$RDV" > "$OUT/copias.log" 2>&1 &
  PID_COP=$!
  tail -f "$OUT/principal.log" --pid=$PID_PRIM &
  wait $PID_PRIM || log "el principal termino con error"
  sleep 10
  kill $PID_COP 2>/dev/null || true
  cp "$OUT"/e8_*_n"${N}"_* "$AGG_DIR/e8/" 2>/dev/null || true
  log "E8 $RUN terminado -> $OUT"
else
  log "E8 $RUN clientes: ${NODES[*]} x$CLIENTES_POR_NODO; esperando al principal"
  srun --ntasks="$SLURM_NTASKS" --overlap python3 "$S" cliente --rendezvous "$RDV" > "$OUT/clientes.log" 2>&1 || true
  log "E8 $RUN clientes terminados"
fi
