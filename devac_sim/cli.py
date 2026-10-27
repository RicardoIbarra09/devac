# uso:
#   poetry run devac-sim generate --policies experiments/nimbuslab_policies.json --window 60 --rate 200 --arrivals poisson --mutation-rate 0.5 --event-rate 0.2 --seed 1 --replications 5 --out trazas/w1.jsonl
#   poetry run devac-sim label --trace trazas/w1_r01.jsonl --out etiquetas.csv
#   poetry run devac-sim run --trace trazas/w1_r*.jsonl --servers 2 --warmup 5 --bin 1 --out-dir resultados/sim
#   poetry run devac-sim verify --trace trazas/w1_r01.jsonl --results replay.csv   (el servidor debe arrancar con las mismas politicas)

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List, Optional

from devac.dpl import DPLLoader
from devac.loader import PolicyLoader
from devac.models import Policy

from devac_sim.arrivals import PATTERNS
from devac_sim.des import SimConfig, simulate
from devac_sim.generator import TraceParams, generate_trace
from devac_sim.oracle import Oracle, load_rules
from devac_sim.stats import across_replications, summarize, time_series
from devac_sim.trace import ACCESS, read_trace, write_trace
from devac_sim.verify import read_results, verify


def load_policies(path: str) -> List[Policy]:
    loader = DPLLoader if Path(path).suffix.lower() == ".dpl" else PolicyLoader
    return loader.from_file(path)


# --policies manda; si no, la ruta que guardo generate
def _policies_for(trace_meta: dict, explicit: Optional[str]) -> str:
    path = explicit or trace_meta.get("policies")
    if not path:
        raise SystemExit("la traza no dice de qué políticas parte; usa --policies")
    return path


