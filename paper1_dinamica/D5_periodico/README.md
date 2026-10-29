# D5: reconstrucción periódica vs regla de estrés

Archivos: `d5_periodico.sbatch` (job array de 360 tareas), `resumen_d5.py`.

Misma secuencia de cambios que D3: los dos conjuntos (`politicas_un_evento`, `politicas_w4u_n2000_s1`), los
escenarios de desactivación (1,000 cambios) y mixto (821), 10 repeticiones, y la repetición r con la semilla
SEED*1000 + r. Lo único que cambia entre estrategias es cuándo se reconstruye:
- **periódica**: cada k cambios, k ∈ {25, 50, 100, 200} (`--rebuild-every k --stress-threshold 1e9`);
- **estrés**: θ ∈ {0.01, 0.02, 0.05, 0.1}, los de D4;
- **sin reconstruir**: referencia de costo mínimo.

`resumen_d5.py` revisa que todas las estrategias de una repetición hayan aplicado los mismos cambios.

Las dos reglas reconstruyen en el mismo punto: después del cambio, con su fila ya escrita. Por eso, en el
cambio que reconstruye, `q_incremental` es la Q de antes de reconstruir en ambas. Sin Q completa
(`--networkx-sample 0`): la exactitud la cubren D1 y D3.

Por corrida se reporta:
- **número de reconstrucciones**;
- **Q promedio** y **Q mínima** sobre los cambios;
- **tamaño reconstruido** (`rebuilt_size`): nodos + aristas de los grafos reconstruidos. No depende de la
  máquina.
- **costo total**:
  - `est_cost_s`: estimado con D2 (node422). Cada reconstrucción cuesta `c·x^b`, donde x es su número de
    nodos + aristas, y cada cambio cuesta `c'·x̄^b'`, donde x̄ es el tamaño medio del grafo. Los dos ajustes
    son en log-log sobre los tiempos de D2 (build inicial, reconstrucción final y tiempo por cambio). Si
    falta `resultados/d2_escalamiento.csv`, la columna queda vacía.
  - `total_cost_s`: medido en amd256 (`timing_not_reported`). No va al paper.

Figura `fig_d5_periodico`: costo total contra Q promedio, un panel por conjunto y escenario, y un punto por
estrategia (media ± IC95). Usa `est_cost_s` si hay resumen de D2 y, si no, nodos + aristas reconstruidos.

## Corrida (amd256)
1. Piloto: `PILOT=1 REPS=1 sbatch --array=0-35 d5_periodico.sbatch` (100 cambios; las 36 combinaciones).
2. Completa: `sbatch d5_periodico.sbatch`.
3. Después de D2: `python3 ../D2_escalamiento/resumen_d2.py`, luego `python3 resumen_d5.py`. Esto genera
   `resultados/d5_periodico.csv` y una tabla por conjunto y escenario.

El reparto del array va de fuera hacia dentro: conjunto, escenario, estrategia y repetición. Por ejemplo, la
tarea 0 es `politicas_un_evento`, desactivación, k=25, r1.

## Opciones
- `REPS`: si cambia, el array debe ser 36 × REPS.
- `SEED`: debe ser el mismo que en D3 para comparar con sus corridas.
- `EXTRA_ARGS`.
- Para otros k o θ, edita `STRATEGIES` y el `--array`.
