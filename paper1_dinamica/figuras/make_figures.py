# todas las figuras del paper 1 desde paper1_dinamica/resultados/
#
# uso (matplotlib esta en el grupo charts de poetry):
#   poetry run python paper1_dinamica/figuras/make_figures.py
#   poetry run python paper1_dinamica/figuras/make_figures.py --d3 paper1_dinamica/resultados/crudo/d3/<JOB>
#
# por defecto toma de resultados/crudo/dN/ el job mas reciente que no sea piloto. las figuras de tiempo
# salen solo de D2 (node422, TIMING=1); D1, D3, D4 y D5 aportan exactitud, trayectorias y barridos.
# el costo de D5 en segundos se estima con resultados/d2_escalamiento.csv (corre antes resumen_d2.py).
# si falta un experimento, sus figuras se omiten

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # sin ventana, solo archivos
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

sys.path.insert(0, str(HERE.parent / "D5_periodico"))

from datos import RESULTS, ci95, cost_model, latest_job, load_runs, mean, subfolders  # noqa: E402
from resumen_d5 import collect as collect_d5, groups as groups_d5  # noqa: E402

OUT = HERE


# cada figura en eps (para el documento) y png
def save(fig, name, **kwargs):
    fig.savefig(OUT / f"{name}.eps", **kwargs)
    fig.savefig(OUT / f"{name}.png", dpi=200, **kwargs)
    plt.close(fig)
    print(f"  {name}")


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
    ("t_graph_s", "Actualizar nodos y aristas (ambos metodos)", MUTED),
]
DATASET_LABELS = {"politicas_un_evento": "Un evento (n=2000)", "politicas_w4u_n2000_s1": "W4-U (n=2000)",
                  "w4u_n20000_s1": "W4-U (n=20000)"}
DATASET_COLORS = [GREEN, ORANGE, BLUE, MUTED]


def label(dataset: str) -> str:
    return DATASET_LABELS.get(dataset, dataset)


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


def _rep(entry, rep):
    reps = entry["reps"]
    match = [r for r in reps if r["summary"].get("rep") == rep]
    return (match or reps)[0]["rows"]


# mediana de una ventana centrada; los tiempos por cambio saltan mucho entre cambios
def rolling_median(values, window=25):
    import statistics
    half = window // 2
    return [statistics.median(values[max(0, i - half):i + half + 1]) for i in range(len(values))]


# --- D1: exactitud ---

def fig_d1_exactitud(job_dir):
    fig, ax = plt.subplots(figsize=(8.0, 4.4))
    worst, k = 0.0, 0
    styles = {"deactivation": "-", "mixed": ":"}
    for folder in subfolders(job_dir):
        color = DATASET_COLORS[k % len(DATASET_COLORS)]
        k += 1
        for config, entry in sorted(load_runs(folder).items()):
            scenario = config.split("_")[0]
            for j, rep in enumerate(entry["reps"]):
                rows = [h for h in rep["rows"] if h["q_networkx"] is not None]
                diffs = [max(abs(h["q_incremental"] - h["q_networkx"]), 1e-18) for h in rows]
                worst = max(worst, max(diffs, default=0.0))
                ax.plot([h["swap"] for h in rows], diffs, color=color, linewidth=0.6, alpha=0.5,
                        linestyle=styles.get(scenario, "-"),
                        label=f"{label(folder.name)}, {scenario}" if j == 0 else None)
    ax.axhline(1e-9, color=RED, linewidth=1.0, linestyle="--", label="Tolerancia de las pruebas (1e-9)")
    ax.set_yscale("log")
    ax.set_ylabel("|Q incremental - Q networkx|")
    ax.set_xlabel("Numero de cambio (sin reconstrucciones)")
    ax.set_title(f"Maxima diferencia en todas las corridas: {worst:.1e}", fontsize=10.5, color=INK, loc="left")
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, fontsize=8.5)
    fig.tight_layout()
    save(fig, "fig_d1_exactitud", bbox_inches="tight")


# --- D2: escalamiento (los unicos tiempos del paper) ---

