# uso:
#   python3 graficas/make_charts.py   (matplotlib no esta en el entorno de poetry)
#   python3 graficas/make_charts.py --data-dir experiments/resultados_dinamica --rep 3
#
# lee lo que escribe experiments/run_dynamics.py:
#   deactivation_rebuild_*   desactivaciones con reconstrucciones (la mayoria de las figuras)
#   deactivation_norebuild_* la misma secuencia sin reconstrucciones
#   mixed_rebuild_*          escenario mixto, para la figura de velocidad
# los deact_history*.json de esta carpeta son de una version anterior (Q sobre el grafo viejo) y ya no se leen

import argparse
import json
import math
import os
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # sin ventana, solo archivos
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "experiments" / "figuras_estres_proxy"


# cada figura en eps (para el documento) y png
def save(fig, name, **kwargs):
    fig.savefig(OUT / f"{name}.eps", **kwargs)
    fig.savefig(OUT / f"{name}.png", dpi=200, **kwargs)
    plt.close(fig)


SURFACE   = "#fcfcfb"
INK       = "#0b0b0b"
INK_SOFT  = "#52514e"
MUTED     = "#898781"
GRID      = "#e1e0d9"
BASELINE  = "#c3c2b7"

BLUE   = "#2a78d6"  # q completa
GREEN  = "#1f9e6e"  # q incremental
ORANGE = "#eb6834"  # aproximacion barata
RED    = "#e34948"
GRAY   = "#c3c2b7"

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Arial", "Helvetica"],
    "text.color": INK,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": INK_SOFT,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "font.size": 10.5,
})

METHODS = [
    ("t_networkx_s", "Modularidad completa (networkx)", BLUE),
    ("t_incremental_s", "Modularidad incremental", GREEN),
    ("t_proxy_s", "Aproximacion barata", ORANGE),
]
T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262,
       10: 2.228, 15: 2.131, 20: 2.086, 30: 2.042}


def clean_axes(ax, x_grid=False, y_grid=True):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    if y_grid:
        ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    else:
        ax.grid(False, axis="y")
    ax.grid(x_grid, axis="x")
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


# --- datos ---

def load(data_dir: Path, scenario: str, tag: str):
    meta_path = data_dir / f"{scenario}_{tag}_meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    runs = [json.loads((data_dir / name).read_text(encoding="utf-8")) for name in meta["files"]]
    return {"meta": meta, "runs": runs}


def ci95(values):
    if len(values) < 2:
        return None
    df = len(values) - 1
    t = T95.get(df) or T95[max(k for k in T95 if k <= df)]
    return t * statistics.stdev(values) / math.sqrt(len(values))


# ciclos = tramos entre reconstrucciones: (inicio, fin, se_reconstruyo_al_final)
def cycles(rows):
    found, start = [], 0
    for i in range(1, len(rows) + 1):
        last = i == len(rows)
        if last or rows[i]["rebuilds"] > rows[i - 1]["rebuilds"]:
            rebuilt_at_end = rows[i - 1]["rebuilds"] > (rows[start - 1]["rebuilds"] if start else 0)
            found.append((start, i - 1, rebuilt_at_end))
            start = i
    return found


# primer cambio del ciclo donde el proxy acumulado llega a su umbral; None si no llega
def proxy_crossing(rows, start, end, threshold):
    if threshold is None:
        return None
    for i in range(start, end + 1):
        if rows[i]["stress_proxy"] >= threshold:
            return i
    return None


# por ciclo completo k >= 2: cambios entre el disparo del proxy y la reconstruccion real.
# recalibrado: el umbral del proxy es el estres proxy acumulado al cerrar el ciclo anterior.
# fijo: el que aprendio al cerrar el primer ciclo
def proxy_offsets(rows):
    done = [c for c in cycles(rows) if c[2]]
    if len(done) < 2:
        return [], [], []
    learned = [rows[end]["stress_proxy"] for _, end, _ in done]
    numbers, fixed, adaptive = [], [], []
    for k in range(1, len(done)):
        start, end, _ = done[k]
        numbers.append(k + 1)
        for out, threshold in ((fixed, learned[0]), (adaptive, learned[k - 1])):
            hit = proxy_crossing(rows, start, end, threshold)
            out.append(None if hit is None else hit - end)
    return numbers, fixed, adaptive


