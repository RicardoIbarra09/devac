"""
Genera las graficas comparativas de la metrica de estres aproximada
(modularidad real vs. aproximacion barata) usadas en el documento LaTeX.

Salida: cada grafica se escribe en .eps (para incluir en Overleaf) y en .png
(para visualizarla rapido), dentro de experiments/figuras_estres_proxy/.

Requiere deact_history.json (junto a este script) -- el historial completo
de los 1,000 swaps de la corrida de recalibracion adaptativa.
"""

import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "experiments", "figuras_estres_proxy")
os.makedirs(OUT, exist_ok=True)


def save(fig, name, **kwargs):
    fig.savefig(os.path.join(OUT, f"{name}.eps"), **kwargs)
    fig.savefig(os.path.join(OUT, f"{name}.png"), dpi=200, **kwargs)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Paleta validada (dataviz skill, references/palette.md) -- superficie clara
# ---------------------------------------------------------------------------
SURFACE   = "#fcfcfb"
INK       = "#0b0b0b"
INK_SOFT  = "#52514e"
MUTED     = "#898781"
GRID      = "#e1e0d9"
BASELINE  = "#c3c2b7"

BLUE   = "#2a78d6"   # slot 1 -- modularidad real
ORANGE = "#eb6834"   # slot 2 -- aproximacion barata (proxy)
RED    = "#e34948"   # polo divergente (tarde / mas lento)
GRAY   = "#c3c2b7"   # "nunca" / sin dato

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


def clean_axes(ax, x_grid=False, y_grid=True):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.grid(y_grid, axis="y", color=GRID, linewidth=0.8)
    ax.grid(x_grid, axis="x")
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


# ===========================================================================
# Grafica 1 -- Velocidad: tiempo promedio por cambio (real vs proxy)
# ===========================================================================
def fig_velocidad():
    scenarios = ["Escenario mixto\n(821 cambios)", "Escenario de desactivacion\n(1,000 cambios)"]
    real_ms   = [773.9797, 318.9651]
    proxy_ms  = [1.3508, 0.6367]

    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    x = range(len(scenarios))
    w = 0.32

    bars_real = ax.bar([i - w/2 for i in x], real_ms, width=w, color=BLUE, label="Modularidad real", zorder=3)
    bars_proxy = ax.bar([i + w/2 for i in x], proxy_ms, width=w, color=ORANGE, label="Aproximacion barata", zorder=3)

    ax.set_yscale("log")
    ax.set_ylim(0.3, 3000)
    ax.set_ylabel("Tiempo promedio por cambio (ms, escala log)")
    ax.set_xticks(list(x))
    ax.set_xticklabels(scenarios)
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper right", ncols=1)

    for b in bars_real:
        ax.annotate(f"{b.get_height():.0f} ms", (b.get_x() + b.get_width()/2, b.get_height()),
                    textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9, color=INK_SOFT)
    for b in bars_proxy:
        ax.annotate(f"{b.get_height():.2f} ms", (b.get_x() + b.get_width()/2, b.get_height()),
                    textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9, color=INK_SOFT)

    fig.tight_layout()
    save(fig, "fig_velocidad")


# ===========================================================================
# Grafica 2 -- El costo real cae; el proxy se mantiene plano (un solo plano,
# eje Y logaritmico -- las dos series comparten el mismo eje y las mismas
# unidades (ms); el log es lo que permite verlas juntas sin que el proxy
# quede aplastado contra el cero. No es un eje doble: sigue siendo una sola
# escala, solo que no lineal.
# ===========================================================================
def fig_costo_en_el_tiempo(hist):
    swaps = [h["swap"] for h in hist]
    t_real = [h["t_real_s"] * 1000 for h in hist]
    t_proxy = [h["t_proxy_s"] * 1000 for h in hist]

    fig, ax = plt.subplots(figsize=(8.0, 4.6))

    ax.plot(swaps, t_real, color=BLUE, linewidth=1.0, label="Modularidad real")
    ax.plot(swaps, t_proxy, color=ORANGE, linewidth=1.0, label="Aproximacion barata")

    ax.set_yscale("log")
    ax.set_ylabel("Tiempo por cambio (ms, escala log)")
    ax.set_xlabel("Numero de cambio (de 1,000)")
    clean_axes(ax)
    ax.legend(frameon=False, loc="upper right")

    #ax.set_title("El costo real cae segun el sistema se achica; el de la\naproximacion barata se mantiene plano todo el tiempo",
                  #fontsize=11, color=INK, loc="left")

    fig.tight_layout()
    save(fig, "fig_costo_en_el_tiempo", bbox_inches="tight")


