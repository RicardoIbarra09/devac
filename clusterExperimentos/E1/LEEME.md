# Bateria DEvAC en Xiuhcoatl -- E1 (correctitud)

Archivos: `common.sh` (entorno), `run_e1.py` (driver), `e1_correctitud.sbatch` (job array: 0=W1, 1=W2).

## Puesta en marcha
1. Copia esta carpeta al cluster (p. ej. `~/devac_cluster`) y el demo a `~/devac` (debe contener `devac/` y `experiments/`).
2. Edita en `common.sh` las lineas [AJUSTAR]: rutas, y el `module load`/venv de Python con pydantic, networkx y numpy.
3. Confirma en `e1_correctitud.sbatch` la particion (`sinfo`) y la cuenta si la piden.
4. Piloto (~1 min):  `cd ~/devac_cluster && PILOT=1 sbatch e1_correctitud.sbatch`
5. Corrida completa: `sbatch e1_correctitud.sbatch`
6. Revisa: `squeue -u $USER`, los `.out`, y los CSV en `$AGG_DIR/e1/`.

## Salidas
`e1_<carga>.csv` (una fila por semilla y por auditoria on/off) y `e1_<carga>_meta.json` (fecha, nodo, CPU, versiones, commit, criterios de aceptacion).

## Opciones
- `EXTRA_ARGS="--demo-semantics" sbatch ...` el oraculo replica el demo (rango 23:00-06:00 nunca coincide).
  Por defecto el oraculo usa la semantica prevista (cruce de medianoche).
- `SEEDS="1 2 3"`, `N_REQUESTS=2000` para cambiar el tamano.