# --- figuras ---

def fig_velocidad(datasets):
    labels, means, errors = [], [], []
    for name, data in datasets:
        n = data["meta"]["params"]["changes"]
        labels.append(f"{name}\n({n:,} cambios, {len(data['runs'])} rep.)")
        per_method_means, per_method_err = [], []
        for key, _, _ in METHODS:
            rep_means = [statistics.fmean(r[key] for r in run) * 1000 for run in data["runs"]]
            per_method_means.append(statistics.fmean(rep_means))
            per_method_err.append(ci95(rep_means) or 0.0)
        means.append(per_method_means)
        errors.append(per_method_err)

    fig, ax = plt.subplots(figsize=(7.4, 4.4))
    w = 0.26
    for j, (_, label, color) in enumerate(METHODS):
        xs = [i + (j - 1) * w for i in range(len(labels))]
        ys = [m[j] for m in means]
        bars = ax.bar(xs, ys, width=w, color=color, label=label, zorder=3,
                      yerr=[e[j] for e in errors], ecolor=INK_SOFT, capsize=2.5)
        for b, y in zip(bars, ys):
            text = f"{y:.0f} ms" if y >= 10 else f"{y:.2f} ms" if y >= 0.01 else f"{y * 1000:.1f} us"
            ax.annotate(text, (b.get_x() + b.get_width() / 2, y), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=8.5, color=INK_SOFT)

    ax.set_yscale("log")  # la diferencia es de cientos de veces
    ax.set_ylabel("Tiempo promedio por cambio (ms, escala log)")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels)
    ax.set_ylim(top=ax.get_ylim()[1] * 6)
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper right", fontsize=9)
    fig.text(0.01, 0.01, "Barras de error: IC95 entre repeticiones", fontsize=8, color=MUTED)

    fig.tight_layout()
    save(fig, "fig_velocidad")


def fig_costo_en_el_tiempo(rows):
    swaps = [h["swap"] for h in rows]
    fig, ax = plt.subplots(figsize=(8.0, 4.6))
    for key, label, color in METHODS:
        ax.plot(swaps, [h[key] * 1000 for h in rows], color=color, linewidth=1.0, label=label)

    ax.set_yscale("log")
    ax.set_ylabel("Tiempo por cambio (ms, escala log)")
    ax.set_xlabel(f"Numero de cambio (de {len(rows):,})")
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper right")

    fig.tight_layout()
    save(fig, "fig_costo_en_el_tiempo", bbox_inches="tight")


def offset_panel(ax, cycle_numbers, offsets, title):
    labels = [f"Ciclo {c}" for c in cycle_numbers]
    for i, o in enumerate(offsets):
        if o is None:
            ax.barh(i, 1, left=-0.5, height=0.55, color=GRAY, hatch="////", edgecolor=SURFACE, linewidth=0.6, zorder=3)
            ax.annotate("nunca cruzo", (0, i), ha="center", va="center", fontsize=8.5, color=INK_SOFT)
        else:
            color = BLUE if o < 0 else RED
            ax.barh(i, o, height=0.55, color=color, zorder=3)
            dx = -6 if o < 0 else 6
            ha = "right" if o < 0 else "left"
            ax.annotate(f"{o:+d}", (o, i), textcoords="offset points", xytext=(dx, 0),
                        ha=ha, va="center", fontsize=9, color=INK_SOFT)
    ax.axvline(0, color=BASELINE, linewidth=1.0, zorder=2)
    ax.set_yticks(list(range(len(cycle_numbers))))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, color=INK, loc="left")
    ax.set_xlabel("Cambios de diferencia\n(negativo = el proxy disparo antes)")
    clean_axes(ax, y_grid=False)
    ax.grid(True, axis="x", color=GRID, linewidth=0.8)


