# D4: sensibilidad al umbral de estrés θ

Archivos: `d4_sensibilidad.sbatch` (job array de 40 tareas), `resumen_d4.py`.

θ ∈ {0.01, 0.02, 0.05, 0.1} × 2 conjuntos (`politicas_w4u_n2000_s1` principal, `politicas_un_evento`
secundario), solo escenario de desactivación (1,000 cambios), 5 repeticiones, sin auditoría. La repetición r aplica los mismos cambios
con todos los θ. Sin Q completa (`--networkx-sample 0`): la exactitud la cubren D1 y D3.

Por conjunto y θ se reporta:
- **número de reconstrucciones**;
- **Q promedio** sobre los cambios;
- **costo total**, en dos formas:
  - `total_cost_s`: actualizaciones incrementales (grafo + Q) más reconstrucciones, en segundos.
    Viene de amd256, así que está marcado como `timing_not_reported`.
  - `rebuilt_size`: nodos + aristas de los grafos reconstruidos. No depende de la máquina, así que
    sirve para el paper; también se puede multiplicar el número de reconstrucciones por los tiempos de D2.

En local, sin auditoría, en 1,000 desactivaciones (r1):
- θ=0.01: W4-U reconstruye 12 veces y `politicas_un_evento` 18;
- θ=0.05: W4-U reconstruye 2 veces y `politicas_un_evento` 3.

## Corrida (amd256)
1. Piloto: `PILOT=1 REPS=1 sbatch --array=0-7 d4_sensibilidad.sbatch` (100 cambios)
2. Completa: `sbatch d4_sensibilidad.sbatch`
3. `python3 resumen_d4.py` → `resultados/d4_sensibilidad.csv`, y una tabla con media ± IC95.

## Opciones
`REPS` (el array debe ser 8 × REPS), `SEED`, `EXTRA_ARGS`. Para otros θ, edita `THETAS` y el `--array`.
