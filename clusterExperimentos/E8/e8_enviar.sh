#!/usr/bin/env bash
# e8_enviar.sh -- manda E8 como dos trabajos normales de Slurm que se encuentran por la carpeta compartida:
#   uno en nodos intel (el primero es el principal; los demas, una copia por nodo)
#   otro en nodos amd (los clientes que piden)
# Uso:
#   bash e8_enviar.sh piloto              # 3 intel + 1 amd, 1 repeticion, copias 1 y 2
#   N=2000  bash e8_enviar.sh completo    # 9 intel + 2 amd, 10 repeticiones, copias 1 2 4 8
#   N=10000 bash e8_enviar.sh completo
set -euo pipefail
MODO="${1:-piloto}"
E8_DIR="$HOME/devac/clusterExperimentos/E8"
export N="${N:-2000}" CLIENTES_POR_NODO="${CLIENTES_POR_NODO:-16}" RATE0="${RATE0:-200}" OP="${OP:-toggle}"
export EXTRA_ARGS="${EXTRA_ARGS:-}" WRATE=20
if [ "$MODO" = "piloto" ]; then
  NI=3; NA=1; T=00:45:00
  export REPS=1 REPLICAS="1 2" STEP=20 STEP_WARMUP=5 MAX_STEPS=12 WINDOW=30
else
  NI=9; NA=2; T="${TIEMPO:-16:00:00}"
  export REPS="${REPS:-10}" REPLICAS="${REPLICAS:-1 2 4 8}" STEP="${STEP:-60}" STEP_WARMUP=10 MAX_STEPS=14 WINDOW=60
fi
export RUN="${MODO}_n${N}_$(date +%Y%m%d_%H%M%S)"    # nombre unico: carpeta de resultados y punto de encuentro
export NCLI=$(( NA * CLIENTES_POR_NODO ))
cd "$E8_DIR"
J1=$(sbatch --parsable --job-name="e8_${MODO}_intel" --partition=intel --nodes="$NI" --ntasks-per-node=1 \
      --exclusive --time="$T" --output="%x_%j.out" --error="%x_%j.err" --export=ALL,ROLE=intel e8_nodo.sbatch)
# los clientes no necesitan el nodo completo: solo sus 16 nucleos (si se atrasan, el registro lo marca)
J2=$(sbatch --parsable --job-name="e8_${MODO}_amd" --partition=amd --nodes="$NA" --ntasks-per-node="$CLIENTES_POR_NODO" \
      --time="$T" --output="%x_%j.out" --error="%x_%j.err" --export=ALL,ROLE=amd e8_nodo.sbatch)
echo "corrida $RUN: trabajo intel $J1 ($NI nodos), trabajo amd $J2 ($NA nodos)"
echo "resultados: \$HOME/devac_resultados/e8/$RUN   (principal.log se llena cuando arrancan los dos)"
