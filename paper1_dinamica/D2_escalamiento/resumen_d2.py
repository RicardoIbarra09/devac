#!/usr/bin/env python3
# D2: tiempo por cambio de la Q completa, la Q incremental y la actualizacion del grafo segun el
# tamano de la red, y tiempo de una reconstruccion completa. escribe resultados/d2_escalamiento.csv
# (una fila por tamano y repeticion); D5 lo usa para pasar su costo a segundos
#
# uso:
#   python3 paper1_dinamica/D2_escalamiento/resumen_d2.py                  (job mas reciente de resultados/crudo/d2)
#   python3 paper1_dinamica/D2_escalamiento/resumen_d2.py resultados/crudo/d2/<JOB>

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datos import RESULTS, ci95, job_dir_from_argv, load_runs, mean, subfolders, write_csv  # noqa: E402


def collect(job_dir: Path) -> list:
    rows = []
    for folder in subfolders(job_dir):
        if not folder.name.startswith("n"):
            continue  # redes/
        for config, entry in load_runs(folder, with_rows=False).items():
            for rep in entry["reps"]:
                s, meta = rep["summary"], rep["meta"]
                final = s.get("final_rebuild") or {}
                rows.append({
                    "job": job_dir.name, "events": int(folder.name[1:]), "config": config, "rep": s["rep"],
                    "plan_seed": s["plan_seed"], "louvain_seed": s["louvain_seed"], "changes": s["changes"],
                    "networkx_changes": s["networkx_changes"],
                    "initial_nodes": s["initial_nodes"], "initial_edges": s["initial_edges"],
                    "mean_t_networkx_s": s["mean_t_networkx_s"], "mean_t_incremental_s": s["mean_t_incremental_s"],
                    "mean_t_graph_s": s["mean_t_graph_s"], "initial_build_s": s["initial_build_s"],
                    "mean_size": s.get("mean_size"),
                    # reconstruccion completa (grafo + louvain + auditoria + segunda pasada + indices) sobre
                    # la red ya modificada, con --final-rebuild
                    "rebuild_s": final.get("s"), "rebuild_nodes": final.get("nodes"),
                    "rebuild_edges": final.get("edges"), "rebuild_t_build_s": final.get("t_build_s"),
                    "rebuild_t_louvain_s": final.get("t_louvain_s"), "rebuild_t_audit_s": final.get("t_audit_s"),
                    "rebuild_t_second_pass_s": (final["t_build_used_s"] + final["t_louvain_used_s"]
                                                if final else None),
                    "max_rss_mb": meta.get("max_rss_mb"),
                    "max_abs_q_diff": s["max_abs_q_diff"], "node": meta["node"], "cpu": meta["cpu"],
                    "cpu_affinity": " ".join(map(str, meta.get("cpu_affinity") or [])),
                    "commit": meta["commit"], "timing": meta["timing"],
                })
    return sorted(rows, key=lambda r: (r["events"], r["rep"]))


def _rebuild(group) -> str:
    times = [r["rebuild_s"] for r in group if r["rebuild_s"] is not None]
    return f"{mean(times):>7.1f} ± {ci95(times):<5.1f}" if times else "-"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    job_dir = job_dir_from_argv(argv, "d2")
    rows = collect(job_dir)
    if not rows:
        sys.exit(f"no hay *_meta.json en {job_dir}/n*/")
    out = Path(argv[1]) if len(argv) > 1 else RESULTS / "d2_escalamiento.csv"
    write_csv(out, rows)

    print(f"{job_dir} -> {out}  (commit {rows[0]['commit']}, {rows[0]['node']}, {rows[0]['cpu']})")
    if any(r["timing"] != "reported" for r in rows):
        print("AVISO: hay corridas sin TIMING=1; esos tiempos no sirven para el paper")
    hdr = (f"{'eventos':>8} {'aristas':>9} {'reps':>4} {'Q completa ms':>17} {'Q incr us':>15} "
           f"{'grafo us':>15} {'veces':>7} {'Q medidas':>9} {'recons s':>15} {'RAM GB':>7}")
    print(hdr)
    print("-" * len(hdr))
    groups = {}
    for row in rows:
        groups.setdefault(row["events"], []).append(row)
    for n, group in sorted(groups.items()):
        full = [r["mean_t_networkx_s"] * 1e3 for r in group]
        inc = [r["mean_t_incremental_s"] * 1e6 for r in group]
        graph = [r["mean_t_graph_s"] * 1e6 for r in group]
        print(f"{n:>8} {mean(r['initial_edges'] for r in group):>9.0f} {len(group):>4} "
              f"{mean(full):>9.2f} ± {ci95(full):<5.2f} {mean(inc):>7.1f} ± {ci95(inc):<5.1f} "
              f"{mean(graph):>7.0f} ± {ci95(graph):<5.0f} {mean(full) * 1e3 / mean(inc):>7.0f} "
              f"{group[0]['networkx_changes']:>4}/{group[0]['changes']} {_rebuild(group):>15} "
              f"{max((r['max_rss_mb'] or 0) for r in group) / 1024:>7.1f}")
    print("\n± = IC95 entre repeticiones. 'veces' = Q completa / Q incremental."
          "\n'Q medidas' = cambios en los que se midio la Q completa (muestra con los tamanos grandes)."
          "\n'recons s' = reconstruccion completa al final de los cambios; 'RAM GB' = pico del proceso (maximo).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