def _d2_points(job_dir):
    points = {}
    for folder in subfolders(job_dir):
        if not folder.name.startswith("n"):
            continue
        for entry in load_runs(folder, with_rows=False).values():
            reps = [r["summary"] for r in entry["reps"]]
            points[int(folder.name[1:])] = {
                key: [s[f"mean_{key}"] * 1000 for s in reps if s[f"mean_{key}"] is not None]
                for key, _, _ in METHODS
            }
            points[int(folder.name[1:])]["timing"] = {r["meta"]["timing"] for r in entry["reps"]}
    return dict(sorted(points.items()))


def fig_d2_escalamiento(job_dir):
    points = _d2_points(job_dir)
    if not points:
        return
    if any(p["timing"] != {"reported"} for p in points.values()):
        print("  aviso: D2 tiene corridas sin TIMING=1")
    sizes = list(points)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.4), gridspec_kw={"width_ratios": [1.5, 1]})
    for key, name, color in METHODS:
        ys = [mean(points[n][key]) for n in sizes]
        err = [ci95(points[n][key]) for n in sizes]
        ax1.errorbar(sizes, ys, yerr=err, color=color, marker="o", markersize=4, linewidth=1.4, capsize=2.5,
                     linestyle="--" if key == "t_graph_s" else "-", label=name)
    ax1.set_xscale("log")
    ax1.set_yscale("log")
    ax1.set_xlabel("Eventos en la red (W4-U)")
    ax1.set_ylabel("Tiempo promedio por cambio (ms, log)")
    clean_axes(ax1)
    ax1.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=1, fontsize=8.5)

    ratio = [mean(points[n]["t_networkx_s"]) / mean(points[n]["t_incremental_s"]) for n in sizes]
    ax2.plot(sizes, ratio, color=INK_SOFT, marker="o", markersize=4, linewidth=1.4)
    for n, r in zip(sizes, ratio):
        ax2.annotate(f"{r:,.0f}x", (n, r), textcoords="offset points", xytext=(0, 7), ha="center",
                     fontsize=8, color=INK_SOFT)
    ax2.set_xscale("log")
    ax2.set_xlabel("Eventos en la red (W4-U)")
    ax2.set_ylabel("Q completa / Q incremental")
    clean_axes(ax2)
    fig.text(0.01, 0.01, "Barras de error: IC95 entre repeticiones", fontsize=8, color=MUTED)
    fig.tight_layout()
    save(fig, "fig_d2_escalamiento", bbox_inches="tight")


REBUILD_PHASES = [
    (lambda f: f["t_build_s"], "Grafo", BLUE),
    (lambda f: f["t_louvain_s"], "Louvain", GREEN),
    (lambda f: f["t_index_s"] + f["t_q_s"], "Indices + Q desde cero", MUTED),
]


# reconstruccion completa al final de los cambios (--final-rebuild), total y por fase. en el paper 1
# es sin auditoria: grafo + louvain (mas reindexar y recalcular Q)
def fig_d2_reconstruccion(job_dir):
    points = {}
    for folder in subfolders(job_dir):
        if not folder.name.startswith("n"):
            continue
        for entry in load_runs(folder, with_rows=False).values():
            finals = [r["summary"].get("final_rebuild") for r in entry["reps"]]
            finals = [f for f in finals if f]
            if finals:
                points[int(folder.name[1:])] = finals
    if not points:
        return
    sizes = sorted(points)

    def series(fn):
        return [mean(fn(f) for f in points[n]) for n in sizes], [ci95([fn(f) for f in points[n]]) for n in sizes]

    fig, ax = plt.subplots(figsize=(7.5, 4.4))
    ys, err = series(lambda f: f["s"])
    ax.errorbar(sizes, ys, yerr=err, color=INK, marker="o", markersize=4, linewidth=1.6, capsize=2.5,
                label="Reconstruccion completa (grafo + Louvain)")
    for fn, name, color in REBUILD_PHASES:
        ys, _ = series(fn)
        ax.plot(sizes, ys, color=color, marker="o", markersize=3, linewidth=1.1, linestyle="--", label=name)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Eventos en la red (W4-U)")
    ax.set_ylabel("Tiempo (s, log)")
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3, fontsize=8.5)
    fig.text(0.01, 0.01, "Barras de error: IC95 entre repeticiones", fontsize=8, color=MUTED)
    fig.tight_layout()
    save(fig, "fig_d2_reconstruccion", bbox_inches="tight")


