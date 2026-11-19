#!/usr/bin/env python3
"""
E7: lecturas en varios procesos (replicas) en lugar de hilos.

En E5 y E6 el lector y el escritor eran hilos del mismo proceso y se turnaban el interprete de Python
(GIL). Aqui un proceso primario tiene el escritor y el monitor (grafo, Q, Louvain) y K procesos lectores
tienen cada uno su replica de DEvAC, con su propio interprete. Tras cada cambio el primario envia a las
replicas la politica y su comunidad; tras un reacomodo, la particion nueva (las replicas no corren
Louvain, solo rehacen sus indices).

Por cada tamano, repeticion y numero de lectores K:
  fase 1, solo lecturas: calentamiento y despues una ventana de medicion sin cambios.
  fase 2, con cambios: el escritor aplica modificaciones a ritmo fijo durante otra ventana, con un
      reacomodo completo a la mitad. Se mide cuantas lecturas por segundo se atienden, su latencia,
      cuanto tarda cada cambio en llegar a todas las replicas y si alguna respuesta fue inconsistente.

Uso:
  python run_e7.py --n 2000 --workers 1 2 4 8 16 --reps 10 --out-dir resultados/e7
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse  # noqa: E402
import copy  # noqa: E402
import csv  # noqa: E402
import gc  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import pickle  # noqa: E402
import socket  # noqa: E402
import statistics as st  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
from array import array  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402
import multiprocessing as mp  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
for sub in ("E6", "E5", "E2", "E1"):
    sys.path.insert(0, str(HERE.parent / sub))

import run_e5 as e5  # noqa: E402
from run_e5 import pct, quiet, run_e1  # noqa: E402
from run_e6 import new_engine  # noqa: E402
from gen_w4 import DEFAULTS as W4_DEFAULTS, MODES as W4_MODES, generate_w4, variant_label  # noqa: E402
from devac import Policy  # noqa: E402

clock = time.perf_counter_ns  # CLOCK_MONOTONIC: comparable entre procesos del mismo nodo
CTX = mp.get_context("fork")


# ---------------------------------------------------------------- nucleos
def cpu_order(allowed):
    """Primero un CPU logico por nucleo fisico; despues los hermanos de hyperthreading.
    Devuelve (orden, numero de nucleos fisicos)."""
    seen, first, rest = set(), [], []
    for c in sorted(allowed):
        try:
            base = f"/sys/devices/system/cpu/cpu{c}/topology"
            key = (open(f"{base}/physical_package_id").read().strip(), open(f"{base}/core_id").read().strip())
        except OSError:
            key = (None, c)
        (rest if key in seen else first).append(c)
        seen.add(key)
    return first + rest, len(first)


def pin(core):
    if core is not None and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {core})


def pss_mb():
    try:
        for line in open("/proc/self/smaps_rollup"):
            if line.startswith("Pss:"):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    return None


def send(conn, msg_bytes):
    conn.send_bytes(msg_bytes)


# ---------------------------------------------------------------- replica
def replica_apply(ev, msg):
    kind = msg[0]
    if kind == "sync":
        _, _, pid, pol, comm = msg
        if pol is None:
            if pid in ev.policies:
                ev.remove_policy(pid)
            return
        # la comunidad la decide el primario: la replica no busca la mas parecida
        ev.community_assigner = lambda _p, c=comm: c
        if pid not in ev.policies:
            ev.add_policy(pol, auto_assign_community=False)
        else:
            ev.update_policy(pol)
        cur = ev.policy_community.get(pid)
        if comm is not None and cur != comm:
            if cur is None:
                ev.assign_policy_to_community(pid, comm)
            else:
                ev.move_policy_to_community(pid, comm)
    elif kind == "rebuild":
        ev.run(list(ev.policies.values()), msg[1], {})


def worker_main(wid, ev, conn, reqs, T, core, tmpdir):
    pin(core)
    ev._on_policy_change = []      # la replica no lleva grafo ni Q: eso vive en el primario
    ev.community_assigner = None
    state = {"version": 0, "stop": False}
    applied = []                   # (secuencia, tipo, t_recibido, t_aplicado)
    errors = Counter()

    # la replica aplica los cambios entre una lectura y la siguiente, en el mismo hilo: ninguna
    # lectura se interrumpe a la mitad (no hay turno de Python que esperar), y una solicitud que llega
    # mientras se aplica un cambio espera a lo mas lo que dura aplicarlo ('aplicar_*' en el resumen)
    def apply_pending(block=False):
        while not state["stop"] and (block or conn.poll()):
            raw = conn.recv_bytes()
            t_recv = clock()
            msg = pickle.loads(raw)
            if msg[0] == "stop":
                state["stop"] = True
                break
            try:
                with quiet():
                    replica_apply(ev, msg)
            except Exception as e:  # noqa: BLE001
                errors["replica:" + type(e).__name__] += 1
            if msg[0] == "sync":
                state["version"] = msg[1] + 1
            applied.append((msg[1] if msg[0] == "sync" else msg[2], 0 if msg[0] == "sync" else 1, t_recv, clock()))

    t0s, t1s, idxs, decs, vers = array("q"), array("q"), array("i"), array("b"), array("i")
    n, i = len(reqs), wid * 37
    while clock() < T["start"]:
        time.sleep(0.001)
    end = T["end"]
    while True:
        apply_pending()
        a = clock()
        if a >= end:
            break
        k = i % n
        v = state["version"]
        try:
            d = 1 if ev.evaluate(reqs[k])["result"] == "allow" else 0
        except Exception as e:  # noqa: BLE001
            errors["lector:" + type(e).__name__] += 1
            d = -1
        b = clock()
        if a >= T["warm_end"]:
            t0s.append(a); t1s.append(b); idxs.append(k); decs.append(d); vers.append(v)  # noqa: E702
        i += 1
    apply_pending(block=True)  # lo que falte, hasta el aviso de fin
    final = [1 if ev.evaluate(r)["result"] == "allow" else 0 for r in reqs]
    np.savez(os.path.join(tmpdir, f"w{wid}.npz"), t0=np.frombuffer(t0s, np.int64), t1=np.frombuffer(t1s, np.int64),
             idx=np.frombuffer(idxs, np.int32), dec=np.frombuffer(decs, np.int8), ver=np.frombuffer(vers, np.int32),
             applied=np.array(applied, dtype=np.int64).reshape(-1, 4), final=np.array(final, np.int8),
             final_version=state["version"], pss=pss_mb() or -1, errors=json.dumps(dict(errors)))
    os._exit(0)


# ---------------------------------------------------------------- una corrida (K lectores)
def run_config(ev, mon, ops, reqs, D, K, cores, args, tmpdir):
    """Corre en un proceso hijo con su propia copia del primario."""
    pin(cores[0])
    warm, win = args.warmup * 1e9, args.window * 1e9
    now = clock()
    T = {"start": now + int((1.5 + 0.1 * K) * 1e9)}
    T["warm_end"] = T["start"] + int(warm)
    T["phase2"] = T["warm_end"] + int(win)
    T["end"] = T["phase2"] + int(win)
    conns, procs = [], []
    for w in range(K):
        parent, child = CTX.Pipe(duplex=False)
        # con mas procesos que CPUs logicos, varios comparten CPU y el sistema los turna
        core = cores[(1 + w) % len(cores)] if 1 + w >= len(cores) else cores[1 + w]
        p = CTX.Process(target=worker_main, args=(w, ev, parent, reqs, T, core, tmpdir))
        p.start()
        child_end = child
        conns.append(child_end)
        procs.append(p)
        parent.close()
    pss_primary = pss_mb()
    pub, rebuilds, w_err = [], [], Counter()
    rebuild_at = len(ops) // 4  # a la cuarta parte, para que el reacomodo termine dentro de la ventana
    while clock() < T["phase2"]:
        time.sleep(0.002)
    base_t, j = clock(), 0
    with quiet():
        for k, op in enumerate(ops):
            target = base_t + int((k - j) * 1e9 / args.rate)
            while clock() < target:
                time.sleep(max(0, (target - clock()) / 1e9 - 0.0002))
            if clock() >= T["end"]:
                break
            try:
                e5.apply_op(ev, op)
            except Exception as ex:  # noqa: BLE001
                w_err[type(ex).__name__] += 1
            pid = (op[2] or op[1]).policy_id
            msg = pickle.dumps(("sync", k, pid, ev.policies.get(pid), ev.policy_community.get(pid)))
            t_pub = clock()
            for c in conns:
                send(c, msg)
            pub.append((k, t_pub))
            if k + 1 == rebuild_at:
                r0 = clock()
                try:
                    mon.rebuild_now()
                except Exception as ex:  # noqa: BLE001
                    w_err["reacomodo:" + type(ex).__name__] += 1
                r1 = clock()
                msg = pickle.dumps(("rebuild", dict(ev.policies_by_community), k))
                for c in conns:
                    send(c, msg)
                rebuilds.append((r0, r1, clock(), len(msg)))
                base_t, j = clock(), k + 1   # el ritmo sigue despues del reacomodo, sin rafaga
    stop = pickle.dumps(("stop",))
    for c in conns:
        send(c, stop)
    for p in procs:
        p.join()
    primary_final = [1 if ev.evaluate(r)["result"] == "allow" else 0 for r in reqs]
    return summarize(K, T, pub, rebuilds, w_err, D, len(reqs), tmpdir, pss_primary, primary_final, args)


def summarize(K, T, pub, rebuilds, w_err, D, n_req, tmpdir, pss_primary, primary_final, args):
    pub_t = {k: t for k, t in pub}
    n_pub = len(pub)
    lat1, lat2, lat_rb_p = [], [], []
    reads1 = reads2 = 0
    inconsistent, r_err, delays, final_ok, pss = 0, Counter(), {}, [], []
    rb_windows = [(r0, r2) for r0, _, r2, _ in rebuilds]
    apply_sync, apply_rb = [], []
    examples = []
    for w in range(K):
        z = np.load(os.path.join(tmpdir, f"w{w}.npz"))
        t0, t1, idx, dec, ver = z["t0"], z["t1"], z["idx"], z["dec"], z["ver"]
        lat = (t1 - t0) / 1000.0
        ph2 = t0 >= T["phase2"]
        reads1 += int((~ph2).sum())
        reads2 += int(ph2.sum())
        lat1.append(lat[~ph2])
        lat2.append(lat[ph2])
        for b0, b1 in rb_windows:
            m = (t0 < b1) & (t1 > b0)
            lat_rb_p.append(lat[m])
        app = z["applied"]
        ok = dec >= 0
        v = np.minimum(ver, D.shape[0] - 1)
        good = D[v, idx] == dec  # entre lecturas: cada lectura ve exactamente la version v
        bad = ok & ~good
        inconsistent += int(bad.sum())
        for q in np.flatnonzero(bad)[: max(0, 5 - len(examples))]:
            examples.append({"lector": w, "solicitud": int(idx[q]), "version": int(ver[q])})
        apply_sync.extend(((app[:, 3] - app[:, 2]) / 1e6)[app[:, 1] == 0].tolist() if len(app) else [])
        apply_rb.extend(((app[:, 3] - app[:, 2]) / 1e6)[app[:, 1] == 1].tolist() if len(app) else [])
        for seq, kind, tr, ta in app:
            if kind == 0 and seq in pub_t:
                delays.setdefault(int(seq), []).append((ta - pub_t[seq]) / 1e6)
        fv = int(z["final_version"])
        final_ok.append(100.0 * float(np.mean(z["final"] == D[min(fv, D.shape[0] - 1)])))
        if fv != n_pub:
            r_err["replica_desfasada"] += 1
        r_err.update(json.loads(str(z["errors"])))
        pss.append(float(z["pss"]))
    l1, l2 = np.concatenate(lat1), np.concatenate(lat2)
    rbp = np.concatenate(lat_rb_p) if lat_rb_p else np.array([])
    all_d = [d for ds in delays.values() for d in ds]
    worst = [max(ds) for ds in delays.values()]  # cuando el cambio ya llego a todas las replicas
    win = args.window
    prim_ok = 100.0 * float(np.mean(np.array(primary_final) == D[min(n_pub, D.shape[0] - 1)]))
    return {
        "lectores": K,
        "lect_por_s_reposo": round(reads1 / win, 1), "lect_por_s_cambios": round(reads2 / win, 1),
        "lectura_reposo_p50_us": pct(l1, 50), "lectura_reposo_p95_us": pct(l1, 95), "lectura_reposo_p99_us": pct(l1, 99),
        "lectura_cambios_p50_us": pct(l2, 50), "lectura_cambios_p95_us": pct(l2, 95), "lectura_cambios_p99_us": pct(l2, 99),
        "razon_p95": round(pct(l2, 95) / pct(l1, 95), 3) if len(l1) and len(l2) else "",
        "lectura_reacomodo_primario_p95_us": pct(rbp, 95) if len(rbp) else "",
        "aplicar_cambio_p50_ms": round(float(np.percentile(apply_sync, 50)), 3) if apply_sync else "",
        "aplicar_cambio_p95_ms": round(float(np.percentile(apply_sync, 95)), 3) if apply_sync else "",
        "aplicar_reacomodo_ms": round(float(np.median(apply_rb)), 3) if apply_rb else "",
        "cambios_aplicados": n_pub, "ritmo_real_por_s": round(n_pub / win, 2),
        "propagacion_p50_ms": round(float(np.percentile(all_d, 50)), 3) if all_d else "",
        "propagacion_p95_ms": round(float(np.percentile(all_d, 95)), 3) if all_d else "",
        "ventana_todas_p95_ms": round(float(np.percentile(worst, 95)), 3) if worst else "",
        "ventana_todas_max_ms": round(max(worst), 3) if worst else "",
        "reacomodo_primario_s": round((rebuilds[0][1] - rebuilds[0][0]) / 1e9, 3) if rebuilds else "",
        "reacomodo_envio_ms": round((rebuilds[0][2] - rebuilds[0][1]) / 1e6, 3) if rebuilds else "",
        "lecturas_fase1": reads1, "lecturas_fase2": reads2,
        "inconsistentes": inconsistent, "errores_lector": sum(r_err.values()), "errores_escritor": sum(w_err.values()),
        "final_ok_min_pct": round(min(final_ok + [prim_ok]), 3),
        "memoria_replica_mb": round(st.median(pss), 1) if pss else "", "memoria_primario_mb": pss_primary,
        "_detalle": {"errores_lector": dict(r_err), "errores_escritor": dict(w_err), "ejemplos": examples},
    }


def child_runner(ev, mon, ops, reqs, D, K, cores, args, out_path):
    tmpdir = tempfile.mkdtemp(prefix="e7_")
    try:
        res = run_config(ev, mon, ops, reqs, D, K, cores, args, tmpdir)
    except Exception as e:  # noqa: BLE001
        res = {"lectores": K, "fallo": repr(e)}
    with open(out_path, "w") as f:
        json.dump(res, f, default=str)
    for fn in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, fn))
    os.rmdir(tmpdir)
    os._exit(0)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="E7: lectores en procesos separados (replicas)")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--workers", nargs="+", type=int, default=[1, 2, 4, 8, 16, 32])
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--warmup", type=float, default=10.0, help="segundos de calentamiento")
    ap.add_argument("--window", type=float, default=30.0, help="segundos de cada ventana de medicion")
    ap.add_argument("--rate", type=float, default=20.0, help="cambios por segundo en la fase 2")
    ap.add_argument("--op", choices=["toggle", "update", "add", "remove"], default="toggle",
                    help="tipo de cambio de la fase 2; toggle no desordena las comunidades (E5), asi que lo que "
                         "cambie en las lecturas se debe a la concurrencia")
    ap.add_argument("--data-seed", type=int, default=1)
    ap.add_argument("--mode", choices=W4_MODES, default="uniforme")
    ap.add_argument("--p-mix", type=float, default=W4_DEFAULTS["p_mix"])
    ap.add_argument("--theta", type=float, default=0.3)
    ap.add_argument("--pool", type=int, default=400)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    w4 = {**W4_DEFAULTS, "mode": args.mode, "p_mix": args.p_mix}
    carga = f"W4-{variant_label(w4)}"
    base = [Policy.model_validate(p) for p in generate_w4(args.n, seed=args.data_seed, **w4)]
    allowed = os.sched_getaffinity(0) if hasattr(os, "sched_getaffinity") else set(range(os.cpu_count()))
    cores, n_phys = cpu_order(allowed)
    tag = f"e7_{carga}_n{args.n}_{args.op}"
    log = lambda m: print(f"[E7 {carga} n={args.n}] {m}", flush=True)
    log(f"CPUs logicos: {len(cores)}, nucleos fisicos: {n_phys}; lectores: {args.workers}")
    for K in args.workers:
        if 1 + K > len(cores):
            log(f"K={K}: mas procesos que CPUs logicos; el sistema los turna (saturacion)")
        elif 1 + K > n_phys:
            log(f"K={K}: no caben en los nucleos fisicos; se usan los hermanos de hyperthreading")
    n_ops = int(args.rate * args.window)

    rows, details = [], []
    for rep in range(args.reps):
        ops = e5.make_ops(args.op, base, rep, n_ops, w4)
        pool = e5.request_pool(base, ops, rep, args.pool)
        reqs = [run_e1.to_access_request(v) for v in pool]
        D = e5.reference_states(base, ops, pool)
        ev, mon = new_engine(base, args.theta, rep + 1, math.inf)  # el reacomodo se fuerza a la mitad
        for r in reqs[:200]:
            ev.evaluate(r)
        gc.collect()
        gc.freeze()  # los hijos heredan el motor sin que el recolector toque esas paginas
        for K in args.workers:
            out_path = out_dir / f".tmp_{tag}_r{rep}_k{K}.json"
            p = CTX.Process(target=child_runner, args=(ev, mon, ops, reqs, D, K, cores, args, str(out_path)))
            p.start()
            p.join()
            res = json.loads(out_path.read_text())
            out_path.unlink()
            det = res.pop("_detalle", {})
            ubic = "fisicos" if 1 + K <= n_phys else ("hyperthreading" if 1 + K <= len(cores) else "saturado")
            row = {"carga": carga, "n": args.n, "rep": rep, **res, "ubicacion": ubic}
            rows.append(row)
            details.append({"rep": rep, "lectores": K, **det})
            if "fallo" in res:
                log(f"rep {rep + 1} K={K}: FALLO {res['fallo']}")
                continue
            log(f"rep {rep + 1}/{args.reps} K={K}: {res['lect_por_s_reposo']}/s reposo, "
                f"{res['lect_por_s_cambios']}/s con cambios | p95 {res['lectura_reposo_p95_us']} -> "
                f"{res['lectura_cambios_p95_us']} us | propagacion p95 {res['propagacion_p95_ms']} ms | "
                f"cambios {res['cambios_aplicados']} | inconsistentes {res['inconsistentes']} | "
                f"errores {res['errores_lector']}/{res['errores_escritor']}")
        gc.unfreeze()

    with open(out_dir / f"{tag}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)), restval="")
        w.writeheader()
        w.writerows(rows)
    summ = {}
    for K in args.workers:
        r = [x for x in rows if x["lectores"] == K and "fallo" not in x]
        if not r:
            continue
        med = lambda k: st.median([x[k] for x in r if x[k] != ""]) if any(x[k] != "" for x in r) else None
        summ[str(K)] = {k: med(k) for k in r[0] if k not in ("carga", "n", "rep", "lectores", "inconsistentes",
                                                             "errores_lector", "errores_escritor",
                                                             "lecturas_fase1", "lecturas_fase2", "ubicacion")}
        summ[str(K)].update({k: sum(x[k] for x in r) for k in ("inconsistentes", "errores_lector",
                                                                "errores_escritor", "lecturas_fase1", "lecturas_fase2")})
        summ[str(K)]["final_ok_min_pct"] = min(x["final_ok_min_pct"] for x in r)
        summ[str(K)]["ubicacion"] = r[0]["ubicacion"]
        summ[str(K)]["ventana_todas_max_ms"] = max(x["ventana_todas_max_ms"] for x in r if x["ventana_todas_max_ms"] != "")
    meta = {
        "experimento": "E7", "carga": carga, "n_eventos": args.n, "repeticiones": args.reps,
        "lectores": args.workers, "calentamiento_s": args.warmup, "ventana_s": args.window,
        "ritmo_cambios_por_s": args.rate, "operacion": args.op, "reacomodo": "uno, a la cuarta parte de los cambios de la fase 2",
        "theta": args.theta, "pool": args.pool, "semilla_datos": args.data_seed, "parametros_w4": w4,
        "nucleos_fisicos": n_phys, "cpus_logicos": len(cores), "resumen": summ, "detalle": details,
        "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodo": socket.gethostname(), "cpu": run_e1.cpu_model(), "python": sys.version.split()[0],
        "dependencias": run_e1.versions(), "commit_demo": os.environ.get("DEVAC_COMMIT", "desconocido"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"), "slurm_array_job_id": os.environ.get("SLURM_ARRAY_JOB_ID"),
        "slurm_array_task_id": os.environ.get("SLURM_ARRAY_TASK_ID"),
    }
    with open(out_dir / f"{tag}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False, default=str)
    log("listo")


if __name__ == "__main__":
    main()
