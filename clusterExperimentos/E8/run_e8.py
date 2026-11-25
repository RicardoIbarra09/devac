#!/usr/bin/env python3
"""
E8: DEvAC repartido en varias computadoras.

Tres papeles, cada uno en su propia computadora (nodo) y comunicados solo por la red (TCP):
  principal  guarda las reglas oficiales, aplica los cambios, hace el reacomodo y coordina todo.
  copia      tiene su propia copia de DEvAC; responde solicitudes y aplica los cambios que le manda el
             principal, en orden, entre una solicitud y la siguiente; avisa al principal cada cambio
             aplicado (acuse de recibo).
  cliente    genera solicitudes a un ritmo fijo (carga abierta) y las reparte por turnos entre las copias;
             mide cuanto tarda cada respuesta desde que debia enviarse.

Por cada repeticion y numero de copias K:
  fase A, solo preguntas: la carga sube en escalones hasta que el sistema se satura (la latencia p95 pasa de
      10 veces la del primer escalon, o mas de 1% de solicitudes sin respuesta o con error).
  fase B, con cambios: carga fija (la mitad de la capacidad hallada en A) mientras el principal aplica
      cambios de reglas a ritmo fijo, con un reacomodo completo; se mide la ventana (del envio de cada cambio
      al acuse de la ultima copia), la latencia antes, durante y despues del reacomodo, y si alguna respuesta
      no coincide con el oraculo para la version de reglas que tenia esa copia.

Todos los procesos se encuentran por medio de un archivo en la carpeta compartida (--rendezvous); por eso los
nodos intel y los amd pueden ser dos trabajos de Slurm separados que arrancan cuando cada uno consigue sus nodos.

Uso (lo lanza e8_enviar.sh):
  run_e8.py principal --rendezvous DIR --replicas 1 2 4 8 --clients 32 --n 2000 --reps 10 --out-dir DIR
  run_e8.py copia     --rendezvous DIR
  run_e8.py cliente   --rendezvous DIR
"""
import os
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse  # noqa: E402
import csv  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import pickle  # noqa: E402
import selectors  # noqa: E402
import socket  # noqa: E402
import statistics as st  # noqa: E402
import struct  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
CTRL_PORT = int(os.environ.get("E8_PORT", "5640"))
DATA_PORT = CTRL_PORT + 1
REQ = struct.Struct("!IHI")     # numero de solicitud, escalon, indice en la lista de solicitudes
RESP = struct.Struct("!IbI")    # numero de solicitud, decision (1 permitir, 0 negar, -1 error), version
LEN = struct.Struct("!I")
now = time.time                 # reloj de pared: comparable entre nodos (NTP), solo para marcar momentos
mono = time.perf_counter        # para medir duraciones dentro de un mismo proceso


# ---------------------------------------------------------------- red
def my_address():
    """IP de la interfaz E8_IFACE (por ejemplo ib0, InfiniBand) si existe; si no, el nombre del nodo."""
    iface = os.environ.get("E8_IFACE", "ib0")
    try:
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", iface], capture_output=True, text=True,
                             timeout=5).stdout.split()
        if "inet" in out:
            return out[out.index("inet") + 1].split("/")[0], iface
    except Exception:  # noqa: BLE001
        pass
    return socket.gethostname(), "nombre del nodo"


def send_msg(sock, obj, lock=None):
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    if lock:
        with lock:
            sock.sendall(LEN.pack(len(data)) + data)
    else:
        sock.sendall(LEN.pack(len(data)) + data)


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("conexion cerrada")
        buf += chunk
    return bytes(buf)


def recv_msg(sock):
    (n,) = LEN.unpack(recv_exact(sock, 4))
    return pickle.loads(recv_exact(sock, n))


class MsgReader:
    """Lee mensajes completos de un socket sin bloquear (para la copia, que no puede esperar)."""

    def __init__(self, sock):
        self.sock, self.buf = sock, bytearray()

    def poll(self):
        try:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise ConnectionError("el principal cerro la conexion")
            self.buf += chunk
        except BlockingIOError:
            pass
        out = []
        while len(self.buf) >= 4:
            (n,) = LEN.unpack_from(self.buf)
            if len(self.buf) < 4 + n:
                break
            out.append(pickle.loads(bytes(self.buf[4:4 + n])))
            del self.buf[:4 + n]
        return out


