# uso:
#   poetry run python graficas/make_charts.py   (matplotlib esta en el grupo charts)
#   poetry run python graficas/make_charts.py --data-dir experiments/resultados_dinamica_w4u --out-dir experiments/figuras_dinamica_w4u
#
# lee lo que escribe experiments/run_dynamics.py:
#   deactivation_rebuild_*   desactivaciones con reconstrucciones (la mayoria de las figuras)
#   deactivation_norebuild_* la misma secuencia sin reconstrucciones
#   mixed_rebuild_*          escenario mixto, para la figura de velocidad
# los deact_history*.json de esta carpeta y experiments/figuras_estres_proxy/ son de una version
# anterior (Q sobre el grafo viejo y estres por proxy) y ya no se leen ni se escriben

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

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / "experiments" / "figuras_dinamica"


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
ORANGE = "#eb6834"
RED    = "#e34948"

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


# ciclos = tramos entre reconstrucciones: (inicio, fin, se_reconstruyo_al_final).
# rebuilds cuenta las hechas despues de aplicar el cambio, asi que la fila que dispara una
# reconstruccion ya trae el contador aumentado: esa fila cierra su ciclo (es el pico del estres)
def cycles(rows):
    found, start = [], 0
    for i, row in enumerate(rows):
        before = rows[i - 1]["rebuilds"] if i else 0
        if row["rebuilds"] > before:
            found.append((start, i, True))
            start = i + 1
    if start < len(rows):
        found.append((start, len(rows) - 1, False))
    return found


def _resets(rows):
    swaps = [h["swap"] for h in rows]
    return [swaps[end] for _, end, rebuilt in cycles(rows) if rebuilt]


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

    fig, ax = plt.subplots(figsize=(6.6, 4.4))
    w = 0.34
    for j, (_, label, color) in enumerate(METHODS):
        xs = [i + (j - 0.5) * w for i in range(len(labels))]
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


# mediana de una ventana centrada; los tiempos por cambio saltan mucho entre cambios
def rolling_median(values, window=25):
    half = window // 2
    return [statistics.median(values[max(0, i - half):i + half + 1]) for i in range(len(values))]


def fig_costo_en_el_tiempo(rows):
    swaps = [h["swap"] for h in rows]
    series = METHODS + [("t_graph_s", "Actualizar nodos y aristas (ambos metodos)", MUTED)]
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    for key, label, color in series:
        values = [h[key] * 1000 for h in rows]
        style = "--" if key == "t_graph_s" else "-"
        ax.plot(swaps, values, color=color, linewidth=0.5, alpha=0.18, linestyle=style)
        ax.plot(swaps, rolling_median(values), color=color, linewidth=1.5, linestyle=style, label=label)

    ax.set_yscale("log")
    ax.set_ylabel("Tiempo por cambio (ms, escala log)")
    ax.set_xlabel(f"Numero de cambio (de {len(rows):,}); linea gruesa: mediana movil de 25 cambios")
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, fontsize=9)

    fig.tight_layout()
    save(fig, "fig_costo_en_el_tiempo", bbox_inches="tight")


def fig_trayectoria_estres(rows, threshold):
    swaps = [h["swap"] for h in rows]
    sq = [h["stress_q"] for h in rows]

    fig, ax = plt.subplots(figsize=(10.5, 5.4))
    for r in _resets(rows):
        ax.axvline(r, color=BASELINE, linewidth=0.9, linestyle=":", zorder=1)

    ax.plot(swaps, sq, color=GREEN, linewidth=1.4, label="Estres acumulado (suma de |dQ| incremental)", zorder=3)
    ax.axhline(threshold, color=RED, linewidth=1.0, linestyle="--", zorder=2, label=f"Umbral ({threshold:g})")
    for _, end, _ in cycles(rows):
        ax.annotate(f"{sq[end]:.3f}", (swaps[end], sq[end]), textcoords="offset points", xytext=(-2, 6),
                    ha="right", fontsize=7.5, color=GREEN)

    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([0], [0], color=BASELINE, linewidth=0.9, linestyle=":"))
    labels.append("Reconstruccion (el estres vuelve a cero)")
    # debajo de la grafica, para no tapar las reconstrucciones
    ax.legend(handles, labels, frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=3, fontsize=9)

    ax.set_xlabel(f"Numero de cambio (de {len(rows):,})")
    ax.set_ylabel("Estres acumulado desde la ultima reconstruccion")
    clean_axes(ax)
    ax.set_ylim(0, max(max(sq), threshold) * 1.15)

    fig.tight_layout()
    save(fig, "fig_trayectoria_estres", bbox_inches="tight")