def fig_offset_ciclos(rows):
    numbers, fixed, adaptive = proxy_offsets(rows)
    if not numbers:
        print("  fig_offset_ciclos: hacen falta al menos dos reconstrucciones, se omite")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 0.55 * len(numbers) + 2.6))
    offset_panel(ax1, numbers, fixed, f"Umbral fijo ({len(numbers)} ciclos)")
    offset_panel(ax2, numbers, adaptive, f"Umbral recalibrado ({len(numbers)} ciclos)")

    known = [o for o in fixed + adaptive if o is not None] or [0]
    for ax in (ax1, ax2):
        ax.set_xlim(min(min(known), 0) - 20, max(max(known), 0) + 20)

    legend_elems = [
        Patch(facecolor=BLUE, label="Proxy disparo antes de tiempo"),
        Patch(facecolor=RED, label="Proxy disparo despues de tiempo"),
        Patch(facecolor=GRAY, hatch="////", edgecolor=SURFACE, label="Proxy nunca alcanzo el umbral"),
    ]
    fig.legend(handles=legend_elems, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.06), fontsize=9.5)

    fig.tight_layout()
    save(fig, "fig_offset_ciclos", bbox_inches="tight")


def fig_umbral_evolucion(rows):
    done = [c for c in cycles(rows) if c[2]]
    if not done:
        print("  fig_umbral_evolucion: no hubo reconstrucciones, se omite")
        return
    numbers = [k + 2 for k in range(len(done))]  # el umbral aprendido en el ciclo k se usa en el k+1
    values = [rows[end]["stress_proxy"] for _, end, _ in done]

    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax.plot(numbers, values, color=ORANGE, linewidth=1.6, marker="o", markersize=5.5, zorder=3)
    for c, v in zip(numbers, values):
        ax.annotate(f"{v:.3f}", (c, v), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9, color=INK_SOFT)
    ax.set_xlabel("Ciclo de reorganizacion")
    ax.set_ylabel("Umbral recalibrado\n(escala de la aproximacion barata)")
    ax.set_xticks(numbers)
    clean_axes(ax)
    ax.set_ylim(0, max(values) * 1.25)

    fig.tight_layout()
    save(fig, "fig_umbral_evolucion", bbox_inches="tight")


def fig_dispersion(rows):
    dq = [h["delta_q"] for h in rows]
    dp = [h["delta_proxy"] for h in rows]
    r = statistics.correlation(dq, dp) if len(set(dq)) > 1 and len(set(dp)) > 1 else float("nan")

    fig, ax = plt.subplots(figsize=(6.2, 5.4))
    ax.scatter(dq, dp, s=10, color=BLUE, alpha=0.35, edgecolors="none", zorder=3, rasterized=True)
    ax.set_xlabel("Cuanto se movio la modularidad en ese cambio (|dQ|)")
    ax.set_ylabel("Cuanto acumulo la aproximacion barata en ese cambio")
    clean_axes(ax)
    ax.annotate(f"r de Pearson = {r:.2f}", xy=(0.97, 0.95), xycoords="axes fraction",
                fontsize=9.5, color=INK_SOFT, ha="right")

    fig.tight_layout()
    save(fig, "fig_dispersion")
    return r


