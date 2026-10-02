from __future__ import annotations

from pathlib import Path

import pytest

from dm2c_m3_context import load_m3_execution_context
from dm2c_research_experiment_runner import build_research_report, publish_research_report
from tests.task11_v2_fixture import write_task10_release


def test_research_report_uses_canonical_context_and_discloses_coverage(tmp_path: Path) -> None:
    context = load_m3_execution_context(write_task10_release(tmp_path / "release"))
    report = build_research_report(context)
    assert report["schemaVersion"] == "m23-canonical-v2"
    assert report["releaseProfile"] == "controlled-fixture"
    assert report["totals"] == pytest.approx(
        {
            "materialSource_kgCO2e": 19.0,
            "productEnergy_kgCO2e": 24.0,
            "productTotal_kgCO2e": 43.0,
            "sourceProcess_kgCO2e": 31.0,
            "allSource_kgCO2e": 50.0,
        }
    )
    assert report["coverage"]["accepted"] == 8
    assert report["coverage"]["rejected"] == 1
    assert report["coverage"]["energySourceStatus"] == "incomplete_path"
    serialized = str(report)
    assert "AtomicCarbonEmission" not in serialized
    assert "blocked_" not in serialized


def test_research_publication_is_staged_new_named_and_no_latest(tmp_path: Path) -> None:
    context = load_m3_execution_context(write_task10_release(tmp_path / "release"))
    report = build_research_report(context)
    output_root = tmp_path / "reports"
    target = publish_research_report(
        report, output_root=output_root, run_id="controlled-v2-research"
    )
    assert (target / "research_report.json").is_file()
    assert not list(output_root.glob("latest_*"))
    with pytest.raises(FileExistsError):
        publish_research_report(
            report, output_root=output_root, run_id="controlled-v2-research"
        )
