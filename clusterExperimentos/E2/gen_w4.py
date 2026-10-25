#!/usr/bin/env python3
# uso:
#   python gen_w4.py --n 2000 --seed 1 --out w4_n2000_s1.json
#   python gen_w4.py --n 2000 --seed 1 --mode estructurada --p-mix 0.1 --out w4e.json

import argparse
import json
import random

# los ultimos tres cruzan medianoche
TIME_RANGES = [
    ("06:00", "14:00"), ("07:00", "15:00"), ("08:00", "16:00"), ("09:00", "18:00"),
    ("10:00", "13:00"), ("12:00", "20:00"), ("14:00", "22:00"), ("16:00", "23:59"),
    ("00:00", "06:00"),
    ("22:00", "06:00"), ("23:00", "07:00"), ("20:00", "04:00"),
]

MODES = ("uniforme", "estructurada")

# cardinalidades y probabilidades de la carga
DEFAULTS = dict(
    mode="uniforme",
    card_rol=40, card_recurso=200, card_ubicacion=20,
    card_franja=4, card_accion=8,
    p_wild=0.10, p_range=0.10, p_deny=0.20, p_conflict=0.02,  # comodin, rango, deny, conflicto
    ev_per_group=50, p_mix=0.10,  # solo para el modo estructurado
    group_roles=2, group_recursos=5, group_ubicaciones=2,
)

FIELDS = [("subject", "rol"), ("asset", "recurso"), ("space", "ubicacion"),
          ("time", "rango_horario"), ("action", "accion")]


def variant_label(params):
    cfg = {**DEFAULTS, **params}
    return "U" if cfg["mode"] == "uniforme" else f"E{cfg['p_mix']:g}"


def generate_w4(n, seed=1, **params):
    cfg = {**DEFAULTS, **params}
    if cfg["mode"] not in MODES:
        raise ValueError(f"mode debe ser uno de {MODES}")
    rng = random.Random(seed)
    franjas = [f"franja_{i}" for i in range(cfg["card_franja"])]
    acciones = [f"accion_{i}" for i in range(cfg["card_accion"])]

    # valores al azar, sin estructura de comunidades
    if cfg["mode"] == "uniforme":
        vocab = {
            "rol": [f"rol_{i}" for i in range(cfg["card_rol"])],
            "recurso": [f"recurso_{i}" for i in range(cfg["card_recurso"])],
            "ubicacion": [f"ubicacion_{i}" for i in range(cfg["card_ubicacion"])],
            "rango_horario": franjas,
            "accion": acciones,
        }

        def base_values():
            return {attr: rng.choice(vocab[attr]) for _, attr in FIELDS}
    else:
        # grupos latentes, deberian salir como comunidades
        n_groups = max(1, n // cfg["ev_per_group"])
        sizes = {"rol": cfg["group_roles"], "recurso": cfg["group_recursos"],
                 "ubicacion": cfg["group_ubicaciones"]}

        def base_values():
            g = rng.randrange(n_groups)
            vals = {}
            for attr, k in sizes.items():
                # con p_mix el valor se toma de otro grupo (ruido)
                gg = rng.randrange(n_groups) if rng.random() < cfg["p_mix"] else g
                vals[attr] = f"{attr}_g{gg}_{rng.randrange(k)}"
            vals["rango_horario"] = rng.choice(franjas)
            vals["accion"] = rng.choice(acciones)
            return vals

    def random_values():
        vals = base_values()
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
        # deny que copia un allow existente: conflicto a proposito
        if effect == "deny" and allow_values and rng.random() < cfg["p_conflict"] / max(cfg["p_deny"], 1e-9):
            vals = dict(rng.choice(allow_values))
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
    ap = argparse.ArgumentParser(description="Genera la carga sintetica W4")
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", required=True)
    for k, v in DEFAULTS.items():
        if k == "mode":
            ap.add_argument("--mode", choices=MODES, default=v)
        else:
            ap.add_argument(f"--{k.replace('_', '-')}", type=type(v), default=v)
    a = ap.parse_args()
    params = {k: getattr(a, k) for k in DEFAULTS}
    raw = generate_w4(a.n, a.seed, **params)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)
    n_deny = sum(p["effect"] == "deny" for p in raw)
    print(f"[W4-{variant_label(params)}] {a.out}: {len(raw)} politicas ({n_deny} deny) semilla={a.seed}")


if __name__ == "__main__":
    main()