# ===========================================================================
# Grafica 3 -- Desplazamiento del disparo por ciclo: umbral fijo vs recalibrado
# ===========================================================================
def offset_panel(ax, cycles, offsets, title):
    labels = [f"Ciclo {c}" for c in cycles]
    ypos = range(len(cycles))
    for i, (c, o) in enumerate(zip(cycles, offsets)):
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
    ax.set_yticks(list(ypos))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=11, color=INK, loc="left")
    ax.set_xlabel("Cambios de diferencia\n(negativo = el proxy disparo antes)")
    clean_axes(ax, y_grid=False)
    ax.grid(True, axis="x", color=GRID, linewidth=0.8)


def fig_offset_ciclos():
    fixed_cycles = list(range(2, 9))
    fixed_offsets = [-32, -77, 3, -66, -18, None, -61]

    adapt_cycles = list(range(2, 9))
    adapt_offsets = [-32, -48, None, -69, None, None, -71]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10.5, 4.6))
    offset_panel(ax1, fixed_cycles, fixed_offsets, "Umbral fijo (8 ciclos)")
    offset_panel(ax2, adapt_cycles, adapt_offsets, "Umbral recalibrado (8 ciclos)")

    xmin = min(min(o for o in fixed_offsets if o is not None), min(o for o in adapt_offsets if o is not None)) - 20
    xmax = max(max(o for o in fixed_offsets if o is not None), max(o for o in adapt_offsets if o is not None)) + 20
    ax1.set_xlim(xmin, xmax)
    ax2.set_xlim(xmin, xmax)

    legend_elems = [
        Patch(facecolor=BLUE, label="Proxy disparo antes de tiempo"),
        Patch(facecolor=RED, label="Proxy disparo despues de tiempo"),
        Patch(facecolor=GRAY, hatch="////", edgecolor=SURFACE, label="Proxy nunca alcanzo el umbral"),
    ]
    fig.legend(handles=legend_elems, loc="lower center", ncol=3, frameon=False, bbox_to_anchor=(0.5, -0.06), fontsize=9.5)

    fig.tight_layout()
    save(fig, "fig_offset_ciclos", bbox_inches="tight")


# ===========================================================================
# Grafica 4 -- Evolucion del umbral recalibrado a traves de los ciclos
# ===========================================================================
def fig_umbral_evolucion():
    cycles_th = [2, 3, 4, 5, 6, 7, 8, 9]
    threshold_vals = [0.1047, 0.1422, 0.2070, 0.0998, 0.2104, 0.1467, 0.0716, 0.2739]

    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax.plot(cycles_th, threshold_vals, color=ORANGE, linewidth=1.6, marker="o", markersize=5.5, zorder=3)
    for c, v in zip(cycles_th, threshold_vals):
        ax.annotate(f"{v:.3f}", (c, v), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=9, color=INK_SOFT)
    ax.set_xlabel("Ciclo de reorganizacion")
    ax.set_ylabel("Umbral recalibrado\n(escala de la aproximacion barata)")
    ax.set_xticks(cycles_th)
    clean_axes(ax)
    ax.set_ylim(0, max(threshold_vals) * 1.25)

    fig.tight_layout()
    save(fig, "fig_umbral_evolucion", bbox_inches="tight")


# ===========================================================================
# Grafica 5 -- Dispersion: cuanto se movio la modularidad real vs la aproximacion
# ===========================================================================
def fig_dispersion(hist):
    dq = [h["delta_q"] for h in hist]
    dp = [h["delta_proxy"] for h in hist]

    fig, ax = plt.subplots(figsize=(6.2, 5.4))
    ax.scatter(dq, dp, s=10, color=BLUE, alpha=0.35, edgecolors="none", zorder=3, rasterized=True)
    ax.set_xlabel("Cuanto se movio la modularidad real en ese cambio")
    ax.set_ylabel("Cuanto acumulo la aproximacion barata en ese cambio")
    clean_axes(ax)
    ax.annotate("r = 0.11  (practicamente sin relacion)", xy=(0.97, 0.95), xycoords="axes fraction",
                fontsize=9.5, color=INK_SOFT, ha="right")

    fig.tight_layout()
    save(fig, "fig_dispersion")