def fig_d2_costo_en_el_tiempo(job_dir, events, rep):
    folder = job_dir / f"n{events}"
    if not folder.is_dir():
        return
    runs = load_runs(folder)
    if not runs:
        return
    rows = _rep(next(iter(runs.values())), rep)
    fig, ax = plt.subplots(figsize=(8.0, 4.8))
    for key, name, color in METHODS:
        pts = [(h["swap"], h[key] * 1000) for h in rows if h[key] is not None]
        xs, values = [p[0] for p in pts], [p[1] for p in pts]
        style = "--" if key == "t_graph_s" else "-"
        ax.plot(xs, values, color=color, linewidth=0.5, alpha=0.18, linestyle=style)
        ax.plot(xs, rolling_median(values), color=color, linewidth=1.5, linestyle=style, label=name)
    ax.set_yscale("log")
    ax.set_ylabel("Tiempo por cambio (ms, escala log)")
    ax.set_xlabel(f"Numero de cambio (de {len(rows):,}, red de {events:,} eventos); linea gruesa: mediana movil de 25")
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, fontsize=9)
    fig.tight_layout()
    save(fig, f"fig_d2_costo_en_el_tiempo_n{events}", bbox_inches="tight")


# --- D3: dinamica con y sin reconstrucciones ---

def fig_d3_estres(rows, threshold, dataset):
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
    ax.set_xlabel(f"Numero de cambio (de {len(rows):,}), {label(dataset)}")
    ax.set_ylabel("Estres acumulado desde la ultima reconstruccion")
    clean_axes(ax)
    ax.set_ylim(0, max(max(sq), threshold) * 1.15)
    fig.tight_layout()
    save(fig, f"fig_d3_estres_{dataset}", bbox_inches="tight")


# la misma repeticion aplica los mismos cambios con y sin reconstrucciones: Q de las dos, cambio a cambio
def fig_d3_q(runs, dataset, rep):
    scenarios = [s for s in ("deactivation", "mixed") if f"{s}_rebuild" in runs]
    if not scenarios:
        return
    fig, axes = plt.subplots(1, len(scenarios), figsize=(5.6 * len(scenarios), 4.6), squeeze=False)
    for ax, scenario in zip(axes[0], scenarios):
        rows = _rep(runs[f"{scenario}_rebuild"], rep)
        for r in _resets(rows):
            ax.axvline(r, color=BASELINE, linewidth=0.9, linestyle=":", zorder=1)
        ax.plot([h["swap"] for h in rows], [h["q_incremental"] for h in rows], color=GREEN, linewidth=1.3,
                zorder=3, label="Con reconstrucciones")
        if f"{scenario}_norebuild" in runs:
            nr = _rep(runs[f"{scenario}_norebuild"], rep)
            ax.plot([h["swap"] for h in nr], [h["q_incremental"] for h in nr], color=ORANGE, linewidth=1.3,
                    zorder=2, label="Sin reconstrucciones")
        ax.set_title({"deactivation": "Desactivaciones", "mixed": "Mixto"}[scenario], fontsize=10.5, loc="left")
        ax.set_xlabel("Numero de cambio")
        ax.set_ylabel("Modularidad (Q)")
        clean_axes(ax)
    handles, labels = axes[0][0].get_legend_handles_labels()
    handles.append(Line2D([0], [0], color=BASELINE, linewidth=0.9, linestyle=":"))
    labels.append("Reconstruccion")
    fig.legend(handles, labels, frameon=False, loc="lower center", ncol=3, fontsize=9, bbox_to_anchor=(0.5, -0.04))
    fig.suptitle(label(dataset), fontsize=11, color=INK, x=0.02, ha="left")
    fig.tight_layout()
    save(fig, f"fig_d3_q_{dataset}", bbox_inches="tight")


