# uso:
#   poetry run devac-loadgen --requests experiments/nimbuslab_requests.json --total 5000 --concurrency 32
#   poetry run devac-loadgen --requests experiments/nimbuslab_requests.json --total 8000 --processes 4 --concurrency 4
#   poetry run devac-loadgen --requests experiments/nimbuslab_requests.json --rate 300 --total 3000
#   poetry run devac-loadgen --requests traza.jsonl --speed 10 --csv resultados.csv

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import numpy as np

from devac_client.client import DevacAPIError, DevacClient

DIMENSIONS = ("subject", "asset", "space", "time", "action")

# (segundo en que debe salir o None, peticion)
ScheduledRequest = Tuple[Optional[float], Dict[str, str]]


@dataclass
class Sample:
    index: int
    intended_offset_s: Optional[float]
    latency_us: float
    server_us: Optional[float]
    eval_time_us: Optional[float]
    steps: Optional[int]
    decision: Optional[str]
    status: int
    error: Optional[str] = None


# acepta formato del nucleo o plano
def _flatten(record: dict) -> Dict[str, str]:
    source = record.get("request", record)
    flat = {}
    for dim in DIMENSIONS:
        value = source[dim]
        flat[dim] = str(value["value"] if isinstance(value, dict) else value)
    return flat


def load_workload(path: str) -> List[ScheduledRequest]:
    p = Path(path)
    if p.suffix == ".jsonl":
        items = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                items.append((record.get("t"), _flatten(record)))  # t = segundo de la traza
        return items
    return [(None, _flatten(r)) for r in json.loads(p.read_text(encoding="utf-8"))]


