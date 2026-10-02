#!/usr/bin/env python3
"""Build typea1 E4 human-recheck package, Section-7 cross-check, and candidate summary."""

from __future__ import annotations

import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    rebuild = Path(
        "outputs/research_experiments/e4_benchmark/typea1_en_rebuild_20260730"
    )
    recheck = Path(
        "outputs/research_experiments/e4_benchmark/typea1_en_recheck_20260730"
    )
    recheck.mkdir(parents=True, exist_ok=True)

    v9 = Path(
        "outputs/research_experiments/e4_benchmark/"
        "frozen_20260712_145321_v9_human_pass/"
        "e4_balanced_benchmark_150_human_reviewed_v9.jsonl"
    )
    cand = rebuild / "e4_balanced_benchmark_150_m2_aligned_candidate_v8.jsonl"
    mig = json.loads((rebuild / "v8_migration_summary.json").read_text(encoding="utf-8"))
    review = json.loads(
        (rebuild / "v8_targeted_review_data.json").read_text(encoding="utf-8")
    )
    remap = json.loads((rebuild / "selection_remap.json").read_text(encoding="utf-8"))
    gate = json.loads(
        (rebuild / "e4_typea1_en_replay_gate_report.json").read_text(encoding="utf-8")
    )
    facts = json.loads(
        Path(
            "outputs/research_experiments/case_study_facts/"
            "m2_typea1_full_en_layerdedup_20260731/case_study_facts.json"
        ).read_text(encoding="utf-8")
    )
    align = json.loads(
        Path(
            "outputs/research_experiments/m2_typea1_full_en_layerdedup_20260731/"
            "m2_alignment_report.json"
        ).read_text(encoding="utf-8")
    )

    old_by = {c["case_id"]: c for c in read_jsonl(v9)}
    new_by = {c["case_id"]: c for c in read_jsonl(cand)}

    flagged: set[str] = set()
    if isinstance(review, dict):
        for key in ("cases", "flagged_cases", "targeted_cases", "items"):
            rows = review.get(key)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("case_id"):
                        flagged.add(str(row["case_id"]))
        if not flagged and isinstance(review.get("case_ids"), list):
            flagged.update(str(x) for x in review["case_ids"])
    if not flagged:
        for cid, nc in new_by.items():
            oc = old_by[cid]
            if oc.get("selected_component_ids") != nc.get(
                "selected_component_ids"
            ) or oc.get("question_template") != nc.get("question_template"):
                flagged.add(cid)

    remap_by = {r["case_id"]: r for r in remap.get("case_remaps", [])}
    rows: list[dict] = []
    for cid in sorted(flagged):
        oc, nc = old_by[cid], new_by[cid]
        oe, ne = oc.get("expected") or {}, nc.get("expected") or {}
        reason = (remap_by.get(cid) or {}).get("reason") or (
            "Question template/target regenerated on typea1_en; "
            "frozen-v9 question text preserved."
        )
        rows.append(
            {
                "case_id": cid,
                "cell": f"{ne.get('perspective')}/{ne.get('operation')}",
                "question": nc.get("question"),
                "old_status": oe.get("status"),
                "new_status": ne.get("status"),
                "old_numeric": oe.get("summary") or {},
                "new_numeric": ne.get("summary") or {},
                "old_targets": oc.get("selected_component_ids") or [],
                "new_targets": nc.get("selected_component_ids") or [],
                "reason": reason,
            }
        )

    pkg = {
        "release": "m2_typea1_full_en_layerdedup_20260731",
        "candidate": str(cand),
        "oracle_mismatch_count": gate.get("oracle_mismatch_count"),
        "migration_flagged_cases": mig.get("targeted_human_recheck_cases"),
        "selection_remapped_cases": mig.get("selected_target_changed_cases"),
        "union_review_cases": len(rows),
        "cases": rows,
    }
    (recheck / "e4_typea1_en_human_recheck_package.json").write_text(
        json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "# E4 typea1_en human recheck package",
        "",
        "- Release: `m2_typea1_full_en_layerdedup_20260731`",
        f"- Frozen v9: `{v9.as_posix()}`",
        f"- Candidate: `{cand.as_posix()}`",
        f"- Replay gate oracle_mismatch_count: **{gate.get('oracle_mismatch_count')}**",
        f"- Migration-flagged cases: **{mig.get('targeted_human_recheck_cases')}**",
        f"- Selection-remapped cases: **{mig.get('selected_target_changed_cases')}**",
        f"- Union requiring review: **{len(rows)}**",
        "",
        "| Case | Cell | Question | Old status | New status | Old numeric | New numeric | Old target(s) | New target(s) | Reason | Review | Notes |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        q = str(r["question"] or "").replace("|", "\\|")
        reason = str(r["reason"]).replace("|", "\\|")
        lines.append(
            "| {case_id} | {cell} | {q} | {old_status} | {new_status} | {old_numeric} | {new_numeric} | {old_targets} | {new_targets} | {reason} |  |  |".format(
                case_id=r["case_id"],
                cell=r["cell"],
                q=q,
                old_status=r["old_status"],
                new_status=r["new_status"],
                old_numeric=json.dumps(r["old_numeric"], ensure_ascii=False),
                new_numeric=json.dumps(r["new_numeric"], ensure_ascii=False),
                old_targets=json.dumps(r["old_targets"], ensure_ascii=False),
                new_targets=json.dumps(r["new_targets"], ensure_ascii=False),
                reason=reason,
            )
        )
    (recheck / "e4_typea1_en_human_recheck_package.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    proj = next(
        c
        for c in new_by.values()
        if c["expected"]["perspective"] == "product"
        and c["expected"]["operation"] == "value"
        and c["expected"]["status"] == "executable"
        and not c.get("selected_component_ids")
    )
    proc = next(
        c
        for c in new_by.values()
        if c["expected"]["perspective"] == "process"
        and c["expected"]["operation"] == "value"
        and c["expected"]["status"] == "executable"
        and not c.get("selected_component_ids")
    )
    s = proj["expected"]["summary"]
    ps = proc["expected"]["summary"]
    checks = [
        (
            "product_total_kgCO2e",
            s["knownTotalCarbon_kgCO2e"],
            facts["carbon_totals"]["product_perspective_total_kgCO2e"],
            align["projectionTotals"]["product"],
        ),
        (
            "material_kgCO2e",
            s["totalMaterialCarbon_kgCO2e"],
            facts["carbon_totals"]["A1_material_kgCO2e"],
            None,
        ),
        (
            "process_attributed_kgCO2e",
            s["knownProcessCarbon_kgCO2e"],
            facts["carbon_totals"]["A3_process_attributed_kgCO2e"],
            None,
        ),
        (
            "process_recorded_kgCO2e",
            ps["knownProcessCarbon_kgCO2e"],
            facts["carbon_totals"]["process_perspective_recorded_kgCO2e"],
            align["projectionTotals"]["sourceProcess"],
        ),
        (
            "components",
            s["components"],
            facts["ifc_composition"]["component_count"],
            None,
        ),
    ]
    cross = {"release": "m2_typea1_full_en_layerdedup_20260731", "checks": []}
    md = ["# Section 7 cross-check (typea1_en)", "", "| quantity | gold | facts | alignment | pass |", "|---|---:|---:|---:|---|"]
    for name, gold, fact, al in checks:
        ok = abs(float(gold) - float(fact)) < 0.01 and (
            al is None or abs(float(gold) - float(al)) < 0.01
        )
        cross["checks"].append(
            {
                "quantity": name,
                "gold": gold,
                "facts": fact,
                "alignment": al,
                "pass": ok,
            }
        )
        md.append(
            f"| {name} | {gold} | {fact} | {al} | {'PASS' if ok else 'FAIL'} |"
        )
    (rebuild / "section7_cross_check.json").write_text(
        json.dumps(cross, indent=2) + "\n", encoding="utf-8"
    )
    (rebuild / "section7_cross_check.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8"
    )

    summary = {
        "run_id": "typea1_en_rebuild_20260730",
        "release": "m2_typea1_full_en_layerdedup_20260731",
        "case_count": 150,
        "oracle_mismatch_count": gate.get("oracle_mismatch_count"),
        "migration": mig,
        "human_recheck_package": str(
            recheck / "e4_typea1_en_human_recheck_package.json"
        ),
        "paper_ready_pending_human_approval": True,
    }
    (rebuild / "e4_typea1_en_candidate_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"review_cases={len(rows)} oracle={gate.get('oracle_mismatch_count')} "
        f"cross_pass={all(c['pass'] for c in cross['checks'])}"
    )


if __name__ == "__main__":
    main()
