#!/usr/bin/env bash
#SBATCH --job-name=p1_d3_repro_compara
#SBATCH --partition=amd256
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=00:15:00
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

# lo lanza repro_d3.sh con REPRO_A y REPRO_B (los dos jobs de D3 a comparar)
set -euo pipefail
export TIMING=0
source "${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/common.sh"
check_env

python3 "$P1_DIR/D3_dinamica/compara_repro.py" \
  "$RAW_DIR/d3_repro/$REPRO_A" "$RAW_DIR/d3_repro/$REPRO_B" \
  --csv "$P1_DIR/resultados/d3_reproducibilidad.csv"
