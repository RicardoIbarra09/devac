# D1: exactitud

Archivos: `d1_exactitud.sbatch` (job array de 12 tareas), `run_d1.py` (driver), `resumen_d1.py`.

Sin reconstrucciones, para que todo el tramo pase por el camino incremental:
- Q incremental contra la Q completa de networkx en **cada** cambio;
- grafo incremental contra un build completo con los pesos congelados (nodos, aristas, pesos) cada
  `CHECK_EVERY` cambios (50) y al final, y la Q de networkx sobre ese grafo reconstruido.

Sin auditoría (`audit: false` en el meta.json), como todo el Paper 1. 2 conjuntos (`politicas_w4u_n2000_s1`
principal, `politicas_un_evento` secundario) × 2 escenarios (desactivación 1,500
cambios; mixto 5,000) × 3 repeticiones. La prueba de la suite usa 1,000 operaciones.

## Corrida (amd256)
1. Piloto (~5 min): `PILOT=1 REPS=1 sbatch --array=0-3 d1_exactitud.sbatch` (100 cambios, grafo cada 25)
2. Completa: `sbatch d1_exactitud.sbatch` (el mixto sobre `politicas_un_evento` es lo más largo: ~2–3 h)
3. `python3 resumen_d1.py` → `resultados/d1_exactitud.csv`, con una fila por tarea y estas columnas:
   `max_abs_q_incremental_vs_networkx`, `node_mismatches`, `edge_mismatches`, `max_abs_weight_diff` y
   `max_abs_q_vs_rebuilt_graph`.

## Opciones
`CHANGES_DEACT`, `CHANGES_MIXED`, `CHECK_EVERY`, `REPS` (el array debe ser 4 × REPS), `SEED`, `EXTRA_ARGS`.