# --- D4: sensibilidad al umbral ---

def fig_d4_sensibilidad(job_dir):
    panels = [("rebuilds", "Reconstrucciones"), ("mean_q", "Q promedio"),
              ("rebuilt_size", "Nodos + aristas reconstruidos")]
    fig, axes = plt.subplots(1, 3, figsize=(12.0, 4.0))
    all_thetas = set()
    for k, folder in enumerate(subfolders(job_dir)):
        by_theta = {}
        for entry in load_runs(folder, with_rows=False).values():
            for rep in entry["reps"]:
                by_theta.setdefault(rep["meta"]["params"]["stress_threshold"], []).append(rep["summary"])
        thetas = sorted(by_theta)
        all_thetas.update(thetas)
        color = DATASET_COLORS[k % len(DATASET_COLORS)]
        for ax, (key, _) in zip(axes, panels):
            values = [[s[key] for s in by_theta[t]] for t in thetas]
            ax.errorbar(thetas, [mean(v) for v in values], yerr=[ci95(v) for v in values], color=color,
                        marker="o", markersize=4, linewidth=1.4, capsize=2.5, label=label(folder.name))
    for ax, (_, name) in zip(axes, panels):
        ax.set_xscale("log")
        ax.minorticks_off()
        ax.set_xticks(sorted(all_thetas), [f"{t:g}" for t in sorted(all_thetas)])
        ax.set_xlabel("Umbral de estres (theta, log)")
        ax.set_title(name, fontsize=10.5, loc="left")
        clean_axes(ax)
    axes[0].legend(frameon=False, fontsize=9)
    fig.text(0.01, 0.01, "Escenario de desactivacion; barras de error: IC95 entre repeticiones",
             fontsize=8, color=MUTED)
    fig.tight_layout()
    save(fig, "fig_d4_sensibilidad", bbox_inches="tight")


# --- D5: periodica vs estres ---

D5_STYLE = {"periodic": (ORANGE, "Periodica (cada k cambios)"), "stress": (GREEN, "Estres (theta)"),
            "none": (MUTED, "Sin reconstruir")}
SCENARIO_LABELS = {"deactivation": "desactivacion", "mixed": "mixto"}


def _fig_d5(rows, key, xlabel, name, note):
    by_panel = groups_d5(rows)
    # w4-u primero (conjunto principal), despues la red grande y el secundario
    panels = sorted(by_panel, key=lambda dc: (dc[0] == "politicas_un_evento", dc[0], dc[1]))
    ncols = min(2, len(panels))
    nrows = (len(panels) + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(5.4 * ncols, 4.1 * nrows), squeeze=False)
    for ax, (dataset, scenario) in zip(axes.flat, panels):
        for rule, (color, legend) in D5_STYLE.items():
            points = [g for g in by_panel[(dataset, scenario)].values() if g[0]["rule"] == rule]
            points.sort(key=lambda g: g[0]["param"] or 0)
            if not points:
                continue
            xs = [mean(r[key] for r in g) for g in points]
            ys = [mean(r["mean_q"] for r in g) for g in points]
            ax.errorbar(xs, ys, xerr=[ci95([r[key] for r in g]) for g in points],
                        yerr=[ci95([r["mean_q"] for r in g]) for g in points], color=color, marker="o",
                        markersize=5, linewidth=1.2 if rule != "none" else 0, capsize=2.5, label=legend)
            for x, y, g in zip(xs, ys, points):
                if rule != "none":
                    ax.annotate(f"{g[0]['strategy']} ({mean(r['rebuilds'] for r in g):.0f})", (x, y),
                                textcoords="offset points", xytext=(5, 4), fontsize=7.5, color=color)
        ax.set_title(f"{label(dataset)}, {SCENARIO_LABELS.get(scenario, scenario)}", fontsize=10.5, loc="left")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Q promedio")
        clean_axes(ax, x_grid=True)
    for ax in list(axes.flat)[len(panels):]:
        ax.set_visible(False)
    legend = {}  # una sola leyenda con las reglas de todos los paneles
    for ax in axes.flat:
        for handle, text in zip(*ax.get_legend_handles_labels()):
            legend.setdefault(text, handle)
    fig.legend(legend.values(), legend.keys(), frameon=False, fontsize=8.5, loc="upper center",
               bbox_to_anchor=(0.5, 0.0), ncol=len(legend))
    fig.text(0.01, -0.05, note, fontsize=8, color=MUTED)
    fig.tight_layout()
    save(fig, name, bbox_inches="tight")