def fig_trayectoria_estres(rows, threshold):
    swaps = [h["swap"] for h in rows]
    sq = [h["stress_q"] for h in rows]
    sp = [h["stress_proxy"] for h in rows]
    tp = [h["threshold_proxy"] if h["threshold_proxy"] is not None else math.nan for h in rows]
    spans = cycles(rows)
    resets = [swaps[end] for _, end, rebuilt in spans if rebuilt]

    fig, ax = plt.subplots(figsize=(10.5, 6.0))
    for r in resets:
        ax.axvline(r, color=BASELINE, linewidth=0.9, linestyle=":", zorder=1)

    ax.plot(swaps, sq, color=GREEN, linewidth=1.4, label="Estres acumulado -- modularidad (incremental, exacta)", zorder=3)
    ax.plot(swaps, sp, color=ORANGE, linewidth=1.4, label="Estres acumulado -- aproximacion barata", zorder=3)
    ax.axhline(threshold, color="#9fd6bf", linewidth=1.1, linestyle="--", zorder=2, label=f"Umbral (fijo, {threshold:g})")
    ax.plot(swaps, tp, color="#f4b298", linewidth=1.1, linestyle="--", zorder=2, label="Umbral proxy (recalibrado)")

    for _, end, _ in spans:
        ax.annotate(f"{sq[end]:.3f}", (swaps[end], sq[end]), textcoords="offset points", xytext=(-2, 6),
                    ha="right", fontsize=7.5, color=GREEN)
        ax.annotate(f"{sp[end]:.3f}", (swaps[end], sp[end]), textcoords="offset points", xytext=(-2, 6),
                    ha="right", fontsize=7.5, color=ORANGE)

    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([0], [0], color=BASELINE, linewidth=0.9, linestyle=":"))
    labels.append("Reconstruccion (el estres se reinicia en ambas)")
    ax.legend(handles, labels, frameon=False, loc="upper left", fontsize=9)

    ax.set_xlabel(f"Numero de cambio (de {len(rows):,})")
    ax.set_ylabel("Estres acumulado desde la ultima reconstruccion")
    clean_axes(ax)
    ax.set_ylim(0, max(max(sp), max(sq), threshold) * 1.15)

    fig.tight_layout()
    save(fig, "fig_trayectoria_estres", bbox_inches="tight")


def fig_q_real_trayectoria(rows):
    swaps = [h["swap"] for h in rows]
    q = [h["q_incremental"] for h in rows]
    spans = cycles(rows)
    resets = [swaps[end] for _, end, rebuilt in spans if rebuilt]
    # donde el proxy, con su umbral recalibrado, habria pedido reconstruir
    triggers = [i for start, end, _ in spans
                for i in [proxy_crossing(rows, start, end, rows[start]["threshold_proxy"])] if i is not None]

    fig, ax = plt.subplots(figsize=(10.5, 5.4))
    for r in resets:
        ax.axvline(r, color=BASELINE, linewidth=0.9, linestyle=":", zorder=1)
    ax.plot(swaps, q, color=GREEN, linewidth=1.3, zorder=3, label="Modularidad (incremental, valor crudo)")
    for i in triggers:
        ax.scatter([swaps[i]], [q[i]], color=ORANGE, s=42, zorder=4, edgecolors=SURFACE, linewidths=1.0)
        ax.annotate(f"{q[i]:.3f}", (swaps[i], q[i]), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=8.5, color=ORANGE)

    handles, labels = ax.get_legend_handles_labels()
    handles += [Line2D([0], [0], marker="o", color=ORANGE, linestyle="none", markersize=6.5, markeredgecolor=SURFACE),
                Line2D([0], [0], color=BASELINE, linewidth=0.9, linestyle=":")]
    labels += ["Cambio donde la aproximacion barata disparo un aviso", "Reconstruccion"]
    ax.legend(handles, labels, frameon=False, loc="lower left", fontsize=9)

    ax.set_xlabel(f"Numero de cambio (de {len(rows):,})")
    ax.set_ylabel("Modularidad (Q)")
    clean_axes(ax)

    fig.tight_layout()
    save(fig, "fig_q_real_trayectoria", bbox_inches="tight")


