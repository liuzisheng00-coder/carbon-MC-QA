"""Replay previously compiled E4e programs against a successor canonical release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from dm2c_canonical_v2_reader import load_canonical_v2_context
from dm2c_carbonql import CarbonQLProgram
from dm2c_carbonql_executor import CarbonQLExecutor


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--allow-synthetic", action="store_true")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.cases.read_text(encoding="utf-8").splitlines() if line]
    unique_rows: list[dict[str, object]] = []
    seen_programs: set[str] = set()
    for row in rows:
        program = row.get("program")
        if not isinstance(program, dict):
            continue
        key = json.dumps(program, sort_keys=True, separators=(",", ":"))
        if key not in seen_programs:
            seen_programs.add(key)
            unique_rows.append(row)
    context = load_canonical_v2_context(args.release, allow_synthetic=args.allow_synthetic)
    executor = CarbonQLExecutor.from_context(context)
    def replay(row: dict[str, object]) -> dict[str, object] | None:
        program = row.get("program")
        if not isinstance(program, dict):
            return None
        try:
            observed = executor.execute(CarbonQLProgram.from_dict(program))
            previous_status = str(row.get("query_status"))
            previous_total = row.get("observed_total_kgCO2e")
            current_total = observed.summary.get("total_kgCO2e")
            status_changed = observed.status != previous_status
            total_changed = not (
                isinstance(previous_total, (int, float))
                and isinstance(current_total, (int, float))
                and math.isclose(float(previous_total), float(current_total), rel_tol=0.0, abs_tol=1e-9)
            )
            if status_changed or total_changed:
                return {"case_id": row.get("case_id"), "repeat": row.get("repeat"), "variant": row.get("variant"), "status_before": previous_status, "status_after": observed.status, "total_before": previous_total, "total_after": current_total}
        except Exception as exc:  # report every incompatible program instead of masking it
            return {"case_id": row.get("case_id"), "repeat": row.get("repeat"), "variant": row.get("variant"), "error": str(exc)}
        return None

    represented = sum(isinstance(row.get("program"), dict) for row in rows)
    checked = len(unique_rows)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        changes = [change for change in pool.map(replay, unique_rows) if change is not None]
    changed_status = sum("status_before" in row and row["status_before"] != row["status_after"] for row in changes)
    changed_total = sum("total_before" in row and row["total_before"] != row["total_after"] for row in changes)
    failed = sum("error" in row for row in changes)
    payload = {"source_cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest().upper(), "release": str(args.release), "represented_program_count": represented, "checked_unique_program_count": checked, "changed_status_count": changed_status, "changed_total_count": changed_total, "failure_count": failed, "changes": changes}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: payload[key] for key in payload if key != "changes"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
