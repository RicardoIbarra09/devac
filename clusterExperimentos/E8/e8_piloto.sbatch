#!/usr/bin/env bash
# Piloto de E8: 1 principal + 2 copias (3 nodos intel) y 1 nodo amd con 16 clientes; 1 repeticion, K = 1 y 2.
#SBATCH --job-name=e8_piloto
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err
#SBATCH --partition=intel --nodes=3 --ntasks-per-node=1 --exclusive --time=00:45:00
#SBATCH hetjob
#SBATCH --partition=amd --nodes=1 --ntasks-per-node=16 --exclusive --time=00:45:00
set -euo pipefail
export N="${N:-2000}" REPS=1 REPLICAS="1 2" CLIENTES_POR_NODO=16
export STEP=20 STEP_WARMUP=5 MAX_STEPS=12 RATE0="${RATE0:-200}" WINDOW=30 WRATE=20 OP=toggle EXTRA_ARGS="${EXTRA_ARGS:-}"
bash "$HOME/devac/clusterExperimentos/E8/e8_lanzar.sh"
