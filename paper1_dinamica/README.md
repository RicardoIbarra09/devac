# Paper 1: dinámica de la red de eventos (Mathematics)

Experimentos del primer paper: mantener exacta y barata la modularidad de la red de eventos mientras
las políticas cambian, y decidir con el estrés acumulado (Σ|ΔQ|) cuándo reconstruir. El paper de DEvAC
(E1–E3 en `clusterExperimentos/`) cita este.

**Sin auditoría.** La reconfiguración dinámica del Paper 1 es solo la red de eventos y su partición. La
auditoría es consistencia de políticas y va en el paper de DEvAC.
- `run_dynamics.py` (y `run_d1.py`) corren sin auditoría por defecto; `--audit` la prende.
- Una reconstrucción es grafo + Louvain. Sin auditoría no hay eventos excluidos, y por lo tanto tampoco
  segunda pasada de Louvain.
- Cada meta.json registra `"audit": false`, y los resúmenes avisan si encuentran una corrida con auditoría.
- En DEvAC (`ModularityMonitor`, API, E1–E3) la auditoría sigue activa por defecto.

## Conjuntos

| papel | conjunto | notas |
|---|---|---|
| principal | `experiments/politicas_w4u_n2000_s1.json` (W4-U, 2,000 eventos) | 1,963 combinaciones de atributos distintas |
| secundario | `experiments/politicas_un_evento.json` (2,000 eventos) | solo 1,167 combinaciones distintas: sin auditoría que los quite, ~42 % de sus eventos repiten a otro |
| escalamiento | W4-U de 500 a 50,000 eventos (`clusterExperimentos/E2/gen_w4.py`, semilla 1) | D2 y la red grande de D5 (20,000) |

En los sbatch y en los paneles de `fig_d5_periodico`, W4-U va primero.

## Experimentos

| Exp. | Pregunta | Sección (tentativa) | Figuras | Partición | Tiempos al paper |
|---|---|---|---|---|---|
| [D1](D1_exactitud/) | ¿La Q incremental es igual a la de networkx y el grafo incremental igual a un build completo? (W4-U y un evento) | 4.1 Exactitud | `fig_d1_exactitud` · tabla `d1_exactitud.csv` | amd256 | no |
| [D2](D2_escalamiento/) | ¿Cuánto cuesta por cambio la Q completa, la incremental y actualizar el grafo, y cuánto una reconstrucción completa (grafo + Louvain, por fase), según el tamaño (W4-U de 500 a 50,000 eventos; 3 repeticiones en 50,000)? | 4.2 Costo y escalamiento | `fig_d2_escalamiento`, `fig_d2_reconstruccion`, `fig_d2_costo_en_el_tiempo_n2000` · tabla `d2_escalamiento.csv` | gold_noIB (node422) | **sí, solo D2** |
| [D3](D3_dinamica/) | ¿Cómo evolucionan Q y el estrés con y sin reconstrucciones? ¿Es reproducible entre máquinas? | 4.3 Dinámica | `fig_d3_estres_<conjunto>`, `fig_d3_q_<conjunto>` · tablas `d3_dinamica.csv`, `d3_reproducibilidad.csv` | amd256 (+ node422 para reproducibilidad) | no |
| [D4](D4_sensibilidad/) | ¿Cómo cambian reconstrucciones, Q promedio y costo con el umbral θ? | 4.4 Sensibilidad al umbral | `fig_d4_sensibilidad` · tabla `d4_sensibilidad.csv` | amd256 | no |
| [D5](D5_periodico/) | Con los mismos cambios, ¿qué costo (reconstrucciones, nodos + aristas reconstruidos) y qué Q da reconstruir cada k cambios frente a la regla de estrés? W4-U de 2,000 y de 20,000, y un evento | 4.5 Comparación con reconstrucción periódica | `fig_d5_periodico`, `fig_d5_periodico_estimado` · tabla `d5_periodico.csv` | amd256 | no (los segundos son una estimación con D2) |

Los números de sección y figura se ajustan cuando el borrador del paper los fije.

## Estructura

```
paper1_dinamica/
  run_dynamics.py     driver de D2 a D5 (D1 lo reutiliza)
  entorno.py          lo que va en cada meta.json: nodo, cpu (lscpu), pip freeze, commit, árbol limpio, TIMING
  datos.py            lectura de resultados para los resúmenes y las figuras
  common.sh           entorno de los sbatch; TIMING=1 (taskset, un núcleo) o TIMING=0 (srun --cpu-bind=cores)
  D1_exactitud/ ... D5_periodico/   sbatch + resumen + README
  figuras/make_figures.py   todas las figuras desde resultados/
  resultados/         tablas resumen (en git); resultados/crudo/<dN>/<JOB>/ queda fuera de git
```