def _write_csv(path: Path, rows: List[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def cmd_generate(args) -> int:
    policies = [p.model_dump() for p in load_policies(args.policies)]
    out = Path(args.out)
    for r in range(args.replications):
        params = TraceParams(
            window=args.window, rate=args.rate, arrivals=args.arrivals,
            mutation_rate=args.mutation_rate, event_rate=args.event_rate,
            match_fraction=args.match_fraction, seed=args.seed + r,
            peak_factor=args.peak_factor, burst_factor=args.burst_factor,
            burst_fraction=args.burst_fraction, burst_len=args.burst_len,
        )
        meta, records = generate_trace(policies, params)
        meta["policies"] = args.policies
        path = out if args.replications == 1 else out.with_name(f"{out.stem}_r{r + 1:02d}{out.suffix}")
        write_trace(str(path), meta, records)
        print(f"{path}: {meta['access']} peticiones, {meta['records'] - meta['access']} de control (semilla {params.seed})")
    return 0


def cmd_label(args) -> int:
    meta, records = read_trace(args.trace)
    oracle = Oracle(load_policies(_policies_for(meta, args.policies)), load_rules(args.rules))
    rows = []
    for record in records:
        if record["kind"] != ACCESS:
            oracle.apply(record)
            continue
        decision, covered = oracle.decide(record["request"])
        rows.append({"index": len(rows), "t": record["t"], "policy_version": oracle.version,
                     "expected": decision, "covered": covered, **record["request"]})
    _write_csv(Path(args.out), rows)
    for error in oracle.errors:
        print(f"aviso: {error}", file=sys.stderr)
    allow = sum(1 for r in rows if r["expected"] == "allow")
    print(f"{args.out}: {len(rows)} peticiones etiquetadas ({allow} allow, {len(rows) - allow} deny), "
          f"versión final {oracle.version}, {oracle.reconfigurations} reconfiguraciones")
    return 0


def _format_summary(name: str, s: dict) -> str:
    resp = s["response_us"]
    return (f"  {name:<14} X {s['throughput_rps']:>9.1f}/s  rho {s['utilization']:.3f}  L {s['mean_in_system']:.3f}  "
            f"W media {resp.get('mean', 0):>9.1f} us  p99 {resp.get('p99', 0):>9.1f} us  "
            f"pasos {s['steps'].get('mean', 0):.1f}  incorrectas {s['incorrect']}  rezagadas {s['stale']}")


def cmd_run(args) -> int:
    config = SimConfig(
        servers=args.servers, engine=args.engine, service_scale=args.service_scale,
        overhead_us=args.overhead_us, rules_module=args.rules,
        similarity_threshold=args.similarity_threshold, stress_threshold=args.stress_threshold,
        audit=not args.no_audit,
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries, request_rows, series_rows, control_rows = [], [], [], []
    print(f"Servidores: {config.servers}  motor: {config.engine}  calentamiento: {args.warmup} s")
    for rep, trace_path in enumerate(args.trace, start=1):
        meta, records = read_trace(trace_path)
        policies = load_policies(_policies_for(meta, args.policies))
        result = simulate(records, policies, config)

        summary = summarize(result, warmup=args.warmup)
        summary["trace"] = trace_path
        summaries.append(summary)
        print(_format_summary(f"réplica {rep}", summary))
        for error in result.oracle_errors:
            print(f"    aviso: {error}", file=sys.stderr)

        request_rows += [{"rep": rep, **r} for r in result.rows]
        series_rows += [{"rep": rep, **b} for b in time_series(result, args.bin)]
        control_rows += [{"rep": rep, **c} for c in result.controls]

    aggregate = across_replications(summaries)
    if len(summaries) > 1:
        print("Entre réplicas (media ± IC95):")
        for name, ci in aggregate.items():
            if ci["mean"] is not None:
                half = f" ± {ci['ci95']:.3f}" if ci["ci95"] is not None else ""
                print(f"  {name:<18} {ci['mean']:.3f}{half}")

    _write_csv(out_dir / "requests.csv", request_rows)
    _write_csv(out_dir / "series.csv", series_rows)
    _write_csv(out_dir / "controls.csv", control_rows)
    (out_dir / "summary.json").write_text(json.dumps({
        "config": vars(args), "replications": summaries, "aggregate": aggregate,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"Resultados en {out_dir}/")
    return 1 if any(s["incorrect"] for s in summaries) else 0


def cmd_verify(args) -> int:
    meta, records = read_trace(args.trace)
    oracle = Oracle(load_policies(_policies_for(meta, args.policies)), load_rules(args.rules))
    report = verify(records, read_results(args.results), oracle)
    mismatches = report.pop("mismatches")

    print(f"Peticiones: {report['access_answered']} respondidas de {report['access_in_trace']} en la traza")
    print(f"  verificadas {report['checked']}, incorrectas {report['incorrect']}")
    print(f"  durante una escritura {report['in_transition']} (incorrectas {report['incorrect_in_transition']})")
    print(f"  atendidas con una versión anterior a la de la traza {report['behind_trace']}, "
          f"posterior {report['ahead_of_trace']}")
    if report["unmapped_version"] or report["controls_failed"]:
        print(f"  aviso: {report['controls_failed']} registros de control fallaron en la API, "
              f"{report['unmapped_version']} respuestas con una versión que no sale de la traza")
    if args.out and mismatches:
        _write_csv(Path(args.out), mismatches)
        print(f"Diferencias en {args.out}")
    return 1 if report["incorrect"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devac-sim", description="Simulación de eventos discretos para DEvAC")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="genera trazas .jsonl")
    gen.add_argument("--policies", required=True, help="políticas iniciales (.json o .dpl)")
    gen.add_argument("--out", required=True)
    gen.add_argument("--window", type=float, default=60.0, help="segundos de tiempo virtual")
    gen.add_argument("--rate", type=float, default=100.0, help="peticiones de acceso por segundo (media)")
    gen.add_argument("--arrivals", choices=PATTERNS, default="poisson",
                     help="uniform = una cada 1/rate; pico = pico a la mitad; rafaga = periodos on/off")
    gen.add_argument("--mutation-rate", type=float, default=0.0, help="altas/cambios/bajas de políticas por segundo")
    gen.add_argument("--event-rate", type=float, default=0.0, help="eventos de dominio por segundo")
    gen.add_argument("--match-fraction", type=float, default=0.5, help="fracción de peticiones armadas para coincidir")
    gen.add_argument("--seed", type=int, default=1)
    gen.add_argument("--replications", type=int, default=1, help="una traza por semilla: seed, seed+1, ...")
    gen.add_argument("--peak-factor", type=float, default=3.0)
    gen.add_argument("--burst-factor", type=float, default=4.0)
    gen.add_argument("--burst-fraction", type=float, default=0.2)
    gen.add_argument("--burst-len", type=float, default=1.0, help="duración media de una ráfaga (s)")
    gen.set_defaults(func=cmd_generate)

    lab = sub.add_parser("label", help="decisión esperada por petición según el oráculo")
    lab.add_argument("--trace", required=True)
    lab.add_argument("--policies")
    lab.add_argument("--rules", help="módulo con register(reconfigurator)")
    lab.add_argument("--out", required=True)
    lab.set_defaults(func=cmd_label)

    run = sub.add_parser("run", help="corre la simulación sobre una o varias trazas (una réplica por traza)")
    run.add_argument("--trace", required=True, nargs="+")
    run.add_argument("--policies")
    run.add_argument("--servers", type=int, default=1)
    run.add_argument("--engine", choices=["devac", "baseline"], default="devac")
    run.add_argument("--warmup", type=float, default=0.0, help="segundos virtuales que no entran al resumen")
    run.add_argument("--bin", type=float, default=1.0, help="ancho de cada intervalo de la serie de tiempo (s)")
    run.add_argument("--service-scale", type=float, default=1.0)
    run.add_argument("--overhead-us", type=float, default=0.0, help="costo fijo extra por petición, ej. HTTP")
    run.add_argument("--rules", help="módulo con register(reconfigurator)")
    run.add_argument("--similarity-threshold", type=float, default=0.3)
    run.add_argument("--stress-threshold", type=float, default=0.5)
    run.add_argument("--no-audit", action="store_true")
    run.add_argument("--out-dir", default="resultados/sim")
    run.set_defaults(func=cmd_run)

    ver = sub.add_parser("verify", help="compara un replay de devac-loadgen (--csv) contra el oráculo")
    ver.add_argument("--trace", required=True)
    ver.add_argument("--results", required=True, help="CSV que escribió devac-loadgen --csv")
    ver.add_argument("--policies")
    ver.add_argument("--rules", help="el mismo módulo que DEVAC_RULES_MODULE del servidor")
    ver.add_argument("--out", help="CSV con las peticiones que no coinciden")
    ver.set_defaults(func=cmd_verify)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