# ===========================================================================
# Grafica 6 -- Trayectoria del estres acumulado: real vs. aproximacion,
# con los umbrales y los puntos de reconstruccion (recalibracion) marcados.
# Pedido del asesor: no se espera que las dos curvas sean iguales, sino ver
# si un cambio en la real se refleja CERCA en el tiempo en la aproximada,
# y donde se van ajustando los umbrales.
# ===========================================================================
def fig_trayectoria_estres(hist):
    import math
    from matplotlib.lines import Line2D

    swaps = [h["swap"] for h in hist]
    sq = [h["stress_q"] for h in hist]
    sp = [h["stress_proxy"] for h in hist]
    tp = [h["threshold_proxy"] if h["threshold_proxy"] is not None else math.nan for h in hist]

    # Segmentar en ciclos (entre una reconstruccion y la siguiente) para
    # encontrar el pico de cada serie en cada ciclo -- ahi es donde se
    # imprime el valor numerico exacto.
    cycles = []
    start = 0
    for i in range(1, len(hist)):
        if sq[i] < sq[i-1] or sp[i] < sp[i-1]:
            cycles.append((start, i - 1))
            start = i
    cycles.append((start, len(hist) - 1))

    resets = [swaps[b] for a, b in cycles[:-1]]  # todo pico de ciclo, salvo el ultimo (incompleto)

    fig, ax = plt.subplots(figsize=(10.5, 6.0))

    for r in resets:
        ax.axvline(r, color=BASELINE, linewidth=0.9, linestyle=":", zorder=1)

    ax.plot(swaps, sq, color=BLUE, linewidth=1.4, label="Estres acumulado -- modularidad real", zorder=3)
    ax.plot(swaps, sp, color=ORANGE, linewidth=1.4, label="Estres acumulado -- aproximacion barata", zorder=3)
    ax.axhline(0.05, color="#a8c7ec", linewidth=1.1, linestyle="--", zorder=2, label="Umbral real (fijo, 0.05)")
    ax.plot(swaps, tp, color="#f4b298", linewidth=1.1, linestyle="--", zorder=2, label="Umbral proxy (recalibrado)")

    # Valor numerico exacto en cada pico (justo antes de cada reconstruccion),
    # para ambas series -- asi no hace falta adivinar la altura a ojo.
    for a, b in cycles:
        peak_swap = swaps[b]
        ax.annotate(f"{sq[b]:.3f}", (peak_swap, sq[b]), textcoords="offset points", xytext=(-2, 6),
                    ha="right", fontsize=7.5, color=BLUE)
        ax.annotate(f"{sp[b]:.3f}", (peak_swap, sp[b]), textcoords="offset points", xytext=(-2, 6),
                    ha="right", fontsize=7.5, color=ORANGE)

    reset_handle = Line2D([0], [0], color=BASELINE, linewidth=0.9, linestyle=":")
    handles, labels = ax.get_legend_handles_labels()
    handles.append(reset_handle)
    labels.append("Reconstruccion (el estres se reinicia en ambas)")
    ax.legend(handles, labels, frameon=False, loc="upper left", fontsize=9)

    ax.set_xlabel("Numero de cambio (de 1,000)")
    ax.set_ylabel("Estres acumulado desde la ultima reconstruccion")
    clean_axes(ax)
    ax.set_ylim(0, max(sp) * 1.15)

    fig.tight_layout()
    save(fig, "fig_trayectoria_estres", bbox_inches="tight")


