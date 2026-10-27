import asyncio
import threading

from devac.models import Policy
from tests.api.conftest import BASE_POLICIES, build_engine, event, policy


def _nueva(i):
    return Policy.model_validate(policy(f"P_N{i}", events=[event(f"E_N{i}", subject="enfermera", space=f"piso{i}")]))


def test_reconstruccion_por_umbral_hace_fila_en_write_lock(capsys):
    async def run():
        engine = build_engine(BASE_POLICIES, stress_threshold=1e-9)  # cualquier cambio cruza el umbral
        seen = []  # (lock tomado, hilo) en cada reconstruccion
        original = engine.monitor.rebuild_now

        def spy():
            seen.append((engine.write_lock.locked(), threading.current_thread().name))
            return original()

        engine.monitor.rebuild_now = spy
        await engine.add_policies([_nueva(i) for i in range(3)])  # una escritura, tres cruces del umbral
        await engine.add_policies([_nueva(3)])
        await engine.aclose()
        return engine, seen

    engine, seen = asyncio.run(run())
    assert seen and all(locked for locked, _ in seen)
    # corrio en el pool de asyncio.to_thread, no en el hilo daemon del monitor
    assert all(name != "apex-graph-rebuild" for _, name in seen)
    # una por escritura: mientras hay una pendiente no se encola otra
    assert len(seen) == 2
    assert engine.monitor.get_stats()["rebuilds"] == 1 + len(seen)  # la inicial mas las de umbral
    assert engine.evaluator.validate_integrity() == []
    capsys.readouterr()
