"""Deterministic research summaries from an immutable canonical-v2 M3 context."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from dm2c_m3_context import M3ExecutionContext


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def build_research_report(context: M3ExecutionContext) -> dict[str, Any]:
    if not isinstance(context, M3ExecutionContext):
        raise TypeError("context must be M3ExecutionContext")
    energy_rejections = context.validation_coverage.relevant_rejections(
        perspective="energy_source"
    )
    energy_status = (
        "incomplete_path"
        if context.availability["energy"] != "complete" or energy_rejections
        else "executable"
    )
    summary = context.project_summary
    return {
        "schemaVersion": "m23-canonical-v2",
        "releaseId": str(context.canonical.manifest["releaseId"]),
        "releaseProfile": context.release_profile,
        "totals": {
            "materialSource_kgCO2e": summary["material_source_kgCO2e"],
            "productEnergy_kgCO2e": summary["product_energy_kgCO2e"],
            "productTotal_kgCO2e": summary["product_total_kgCO2e"],
            "sourceProcess_kgCO2e": summary["source_process_kgCO2e"],
            "allSource_kgCO2e": summary["all_source_kgCO2e"],
        },
        "coverage": {
            "accepted": context.validation_coverage.accepted_count,
            "rejected": context.validation_coverage.rejected_count,
            "materialAvailability": context.availability["material"],
            "energyAvailability": context.availability["energy"],
            "energySourceStatus": energy_status,
        },
        "components": [
            {
                "componentId": row.entity_id,
                "name": row.name,
                "value_kgCO2e": row.value_kgCO2e,
                "emissionIds": list(row.emission_ids),
            }
            for row in context.component_summaries
        ],
        "materials": [
            {
                "materialId": row.entity_id,
                "name": row.name,
                "sourceValue_kgCO2e": row.value_kgCO2e,
            }
            for row in context.material_summaries
        ],
        "carriers": [
            {
                "carrierId": row.entity_id,
                "name": row.name,
                "sourceValue_kgCO2e": row.value_kgCO2e,
            }
            for row in context.carrier_summaries
        ],
    }


def publish_research_report(
    report: Mapping[str, Any], *, output_root: Path, run_id: str
) -> Path:
    if not _SAFE_ID.fullmatch(run_id) or "v2" not in run_id.casefold():
        raise ValueError("run_id must be a safe v2 identifier")
    target = output_root / run_id
    staging = output_root / f".{run_id}.staging"
    if target.exists() or staging.exists():
        raise FileExistsError(target if target.exists() else staging)
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")
    output_root.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        (staging / "research_report.json").write_bytes(payload)
        staging.rename(target)
    except Exception:
        if staging.exists():
            for item in staging.iterdir():
                if item.is_file():
                    item.unlink()
            staging.rmdir()
        raise
    return target


__all__ = ["build_research_report", "publish_research_report"]
