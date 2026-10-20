"""
Demostracion en vivo del compilador DPL (DEvAC Policy Language).

Corre con:  python3 experiments/demo_dpl.py

  1. Se escribe una politica en DPL (texto legible por una persona).
  2. Se compila -- el compilador la convierte en objetos Policy/Event.
  3. Esos objetos se usan: se corren por el pipeline completo
     (GraphBuilder -> CommunityDetector -> CommunityPolicyEvaluator) y se
     evaluan solicitudes de acceso reales contra ellos.
  4. Un DPL con un error de sintaxis a proposito, para mostrar que el
     compilador reporta errores con la linea exacta -- no es solo un
     parser silencioso.
"""

from devac import (
    AccessRequest, AttributeComponent, CommunityDetector,
    CommunityPolicyEvaluator, DPLLoader, DPLSyntaxError, GraphBuilder,
)

DPL_TEXT = """
POLICY P-DOC-01 EFFECT ALLOW RECONFIGURE_ON (turno_terminado) {
    EVENT E1 WHERE (
        SUBJECT  = medico,
        RESOURCE = expediente_clinico,
        LOCATION = consultorio,
        TIME     RANGE (08:00, 20:00),
        ACTION   = leer
    );
    EVENT E2 WHERE (
        SUBJECT  IN (medico, enfermera),
        RESOURCE = expediente_clinico,
        LOCATION = urgencias,
        TIME     = *,
        ACTION   = leer
    );
}
"""

DPL_TEXT_CON_ERROR = """
POLICY P-MAL EFFECT ALLOW {
    EVENT E1 WHERE (
        SUBJECT  = medico,
        RESOURCE  expediente_clinico,
        LOCATION = consultorio,
        TIME     = *,
        ACTION   = leer
    );
}
"""


def req(rol, recurso, ubicacion, accion, hora):
    # OJO: las politicas compiladas desde DPL guardan `attribute` en ingles
    # (vocabulario propio de DPL: subject/asset/space/time/action) -- hay
    # que usar las mismas etiquetas aqui, porque EventMatcher.match_component
    # exige que el texto de `attribute` coincida exacto antes de comparar
    # valores (ver nota en la respuesta: es una inconsistencia real entre
    # esto y el comentario del propio dpl.py, que dice que ese campo deberia
    # ser una "etiqueta libre").
    return AccessRequest(
        subject=AttributeComponent(attribute="subject", value=rol),
        asset=AttributeComponent(attribute="asset", value=recurso),
        space=AttributeComponent(attribute="space", value=ubicacion),
        time=AttributeComponent(attribute="time", value=hora),
        action=AttributeComponent(attribute="action", value=accion),
    )


def main():
    print("=" * 70)
    print("1) TEXTO DPL (lo que escribiria una persona)")
    print("=" * 70)
    print(DPL_TEXT)

    print("=" * 70)
    print("2) COMPILANDO...")
    print("=" * 70)
    dpl_path = "experiments/demo_policy.dpl"
    with open(dpl_path, "w", encoding="utf-8") as f:
        f.write(DPL_TEXT)

    policies = DPLLoader.from_file(dpl_path)
    print(f"Compilado OK -- {len(policies)} politica(s) real(es):\n")
    for p in policies:
        print(f"  Policy(policy_id={p.policy_id!r}, effect={p.effect!r}, "
              f"reconfigure_on={p.reconfigure_on}, eventos={len(p.events)})")
        for e in p.events:
            print(f"    - Event {e.event_id}: subject={e.subject.value!r} "
                  f"asset={e.asset.value!r} space={e.space.value!r} "
                  f"time={e.time.value!r} action={e.action.value!r}")
    print()

    print("=" * 70)
    print("3) USANDO ESAS POLITICAS DE VERDAD (pipeline real, no simulado)")
    print("=" * 70)
    gb = GraphBuilder()
    graph = gb.build_event_graph(policies)
    cd = CommunityDetector()
    _, e2c = cd.detect_communities(graph)
    e2p = {e.event_id: p.policy_id for p in policies for e in p.events}
    pbc = cd.map_policies_to_communities(e2p, e2c)

    ce = CommunityPolicyEvaluator()
    ce.run(policies, pbc)

    casos = [
        ("medico en consultorio, 10:00 (deberia dar ALLOW via E1)",
         req("medico", "expediente_clinico", "consultorio", "leer", "10:00")),
        ("enfermera en urgencias, cualquier hora (deberia dar ALLOW via E2)",
         req("enfermera", "expediente_clinico", "urgencias", "leer", "23:30")),
        ("medico en consultorio, 23:00 -- fuera del rango 08:00-20:00 (deberia dar DENY)",
         req("medico", "expediente_clinico", "consultorio", "leer", "23:00")),
    ]

    for descripcion, r in casos:
        resultado = ce.evaluate(r)
        print(f"  {descripcion}")
        print(f"    -> result={resultado['result']!r}  policy_id={resultado['policy_id']!r} "
              f"event_id={resultado['event_id']!r}  steps={resultado['steps']}")
    print()

    print("=" * 70)
    print("4) EL COMPILADOR SI REPORTA ERRORES DE SINTAXIS (con la linea exacta)")
    print("=" * 70)
    print("DPL con un error a proposito (falta '=' antes de RESOURCE):\n")
    print(DPL_TEXT_CON_ERROR)
    try:
        DPLLoader.from_file(dpl_path)  # placeholder, se sobreescribe abajo
    except Exception:
        pass
    with open(dpl_path, "w", encoding="utf-8") as f:
        f.write(DPL_TEXT_CON_ERROR)
    try:
        DPLLoader.from_file(dpl_path)
        print("(no debería llegar aquí)")
    except DPLSyntaxError as exc:
        print(f"DPLSyntaxError capturado correctamente: {exc}")


if __name__ == "__main__":
    main()
