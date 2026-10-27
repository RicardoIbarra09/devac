# E4 (dinamica: Q completa vs Q incremental)

Archivos: `e4_dinamica.sbatch` (job array de 80 tareas), `resumen_e4.py` (junta las repeticiones y resume).
El driver es `experiments/run_dynamics.py` del repo; usa el mismo `common.sh` de E1.

Cada tarea corre una repeticion de una configuracion:
2 conjuntos (`politicas_un_evento`, `politicas_w4u_n2000_s1`) x 4 configuraciones
(desactivacion y mixto, con y sin reconstrucciones) x 10 repeticiones.
Todas las repeticiones aplican la misma secuencia de cambios (`SEED`); solo cambia la semilla de louvain.

## Corrida
1. En el cluster, `~/devac` en el commit que se quiere medir (queda en cada meta.json).
2. Piloto (pocos minutos):  `PILOT=1 REPS=2 sbatch --array=0-15 e4_dinamica.sbatch`
3. Completa:  `sbatch e4_dinamica.sbatch`
4. Juntar y resumir:  `python3 resumen_e4.py ~/devac_agregados/e4/<JOB>/politicas_un_evento ~/devac_agregados/e4/<JOB>/politicas_w4u_n2000_s1`
5. Figuras (donde haya matplotlib, p. ej. local con el grupo charts de poetry):
   `poetry run python graficas/make_charts.py --data-dir <carpeta del conjunto> --out-dir <figuras>`

## Opciones
- `THRESHOLD_W4U=0.02 sbatch ...` (o `THRESHOLD_UN`): umbral de estres por conjunto. Con 0.05, W4-U solo
  reconstruye 1 o 2 veces por cada 1,000 cambios.
- `REPS=5 sbatch --array=0-39 ...`: otro numero de repeticiones (el array debe ser 2 x 4 x REPS).
- `EXTRA_ARGS="--no-audit"`: sin auditoria.

## Duracion
Con `--exclusive` en un solo nodo las tareas van de una en una: una repeticion de `politicas_un_evento`
tarda del orden de 10 min y una de W4-U unos 5 min, asi que la corrida completa toma varias horas.
Sin `--exclusive` corren en paralelo, pero los tiempos medidos se ensucian.