def connect_primary(rdv, timeout=7200):
    f = Path(rdv) / "principal.addr"
    t_end = mono() + timeout
    while mono() < t_end:
        if f.exists():
            host = f.read_text().strip()
            try:
                s = socket.create_connection((host, CTRL_PORT), timeout=30)
                s.settimeout(None)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                return s
            except OSError:
                pass
        time.sleep(1)
    raise TimeoutError("no se encontro al principal")


def pct(a, q):
    a = np.asarray(a)
    return round(float(np.percentile(a, q)), 3) if a.size else ""


# ---------------------------------------------------------------- copia
def replica_apply(ev, msg):
    kind = msg[0]
    if kind == "sync":
        _, _, pid, pol, comm = msg
        if pol is None:
            if pid in ev.policies:
                ev.remove_policy(pid)
            return
        ev.community_assigner = lambda _p, c=comm: c  # la comunidad la decide el principal
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


def role_replica(args):
    sys.path.insert(0, str(HERE.parent / "E5"))
    for sub in ("E2", "E1"):
        sys.path.insert(0, str(HERE.parent / sub))
    from run_e5 import quiet
    from devac import CommunityPolicyEvaluator

    addr, via = my_address()
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    port = int(os.environ.get("E8_DATA_PORT", DATA_PORT))  # otro puerto solo para pruebas en una maquina
    lsock.bind(("0.0.0.0", port))
    lsock.listen(1024)
    lsock.setblocking(False)
    ctrl = connect_primary(args.rendezvous)
    send_msg(ctrl, ("hola", "copia", f"{socket.gethostname()}:{port}", (addr, port), via))
    ctrl.setblocking(False)
    reader = MsgReader(ctrl)
    sel = selectors.DefaultSelector()
    sel.register(lsock, selectors.EVENT_READ, "escucha")
    sel.register(ctrl, selectors.EVENT_READ, "control")

    ev, reqs = None, []
    state = {"version": 0, "phase": -1, "active": False}
    clients = {}        # socket -> bytearray pendiente
    errors = Counter()

    def ctrl_send(obj):
        ctrl.setblocking(True)
        send_msg(ctrl, obj)
        ctrl.setblocking(False)

    def handle_ctrl():
        nonlocal ev, reqs
        for msg in reader.poll():
            kind = msg[0]
            if kind == "snapshot":
                # la foto completa: reglas, sus grupos y la lista de solicitudes de esta corrida
                t0 = mono()
                _, pols, pbc, reqs = msg
                ev = CommunityPolicyEvaluator()
                with quiet():
                    ev.run(pols, pbc, {})
                for r in reqs[:200]:
                    ev.evaluate(r)
                ev._on_policy_change = []
                ev.community_assigner = None
                state.update(version=0, phase=-1, active=True)
                errors.clear()
                ctrl_send(("lista", round(mono() - t0, 3)))
            elif kind == "fase":
                state["phase"] = msg[1]
            elif kind in ("sync", "rebuild"):
                t0 = mono()
                try:
                    with quiet():
                        replica_apply(ev, msg)
                except Exception as e:  # noqa: BLE001
                    errors["copia:" + type(e).__name__] += 1
                if kind == "sync":
                    state["version"] = msg[1] + 1
                    ctrl_send(("acuse", msg[1], round((mono() - t0) * 1e3, 4)))
                else:
                    ctrl_send(("acuse_reacomodo", msg[2], round((mono() - t0) * 1e3, 3)))
            elif kind == "final":
                final = np.array([1 if ev.evaluate(r)["result"] == "allow" else 0 for r in reqs], np.int8)
                ctrl_send(("final", final, state["version"], dict(errors)))
                state["active"] = False
            elif kind == "salir":
                return False
        return True

    batch = 32
    while True:
        for key, _ in sel.select(timeout=0.05):
            if key.data == "escucha":
                try:
                    while True:
                        c, _ = lsock.accept()
                        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        c.setblocking(False)
                        clients[c] = bytearray()
                        sel.register(c, selectors.EVENT_READ, "cliente")
                except BlockingIOError:
                    pass
            elif key.data == "control":
                if not handle_ctrl():
                    return
            else:
                c = key.fileobj
                try:
                    chunk = c.recv(1 << 20)
                except BlockingIOError:
                    chunk = None
                except OSError:
                    chunk = b""
                if chunk == b"":
                    sel.unregister(c)
                    c.close()
                    clients.pop(c, None)
                elif chunk:
                    clients[c] += chunk
        # atender lo pendiente por turnos entre clientes; antes de cada solicitud, aplicar los cambios que
        # ya llegaron del principal (asi ninguna solicitud se evalua a la mitad de un cambio)
        pending = True
        while pending:
            pending = False
            for c, buf in list(clients.items()):
                k = min(len(buf) // REQ.size, batch)
                if not k:
                    continue
                pending = True
                out = bytearray()
                for j in range(k):
                    if not handle_ctrl():
                        return
                    seq, ph, idx = REQ.unpack_from(buf, j * REQ.size)
                    if ph != state["phase"] or ev is None or not state["active"]:
                        continue  # solicitud de un escalon que ya termino: se descarta sin evaluar
                    try:
                        d = 1 if ev.evaluate(reqs[idx])["result"] == "allow" else 0
                    except Exception as e:  # noqa: BLE001
                        errors["lector:" + type(e).__name__] += 1
                        d = -1
                    out += RESP.pack(seq, d, state["version"])
                del buf[:k * REQ.size]
                if out:
                    try:
                        c.setblocking(True)
                        c.sendall(out)
                        c.setblocking(False)
                    except OSError:
                        pass


# ---------------------------------------------------------------- cliente
def run_step(socks, phase, rate, duration, warm, pool_n, cid, grace):
    """Carga abierta: la solicitud i debe salir en t0 + i/rate, a la copia i mod K. La latencia se mide desde
    ese momento programado (si el cliente o la red se atrasan, el atraso cuenta)."""
    K = len(socks)
    n_tot = int(rate * duration)
    t_sched = np.full(n_tot, np.nan)
    t_recv = np.full(n_tot, np.nan)
    dec = np.full(n_tot, -2, np.int8)
    ver = np.full(n_tot, -1, np.int32)
    # cual solicitud se pide es al azar e independiente de a que copia va (la copia se elige por turnos); asi cada
    # copia recibe toda la variedad de solicitudes y no solo una parte (lo que la haria artificialmente mas rapida)
    idx = np.random.default_rng([cid, phase]).integers(0, pool_n, n_tot).astype(np.int32)
    outb = [bytearray() for _ in range(K)]
    inb = [bytearray() for _ in range(K)]
    sel = selectors.DefaultSelector()
    for k, s in enumerate(socks):
        sel.register(s, selectors.EVENT_READ, k)
    lag = []
    t0 = now() + 0.2
    i = 0
    answered = 0
    t_stop = t0 + duration + grace
    while True:
        t = now()
        while i < n_tot and t0 + i / rate <= t:
            ts = t0 + i / rate
            t_sched[i] = ts
            outb[i % K] += REQ.pack(i, phase, int(idx[i]))
            if i % 64 == 0:
                lag.append(t - ts)
            i += 1
        for k, s in enumerate(socks):
            if outb[k]:
                try:
                    sent = s.send(outb[k])
                    del outb[k][:sent]
                except BlockingIOError:
                    pass
        nxt = t0 + i / rate if i < n_tot else t + 0.01
        for key, _ in sel.select(timeout=max(0.0, min(0.002, nxt - now()))):
            k = key.data
            try:
                chunk = key.fileobj.recv(1 << 20)
            except BlockingIOError:
                continue
            if not chunk:
                raise ConnectionError("una copia cerro la conexion")
            tr = now()
            b = inb[k]
            b += chunk
            m = len(b) // RESP.size
            for j in range(m):
                seq, d, v = RESP.unpack_from(b, j * RESP.size)
                if seq < n_tot and np.isnan(t_recv[seq]):
                    t_recv[seq] = tr
                    dec[seq] = d
                    ver[seq] = v
                    answered += 1
            del b[:m * RESP.size]
        if i >= n_tot and (answered >= n_tot or now() >= t_stop):
            break
    meas = t_sched >= t0 + warm
    return {"t_sched": t_sched[meas], "lat_ms": ((t_recv - t_sched) * 1e3)[meas].astype(np.float32),
            "dec": dec[meas], "ver": ver[meas], "idx": idx[meas],
            "atraso_p99_ms": round(float(np.percentile(lag, 99)) * 1e3, 3) if lag else 0.0,
            "enviadas": int(i)}


def role_client(args):
    ctrl = connect_primary(args.rendezvous)
    send_msg(ctrl, ("hola", "cliente", socket.gethostname(), None, None))
    socks, cur = [], None
    while True:
        msg = recv_msg(ctrl)
        if msg[0] == "salir":
            return
        if msg[0] == "escalon":
            cfg = msg[1]
            if cur != cfg["copias_addr"]:
                for s in socks:
                    s.close()
                socks = []
                for host, port in cfg["copias_addr"]:
                    s = socket.create_connection((host, port), timeout=30)
                    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    s.setblocking(False)
                    socks.append(s)
                cur = cfg["copias_addr"]
            try:
                res = run_step(socks, cfg["fase"], cfg["ritmo_cliente"], cfg["duracion"], cfg["calentamiento"],
                               cfg["pool"], cfg["cid"], cfg["espera_final"])
            except Exception as e:  # noqa: BLE001
                res = {"fallo": repr(e)}
                for s in socks:
                    s.close()
                socks, cur = [], None
            send_msg(ctrl, ("resultado", res))


# ---------------------------------------------------------------- principal
class Peer:
    def __init__(self, sock, kind, host, addr, via):
        self.sock, self.kind, self.host, self.addr, self.via = sock, kind, host, addr, via
        self.lock = threading.Lock()


def accept_peers(lsock, n_rep, n_cli, log, timeout):
    reps, clis = [], []
    lsock.settimeout(timeout)
    while len(reps) < n_rep or len(clis) < n_cli:
        s, _ = lsock.accept()
        s.settimeout(None)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        _, kind, host, addr, via = recv_msg(s)
        (reps if kind == "copia" else clis).append(Peer(s, kind, host, addr, via))
        log(f"conectado {kind} {host} ({len(reps)}/{n_rep} copias, {len(clis)}/{n_cli} clientes)")
    reps.sort(key=lambda p: p.host)
    return reps, clis


def run_load(clis, addrs, phase, total_rate, duration, warm, pool_n, grace, on_start=None):
    """Manda a todos los clientes el mismo escalon y junta sus resultados."""
    C = len(clis)
    for c_id, c in enumerate(clis):
        send_msg(c.sock, ("escalon", {"copias_addr": addrs, "fase": phase, "ritmo_cliente": total_rate / C,
                                      "duracion": duration, "calentamiento": warm, "pool": pool_n,
                                      "cid": c_id, "espera_final": grace}))
    extra = on_start() if on_start else None
    res = [recv_msg(c.sock)[1] for c in clis]
    return res, extra


def merge(res):
    fails = [r["fallo"] for r in res if "fallo" in r]
    if fails:
        return None, fails
    cat = lambda k: np.concatenate([r[k] for r in res])  # noqa: E731
    return {"t_sched": cat("t_sched"), "lat_ms": cat("lat_ms"), "dec": cat("dec"), "ver": cat("ver"),
            "idx": cat("idx"), "atraso_p99_ms": max(r["atraso_p99_ms"] for r in res)}, []


def judge(m, D, duration_meas):
    lat, dec, ver, idx = m["lat_ms"], m["dec"], m["ver"], m["idx"]
    ans = ~np.isnan(lat)
    ok = ans & (dec >= 0)
    v = np.clip(ver, 0, D.shape[0] - 1)
    bad = ok & (D[v, idx] != dec)
    n = len(lat)
    return {
        "solicitudes": n, "respondidas_por_s": round(int(ans.sum()) / duration_meas, 1),
        "sin_respuesta_pct": round(100.0 * (n - int(ans.sum())) / max(n, 1), 3),
        "errores": int((ans & (dec < 0)).sum()),
        "p50_ms": pct(lat[ans], 50), "p95_ms": pct(lat[ans], 95), "p99_ms": pct(lat[ans], 99),
        "inconsistentes": int(bad.sum()), "atraso_cliente_p99_ms": m["atraso_p99_ms"],
    }


def role_primary(args):
    for sub in ("E6", "E5", "E2", "E1"):
        sys.path.insert(0, str(HERE.parent / sub))
    import run_e5 as e5
    from run_e5 import quiet, run_e1
    from run_e6 import new_engine
    from gen_w4 import DEFAULTS as W4_DEFAULTS, generate_w4, variant_label
    from devac import Policy

    sys.setswitchinterval(0.0005)  # el hilo que recibe los acuses no espera mucho su turno
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rdv = Path(args.rendezvous)
    rdv.mkdir(parents=True, exist_ok=True)
    w4 = {**W4_DEFAULTS, "mode": "uniforme", "p_mix": W4_DEFAULTS["p_mix"]}
    carga = f"W4-{variant_label(w4)}"
    log = lambda m: print(f"[E8 n={args.n}] {m}", flush=True)  # noqa: E731

    addr, via = my_address()
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    lsock.bind(("0.0.0.0", CTRL_PORT))
    lsock.listen(1024)
    (rdv / "principal.addr").write_text(addr)
    log(f"principal en {socket.gethostname()} ({addr}, red: {via}); esperando {max(args.replicas)} copias "
        f"y {args.clients} clientes")
    reps_all, clis = accept_peers(lsock, max(args.replicas), args.clients, log, timeout=args.wait_peers)
    log("copias: " + ", ".join(f"{p.host}={p.addr} ({p.via})" for p in reps_all))
    log(f"clientes: {dict(Counter(c.host for c in clis))}")

    base = [Policy.model_validate(p) for p in generate_w4(args.n, seed=args.data_seed, **w4)]
    n_ops = int(args.wrate * args.window)
    steps_rows, change_rows, details = [], [], []
    phase_id = 0

    for rep in range(args.reps):
        ops = e5.make_ops(args.op, base, rep, n_ops, w4)
        pool = e5.request_pool(base, ops, rep, args.pool)
        reqs = [run_e1.to_access_request(v) for v in pool]
        D = e5.reference_states(base, ops, pool)
        for K in args.replicas:
            reps = reps_all[:K]
            addrs = [p.addr for p in reps]
            t_build = mono()
            ev, mon = new_engine(base, args.theta, rep + 1, math.inf)
            t_build = mono() - t_build
            snap = ("snapshot", list(ev.policies.values()), dict(ev.policies_by_community), reqs)
            t0 = mono()
            for p in reps:
                send_msg(p.sock, snap)
            ready = [recv_msg(p.sock) for p in reps]
            log(f"rep {rep + 1} K={K}: motor armado en {t_build:.1f} s; foto enviada y copias listas en "
                f"{mono() - t0:.1f} s (armado en cada copia: {[r[1] for r in ready]} s)")

            # ---------------- fase A: escalones sin cambios
            # primero sube multiplicando por --step-factor hasta saturar; despues afina entre el ultimo escalon que
            # aguanto y el que se saturo, partiendo la diferencia (en proporcion) --refine veces
            rate = args.rate0 * K
            base_p95, best, sat_rate, ok_rate = None, None, None, None
            s, refining, n_ref = 0, False, 0
            while s < args.max_steps + args.refine:
                phase_id += 1
                for p in reps:
                    send_msg(p.sock, ("fase", phase_id))
                res, _ = run_load(clis, addrs, phase_id, rate, args.step, args.step_warmup, len(reqs), args.grace)
                m, fails = merge(res)
                if fails:
                    log(f"rep {rep + 1} K={K} escalon {s + 1}: FALLO {fails[:2]}")
                    break
                j = judge(m, D[:1], args.step - args.step_warmup)
                if base_p95 is None:
                    base_p95 = j["p95_ms"] or 1e-3
                razon = round(j["p95_ms"] / base_p95, 2) if j["p95_ms"] != "" else math.inf
                saturado = (razon > args.sat_factor) or (j["sin_respuesta_pct"] + 100.0 * j["errores"] /
                                                         max(j["solicitudes"], 1) > 1.0)
                cliente_lento = j["atraso_cliente_p99_ms"] > args.client_lag_ms
                row = {"carga": carga, "n": args.n, "rep": rep, "copias": K, "escalon": s + 1,
                       "tipo": "afinar" if refining else "subir",
                       "ritmo_pedido_por_s": round(rate, 1), **j, "razon_p95_vs_primero": razon,
                       "saturado": saturado, "cliente_atrasado": cliente_lento}
                steps_rows.append(row)
                log(f"rep {rep + 1} K={K} escalon {s + 1}{' (afinar)' if refining else ''}: pedido {rate:.0f}/s, "
                    f"respondidas {j['respondidas_por_s']}/s, p95 {j['p95_ms']} ms (x{razon}), sin respuesta "
                    f"{j['sin_respuesta_pct']}%, inconsistentes {j['inconsistentes']}, atraso cliente p99 "
                    f"{j['atraso_cliente_p99_ms']} ms{' SATURADO' if saturado else ''}"
                    f"{' (OJO: clientes atrasados)' if cliente_lento else ''}")
                s += 1
                if saturado:
                    sat_rate = rate
                    time.sleep(args.grace)  # que la copia descarte la fila del escalon saturado
                else:
                    best, ok_rate = max(best or 0, j["respondidas_por_s"]), max(ok_rate or 0, rate)
                if not refining:
                    if not saturado:
                        rate *= args.step_factor
                        if s >= args.max_steps:
                            break
                        continue
                    if ok_rate is None or args.refine == 0:
                        break
                    refining = True
                else:
                    n_ref += 1
                    if n_ref >= args.refine:
                        break
                rate = math.sqrt(ok_rate * sat_rate)
            capacity = best
            log(f"rep {rep + 1} K={K}: capacidad {capacity}/s (aguanto {ok_rate and round(ok_rate)}/s, "
                f"se saturo en {sat_rate and round(sat_rate)}/s)")

            # ---------------- fase B: carga fija con cambios y un reacomodo
            phase_id += 1
            for p in reps:
                send_msg(p.sock, ("fase", phase_id))
            b_rate = max(args.rate0, (capacity or args.rate0 * K) * args.b_fraction)
            acks = {}          # secuencia -> lista de (copia, momento del acuse)
            rb_acks = []
            apply_ms = []

            def ack_reader(p):
                while True:
                    msg = recv_msg(p.sock)
                    t = mono()
                    if msg[0] == "acuse":
                        acks.setdefault(msg[1], []).append(t)
                        apply_ms.append(msg[2])
                    elif msg[0] == "acuse_reacomodo":
                        rb_acks.append((p.host, t, msg[2]))
                    elif msg[0] == "final":
                        p.final = msg
                        return

            pub, rebuild = {}, {}
            w_err = Counter()

            def writer():
                threads = [threading.Thread(target=ack_reader, args=(p,), daemon=True) for p in reps]
                for th in threads:
                    th.start()
                time.sleep(0.2 + args.b_warmup)
                rebuild_at = int(len(ops) * args.rebuild_at)
                base_t, j0 = mono(), 0
                with quiet():
                    for k, op in enumerate(ops):
                        target = base_t + (k - j0) / args.wrate
                        while mono() < target:
                            time.sleep(max(0.0, target - mono() - 0.0002))
                        try:
                            e5.apply_op(ev, op)
                        except Exception as ex:  # noqa: BLE001
                            w_err[type(ex).__name__] += 1
                        pid = (op[2] or op[1]).policy_id
                        msg = ("sync", k, pid, ev.policies.get(pid), ev.policy_community.get(pid))
                        pub[k] = mono()
                        for p in reps:
                            send_msg(p.sock, msg, p.lock)
                        if k + 1 == rebuild_at:
                            rebuild["inicio_pared"] = now()
                            r0 = mono()
                            try:
                                mon.rebuild_now()
                            except Exception as ex:  # noqa: BLE001
                                w_err["reacomodo:" + type(ex).__name__] += 1
                            rebuild["principal_s"] = mono() - r0
                            rebuild["enviado"] = mono()
                            for p in reps:
                                send_msg(p.sock, ("rebuild", dict(ev.policies_by_community), k), p.lock)
                            base_t, j0 = mono(), k + 1
                return threads

            box = {}
            wt = threading.Thread(target=lambda: box.update(threads=writer()), daemon=True)
            # los clientes cubren los cambios y el reacomodo completo (su duracion ~ la del armado inicial)
            b_dur = args.b_warmup + args.window + 1.5 * t_build + 5
            res, _ = run_load(clis, addrs, phase_id, b_rate, b_dur, args.b_warmup, len(reqs), args.grace,
                              on_start=wt.start)
            wt.join()
            for p in reps:
                send_msg(p.sock, ("final",), p.lock)
            for th in box.get("threads", []):
                th.join()
            m, fails = merge(res)
            if fails:
                log(f"rep {rep + 1} K={K} fase B: FALLO {fails[:2]}")
                continue
            n_pub = len(pub)
            j = judge(m, D, b_dur - args.b_warmup)
            ventana = [(max(acks[k]) - pub[k]) * 1e3 for k in pub if len(acks.get(k, [])) == K]
            unico = [(t - pub[k]) * 1e3 for k in pub for t in acks.get(k, [])]
            perdidos = sum(1 for k in pub if len(acks.get(k, [])) < K)
            t_rb0 = rebuild.get("inicio_pared")
            rb_end_all = max((t for _, t, _ in rb_acks), default=None)
            rb_total = (rb_end_all - (rebuild["enviado"] - rebuild["principal_s"])) if rb_end_all else None
            lat, ts = m["lat_ms"], m["t_sched"]
            ans = ~np.isnan(lat)
            if t_rb0 and rb_total:
                during = ans & (ts >= t_rb0) & (ts < t_rb0 + rb_total)
                before = ans & (ts < t_rb0)
                after = ans & (ts >= t_rb0 + rb_total)
            else:
                during = before = after = np.zeros_like(ans)
            finals = [p.final for p in reps]
            final_ok = [100.0 * float(np.mean(f[1] == D[min(f[2], D.shape[0] - 1)])) for f in finals]
            desfasadas = sum(1 for f in finals if f[2] != n_pub)
            r_err = Counter()
            for f in finals:
                r_err.update(f[3])
            row = {"carga": carga, "n": args.n, "rep": rep, "copias": K, "capacidad_fase_a_por_s": capacity,
                   "ritmo_aguanto_por_s": ok_rate and round(ok_rate, 1), "ritmo_saturacion_por_s": sat_rate and round(sat_rate, 1), "ritmo_fase_b_por_s": round(b_rate, 1), **j,
                   "p95_antes_reacomodo_ms": pct(lat[before], 95), "p95_durante_reacomodo_ms": pct(lat[during], 95),
                   "p95_despues_reacomodo_ms": pct(lat[after], 95),
                   "cambios_enviados": n_pub, "cambios_sin_acuse_de_todas": perdidos,
                   "ventana_p50_ms": pct(ventana, 50), "ventana_p95_ms": pct(ventana, 95),
                   "ventana_max_ms": round(max(ventana), 3) if ventana else "",
                   "acuse_una_copia_p50_ms": pct(unico, 50),
                   "aplicar_cambio_copia_p50_ms": pct(apply_ms, 50),
                   "reacomodo_principal_s": round(rebuild.get("principal_s", float("nan")), 3),
                   "reacomodo_hasta_ultima_copia_s": round(rb_total, 3) if rb_total else "",
                   "reacomodo_aplicar_copia_s": round(st.median([x[2] for x in rb_acks]) / 1e3, 3) if rb_acks else "",
                   "final_ok_min_pct": round(min(final_ok), 3), "copias_desfasadas": desfasadas,
                   "errores_copias": sum(r_err.values()), "errores_principal": sum(w_err.values())}
            change_rows.append(row)
            details.append({"rep": rep, "copias": K, "errores_copias": dict(r_err), "errores_principal": dict(w_err)})
            log(f"rep {rep + 1} K={K} fase B: {j['respondidas_por_s']}/s a {b_rate:.0f}/s pedidas; p95 "
                f"{row['p95_antes_reacomodo_ms']} -> {row['p95_durante_reacomodo_ms']} (reacomodo) -> "
                f"{row['p95_despues_reacomodo_ms']} ms; ventana p95 {row['ventana_p95_ms']} ms, max "
                f"{row['ventana_max_ms']} ms; aplicar un cambio en la copia {row['aplicar_cambio_copia_p50_ms']} ms; reacomodo {row['reacomodo_principal_s']} s en el principal, "
                f"{row['reacomodo_hasta_ultima_copia_s']} s hasta la ultima copia; inconsistentes "
                f"{j['inconsistentes']}; final ok {row['final_ok_min_pct']}%; errores "
                f"{row['errores_copias']}/{row['errores_principal']}")

    for p in reps_all + clis:
        try:
            send_msg(p.sock, ("salir",))
        except OSError:
            pass
    tag = f"e8_{carga}_n{args.n}"
    for name, rows in (("escalones", steps_rows), ("cambios", change_rows)):
        if rows:
            with open(out_dir / f"{tag}_{name}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)), restval="")
                w.writeheader()
                w.writerows(rows)
    sys.path.insert(0, str(HERE.parent / "E1"))
    meta = {
        "experimento": "E8", "carga": carga, "n_eventos": args.n, "repeticiones": args.reps,
        "copias": args.replicas, "clientes": args.clients, "operacion": args.op,
        "fase_a": {"ritmo_inicial_por_copia": args.rate0, "factor": args.step_factor, "escalon_s": args.step,
                   "calentamiento_s": args.step_warmup, "afinar": args.refine, "saturacion": f"p95 > {args.sat_factor}x primer escalon "
                   "o > 1% sin respuesta/error", "max_escalones": args.max_steps},
        "fase_b": {"fraccion_capacidad": args.b_fraction, "cambios_por_s": args.wrate, "ventana_s": args.window,
                   "reacomodo_en_fraccion": args.rebuild_at},
        "principal": socket.gethostname(), "red": via,
        "copias_nodos": [p.host for p in reps_all], "clientes_nodos": dict(Counter(c.host for c in clis)),
        "detalle": details, "fecha_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cpu_principal": run_e1.cpu_model(), "python": sys.version.split()[0],
        "commit_demo": os.environ.get("DEVAC_COMMIT", "desconocido"), "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
    }
    with open(out_dir / f"{tag}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False, default=str)
    log("listo")


def main():
    ap = argparse.ArgumentParser(description="E8: DEvAC repartido en varias computadoras")
    ap.add_argument("papel", choices=["principal", "copia", "cliente"])
    ap.add_argument("--rendezvous", required=True, help="carpeta compartida donde el principal deja su direccion")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--replicas", nargs="+", type=int, default=[1, 2, 4, 8])
    ap.add_argument("--clients", type=int, default=32, help="procesos cliente en total")
    ap.add_argument("--rate0", type=float, default=200.0, help="ritmo del primer escalon, por copia (solicitudes/s)")
    ap.add_argument("--step-factor", type=float, default=1.5, help="cada escalon pide esta vez mas que el anterior")
    ap.add_argument("--step", type=float, default=60.0, help="segundos de cada escalon")
    ap.add_argument("--step-warmup", type=float, default=10.0, help="segundos iniciales de cada escalon que no se miden")
    ap.add_argument("--max-steps", type=int, default=14)
    ap.add_argument("--refine", type=int, default=3, help="escalones extra para afinar el punto de saturacion")
    ap.add_argument("--sat-factor", type=float, default=10.0)
    ap.add_argument("--grace", type=float, default=3.0, help="segundos de espera de respuestas al final de un escalon")
    ap.add_argument("--client-lag-ms", type=float, default=50.0)
    ap.add_argument("--b-fraction", type=float, default=0.5, help="carga de la fase B como fraccion de la capacidad")
    ap.add_argument("--b-warmup", type=float, default=10.0)
    ap.add_argument("--window", type=float, default=60.0, help="segundos de cambios en la fase B")
    ap.add_argument("--wrate", type=float, default=20.0, help="cambios de reglas por segundo en la fase B")
    ap.add_argument("--rebuild-at", type=float, default=0.5, help="el reacomodo ocurre tras esta fraccion de los cambios")
    ap.add_argument("--op", choices=["toggle", "update", "add", "remove"], default="toggle")
    ap.add_argument("--theta", type=float, default=0.3)
    ap.add_argument("--pool", type=int, default=400)
    ap.add_argument("--data-seed", type=int, default=1)
    ap.add_argument("--wait-peers", type=float, default=7200.0,
                    help="segundos que el principal espera a que se conecten todas las copias y clientes")
    args = ap.parse_args()
    {"principal": role_primary, "copia": role_replica, "cliente": role_client}[args.papel](args)


if __name__ == "__main__":
    main()
