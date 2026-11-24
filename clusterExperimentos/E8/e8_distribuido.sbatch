#!/usr/bin/env bash
# E8 completo: 1 principal + 8 copias (9 nodos intel) y 2 nodos amd con 16 clientes cada uno.
# Una corrida por tamano:  N=2000 sbatch e8_distribuido.sbatch   y   N=10000 sbatch e8_distribuido.sbatch
#SBATCH --job-name=e8_distribuido
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err
#SBATCH --partition=intel --nodes=9 --ntasks-per-node=1 --exclusive --time=16:00:00
#SBATCH hetjob
#SBATCH --partition=amd --nodes=2 --ntasks-per-node=16 --exclusive --time=16:00:00
set -euo pipefail
export N="${N:-2000}" REPS="${REPS:-10}" REPLICAS="${REPLICAS:-1 2 4 8}" CLIENTES_POR_NODO=16
export STEP="${STEP:-60}" STEP_WARMUP=10 MAX_STEPS=14 RATE0="${RATE0:-200}" WINDOW=60 WRATE=20 OP="${OP:-toggle}"
export EXTRA_ARGS="${EXTRA_ARGS:-}"
bash "$HOME/devac/clusterExperimentos/E8/e8_lanzar.sh"
