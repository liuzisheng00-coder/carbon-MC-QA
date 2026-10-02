from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from dm2c_carbonql_benchmark import CarbonQLCase, ReferenceSpec
from dm2c_real_baseline_runner import (
    StaticLLMClient,
    build_baseline_messages,
    build_case_context,
    load_baseline_context,
    publish_baseline_run,
    run_real_baseline_suite,
)
from tests.task11_v2_fixture import (
    controlled_programs,
    write_actual_like_no_energy_release,
    write_task10_release,
)


def test_baseline_runner_cli_documents_release_and_deepseek_configuration() -> None:
    completed = subprocess.run(
        [sys.executable, "dm2c_real_baseline_runner.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--benchmark" in completed.stdout
    assert "--kg-dir" in completed.stdout
    assert "--allow-synthetic" in completed.stdout
    assert "deepseek-chat" in completed.stdout
    assert "temperature=0" in completed.stdout
    assert "e4_balanced_benchmark_150_human_reviewed_v10_layerdedup.jsonl" in completed.stdout


def _case() -> CarbonQLCase:
    query = controlled_programs()[0][1]
    return CarbonQLCase(
        case_id="controlled-v2-product",
        question="What is the controlled product total?",
        category="controlled-v2",
        gold_program=query,
        expected_compiler_status="valid",
        reference=ReferenceSpec(projection_perspective="product"),
    )


def test_baseline_loads_only_a_file_backed_canonical_release(tmp_path: Path) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    assert context.project_summary["product_total_kgCO2e"] == pytest.approx(43.0)
    assert not hasattr(context, "calculated_atoms")
    assert not hasattr(context, "blocked_atoms")


def test_case_snapshot_does_not_leak_gold_program_or_reference(tmp_path: Path) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    snapshot = build_case_context(context, _case(), mode="llm_only_real")
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "gold_program" not in serialized
    assert "reference" not in serialized
    assert "controlled-v2-product" not in serialized
    assert snapshot["release_profile"] == "controlled-fixture"
    assert snapshot["representation"] == "raw_source_tables_ungraphed"
    assert "project_summary" not in snapshot
    assert "material_quantities" in snapshot
    assert "emission_factors" in snapshot
    assert all("value_kgCO2e" not in row for row in snapshot["material_quantities"])
    assert snapshot["availability"] == {"material": "complete", "energy": "complete"}
    assert snapshot["coverage"]["by_kind"]["energy"] == {
        "accepted": 4,
        "rejected": 1,
    }


def test_kg_multitable_snapshot_exposes_computed_carbon_but_no_grand_totals(
    tmp_path: Path,
) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    snapshot = build_case_context(context, _case(), mode="unconstrained_kg_llm_real")
    assert snapshot["representation"] == "constructed_kg_multitable"
    assert "project_summary" not in snapshot
    assert "components" in snapshot
    assert "material_families" in snapshot
    assert "carriers" in snapshot
    assert all("value_kgCO2e" in row for row in snapshot["components"])


def test_actual_like_no_energy_snapshot_discloses_unavailable_coverage(
    tmp_path: Path,
) -> None:
    release = write_actual_like_no_energy_release(tmp_path / "actual-like")
    context = load_baseline_context(release)
    snapshot = build_case_context(context, _case(), mode="llm_only_real")
    assert snapshot["availability"]["energy"] == "not_available"
    assert snapshot["coverage"]["by_kind"].get("energy") is None


def test_baseline_uses_static_client_and_canonical_result_schema(tmp_path: Path) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    response = {
        "content": json.dumps(
            {
                "status": "executable",
                "coverage_status": "complete",
                "perspective": "product",
                "operation": "aggregate",
                "summary": {"total_kgCO2e": 43.0},
                "evidence_ids": ["emission:material"],
                "answer": "The controlled product total is 43 kgCO2e.",
            }
        )
    }
    client = StaticLLMClient(response)
    report = run_real_baseline_suite(
        cases=(_case(),), context=context, client=client, variants=("llm_only_real",)
    )
    assert report["llm_only_real"]["case_count"] == 1
    assert report["llm_only_real"]["transport_error_count"] == 0
    assert client.call_count == 1
    messages = build_baseline_messages(context, _case(), "llm_only_real")
    assert "raw source tables" in messages[0]["content"]
    assert "material_quantities" in messages[0]["content"]


def test_baseline_preserves_successful_provider_prose_as_unstructured_response(
    tmp_path: Path,
) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    prose = (
        "Based solely on the supplied canonical-v2 summary, the project's known "
        "carbon total is **8,197.70 kgCO2e**, split into **7,928.41 kgCO2e** "
        "for materials and **269.28 kgCO2e** for factory energy."
    )
    client = StaticLLMClient({"role": "assistant", "content": prose})

    report = run_real_baseline_suite(
        cases=(_case(),), context=context, client=client, variants=("llm_only_real",)
    )

    observed = report["llm_only_real"]["cases"][0]["observed"]
    assert report["llm_only_real"]["transport_error_count"] == 0
    assert observed == {
        "response_format": "unstructured",
        "answer": prose,
        "raw_content": prose,
    }


def test_structured_json_baseline_requires_json_and_documents_schema(
    tmp_path: Path,
) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    messages = build_baseline_messages(context, _case(), "llm_structured_json_real")
    assert "Reply with ONLY one JSON object" in messages[0]["content"]
    assert '"perspective"' in messages[0]["content"] or "perspective:" in messages[0]["content"]

    json_client = StaticLLMClient(
        {
            "content": json.dumps(
                {
                    "status": "executable",
                    "perspective": "product",
                    "operation": "value",
                    "summary": {"knownTotalCarbon_kgCO2e": 43.0},
                    "answer": "43 kgCO2e",
                }
            )
        }
    )
    ok = run_real_baseline_suite(
        cases=(_case(),),
        context=context,
        client=json_client,
        variants=("llm_structured_json_real",),
    )
    observed = ok["llm_structured_json_real"]["cases"][0]["observed"]
    assert observed["perspective"] == "product"
    assert observed["operation"] == "value"
    assert observed["summary"]["knownTotalCarbon_kgCO2e"] == pytest.approx(43.0)

    prose_client = StaticLLMClient({"content": "The total is forty three."})
    bad = run_real_baseline_suite(
        cases=(_case(),),
        context=context,
        client=prose_client,
        variants=("llm_structured_json_real",),
    )
    assert bad["llm_structured_json_real"]["transport_error_count"] == 1
    assert bad["llm_structured_json_real"]["cases"][0]["observed"]["status"] == (
        "transport_error"
    )


def test_baseline_keeps_provider_connection_failures_as_transport_errors(
    tmp_path: Path,
) -> None:
    class FailingClient:
        def complete(self, *_args: object, **_kwargs: object) -> dict[str, object]:
            raise ConnectionError("provider connection failed")

    context = load_baseline_context(write_task10_release(tmp_path / "release"))

    report = run_real_baseline_suite(
        cases=(_case(),),
        context=context,
        client=FailingClient(),  # type: ignore[arg-type]
        variants=("llm_only_real",),
    )

    observed = report["llm_only_real"]["cases"][0]["observed"]
    assert report["llm_only_real"]["transport_error_count"] == 1
    assert observed == {
        "status": "transport_error",
        "error": "provider connection failed",
    }


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_baseline_rejects_nonfinite_json_constants_as_transport_errors(
    tmp_path: Path, constant: str
) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    client = StaticLLMClient(
        {
            "content": (
                '{"status":"executable","summary":{"total_kgCO2e":'
                + constant
                + "}}"
            )
        }
    )

    report = run_real_baseline_suite(
        cases=(_case(),), context=context, client=client, variants=("llm_only_real",)
    )

    observed = report["llm_only_real"]["cases"][0]["observed"]
    assert report["llm_only_real"]["transport_error_count"] == 1
    assert observed["status"] == "transport_error"
    assert constant in observed["raw_content"]


@pytest.mark.parametrize(
    "constant", (float("nan"), float("inf"), float("-inf"))
)
def test_baseline_rejects_nonfinite_mapping_content_as_transport_errors(
    tmp_path: Path, constant: float
) -> None:
    context = load_baseline_context(write_task10_release(tmp_path / "release"))
    client = StaticLLMClient(
        {
            "content": {
                "status": "executable",
                "summary": {"total_kgCO2e": constant},
            }
        }
    )

    report = run_real_baseline_suite(
        cases=(_case(),), context=context, client=client, variants=("llm_only_real",)
    )

    observed = report["llm_only_real"]["cases"][0]["observed"]
    assert report["llm_only_real"]["transport_error_count"] == 1
    assert observed["status"] == "transport_error"


def test_publication_is_new_named_no_overwrite_and_has_no_latest_pointer(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    target = publish_baseline_run(
        {"schemaVersion": "m23-canonical-v2", "status": "complete"},
        output_root=output_root,
        run_id="controlled-v2-baseline",
    )
    assert target.name == "controlled-v2-baseline"
    assert not list(output_root.glob("latest_*"))
    with pytest.raises(FileExistsError):
        publish_baseline_run({}, output_root=output_root, run_id="controlled-v2-baseline")


def test_publication_rejects_nonfinite_json_before_creating_output(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs"
    with pytest.raises(ValueError, match="Out of range float"):
        publish_baseline_run(
            {"value": float("nan")},
            output_root=output_root,
            run_id="controlled-v2-nan",
        )
    assert not output_root.exists()
