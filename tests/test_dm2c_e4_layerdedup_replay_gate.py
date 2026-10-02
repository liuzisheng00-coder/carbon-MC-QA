from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from dm2c_e4_layerdedup_benchmark import build_generated_cases
from dm2c_e4_layerdedup_replay_gate import (
    OracleMismatchError,
    audit_benchmark,
    require_clean_audit,
    require_clean_benchmark,
)


RELEASE_DIR = Path(
    "outputs/research_experiments/m2_typed_completed_20260728_layerdedup"
)
REVIEWED_V9 = Path(
    "outputs/research_experiments/e4_benchmark/"
    "frozen_20260712_145321_v9_human_pass/"
    "e4_balanced_benchmark_150_human_reviewed_v9.jsonl"
)


def _write_jsonl(rows: list[dict[str, object]], path: Path) -> Path:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(scope="module")
def generated_cases() -> list[dict[str, object]]:
    cases = build_generated_cases(RELEASE_DIR, REVIEWED_V9)
    assert len(cases) == 150
    return cases


def test_audit_names_a_150_case_candidate_with_corrupted_numeric_gold(
    tmp_path: Path,
    generated_cases: list[dict[str, object]],
) -> None:
    corrupted = deepcopy(generated_cases)
    corrupted_case_id = "e4_balanced_001_product_value_executable"
    assert corrupted[0]["case_id"] == corrupted_case_id
    corrupted[0]["expected"]["summary"]["knownTotalCarbon_kgCO2e"] += 1.0
    candidate_path = _write_jsonl(corrupted, tmp_path / "corrupted.jsonl")

    report = audit_benchmark(RELEASE_DIR, candidate_path)

    assert report["case_count"] == 150
    assert report["oracle_mismatch_count"] == 1
    assert report["oracle_mismatch_case_ids"] == [corrupted_case_id]
    corrupted_observation = next(
        item
        for item in report["case_observations"]
        if item["case_id"] == corrupted_case_id
    )
    assert corrupted_observation["mismatch_fields"] == [
        "summary.knownTotalCarbon_kgCO2e"
    ]


def test_audit_derives_selected_targets_without_trusting_candidate_ids(
    tmp_path: Path,
    generated_cases: list[dict[str, object]],
) -> None:
    corrupted = deepcopy(generated_cases)
    corrupted_case_id = "e4_balanced_004_product_compare_executable"
    assert corrupted[3]["case_id"] == corrupted_case_id
    original_ids = list(corrupted[3]["selected_component_ids"])
    corrupted[3]["selected_component_ids"] = list(reversed(original_ids))
    candidate_path = _write_jsonl(
        corrupted, tmp_path / "corrupted-target.jsonl"
    )

    report = audit_benchmark(RELEASE_DIR, candidate_path)

    assert report["oracle_mismatch_count"] == 1
    assert report["oracle_mismatch_case_ids"] == [corrupted_case_id]
    observation = report["case_observations"][3]
    assert observation["case_id"] == corrupted_case_id
    assert observation["expected"]["selected_target_ids"] == list(
        reversed(original_ids)
    )
    assert observation["observed"]["selected_target_ids"] == original_ids
    assert observation["mismatch_fields"] == ["selected_target_ids"]


def test_audit_names_a_corrupted_status_as_the_exact_only_mismatch(
    tmp_path: Path,
    generated_cases: list[dict[str, object]],
) -> None:
    corrupted = deepcopy(generated_cases)
    corrupted_case_id = "e4_balanced_001_product_value_executable"
    assert corrupted[0]["case_id"] == corrupted_case_id
    corrupted[0]["expected"]["status"] = "empty_result"
    candidate_path = _write_jsonl(
        corrupted, tmp_path / "corrupted-status.jsonl"
    )

    report = audit_benchmark(RELEASE_DIR, candidate_path)

    assert report["oracle_mismatch_count"] == 1
    assert report["oracle_mismatch_case_ids"] == [corrupted_case_id]
    observation = report["case_observations"][0]
    assert observation["mismatch_fields"] == ["status"]


def test_require_clean_audit_blocks_a_report_with_oracle_mismatches() -> None:
    report = {
        "oracle_mismatch_count": 1,
        "oracle_mismatch_case_ids": ["e4_balanced_001_product_value_executable"],
    }

    with pytest.raises(
        OracleMismatchError,
        match="e4_balanced_001_product_value_executable",
    ):
        require_clean_audit(report)


def test_require_clean_benchmark_blocks_corrupt_jsonl(
    tmp_path: Path,
    generated_cases: list[dict[str, object]],
) -> None:
    corrupted = deepcopy(generated_cases)
    corrupted_case_id = "e4_balanced_001_product_value_executable"
    corrupted[0]["expected"]["summary"]["knownTotalCarbon_kgCO2e"] += 1.0
    candidate_path = _write_jsonl(
        corrupted, tmp_path / "blocking-corrupted.jsonl"
    )

    with pytest.raises(OracleMismatchError, match=corrupted_case_id):
        require_clean_benchmark(RELEASE_DIR, candidate_path)


def test_cli_writes_corrupt_report_and_exits_nonzero(
    tmp_path: Path,
    generated_cases: list[dict[str, object]],
) -> None:
    corrupted = deepcopy(generated_cases)
    corrupted_case_id = "e4_balanced_001_product_value_executable"
    corrupted[0]["expected"]["summary"]["knownTotalCarbon_kgCO2e"] += 1.0
    candidate_path = _write_jsonl(
        corrupted, tmp_path / "cli-corrupted.jsonl"
    )
    report_path = tmp_path / "cli-report.json"

    completed = subprocess.run(
        [
            sys.executable,
            "dm2c_e4_layerdedup_replay_gate.py",
            "--release-dir",
            str(RELEASE_DIR),
            "--benchmark",
            str(candidate_path),
            "--report",
            str(report_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["oracle_mismatch_count"] == 1
    assert report["oracle_mismatch_case_ids"] == [corrupted_case_id]
