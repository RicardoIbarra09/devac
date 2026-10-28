#!/usr/bin/env bash
# reproducibilidad: la misma configuracion piloto de D3 en node422 (gold_noIB) y en node218 (amd256),
# y despues un job que compara las columnas de Q de las dos corridas.
# se lanza desde el nodo de login:  bash ~/devac/paper1_dinamica/D3_dinamica/repro_d3.sh
set -euo pipefail

HERE="${DEVAC_DIR:-$HOME/devac}/paper1_dinamica/D3_dinamica"
cd "$HERE"
export PILOT=1 REPS=2 D3_SUBDIR=d3_repro

# --parsable devuelve "jobid" o "jobid;cluster"
A=$(sbatch --parsable --array=0-15%32 --partition=gold_noIB --nodelist=node422 \
      --job-name=p1_d3_repro_node422 d3_dinamica.sbatch)
A="${A%%;*}"
B=$(sbatch --parsable --array=0-15%32 --partition=amd256 --nodelist=node218 \
      --job-name=p1_d3_repro_node218 d3_dinamica.sbatch)
B="${B%%;*}"
C=$(sbatch --parsable --dependency="afterok:$A:$B" --export="ALL,REPRO_A=$A,REPRO_B=$B" compara_repro.sbatch)
C="${C%%;*}"

echo "node422: job $A   node218: job $B   comparacion: job $C (espera a los dos)"
echo "resultado: paper1_dinamica/resultados/d3_reproducibilidad.csv y el .out de p1_d3_repro_compara"