# decide cuando sale cada peticion
def build_schedule(items: List[ScheduledRequest], total: int, rate: float, speed: float) -> List[ScheduledRequest]:
    if not items:
        raise ValueError("la carga de trabajo está vacía")
    n = len(items)
    if rate > 0:
        return [(i / rate, items[i % n][1]) for i in range(total)]  # lazo abierto a tasa fija

    if speed > 0:
        if any(t is None for t, _ in items):
            raise ValueError('--speed requiere una traza .jsonl donde todas las líneas tengan "t"')
        times = [float(t) for t, _ in items]
        first, last = times[0], times[-1]
        # si la traza se repite, cada vuelta arranca despues de la anterior
        span = (last - first) + ((last - first) / (n - 1) if n > 1 else 1.0)
        return [((times[i % n] - first + (i // n) * span) / speed, items[i % n][1]) for i in range(total)]

    return [(None, items[i % n][1]) for i in range(total)]  # lazo cerrado: sin horario


async def _send(client: DevacClient, index: int, scheduled: ScheduledRequest, engine: str,
                t0: float, samples: List[Optional[Sample]]) -> None:
    offset, request = scheduled
    # en lazo abierto se mide desde la hora programada
    started = time.perf_counter() if offset is None else t0 + offset
    decision, server_us, status, error = None, None, 200, None
    try:
        decision, server_us = await client.evaluate_timed(request, engine)
    except DevacAPIError as exc:
        status, error = exc.status_code, str(exc.detail)
    except httpx.HTTPError as exc:
        status, error = 0, f"{type(exc).__name__}: {exc}"
    latency_us = (time.perf_counter() - started) * 1e6

    samples[index] = Sample(
        index=index,
        intended_offset_s=offset,
        latency_us=latency_us,
        server_us=server_us,
        eval_time_us=decision["eval_time_us"] if decision else None,
        steps=decision["steps"] if decision else None,
        decision=decision["decision"] if decision else None,
        status=status,
        error=error,
    )


# cada productor manda la siguiente cuando le responden
async def _closed_loop(client, schedule, engine, concurrency, samples) -> None:
    queue: asyncio.Queue = asyncio.Queue()
    for item in enumerate(schedule):
        queue.put_nowait(item)

    async def producer() -> None:
        while True:
            try:
                index, scheduled = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            await _send(client, index, scheduled, engine, 0.0, samples)

    await asyncio.gather(*(producer() for _ in range(concurrency)))


# salen a su hora aunque el servidor vaya atrasado
async def _open_loop(client, schedule, engine, concurrency, samples, t0) -> None:
    in_flight = asyncio.Semaphore(concurrency)
    tasks = []
    for index, scheduled in enumerate(schedule):
        delay = t0 + scheduled[0] - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        await in_flight.acquire()  # tope de peticiones en vuelo
        task = asyncio.create_task(_send(client, index, scheduled, engine, t0, samples))
        task.add_done_callback(lambda _t: in_flight.release())
        tasks.append(task)
    await asyncio.gather(*tasks)


async def run_load(url: str, schedule: List[ScheduledRequest], *, engine: str = "devac", concurrency: int = 16,
                   warmup: int = 0, timeout: float = 10.0,
                   transport: Optional[httpx.AsyncBaseTransport] = None) -> Tuple[List[Sample], float]:
    open_loop = schedule[0][0] is not None
    async with DevacClient(url, timeout=timeout, max_connections=concurrency, transport=transport) as client:
        await client.health()
        # calentamiento, no se cuenta
        if warmup:
            discard: List[Optional[Sample]] = [None] * warmup
            warm = [(None, schedule[i % len(schedule)][1]) for i in range(warmup)]
            await _closed_loop(client, warm, engine, concurrency, discard)

        samples: List[Optional[Sample]] = [None] * len(schedule)
        t0 = time.perf_counter()
        if open_loop:
            await _open_loop(client, schedule, engine, concurrency, samples, t0)
        else:
            await _closed_loop(client, schedule, engine, concurrency, samples)
        duration = time.perf_counter() - t0
    return samples, duration


def _run_shard(url: str, shard: List[ScheduledRequest], engine: str, concurrency: int,
               warmup: int, timeout: float) -> Tuple[List[Sample], float]:
    return asyncio.run(run_load(url, shard, engine=engine, concurrency=concurrency, warmup=warmup, timeout=timeout))


def run_load_multiprocess(url: str, schedule: List[ScheduledRequest], *, processes: int, engine: str = "devac",
                          concurrency: int = 16, warmup: int = 0, timeout: float = 10.0) -> Tuple[List[Sample], float]:
    from concurrent.futures import ProcessPoolExecutor

    shards = [schedule[i::processes] for i in range(processes)]  # peticion i -> proceso i % P
    with ProcessPoolExecutor(max_workers=processes) as pool:
        futures = [pool.submit(_run_shard, url, s, engine, concurrency, warmup, timeout) for s in shards if s]
        results = [f.result() for f in futures]

    samples: List[Sample] = []
    for shard_index, (shard_samples, _) in enumerate(results):
        for s in shard_samples:
            s.index = s.index * processes + shard_index  # regresa al indice global
            samples.append(s)
    samples.sort(key=lambda s: s.index)
    return samples, max(duration for _, duration in results)


def _pcts(values: List[float]) -> dict:
    if not values:
        return {}
    arr = np.asarray(values, dtype=float)
    return {
        "mean": float(arr.mean()),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(arr.max()),
    }


# percentiles de latencia, pasos y decisiones
def summarize(samples: List[Sample], duration_s: float) -> dict:
    ok = [s for s in samples if s.status == 200]
    return {
        "requests": len(samples),
        "ok": len(ok),
        "errors": len(samples) - len(ok),
        "error_kinds": dict(Counter(s.error.split(":")[0] for s in samples if s.error)),
        "duration_s": duration_s,
        "throughput_rps": len(ok) / duration_s if duration_s > 0 else 0.0,
        "client_latency_us": _pcts([s.latency_us for s in ok]),
        "server_time_us": _pcts([s.server_us for s in ok if s.server_us is not None]),
        "eval_time_us": _pcts([s.eval_time_us for s in ok]),
        "steps": _pcts([float(s.steps) for s in ok]),
        "decisions": dict(Counter(s.decision for s in ok)),
    }


def _format(summary: dict, engine: str) -> str:
    def row(name: str, stats: dict) -> str:
        if not stats:
            return f"  {name:<24} -"
        return (f"  {name:<24} media {stats['mean']:>10.1f}  p50 {stats['p50']:>10.1f}  "
                f"p95 {stats['p95']:>10.1f}  p99 {stats['p99']:>10.1f}  max {stats['max']:>10.1f}")

    lines = [
        f"Motor: {engine}",
        f"Solicitudes: {summary['requests']} (ok {summary['ok']}, errores {summary['errors']}) "
        f"en {summary['duration_s']:.3f} s -> {summary['throughput_rps']:.1f} solicitudes/s",
        row("latencia cliente (us)", summary["client_latency_us"]),
        row("tiempo servidor (us)", summary["server_time_us"]),
        row("evaluate() (us)", summary["eval_time_us"]),
        row("pasos", summary["steps"]),
        f"  decisiones: {summary['decisions']}",
    ]
    if summary["error_kinds"]:
        lines.append(f"  errores: {summary['error_kinds']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generador de carga para la API de DEvAC")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", required=True, help="JSON (arreglo) o JSONL de solicitudes")
    parser.add_argument("--engine", choices=["devac", "baseline"], default="devac")
    parser.add_argument("--total", type=int, default=None, help="solicitudes a enviar (cicla la carga; por defecto, su tamaño)")
    parser.add_argument("--concurrency", type=int, default=16, help="por proceso: productores en lazo cerrado / tope en vuelo en lazo abierto")
    parser.add_argument("--processes", type=int, default=1, help="procesos generadores en paralelo")
    parser.add_argument("--rate", type=float, default=0.0, help="lazo abierto: solicitudes por segundo")
    parser.add_argument("--speed", type=float, default=0.0, help="replay de traza .jsonl a N veces tiempo real (0 = tiempo virtual)")
    parser.add_argument("--warmup", type=int, default=0, help="solicitudes de calentamiento descartadas")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--csv", help="guardar una fila por solicitud")
    parser.add_argument("--json-out", help="guardar el resumen en JSON")
    args = parser.parse_args(argv)

    if args.rate > 0 and args.speed > 0:
        parser.error("--rate y --speed son excluyentes")

    items = load_workload(args.requests)
    schedule = build_schedule(items, args.total or len(items), args.rate, args.speed)
    if args.processes > 1:
        samples, duration = run_load_multiprocess(
            args.url, schedule, processes=args.processes, engine=args.engine,
            concurrency=args.concurrency, warmup=args.warmup, timeout=args.timeout,
        )
    else:
        samples, duration = asyncio.run(run_load(
            args.url, schedule, engine=args.engine, concurrency=args.concurrency,
            warmup=args.warmup, timeout=args.timeout,
        ))

    summary = summarize(samples, duration)
    print(f"Procesos generadores: {args.processes}")
    print(_format(summary, args.engine))

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(asdict(samples[0])))
            writer.writeheader()
            writer.writerows(asdict(s) for s in samples)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"engine": args.engine, **summary}, indent=2), encoding="utf-8")
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
