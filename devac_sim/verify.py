import csv
from collections import Counter
from typing import Dict, List, Optional

from devac_sim.oracle import Oracle
from devac_sim.trace import ACCESS


def _int(value) -> Optional[int]:
    return int(value) if value not in (None, "", "None") else None


def _bool(value) -> Optional[bool]:
    if value in (None, "", "None"):
        return None
    return value if isinstance(value, bool) else value == "True"


def read_results(path: str) -> Dict[int, dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return {int(row["index"]): row for row in csv.DictReader(f)}


# compara lo que respondio la API contra el oraculo en la version que la API reporto
def verify(records: List[dict], results: Dict[int, dict], oracle: Oracle) -> dict:
    # version de la API -> version del oraculo; se arma con las escrituras que si se aplicaron
    api_to_oracle: Dict[int, int] = {}
    trace_version: Dict[int, int] = {}  # version del oraculo segun la posicion en la traza
    control_failed = 0
    for i, record in enumerate(records):
        if record["kind"] == ACCESS:
            trace_version[i] = oracle.version
            continue
        version = oracle.apply(record)
        row = results.get(i)
        api_version = _int(row.get("policy_version")) if row else None
        if row is None or int(row["status"]) >= 400 or api_version is None:
            control_failed += 1
            continue
        api_to_oracle[api_version] = version

    access_rows = [(i, results[i]) for i in trace_version if i in results and int(results[i]["status"]) == 200]
    if api_to_oracle:
        api_to_oracle.setdefault(min(api_to_oracle) - 1, 0)  # estado antes de la primera escritura
    else:
        # sin escrituras: todas deben traer la misma version
        for api_version in {_int(r["policy_version"]) for _, r in access_rows}:
            api_to_oracle[api_version] = 0

    counts = Counter()
    mismatches = []
    for i, row in access_rows:
        api_version = _int(row["policy_version"])
        version = api_to_oracle.get(api_version)
        if version is None:
            counts["unmapped"] += 1
            continue
        request = records[i]["request"]
        expected, _ = oracle.decide(request, version)

        if _bool(row.get("consistent")) is False:
            # escritura a medias: vale el estado de antes o el de despues
            counts["in_transition"] += 1
            after = api_to_oracle.get(api_version + 1)
            allowed = {expected} | ({oracle.decide(request, after)[0]} if after is not None else set())
            if row["decision"] not in allowed:
                counts["incorrect_in_transition"] += 1
                mismatches.append({"index": i, "api_version": api_version, "oracle_version": version,
                                   "decision": row["decision"], "expected": "|".join(sorted(allowed)), **request})
            continue

        counts["checked"] += 1
        if row["decision"] != expected:
            counts["incorrect"] += 1
            mismatches.append({"index": i, "api_version": api_version, "oracle_version": version,
                               "decision": row["decision"], "expected": expected, **request})
        if version < trace_version[i]:
            counts["behind_trace"] += 1  # la escritura todavia no llegaba
        elif version > trace_version[i]:
            counts["ahead_of_trace"] += 1

    return {
        "access_in_trace": len(trace_version),
        "access_answered": len(access_rows),
        "checked": counts["checked"],
        "incorrect": counts["incorrect"],
        "in_transition": counts["in_transition"],
        "incorrect_in_transition": counts["incorrect_in_transition"],
        "unmapped_version": counts["unmapped"],
        "behind_trace": counts["behind_trace"],
        "ahead_of_trace": counts["ahead_of_trace"],
        "controls_failed": control_failed,
        "mismatches": mismatches,
    }