# ===========================================================================
# Grafica 7 -- Valor CRUDO de la modularidad real (nunca se reinicia, sube y
# baja de verdad) a lo largo de los 1,000 cambios. Pedido explicito del
# asesor: no se le inventa una curva a la aproximacion barata (no sabe
# direccion) -- se marcan, sobre la propia trayectoria real, los cambios
# donde la aproximacion SI hubiera disparado un aviso, para leerlos junto a
# la tabla del chat.
# ===========================================================================
def fig_q_real_trayectoria(hist):
    swaps = [h["swap"] for h in hist]
    qr = [h["q_real"] for h in hist]
    sq = [h["stress_q"] for h in hist]
    sp = [h["stress_proxy"] for h in hist]
    by_swap = {h["swap"]: h for h in hist}

    resets = [swaps[i] for i in range(1, len(hist)) if sq[i] < sq[i-1] or sp[i] < sp[i-1]]

    # Cambios donde la aproximacion barata cruzo su propio umbral recalibrado
    # dentro de su ciclo (ver tabla) -- son los "avisos" del proxy.
    proxy_trigger_swaps = [333, 490, 679, 899]

    fig, ax = plt.subplots(figsize=(10.5, 5.4))

    for r in resets:
        ax.axvline(r, color=BASELINE, linewidth=0.9, linestyle=":", zorder=1)

    ax.plot(swaps, qr, color=BLUE, linewidth=1.3, zorder=3, label="Modularidad real (valor crudo, sin reiniciar)")

    for s in proxy_trigger_swaps:
        q = by_swap[s]["q_real"]
        ax.scatter([s], [q], color=ORANGE, s=42, zorder=4, edgecolors=SURFACE, linewidths=1.0)
        ax.annotate(f"{q:.3f}", (s, q), textcoords="offset points", xytext=(0, 9),
                    ha="center", fontsize=8.5, color=ORANGE)

    from matplotlib.lines import Line2D
    reset_handle = Line2D([0], [0], color=BASELINE, linewidth=0.9, linestyle=":")
    proxy_handle = Line2D([0], [0], marker="o", color=ORANGE, linestyle="none", markersize=6.5,
                           markeredgecolor=SURFACE)

    handles, labels = ax.get_legend_handles_labels()
    handles += [proxy_handle, reset_handle]
    labels += ["Cambio donde la aproximacion barata disparo un aviso", "Reconstruccion"]
    ax.legend(handles, labels, frameon=False, loc="lower left", fontsize=9)

    ax.set_xlabel("Numero de cambio (de 1,000)")
    ax.set_ylabel("Modularidad real (Q)")
    clean_axes(ax)

    fig.tight_layout()
    save(fig, "fig_q_real_trayectoria", bbox_inches="tight")


# ===========================================================================
# Grafica 8 -- Sin reconstruccion de por medio: un solo tramo continuo de
# 1,000 cambios. Arriba, la Q real cruda (nunca se reinicia, sube y baja de
# verdad). Abajo, lo que acumula la aproximacion barata en ese MISMO tramo
# (tambien continuo, sin reiniciar -- aqui si tiene su propia linea, porque
# es lo unico que el proxy en verdad produce). Comparten el eje horizontal
# para poder ver si los tramos de mayor movimiento coinciden.
# ===========================================================================
def fig_comparacion_sin_reconstruccion(hist):
    swaps = [h["swap"] for h in hist]
    qr = [h["q_real"] for h in hist]
    sp = [h["stress_proxy"] for h in hist]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9.5, 6.6), sharex=True,
                                    gridspec_kw={"height_ratios": [1, 1]})

    ax1.plot(swaps, qr, color=BLUE, linewidth=1.3, zorder=3)
    ax1.set_ylabel("Modularidad real\n(Q, valor crudo)")
    clean_axes(ax1)

    ax2.plot(swaps, sp, color=ORANGE, linewidth=1.3, zorder=3)
    ax2.set_ylabel("Estres acumulado\nde la aproximacion barata")
    ax2.set_xlabel("Numero de cambio (de 1,000) -- sin ninguna reconstruccion en todo el tramo")
    clean_axes(ax2)

    fig.suptitle("Sin reconstrucciones de por medio: Q real (arriba) vs. lo que\nacumula la aproximacion barata (abajo), en el mismo tramo continuo",
                 fontsize=11, color=INK, x=0.02, ha="left")

    fig.tight_layout()
    save(fig, "fig_comparacion_sin_reconstruccion", bbox_inches="tight")


if __name__ == "__main__":
    hist = json.load(open(os.path.join(HERE, "deact_history.json")))
    hist_norebuild = json.load(open(os.path.join(HERE, "deact_history_norebuild.json")))

    fig_velocidad()
    fig_costo_en_el_tiempo(hist)
    fig_offset_ciclos()
    fig_umbral_evolucion()
    fig_trayectoria_estres(hist)
    fig_dispersion(hist)
    fig_q_real_trayectoria(hist)
    fig_comparacion_sin_reconstruccion(hist_norebuild)

    print(f"listo -- graficas en {os.path.abspath(OUT)}")