def fig_q_trayectoria(rows):
    swaps = [h["swap"] for h in rows]
    fig, ax = plt.subplots(figsize=(10.5, 5.0))
    for r in _resets(rows):
        ax.axvline(r, color=BASELINE, linewidth=0.9, linestyle=":", zorder=1)
    ax.plot(swaps, [h["q_incremental"] for h in rows], color=GREEN, linewidth=1.3, zorder=3,
            label="Modularidad (incremental, valor crudo)")

    handles, labels = ax.get_legend_handles_labels()
    handles.append(Line2D([0], [0], color=BASELINE, linewidth=0.9, linestyle=":"))
    labels.append("Reconstruccion")
    ax.legend(handles, labels, frameon=False, loc="lower left", fontsize=9)

    ax.set_xlabel(f"Numero de cambio (de {len(rows):,})")
    ax.set_ylabel("Modularidad (Q)")
    clean_axes(ax)

    fig.tight_layout()
    save(fig, "fig_q_trayectoria", bbox_inches="tight")


def fig_comparacion_sin_reconstruccion(rows, threshold):
    swaps = [h["swap"] for h in rows]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9.5, 6.6), sharex=True, gridspec_kw={"height_ratios": [1, 1]})

    ax1.plot(swaps, [h["q_incremental"] for h in rows], color=GREEN, linewidth=1.3, zorder=3)
    ax1.set_ylabel("Modularidad\n(Q, valor crudo)")
    clean_axes(ax1)

    ax2.plot(swaps, [h["stress_q"] for h in rows], color=GREEN, linewidth=1.3, zorder=3)
    ax2.axhline(threshold, color=RED, linewidth=1.0, linestyle="--", zorder=2)
    ax2.annotate(f"umbral de la corrida con reconstrucciones ({threshold:g})", (swaps[0], threshold),
                 textcoords="offset points", xytext=(4, 5), fontsize=8.5, color=RED)
    ax2.set_ylabel("Estres acumulado\n(suma de |dQ|)")
    ax2.set_xlabel(f"Numero de cambio (de {len(rows):,}) -- sin ninguna reconstruccion en todo el tramo")
    clean_axes(ax2)

    fig.suptitle("Sin reconstrucciones de por medio: cuanto se aleja Q (arriba)\ny cuanto desgaste se acumula (abajo)",
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
    ax.legend(frameon=False, loc="lower left", fontsize=8.5)

    fig.tight_layout()
    save(fig, "fig_verificacion", bbox_inches="tight")
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description="Figuras de estrés y modularidad incremental")
    parser.add_argument("--data-dir", default=str(ROOT / "experiments" / "resultados_dinamica"))
    parser.add_argument("--out-dir", help="por defecto experiments/figuras_dinamica")
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
    threshold = deact["meta"]["params"]["stress_threshold"]

    speed = [(n, d) for n, d in (("Escenario mixto", mixed), ("Escenario de desactivacion", deact)) if d]
    fig_velocidad(speed)
    fig_costo_en_el_tiempo(rows)
    fig_trayectoria_estres(rows, threshold)
    fig_q_trayectoria(rows)
    if deact_nr:
        fig_comparacion_sin_reconstruccion(deact_nr["runs"][args.rep - 1], threshold)
    worst = fig_verificacion([(n, d) for n, d in (("desactivacion", deact), ("desactivacion sin reconstruir", deact_nr),
                                                   ("mixto", mixed)) if d])

    print(f"máxima |Q incremental - Q networkx| = {worst:.1e}")
    print(f"listo -- graficas en {OUT}")


if __name__ == "__main__":
    main()
