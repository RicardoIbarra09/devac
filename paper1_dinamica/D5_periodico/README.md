# D5: reconstrucción periódica vs regla de estrés

Archivos:
- `d5_periodico.sbatch`: las redes de 2,000 eventos (job array de 360 tareas);
- `d5_grande.sbatch`: W4-U de 20,000 eventos (job array de 27 tareas);
- `resumen_d5.py`: junta los dos jobs.

En cada repetición, todas las estrategias aplican la misma secuencia de cambios. Lo único que cambia
entre ellas es cuándo se reconstruye:
- **periódica**: cada k cambios, k ∈ {25, 50, 100, 200} (`--rebuild-every k --stress-threshold 1e9`);
- **estrés**: θ ∈ {0.01, 0.02, 0.05, 0.1}, los de D4;
- **sin reconstruir**: referencia.

`resumen_d5.py` revisa que todas las estrategias de una repetición hayan aplicado los mismos cambios.

Las corridas van sin auditoría (`audit: false` en el meta.json), así que cada reconstrucción es grafo +
Louvain. Las dos reglas reconstruyen en el mismo punto: después del cambio, con su fila ya escrita. Por eso,
en el cambio que reconstruye, `q_incremental` es la Q de antes de reconstruir en ambas. No hay Q completa
(`--networkx-sample 0`), porque la exactitud la cubren D1 y D3.

## Redes

| | conjunto | escenarios | repeticiones | sbatch |
|---|---|---|---|---|
| principal | `politicas_w4u_n2000_s1` | desactivación (1,000 cambios) y mixto (821) | 10, las de D3 | `d5_periodico.sbatch` |
| secundario | `politicas_un_evento` | desactivación y mixto | 10, las de D3 | `d5_periodico.sbatch` |
| grande | W4-U de 20,000 eventos (`gen_w4.py`, semilla 1, como en D2) | solo desactivación (1,000 cambios) | 3 | `d5_grande.sbatch` |

La repetición r usa la semilla SEED*1000 + r, igual que D3.

## Qué se reporta

Por corrida:
- **costo**, en dos métricas:
  - principal: **número de reconstrucciones** y **nodos + aristas reconstruidos** (`rebuilt_size`). No
    dependen de la máquina.
  - secundaria, marcada como **estimación**: `est_cost_s`, segundos calculados con los tiempos de D2
    (node422).
    - Cada reconstrucción cuesta `c·x^b`, donde x es su número de nodos + aristas.
    - Cada cambio cuesta `c'·x̄^b'`, donde x̄ es el tamaño medio del grafo.
    - Ambos ajustes son en log-log sobre D2: build inicial, reconstrucción final y tiempo por cambio.
    - Si falta `resultados/d2_escalamiento.csv`, la columna queda vacía.
- **Q promedio** y **Q mínima** sobre los cambios.

`total_cost_s` viene de amd256 (`timing_not_reported`) y no va al paper.

## Figuras

Las dos figuras tienen un panel por red y escenario y un punto por estrategia (media ± IC95). Junto a cada
punto va la estrategia con su número medio de reconstrucciones entre paréntesis.

- `fig_d5_periodico`: Q promedio contra nodos + aristas reconstruidos.
- `fig_d5_periodico_estimado`: lo mismo, pero contra los segundos estimados con D2. Solo se genera si ya
  hay resumen de D2, y su pie la marca como estimación.

## Corrida (amd256)
1. Pilotos (100 cambios):
   - `PILOT=1 REPS=1 sbatch --array=0-35 d5_periodico.sbatch` (las 36 combinaciones; minutos);
   - `PILOT=1 REPS=1 sbatch --array=0-8 d5_grande.sbatch` (~30 min la de k=25). Revisa `max_rss_mb` en sus
     meta.json.
2. Completas:
   - `sbatch d5_periodico.sbatch` (hasta 3 h por tarea).
   - `sbatch d5_grande.sbatch` (`--mem=16G`, `--time=10:00:00`, `%14`).
     - Estimado: pico de ~6 GB.
     - La tarea más larga es k=25: 41 reconstrucciones de ~2 min en local, unas 3 veces más lentas en el
       Opteron, lo que da ~4–5 h.
     - Con θ, una red más grande acumula menos estrés por cambio, así que reconstruye menos.
3. Después de D2: `python3 ../D2_escalamiento/resumen_d2.py`, luego `python3 resumen_d5.py`.
   - Toma el job más reciente de `crudo/d5` y de `crudo/d5_grande`. También se le pueden pasar las
     carpetas.
   - Genera `resultados/d5_periodico.csv` y una tabla por red y escenario.

El reparto de `d5_periodico.sbatch` va de fuera hacia dentro: conjunto, escenario, estrategia y repetición.
Por ejemplo, la tarea 0 es `politicas_w4u_n2000_s1`, desactivación, k=25, r1. En `d5_grande.sbatch` el
reparto es estrategia y luego repetición.

## Opciones
- `REPS`: el array debe ser 36 × REPS en `d5_periodico.sbatch` y 9 × REPS en `d5_grande.sbatch`.
- `SEED`: debe ser el mismo que en D3 para comparar con sus corridas.
- `EXTRA_ARGS`.
- Para otros k o θ, edita `STRATEGIES` y el `--array`.
