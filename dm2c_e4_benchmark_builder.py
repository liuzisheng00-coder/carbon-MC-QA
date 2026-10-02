"""Canonical-v2 context adapter and safe E4 benchmark publication helpers."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from dm2c_canonical_v2_reader import (
    CanonicalV2Context,
    iter_emissions,
    iter_product_contributions,
    load_canonical_v2_context,
)


@dataclass(frozen=True, slots=True)
class GraphContext:
    """Thin immutable adapter; no raw graph or legacy calculation caches."""

    canonical: CanonicalV2Context
    validation_rows: tuple[Mapping[str, str], ...]
    release_profile: str


def load_main_graph_context(kg_dir: Path | str) -> GraphContext:
    canonical = load_canonical_v2_context(kg_dir)
    return GraphContext(
        canonical=canonical,
        validation_rows=canonical.validation_rows,
        release_profile=str(canonical.manifest["releaseProfile"]),
    )


def _canonical(context: GraphContext | CanonicalV2Context) -> CanonicalV2Context:
    if isinstance(context, GraphContext):
        return context.canonical
    if isinstance(context, CanonicalV2Context):
        return context
    raise TypeError("context must be GraphContext or CanonicalV2Context")


def total_material(context: GraphContext | CanonicalV2Context) -> float:
    return sum(fact.emission_value for fact in iter_emissions(_canonical(context), kind="material"))


def total_process(context: GraphContext | CanonicalV2Context) -> float:
    return sum(fact.emission_value for fact in iter_emissions(_canonical(context), kind="energy"))


def total_product_process(context: GraphContext | CanonicalV2Context) -> float:
    return sum(
        item.projected_value
        for item in iter_product_contributions(_canonical(context))
        if item.mode in {"direct", "allocated"}
    )


def total_known(context: GraphContext | CanonicalV2Context) -> float:
    canonical = _canonical(context)
    return sum(fact.emission_value for fact in iter_emissions(canonical))


def summarize_cases(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "caseCount": len(cases),
        "compilerStatusCounts": {
            status: sum(row.get("expected_compiler_status") == status for row in cases)
            for status in sorted({str(row.get("expected_compiler_status") or "") for row in cases})
            if status
        },
    }


def write_jsonl(path: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in cases
    )
    path.write_text(payload, encoding="utf-8", newline="")


_SUCCESSOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def publish_v2_benchmark(
    cases: Sequence[Mapping[str, Any]], *, output_root: Path, release_id: str
) -> Path:
    """Publish a separately named successor, failing before any overwrite."""
    if not _SUCCESSOR_ID.fullmatch(release_id) or "v2" not in release_id.lower():
        raise ValueError("release_id must be a safe, explicitly v2 successor id")
    output_dir = output_root / release_id
    if output_dir.exists():
        raise FileExistsError(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".{release_id}.staging"
    if staging.exists():
        raise FileExistsError(staging)
    staging.mkdir()
    try:
        write_jsonl(staging / "e4_benchmark_v2.jsonl", cases)
        (staging / "e4_benchmark_v2_summary.json").write_text(
            json.dumps(summarize_cases(cases), ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
            newline="",
        )
        staging.rename(output_dir)
    except Exception:
        # New staging contains no user data and is safe to remove file-by-file.
        if staging.exists():
            for path in staging.iterdir():
                if path.is_file():
                    path.unlink()
            staging.rmdir()
        raise
    return output_dir


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kg-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--release-id", required=True)
    return parser


__all__ = [
    "GraphContext",
    "load_main_graph_context",
    "publish_v2_benchmark",
    "summarize_cases",
    "total_known",
    "total_material",
    "total_process",
    "total_product_process",
    "write_jsonl",
]
