#!/usr/bin/env bash
# e8_enviar.sh -- manda E8 como dos trabajos normales de Slurm que se encuentran por la carpeta compartida:
#   uno en nodos intel (el primero es el principal; los demas, una copia por nodo)
#   otro en nodos amd (los clientes que piden)
# Uso:
#   bash e8_enviar.sh piloto              # 3 intel + 1 amd, 1 repeticion, copias 1 y 2
#   N=2000  bash e8_enviar.sh completo    # 9 intel + 2 amd, 10 repeticiones, copias 1 2 4 8
#   N=10000 bash e8_enviar.sh completo
#   N=2000  bash e8_enviar.sh desglose    # en que se va el tiempo de cada respuesta (red, DEvAC, fila), ~20 min
set -euo pipefail
MODO="${1:-piloto}"
E8_DIR="$HOME/devac/clusterExperimentos/E8"
export N="${N:-2000}" CLIENTES_POR_NODO="${CLIENTES_POR_NODO:-16}" RATE0="${RATE0:-200}" OP="${OP:-toggle}"
export EXTRA_ARGS="${EXTRA_ARGS:-}" WRATE=20
if [ "$MODO" = "desglose" ]; then
  # desglose del tiempo de respuesta: 1 y 8 copias, carga baja (200/s por copia) y media (~la mitad de la capacidad)
  NI=9; NA=1; T=01:00:00
  if [ "$N" = "10000" ]; then RITMOS="${RITMOS:-200 700}"; else RITMOS="${RITMOS:-200 3000}"; fi
  export REPS="${REPS:-3}" REPLICAS="1 8" STEP=60 STEP_WARMUP=10 MAX_STEPS=1 WINDOW=60
  export EXTRA_ARGS="--desglose $RITMOS $EXTRA_ARGS"
elif [ "$MODO" = "piloto" ]; then
  NI=3; NA=1; T=00:45:00
  export REPS=1 REPLICAS="1 2" STEP=20 STEP_WARMUP=5 MAX_STEPS=12 WINDOW=30
else
  NI=9; NA=2; T="${TIEMPO:-16:00:00}"
  export REPS="${REPS:-10}" REPLICAS="${REPLICAS:-1 2 4 8}" STEP="${STEP:-60}" STEP_WARMUP=10 MAX_STEPS=14 WINDOW=60
fi
export E8_COPIA_GC="${E8_COPIA_GC:-normal}"   # normal | freeze | apagado: manejo de memoria en las copias
SUF=""; [ "$E8_COPIA_GC" != "normal" ] && SUF="_gc${E8_COPIA_GC}"
export RUN="${MODO}_n${N}${SUF}_$(date +%Y%m%d_%H%M%S)"    # nombre unico: carpeta de resultados y punto de encuentro
export NCLI=$(( NA * CLIENTES_POR_NODO ))
cd "$E8_DIR"
J1=$(sbatch --parsable --job-name="e8_${MODO}_intel" --partition=intel --nodes="$NI" --ntasks-per-node=1 \
      --exclusive --time="$T" --output="%x_%j.out" --error="%x_%j.err" --export=ALL,ROLE=intel e8_nodo.sbatch)
# nodos amd en exclusiva (por defecto): si otros trabajos comparten el nodo, los clientes pueden congelarse y
# fingir una saturacion. AMD_EXCLUSIVO=0 los comparte (arranca antes, pero hay que revisar los atrasos del cliente)
EXCL=""; [ "${AMD_EXCLUSIVO:-1}" = "1" ] && EXCL="--exclusive"
J2=$(sbatch --parsable --job-name="e8_${MODO}_amd" --partition=amd --nodes="$NA" --ntasks-per-node="$CLIENTES_POR_NODO" \
      $EXCL --time="$T" --output="%x_%j.out" --error="%x_%j.err" --export=ALL,ROLE=amd e8_nodo.sbatch)
echo "corrida $RUN: trabajo intel $J1 ($NI nodos), trabajo amd $J2 ($NA nodos)"
echo "resultados: \$HOME/devac_resultados/e8/$RUN   (principal.log se llena cuando arrancan los dos)"