# costo principal: nodos + aristas reconstruidos (y entre parentesis las reconstrucciones), que no dependen
# de la maquina. si hay resumen de D2, otra figura con los segundos estimados, marcada como estimacion
def fig_d5_periodico(job_dirs, d2_csv):
    model = cost_model(d2_csv)
    rows = collect_d5(job_dirs, model)
    if not rows:
        return
    _fig_d5(rows, "rebuilt_size", "Nodos + aristas reconstruidos", "fig_d5_periodico",
            "Un punto por estrategia (media de las repeticiones); entre parentesis, reconstrucciones. "
            "Barras de error: IC95")
    if model is None:
        print("  aviso: sin resumen de D2, se omite fig_d5_periodico_estimado")
        return
    _fig_d5(rows, "est_cost_s", "Costo total estimado con los tiempos de D2 (s)", "fig_d5_periodico_estimado",
            "Estimacion: reconstrucciones y cambios con los tiempos de D2 (node422), no medidos en esta corrida")


def _job(arg, experiment):
    job = Path(arg) if arg else latest_job(experiment)
    if job is None or not job.is_dir():
        print(f"{experiment.upper()}: sin resultados, se omiten sus figuras")
        return None
    print(f"{experiment.upper()}: {job}")
    return job


def main(argv=None):
    parser = argparse.ArgumentParser(description="Figuras del paper 1 (dinamica de la red de eventos)")
    for exp in ("d1", "d2", "d3", "d4", "d5"):
        parser.add_argument(f"--{exp}", help=f"carpeta del job de {exp.upper()} (por defecto la mas reciente)")
    parser.add_argument("--d5-grande", help="carpeta del job de d5_grande.sbatch (por defecto la mas reciente)")
    parser.add_argument("--d2-csv", default=str(RESULTS / "d2_escalamiento.csv"),
                        help="resumen de D2 con el que se estima el costo de D5 en segundos")
    parser.add_argument("--out-dir", help="por defecto paper1_dinamica/figuras")
    parser.add_argument("--rep", type=int, default=1, help="repeticion para las figuras de una sola corrida")
    parser.add_argument("--d2-events", type=int, default=2000, help="red de D2 para la figura de costo en el tiempo")
    args = parser.parse_args(argv)

    global OUT
    if args.out_dir:
        OUT = Path(args.out_dir)
    OUT.mkdir(parents=True, exist_ok=True)

    if job := _job(args.d1, "d1"):
        fig_d1_exactitud(job)
    if job := _job(args.d2, "d2"):
        fig_d2_escalamiento(job)
        fig_d2_reconstruccion(job)
        fig_d2_costo_en_el_tiempo(job, args.d2_events, args.rep)
    if job := _job(args.d3, "d3"):
        for folder in subfolders(job):
            runs = load_runs(folder)
            if "deactivation_rebuild" in runs:
                entry = runs["deactivation_rebuild"]
                fig_d3_estres(_rep(entry, args.rep), entry["metas"][0]["params"]["stress_threshold"], folder.name)
            fig_d3_q(runs, folder.name, args.rep)
    if job := _job(args.d4, "d4"):
        fig_d4_sensibilidad(job)
    d5 = [job for job in (_job(args.d5, "d5"), _job(args.d5_grande, "d5_grande")) if job]
    if d5:
        fig_d5_periodico(d5, Path(args.d2_csv))
    print(f"listo -- figuras en {OUT}")


if __name__ == "__main__":
    main()
