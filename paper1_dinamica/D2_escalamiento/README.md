# D2: escalamiento (los tiempos del paper)

Archivos: `d2_escalamiento.sbatch` (job array de 25 tareas, una a la vez), `resumen_d2.py`.

Redes W4-U generadas con `clusterExperimentos/E2/gen_w4.py` (misma configuración y semilla 1; solo
cambia `--n`): 500, 1,000, 2,000, 5,000 y 10,000 eventos. La de 2,000 es idéntica a
`experiments/politicas_w4u_n2000_s1.json`. Cada tarea la regenera (es determinista) y su sha256
queda en el meta.json.

Por cambio (escenario mixto, 500 cambios, sin reconstrucciones) se mide el tiempo de la Q completa,
de la Q incremental y de la actualización del grafo. Con 10,000 eventos la Q completa (~1.5 s) se
mide solo en 50 cambios repartidos parejo; el meta.json lo dice en `networkx_sample`, y en los demás
cambios `q_networkx` y `t_networkx_s` son null.

Medido en local (i7-1165G7, para dimensionar): con 10,000 eventos hay ~925 mil aristas, el proceso
llega a 3.3 GB y el build inicial con auditoría tarda ~170 s.

## Corrida (gold_noIB, node422)
1. Piloto: `PILOT=1 REPS=1 sbatch --array=0-4%1 d2_escalamiento.sbatch` (30 cambios, muestra de 10;
   incluye la red de 10,000 para ver memoria y build)
2. Completa: `sbatch d2_escalamiento.sbatch` (~1–2 h en total)
3. `python3 resumen_d2.py` → `resultados/d2_escalamiento.csv` y una tabla con media ± IC95 por tamaño.

`--exclusive`, `taskset` a un núcleo, `*_NUM_THREADS=1` y `%1`: nada más corre en el nodo mientras se mide.
No lances la prueba de reproducibilidad de D3 en node422 al mismo tiempo.

## Opciones
`CHANGES`, `NX_SAMPLE_FROM` (10000), `NX_SAMPLE` (50), `REPS` (el array debe ser 5 × REPS), `SEED`,
`TIMING_CORE` (por defecto el primer núcleo permitido).
