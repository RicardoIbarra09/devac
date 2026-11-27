#!/usr/bin/env python3
"""
resumen_e8.py -- junta las repeticiones de E8 en tablas.

Uso:
  python3 resumen_e8.py ~/devac_resultados/e8/completo_n2000_*      (una o varias carpetas de corrida)

Lee e8_*_escalones.csv y e8_*_cambios.csv de cada carpeta y muestra, por numero de copias:
  1. capacidad (fase A): hasta donde aguanto y donde se saturo, y cuantas veces lo de 1 copia, como intervalo
     (la escalera de cada numero de copias es proporcional, asi que el cociente exacto no es informativo:
     lo honesto es  minimo = aguanto_K / satura_1   y   maximo = satura_K / aguanto_1)
  2. fase B: atendidas contra pedidas, tiempo de respuesta antes, durante y despues del reacomodo, ventana,
     costo de aplicar un cambio en la copia, duracion del reacomodo y exactitud.
"""
import csv
import glob
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def load(pattern_dirs, kind):
    rows = []
    for d in pattern_dirs:
        for f in sorted(glob.glob(str(Path(d) / f"e8_*_{kind}.csv"))):
            with open(f, encoding="utf-8") as fh:
                rows += list(csv.DictReader(fh))
    return rows


def med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else None


def fmt(v, d=1):
    if v is None:
        return "-"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    return f"{v:.{d}f}"


def table(head, rows):
    w = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(head)]
    line = lambda r: "  ".join(str(c).rjust(w[i]) for i, c in enumerate(r))  # noqa: E731
    print(line(head))
    print("  ".join("-" * x for x in w))
    for r in rows:
        print(line(r))
    print()


def breakdown(rows):
    g = defaultdict(list)
    for x in rows:
        g[(int(x["n"]), int(x["copias"]), float(x["ritmo_por_copia"]), x.get("gc_copia") or "normal")].append(x)
    for n in sorted({k[0] for k in g}):
        print(f"=== E8, desglose del tiempo de respuesta, {n} politicas (promedios en ms, mediana de las repeticiones)\n")
        out = []
        for (nn, K, rate, _gcm), rr in sorted(g.items()):
            if nn != n:
                continue
            f = lambda c: med(num(x[c]) for x in rr)  # noqa: E731
            resp = f("respuesta_media_ms")
            has = rr[0].get("copia_sin_evaluar_media_ms") not in (None, "")
            parts = [f("envio_cliente_media_ms"), f("red_ida_vuelta_media_ms"), f("evaluacion_media_ms")]
            parts += [f("copia_sin_evaluar_media_ms"), f("fuera_media_ms")] if has else [f("resto_media_ms"), None]
            pc = [f"{fmt(v, 3)} ({100 * v / resp:.0f}%)" if v is not None and resp else "-" for v in parts]
            cpu = f("copia_cpu_pct")
            gcm = rr[0].get("gc_copia") or "normal"
            out.append([K, fmt(rate * K), gcm, fmt(resp, 3), *pc, fmt(f("copia_ocupada_pct"), 0) + "%",
                        (fmt(cpu, 0) + "%") if cpu is not None else "-", rr[0].get("limpiezas_gen0_gen1_gen2_por_s") or "-",
                        sum(int(x["inconsistentes"]) for x in rr)])
        table(["copias", "pedidas/s", "gc", "respuesta", "envio cliente", "red ida y vuelta", "evaluacion DEvAC",
               "en copia sin evaluar", "fuera de la copia", "copia evaluando", "procesador copia",
               "limpiezas/s gen0/1/2", "incorrectas"], out)
        print("   respuesta = envio del cliente + red ida y vuelta + evaluacion en DEvAC + en la copia sin evaluar\n"
              "               (esperar turno, empacar, enviar) + fuera de la copia (viaje con carga, recibir en el cliente)\n"
              "   (en corridas anteriores a esta medicion, la penultima columna es todo el resto y la ultima va vacia)\n")