def fig_comparacion_sin_reconstruccion(rows):
    swaps = [h["swap"] for h in rows]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9.5, 6.6), sharex=True, gridspec_kw={"height_ratios": [1, 1]})

    ax1.plot(swaps, [h["q_incremental"] for h in rows], color=GREEN, linewidth=1.3, zorder=3)
    ax1.set_ylabel("Modularidad\n(Q, valor crudo)")
    clean_axes(ax1)

    ax2.plot(swaps, [h["stress_proxy"] for h in rows], color=ORANGE, linewidth=1.3, zorder=3)
    ax2.set_ylabel("Estres acumulado\nde la aproximacion barata")
    ax2.set_xlabel(f"Numero de cambio (de {len(rows):,}) -- sin ninguna reconstruccion en todo el tramo")
    clean_axes(ax2)

    fig.suptitle("Sin reconstrucciones de por medio: Q (arriba) vs. lo que\nacumula la aproximacion barata (abajo), en el mismo tramo continuo",
                 fontsize=11, color=INK, x=0.02, ha="left")

    fig.tight_layout()
    save(fig, "fig_comparacion_sin_reconstruccion", bbox_inches="tight")


# diferencia entre la Q incremental y la de networkx en todas las corridas
def fig_verificacion(datasets):
    fig, ax = plt.subplots(figsize=(8.0, 4.2))
    worst = 0.0
    colors = [GREEN, BLUE, ORANGE, MUTED]
    for (name, data), color in zip(datasets, colors):
        for k, run in enumerate(data["runs"]):
            diffs = [max(abs(h["q_incremental"] - h["q_networkx"]), 1e-18) for h in run]
            worst = max(worst, max(diffs))
            ax.plot([h["swap"] for h in run], diffs, color=color, linewidth=0.6, alpha=0.5,
                    label=name if k == 0 else None)
    ax.axhline(1e-9, color=RED, linewidth=1.0, linestyle="--", label="Tolerancia de la prueba (1e-9)")
    ax.set_yscale("log")
    ax.set_ylabel("|Q incremental - Q networkx|")
    ax.set_xlabel("Numero de cambio")
    ax.set_title(f"Maxima diferencia en todas las corridas: {worst:.1e}", fontsize=10.5, color=INK, loc="left")
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper right", fontsize=8.5)

    fig.tight_layout()
    save(fig, "fig_verificacion", bbox_inches="tight")
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description="Figuras de estrés, modularidad y proxy")
    parser.add_argument("--data-dir", default=str(ROOT / "experiments" / "resultados_dinamica"))
    parser.add_argument("--out-dir", help="por defecto experiments/figuras_estres_proxy")
    parser.add_argument("--rep", type=int, default=1, help="repetición para las figuras de una sola corrida")
    args = parser.parse_args(argv)

    global OUT
    if args.out_dir:
        OUT = Path(args.out_dir)
    os.makedirs(OUT, exist_ok=True)
    data_dir = Path(args.data_dir)

    deact = load(data_dir, "deactivation", "rebuild")
    deact_nr = load(data_dir, "deactivation", "norebuild")
    mixed = load(data_dir, "mixed", "rebuild")
    if deact is None:
        raise SystemExit(f"no está {data_dir}/deactivation_rebuild_meta.json; corre experiments/run_dynamics.py")
    if not 1 <= args.rep <= len(deact["runs"]):
        raise SystemExit(f"--rep debe estar entre 1 y {len(deact['runs'])}")
    rows = deact["runs"][args.rep - 1]

    speed = [(n, d) for n, d in (("Escenario mixto", mixed), ("Escenario de desactivacion", deact)) if d]
    fig_velocidad(speed)
    fig_costo_en_el_tiempo(rows)
    fig_offset_ciclos(rows)
    fig_umbral_evolucion(rows)
    fig_trayectoria_estres(rows, deact["meta"]["params"]["stress_threshold"])
    r = fig_dispersion(rows)
    fig_q_real_trayectoria(rows)
    if deact_nr:
        fig_comparacion_sin_reconstruccion(deact_nr["runs"][args.rep - 1])
    worst = fig_verificacion([(n, d) for n, d in (("desactivacion", deact), ("desactivacion sin reconstruir", deact_nr),
                                                   ("mixto", mixed)) if d])

    print(f"r(|dQ|, proxy) = {r:.3f}; máxima |Q incremental - Q networkx| = {worst:.1e}")
    print(f"listo -- graficas en {OUT}")


if __name__ == "__main__":
    main()
