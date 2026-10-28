# D3: dinámica con y sin reconstrucciones (antes E4)

Archivos: `d3_dinamica.sbatch` (job array de 80 tareas), `resumen_d3.py`, y para reproducibilidad
`repro_d3.sh`, `compara_repro.sbatch` y `compara_repro.py`. El driver es `paper1_dinamica/run_dynamics.py`.

Cada tarea corre una repetición de una configuración:
2 conjuntos (`politicas_un_evento`, `politicas_w4u_n2000_s1`) × 4 configuraciones
(desactivación y mixto, con y sin reconstrucciones) × 10 repeticiones.
La repetición r usa la semilla `SEED*1000 + r` para su secuencia de cambios y para Louvain (las dos
en el meta.json); con y sin reconstrucciones, la misma r aplica los mismos cambios.

Q completa con networkx en cada cambio (verificación). Los tiempos se guardan pero no van al paper
(`timing_not_reported`); salen de D2.

## Corrida (amd256)
1. Piloto: `PILOT=1 REPS=2 sbatch --array=0-15 d3_dinamica.sbatch` (100 cambios)
2. Completa: `sbatch d3_dinamica.sbatch`
3. `python3 resumen_d3.py` → `resultados/d3_dinamica.csv` (reconstrucciones, estrés máximo, Q media,
   |Q incremental − Q networkx| máximo, por repetición)

## Reproducibilidad (node422 y node218)
`bash repro_d3.sh` lanza la misma configuración piloto en node422 (gold_noIB) y en node218 (amd256),
y un tercer job que espera a los dos y compara, cambio por cambio, `q_incremental`, `q_networkx`,
`delta_q` y `stress_q` (diferencia máxima), además de reconstrucciones, nodos, aristas y política de cada
cambio (deben coincidir). Resultado: `resultados/d3_reproducibilidad.csv` y el `.out` de `p1_d3_repro_compara`.
Los crudos van a `resultados/crudo/d3_repro/<JOB>/`.

## Opciones
- `THRESHOLD_W4U=0.02 sbatch ...` (o `THRESHOLD_UN`): umbral por conjunto. Con 0.05, W4-U solo
  reconstruye 1 o 2 veces por cada 1,000 cambios.
- `REPS=5 sbatch --array=0-39 ...`: otro número de repeticiones (el array debe ser 2 × 4 × REPS).
- `EXTRA_ARGS="--no-audit"`: sin auditoría.

## Duración
Hasta 32 tareas a la vez en los dos nodos. Una repetición tarda del orden de 20–60 min en el Opteron
(la Q completa en cada cambio es lo que más pesa en `politicas_un_evento`); `--time=04:00:00` deja margen.