def main():
    dirs = [d for a in sys.argv[1:] for d in glob.glob(a)]
    if not dirs:
        sys.exit("uso: resumen_e8.py CARPETA_DE_CORRIDA [...]")
    camb = load(dirs, "cambios")
    esc = load(dirs, "escalones")
    desg = load(dirs, "desglose")
    if desg:
        breakdown(desg)
    if not camb and desg:
        return
    if not camb:
        sys.exit("no encontre e8_*_cambios.csv en " + ", ".join(dirs))
    # mediciones descartadas: la saturacion la causo el cliente (se atraso mas de 1 s al enviar) y no DEvAC
    # (que si atendio al menos el 99% de lo pedido). Como la fase B usa la mitad de esa capacidad, tambien se descarta.
    bad = {}
    for s in esc:
        if s.get("saturado") != "True":
            continue
        lag, ped, resp = num(s.get("atraso_cliente_p99_ms")), num(s["ritmo_pedido_por_s"]), num(s["respondidas_por_s"])
        if lag and lag > 1000 and ped and resp and resp >= 0.99 * ped:
            key = (int(s["n"]), int(s["rep"]), int(s["copias"]))
            bad.setdefault(key, []).append(f"escalon {s['escalon']}: {fmt(ped)}/s pedidas, {fmt(resp)} atendidas, "
                                           f"atraso del cliente {fmt(lag / 1000, 1)} s")
    if bad:
        print("Mediciones descartadas (saturacion causada por el cliente, no por DEvAC):")
        for (n, rp, k), why in sorted(bad.items()):
            print(f"   {n} politicas, repeticion {rp + 1}, {k} copias -> " + "; ".join(why))
        print()
    camb = [r for r in camb if (int(r["n"]), int(r["rep"]), int(r["copias"])) not in bad]
    by_n = defaultdict(list)
    for r in camb:
        by_n[int(r["n"])].append(r)

    for n, rows in sorted(by_n.items()):
        reps = sorted({int(r["rep"]) for r in rows})
        K = sorted({int(r["copias"]) for r in rows})
        print(f"=== E8, {n} politicas, {len(reps)} repeticiones, copias {K}\n")
        cnt = {k: sum(1 for r in rows if int(r["copias"]) == k) for k in K}
        if len(set(cnt.values())) > 1:
            print("   repeticiones validas por numero de copias: " + ", ".join(f"{k}: {c}" for k, c in cnt.items()) + "\n")
        g = defaultdict(list)
        for r in rows:
            g[int(r["copias"])].append(r)

        # ---------------- capacidad
        ok = {k: med(num(r["ritmo_aguanto_por_s"]) for r in g[k]) for k in K}
        sat = {k: med(num(r["ritmo_saturacion_por_s"]) for r in g[k]) for k in K}
        out = []
        for k in K:
            rng_ok = [num(r["ritmo_aguanto_por_s"]) for r in g[k]]
            rng_ok = [v for v in rng_ok if v is not None]
            lo = ok[k] / sat[1] if (1 in sat and sat[1] and ok[k]) else None
            hi = sat[k] / ok[1] if (1 in ok and ok[1] and sat[k]) else None
            out.append([k, fmt(ok[k]), fmt(sat[k]),
                        f"{fmt(min(rng_ok))} a {fmt(max(rng_ok))}" if rng_ok else "-",
                        f"{lo:.2f} a {hi:.2f}" if lo and hi else "-",
                        f"{100 * lo / k:.0f}% a {min(100.0, 100 * hi / k):.0f}%" if lo and hi else "-"])
        print("1) Capacidad, solo preguntas (mediana de las repeticiones)")
        table(["copias", "aguanto/s", "se saturo/s", "aguanto/s (min a max)", "veces lo de 1 copia",
               "eficiencia"], out)
        sat_rows = [r for r in esc if int(r["n"]) == n]
        late = sum(1 for r in sat_rows if r.get("cliente_atrasado") == "True")
        bad = sum(int(r["inconsistentes"]) for r in sat_rows)
        print(f"   escalones: {len(sat_rows)}; respuestas incorrectas en escalones: {bad}; "
              f"escalones con clientes atrasados: {late}\n")

        # ---------------- fase B
        # el tiempo "despues del reacomodo" tiene dos grupos (repeticiones que quedan lentas y normales): la mediana
        # salta de un grupo a otro, asi que se cuentan las lentas (p95 > 1 s) y se da el rango de cada grupo
        out = []
        for k in K:
            rr = g[k]
            ped = med(num(r["ritmo_fase_b_por_s"]) for r in rr)
            att = med(num(r["respondidas_por_s"]) for r in rr)
            aft = [v for v in (num(r["p95_despues_reacomodo_ms"]) for r in rr) if v is not None]
            slow = [v for v in aft if v > 1000]
            fast = [v for v in aft if v <= 1000]
            out.append([k, fmt(ped), f"{100 * att / ped:.0f}%" if ped and att else "-",
                        fmt(med(num(r["p95_antes_reacomodo_ms"]) for r in rr)),
                        fmt(med(num(r["p95_durante_reacomodo_ms"]) for r in rr)),
                        f"{len(slow)} de {len(aft)}",
                        f"{fmt(min(slow) / 1000, 1)} a {fmt(max(slow) / 1000, 1)} s" if slow else "-",
                        f"{fmt(min(fast))} a {fmt(max(fast))} ms" if fast else "-",
                        fmt(med(num(r["sin_respuesta_pct"]) for r in rr), 2)])
        print("2) Con cambios y un reacomodo: tiempo de respuesta p95 (antes y durante: mediana en ms)")
        table(["copias", "pedidas/s", "atendidas", "antes", "durante", "lentas despues (>1 s)",
               "rango lentas", "rango normales", "sin respuesta %"], out)

        out = []
        for k in K:
            rr = g[k]
            vmax = [num(r["ventana_max_ms"]) for r in rr]
            out.append([k, fmt(med(num(r["ventana_p50_ms"]) for r in rr), 2),
                        fmt(med(num(r["ventana_p95_ms"]) for r in rr), 2),
                        fmt(max(v for v in vmax if v is not None), 1) if any(v is not None for v in vmax) else "-",
                        fmt(med(num(r["aplicar_cambio_copia_p50_ms"]) for r in rr), 2),
                        fmt(med(num(r["reacomodo_principal_s"]) for r in rr), 2),
                        fmt(med(num(r["reacomodo_hasta_ultima_copia_s"]) for r in rr), 2),
                        sum(int(r["cambios_sin_acuse_de_todas"]) for r in rr)])
        print("3) Mantener las copias al dia (mediana; la ventana maxima es la peor de todas las repeticiones)")
        table(["copias", "ventana p50 ms", "ventana p95 ms", "ventana max ms", "aplicar cambio ms",
               "reacomodo s", "hasta ultima copia s", "sin acuse"], out)

        out = []
        for k in K:
            rr = g[k]
            out.append([k, sum(int(r["inconsistentes"]) for r in rr),
                        f"{min(num(r['final_ok_min_pct']) for r in rr):.1f}%",
                        sum(int(r["copias_desfasadas"]) for r in rr),
                        sum(int(r["errores_copias"]) for r in rr), sum(int(r["errores_principal"]) for r in rr)])
        print("4) Exactitud (suma de las repeticiones)")
        table(["copias", "respuestas incorrectas", "final igual al oraculo (peor)", "copias desfasadas",
               "errores copias", "errores principal"], out)

        # ---------------- por repeticion (para ver la variacion del reacomodo)
        out = []
        for rep in reps:
            for k in K:
                r = next((x for x in g[k] if int(x["rep"]) == rep), None)
                if r:
                    out.append([rep + 1, k, fmt(num(r["p95_antes_reacomodo_ms"])),
                                fmt(num(r["p95_durante_reacomodo_ms"])), fmt(num(r["p95_despues_reacomodo_ms"])),
                                f"{100 * num(r['respondidas_por_s']) / num(r['ritmo_fase_b_por_s']):.0f}%"])
        print("5) Por repeticion: p95 (ms) alrededor del reacomodo")
        table(["rep", "copias", "antes", "durante", "despues", "atendidas"], out)


if __name__ == "__main__":
    main()
