# D2: escalamiento (los tiempos del paper)

Archivos: `d2_escalamiento.sbatch` (job array de 35 tareas, una a la vez), `resumen_d2.py`.

Redes W4-U generadas con `clusterExperimentos/E2/gen_w4.py` (misma configuración y semilla 1; solo
cambia `--n`): 500, 1,000, 2,000, 5,000, 10,000, 20,000 y 50,000 eventos. La de 2,000 es idéntica a
`experiments/politicas_w4u_n2000_s1.json`. Cada tarea la regenera (es determinista) y su sha256
queda en el meta.json.

Por cambio (escenario mixto, 500 cambios, sin reconstrucciones) se mide el tiempo de la Q completa,
de la Q incremental y de la actualización del grafo. Desde 10,000 eventos la Q completa (~1.9 s con 10,000,
~7 s con 20,000 y ~45 s con 50,000) se mide solo en 50 cambios repartidos parejo; el meta.json lo dice en
`networkx_sample`, y en los demás cambios `q_networkx` y `t_networkx_s` son null.

Además se mide **una reconstrucción completa** por tamaño (`--final-rebuild`): al terminar los cambios se
reconstruye una vez más. Esa reconstrucción incluye grafo, Louvain, auditoría, segunda pasada (grafo sin
excluidos + Louvain), índices y Q. Se guardan el tiempo total y el de cada fase, en `final_rebuild` del
resumen y en las columnas `rebuild_*` del csv. El build inicial sigue el mismo camino y queda en
`initial_build_s`. Con estos tiempos D5 estima su costo en segundos.

Cada meta.json guarda `max_rss_mb`, el pico de memoria del proceso, porque el cluster no tiene accounting.

### Dimensionamiento (medido en local con i7-1165G7: 30 cambios, una reconstrucción final)

| eventos | aristas | reconstrucción | auditoría | Q completa | memoria pico |
|---|---|---|---|---|---|
| 2,000 | 37 mil | 5.8 s | 3.7 s | 40 ms | 0.2 GB |
| 5,000 | 232 mil | 38 s | 26 s | 0.32 s | 0.9 GB |
| 10,000 | 925 mil | 165 s | 112 s | 1.9 s | 3.3 GB |
| 20,000 (estimado) | ~3.7 M | ~12 min | | ~7 s | ~12 GB |
| 50,000 (estimado) | ~23 M | ~80 min | | ~45 s | 65–85 GB |

Las estimaciones extrapolan de 5,000 a 10,000:
- las aristas crecen como n² (W4-U es denso);
- la reconstrucción crece como n^2.1, y la auditoría es ~70 % de ella;
- la memoria crece entre n^1.8 y n² (~3.6 kB por arista).

Por tarea (dos reconstrucciones, 50 Q completas y 500 cambios):
- 20,000: ~30 min en local;
- 50,000: ~3.5 h en local.

Por eso el sbatch pide `--mem=160G` y `--time=12:00:00` (node422 tiene 1 TB y el job es `--exclusive`). Si
el Gold 6342 resulta hasta 1.5× más lento por núcleo, 50,000 sigue cabiendo con margen. Con 5
repeticiones, D2 completo tarda ~1 día. Para acortarlo se pueden lanzar menos repeticiones de 50,000 (ver
Opciones).

## Corrida (gold_noIB, node422)
1. Antes de todo, 20,000 solo: `PILOT=1 REPS=1 sbatch --array=5 d2_escalamiento.sbatch` (30 cambios,
   muestra de 10, ~30 min). Revisa en su meta.json `max_rss_mb`, `initial_build_s` y
   `replications[0].final_rebuild.s`, y con ellos ajusta `--mem` y `--time`. Para 50,000 multiplica el
   tiempo por ~7 y la memoria por ~6–7.
2. Piloto de todos los tamaños: `PILOT=1 REPS=1 sbatch --array=0-6%1 d2_escalamiento.sbatch`. Con 50,000
   tarda ~3 h, porque incluye dos reconstrucciones.
3. Completa: `sbatch d2_escalamiento.sbatch` (~1 día). Las tareas 25–29 son 20,000 y las 30–34 son 50,000.
4. `python3 resumen_d2.py` → `resultados/d2_escalamiento.csv` y una tabla con media ± IC95 por tamaño,
   incluida la reconstrucción y la memoria.

`--exclusive`, `taskset` a un núcleo, `*_NUM_THREADS=1` y `%1`: nada más corre en el nodo mientras se mide.
No lances la prueba de reproducibilidad de D3 en node422 al mismo tiempo.

## Opciones
`CHANGES`, `NX_SAMPLE_FROM` (10000), `NX_SAMPLE` (50), `REPS` (el array debe ser 7 × REPS), `SEED`,
`TIMING_CORE` (por defecto el primer núcleo permitido).

Para menos repeticiones de 50,000, lanza un subconjunto del array, por ejemplo
`sbatch --array=0-32%1 d2_escalamiento.sbatch`. Eso deja 3 repeticiones de 50,000 y el resto con 5.