## Infraestructura (Xiuhcoatl, SLURM 22.05, sin accounting)

- Todos los sbatch llevan `--mem` explícito: sin él los trabajos se quedan en cola (`DefMemPerNode=UNLIMITED`).
- **Tiempos (solo D2):** node422, `gold_noIB`, Xeon Gold 6342. `--exclusive`, una tarea a la vez
  (`--array=...%1`), `taskset` a un núcleo y `OMP/OPENBLAS/MKL_NUM_THREADS=1` (`TIMING=1`).
- **Resultados de red (D1, D3, D4, D5):** `amd256` (node218 y node227, Opteron 6274), sin `--exclusive`,
  `--cpus-per-task=2` (un módulo: dos hilos comparten la FPU), `--mem=8G`, `--array=...%32` (la red grande
  de D5: `--mem=16G`, `%14`), y
  `srun --cpu-bind=cores` en vez de `taskset` (`TIMING=0`). Sus columnas de tiempo se guardan, pero el
  meta.json dice `"timing": "timing_not_reported"`.
- Los dos nodos de amd256 suman 64 hilos: caben 32 tareas de 2 a la vez entre D1, D3 y D4. Si se lanzan
  juntos, el límite real lo pone la capacidad de los nodos (cada array además lleva `%32`). D5 es el más
  grande (360 tareas).
- Si amd256 tiene más nodos que node218 y node227, agrega `--exclude=` a los sbatch.
- El `cpu_affinity` del meta.json muestra los dos CPUs que recibió cada tarea; con `lscpu -e` se puede
  confirmar que son del mismo módulo.

## Puesta en marcha

1. En el cluster, `~/devac` en el commit que se va a medir (queda en cada meta.json, junto con `dirty`).
2. Venv con Python 3.12: `python3 -m venv ~/venvs/devac && pip install pydantic networkx numpy` (otra ruta: `DEVAC_VENV=...`).
3. **Primero el piloto de 20,000 eventos en node422:**
   `cd ~/devac/paper1_dinamica/D2_escalamiento && PILOT=1 REPS=1 sbatch --array=5 d2_escalamiento.sbatch`
   (~5 min).
   - Con su `max_rss_mb`, `initial_build_s` y `final_rebuild.s` se confirman `--mem` y `--time` de D2 (50,000)
     y de `d5_grande.sbatch`. El README de D2 tiene la tabla de estimaciones y los factores de escala.
4. Pilotos en amd256:
   - `cd ~/devac/paper1_dinamica/D3_dinamica && PILOT=1 REPS=2 sbatch --array=0-15 d3_dinamica.sbatch`
   - `cd ~/devac/paper1_dinamica/D5_periodico && PILOT=1 REPS=1 sbatch --array=0-8 d5_grande.sbatch` (red
     de 20,000; ~30 min)
5. Corridas completas: ver el README de cada experimento.
   - D2 completo tarda ~6–7 h en node422.
   - En amd256 los arrays más grandes son D5 (360 tareas, más 27 de la red grande) y D3 (80).
6. Resúmenes (en el cluster o local): `python3 paper1_dinamica/D<N>_*/resumen_d<N>.py` → `resultados/*.csv`.
   D2 antes que D5: el costo de D5 en segundos sale de `resultados/d2_escalamiento.csv`.
7. Figuras (donde haya matplotlib: `poetry install --with charts`):
   `poetry run python paper1_dinamica/figuras/make_figures.py` (toma el job más reciente que no sea piloto de cada experimento).

Para traer los crudos del cluster: `rsync -av xiuhcoatl:devac/paper1_dinamica/resultados/ paper1_dinamica/resultados/`.

## Semillas

La repetición r usa `SEED*1000 + r` para su secuencia de cambios y para Louvain; las dos quedan en el
meta.json (`plan_seeds`, `louvain_seeds`). Con y sin reconstrucciones (D3), para todos los θ (D4) y para
todas las estrategias de D5 (que usa las semillas de D3), la misma r aplica exactamente los mismos cambios,
así que las comparaciones son pareadas.

Cada meta.json guarda también `max_rss_mb`, el pico de memoria del proceso, porque el cluster no tiene
accounting.
