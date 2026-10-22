#!/usr/bin/env python3
"""
W4: carga sintetica de politicas DEvAC con n eventos, determinista por semilla.

Cada politica tiene un evento. Parametros por defecto (configurables):
  - cardinalidad por atributo: rol=40, recurso=200, ubicacion=20, franja=4, accion=8
  - 10% de eventos con un comodin "*" en un atributo elegido al azar
  - rango_horario: 10% rangos HH:MM (de un catalogo que incluye rangos que cruzan
    medianoche); el resto, franja categorica (franja_0 .. franja_3)
  - 20% de politicas deny
  - 2% de politicas deny que copian exactamente un evento allow ya generado
    (conflicto deliberado para ejercitar "deny gana")

Uso como script:
  python gen_w4.py --n 2000 --seed 1 --out w4_n2000_s1.json
Uso como modulo:
  from gen_w4 import generate_w4
  raw = generate_w4(2000, seed=1)       # lista de dicts en el formato JSON del repo
"""
import argparse
import json
import random

# Catalogo de rangos horarios; los tres ultimos cruzan medianoche.
TIME_RANGES = [
    ("06:00", "14:00"), ("07:00", "15:00"), ("08:00", "16:00"), ("09:00", "18:00"),
    ("10:00", "13:00"), ("12:00", "20:00"), ("14:00", "22:00"), ("16:00", "23:59"),
    ("00:00", "06:00"),
    ("22:00", "06:00"), ("23:00", "07:00"), ("20:00", "04:00"),
]

DEFAULTS = dict(
    card_rol=40, card_recurso=200, card_ubicacion=20, card_franja=4, card_accion=8,
    p_wild=0.10, p_range=0.10, p_deny=0.20, p_conflict=0.02,
)

FIELDS = [("subject", "rol"), ("asset", "recurso"), ("space", "ubicacion"),
          ("time", "rango_horario"), ("action", "accion")]


def generate_w4(n, seed=1, **params):
    cfg = {**DEFAULTS, **params}
    rng = random.Random(seed)
    vocab = {
        "rol": [f"rol_{i}" for i in range(cfg["card_rol"])],
        "recurso": [f"recurso_{i}" for i in range(cfg["card_recurso"])],
        "ubicacion": [f"ubicacion_{i}" for i in range(cfg["card_ubicacion"])],
        "rango_horario": [f"franja_{i}" for i in range(cfg["card_franja"])],
        "accion": [f"accion_{i}" for i in range(cfg["card_accion"])],
    }

    def random_values():
        vals = {attr: rng.choice(vocab[attr]) for _, attr in FIELDS}
        if rng.random() < cfg["p_range"]:
            lo, hi = rng.choice(TIME_RANGES)
            vals["rango_horario"] = {"range": [lo, hi]}
        if rng.random() < cfg["p_wild"]:
            vals[rng.choice(FIELDS)[1]] = "*"
        return vals

    out = []
    allow_values = []
    for i in range(1, n + 1):
        pid = f"w4-{i:06d}"
        effect = "deny" if rng.random() < cfg["p_deny"] else "allow"
        if effect == "deny" and allow_values and rng.random() < cfg["p_conflict"] / max(cfg["p_deny"], 1e-9):
            vals = dict(rng.choice(allow_values))   # conflicto exacto con un allow previo
            desc = "W4 deny en conflicto deliberado con un allow"
        else:
            vals = random_values()
            desc = f"W4 {effect}"
        if effect == "allow":
            allow_values.append(vals)
        event = {"event_id": f"{pid}-e1"}
        for fld, attr in FIELDS:
            event[fld] = {"attribute": attr, "value": vals[attr]}
        out.append({"policy_id": pid, "description": desc, "events": [event], "effect": effect})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k.replace('_', '-')}", type=type(v), default=v)
    a = ap.parse_args()
    params = {k: getattr(a, k) for k in DEFAULTS}
    raw = generate_w4(a.n, a.seed, **params)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)
    n_deny = sum(p["effect"] == "deny" for p in raw)
    print(f"[W4] {a.out}: {len(raw)} politicas ({n_deny} deny) semilla={a.seed}")


if __name__ == "__main__":
    main()
