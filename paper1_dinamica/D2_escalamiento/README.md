# D2: escalamiento (los tiempos del paper)

Archivos: `d2_escalamiento.sbatch` (job array de 33 tareas, una a la vez), `resumen_d2.py`.

Redes W4-U generadas con `clusterExperimentos/E2/gen_w4.py` (misma configuración y semilla 1; solo
cambia `--n`): 500, 1,000, 2,000, 5,000, 10,000, 20,000 y 50,000 eventos. La de 2,000 es idéntica a
`experiments/politicas_w4u_n2000_s1.json`. Cada tarea la regenera (es determinista) y su sha256
queda en el meta.json. Hay 5 repeticiones por tamaño, salvo 50,000, que lleva 3 para no ocupar node422 un
día entero.

Todo corre sin auditoría (`audit: false` en el meta.json). En el Paper 1, una reconstrucción es solo grafo +
Louvain: sin auditoría no hay eventos excluidos, y por lo tanto tampoco segunda pasada de Louvain
(`t_audit_s`, `t_build_used_s` y `t_louvain_used_s` quedan en 0).

Por cambio (escenario mixto, 500 cambios, sin reconstrucciones) se mide el tiempo de la Q completa,
de la Q incremental y de la actualización del grafo. Desde 10,000 eventos la Q completa (~1.3 s con 10,000,
~6 s con 20,000 y ~45 s con 50,000) se mide solo en 50 cambios repartidos parejo; el meta.json lo dice en
`networkx_sample`, y en los demás cambios `q_networkx` y `t_networkx_s` son null.

Además se mide **una reconstrucción completa** por tamaño (`--final-rebuild`): al terminar los cambios se
reconstruye una vez más. Se guardan el tiempo total y el de cada fase: grafo, Louvain, e índices + Q
desde cero. Quedan en `final_rebuild` del resumen, en las columnas `rebuild_*` del csv y en
`fig_d2_reconstruccion`. El build inicial sigue el mismo camino y queda en `initial_build_s`. Con estos
tiempos D5 estima sus segundos.

Cada meta.json guarda `max_rss_mb`, el pico de memoria del proceso, porque el cluster no tiene accounting.

### Dimensionamiento (medido en local con i7-1165G7, sin auditoría: 30 cambios, una reconstrucción final)

| eventos | aristas | reconstrucción | grafo / Louvain | Q completa | por cambio (grafo + Q incr.) | memoria pico |
|---|---|---|---|---|---|---|
| 2,000 | 37 mil | 0.9 s | 0.4 / 0.5 s | 40 ms | 0.4 ms | 0.14 GB |
| 5,000 | 233 mil | 6.1 s | 2.8 / 2.9 s | 0.28 s | 1.2 ms | 0.41 GB |
| 10,000 | 937 mil | 26.5 s | 11.4 / 13.7 s | 1.3 s | 2.4 ms | 1.46 GB |
| 20,000 (estimado) | ~3.7 M | ~2 min | | ~6 s | ~5 ms | 5.5–6 GB |
| 50,000 (estimado) | ~23 M | ~13 min | | ~45 s | ~12 ms | 30–37 GB |

Las estimaciones extrapolan de 5,000 a 10,000:
- las aristas crecen como n² (W4-U es denso);
- la reconstrucción crece como n^2.1, repartida casi por mitades entre grafo y Louvain;
- la Q completa crece como n^2.2 (es lineal en las aristas);
- la memoria crece entre n^1.9 y n² (~1.6 kB por arista).

Por tarea (dos reconstrucciones, 50 Q completas y 500 cambios), en local:
- 20,000: ~9 min;
- 50,000: ~65 min, de los cuales ~40 son las Q completas.

Por eso el sbatch pide `--mem=96G` (casi 3× la estimación de 50,000; node422 tiene 1 TB y el job es
`--exclusive`) y `--time=04:00:00`. Eso cubre 50,000 aunque el Gold 6342 resulte ~1.5× más lento por
núcleo que el i7. D2 completo tarda ~4.5 h en local, unas 6–7 h en node422.

## Corrida (gold_noIB, node422)
1. **Primero, 20,000 solo**: `PILOT=1 REPS=1 sbatch --array=5 d2_escalamiento.sbatch` (30 cambios, muestra
   de 10, ~5 min).
   - Revisa en su meta.json `max_rss_mb`, `initial_build_s` y `replications[0].final_rebuild.s`.
   - Para pasar a 50,000, multiplica el tiempo de reconstrucción por ~7, la Q completa por ~7.5 y la
     memoria por ~6.
   - Si no cuadra con la tabla, ajusta `--mem` y `--time`.
2. Piloto de todos los tamaños: `PILOT=1 REPS=1 sbatch --array=0-6%1 d2_escalamiento.sbatch` (~45 min, casi
   todo en 50,000).
3. Completa: `sbatch d2_escalamiento.sbatch` (33 tareas: las 0–29 son 5 repeticiones de cada tamaño hasta
   20,000, con 20,000 en 25–29, y las 30–32 son 50,000).
4. `python3 resumen_d2.py` → `resultados/d2_escalamiento.csv` y una tabla con media ± IC95 por tamaño,
   incluidas la reconstrucción y la memoria. Avisa si alguna corrida tiene auditoría.

`--exclusive`, `taskset` a un núcleo, `*_NUM_THREADS=1` y `%1`: nada más corre en el nodo mientras se mide.
No lances la prueba de reproducibilidad de D3 en node422 al mismo tiempo.

## Opciones
- `CHANGES`, `NX_SAMPLE_FROM` (10000), `NX_SAMPLE` (50), `SEED`.
- `TIMING_CORE`: por defecto el primer núcleo permitido.
- Repeticiones: `REPS_BY_SIZE` en el sbatch (5 por tamaño y 3 para 50,000). `REPS=n` pone n a todos los
  tamaños. Si cambias cualquiera de los dos, ajusta `--array` al total de tareas.
