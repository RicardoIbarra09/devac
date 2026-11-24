#!/usr/bin/env bash
# No se manda directo: lo manda e8_enviar.sh (que pone ROLE, RUN y los parametros).
set -euo pipefail
bash "$HOME/devac/clusterExperimentos/E8/e8_lanzar.sh" "$ROLE"
