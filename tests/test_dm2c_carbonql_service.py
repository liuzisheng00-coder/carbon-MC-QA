from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from dm2c_carbonql_service import CarbonQLService
from tests.task10_v2_fixture import write_task10_release


class StubLLMClient:
    """Return a fixed compiler output so the chain can be tested without a provider."""

    def __init__(self, program: Mapping[str, Any] | str):
        self.program = program
        self.calls: list[Sequence[Mapping[str, str]]] = []

    def complete(self, messages: Sequence[Mapping[str, str]], **_: Any) -> dict[str, Any]:
        self.calls.append(messages)
        content = (
            self.program
            if isinstance(self.program, str)
            else json.dumps(self.program, ensure_ascii=False)
        )
        return {"content": content}


PROJECT_MATERIAL_TOTAL = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]
}

MATERIAL_BREAKDOWN = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "material"},
        {"op": "GroupBy", "keys": ["material"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]
}

WITHHELD_SOURCE = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "?"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ],
    "holes": [{"dimension": "emission_source", "candidates": ["material", "process"]}],
}

UNKNOWN_CARRIER_FILTER = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "process"},
        {"op": "Filter", "field": "carrier", "equals": "unobtainium"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]
}


def service(tmp_path: Path, program: Mapping[str, Any] | str) -> CarbonQLService:
    return CarbonQLService.from_release(
        write_task10_release(tmp_path / "release"),
        StubLLMClient(program),
        variant="V2",
    )


def _write_synthetic_release(tmp_path: Path) -> Path:
    release = write_task10_release(tmp_path / "synthetic-release")
    manifest_path = release / "case_version_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["syntheticFactoryInputsUsed"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return release


def test_answered_question_reports_value_scope_and_evidence(tmp_path: Path) -> None:
    answer = service(tmp_path, PROJECT_MATERIAL_TOTAL).answer(
        "How much embodied carbon does the project carry?"
    )
    assert answer.answered
    assert answer.query_status == "ok"
    assert answer.total_kgCO2e == pytest.approx(19.0)
    assert answer.perspective == "product"
    assert answer.source_scope == ("material",)
    assert answer.evidence_ids
    assert answer.compiler_error == {}


def test_grouping_dimension_sets_the_reported_perspective(tmp_path: Path) -> None:
    answer = service(tmp_path, MATERIAL_BREAKDOWN).answer(
        "Break the project's embodied carbon down by material."
    )
    assert answer.answered
    assert answer.perspective == "material"
    assert answer.view_signature is not None
    assert answer.view_signature["base_views"] == ["material"]
    assert len(answer.rows) > 1


def test_perspective_views_are_read_without_calling_the_compiler(tmp_path: Path) -> None:
    client = StubLLMClient(PROJECT_MATERIAL_TOTAL)
    accounts = CarbonQLService.from_release(
        write_task10_release(tmp_path / "release"), client, variant="V2"
    ).perspective_accounts()

    assert client.calls == []
    assert set(accounts) == {"product", "material", "process"}
    for rows in accounts.values():
        assert rows
        assert all(row["name"] for row in rows)
    # The material panel is where opaque node ids leaked into the interface.
    assert all(row["name"] != row["id"] for row in accounts["material"])
    assert all(row["context"].endswith("component(s)") for row in accounts["material"])


def test_product_view_totals_agree_with_the_project_measurement(tmp_path: Path) -> None:
    subject = CarbonQLService.from_release(
        write_task10_release(tmp_path / "release"),
        StubLLMClient(PROJECT_MATERIAL_TOTAL),
        variant="V2",
    )
    measured = subject.measure()
    rows = subject.perspective_accounts()["product"]

    assert sum(row["totalKgCO2e"] for row in rows) == pytest.approx(
        measured.C_total_kgCO2e
    )
    assert rows == tuple(
        sorted(rows, key=lambda row: row["totalKgCO2e"], reverse=True)
    )


def test_product_rows_name_the_carbon_source_they_are_missing(tmp_path: Path) -> None:
    rows = (
        CarbonQLService.from_release(
            write_task10_release(tmp_path / "release"),
            StubLLMClient(PROJECT_MATERIAL_TOTAL),
            variant="V2",
        )
        .perspective_accounts()["product"]
    )

    for row in rows:
        if row["materialKgCO2e"] is not None and row["processKgCO2e"] is not None:
            assert row["status"] == "measured"
        elif row["materialKgCO2e"] is not None:
            assert row["status"] == "material_only"
        else:
            assert row["status"] == "process_only"


def test_process_view_keeps_the_burden_no_product_claims(tmp_path: Path) -> None:
    """The process view is source-side, so it may exceed what products carry."""
    subject = CarbonQLService.from_release(
        write_task10_release(tmp_path / "release"),
        StubLLMClient(PROJECT_MATERIAL_TOTAL),
        variant="V2",
    )
    accounts = subject.perspective_accounts()
    attributed = sum(
        row["processKgCO2e"] or 0.0 for row in accounts["product"]
    )
    recorded = sum(row["totalKgCO2e"] for row in accounts["process"])

    assert recorded >= attributed
    assert sum(row["totalKgCO2e"] for row in accounts["material"]) == pytest.approx(
        subject.measure().C_mat_kgCO2e
    )


def test_withheld_source_is_fail_closed_with_a_clarification(tmp_path: Path) -> None:
    answer = service(tmp_path, WITHHELD_SOURCE).answer(
        "How much carbon is there, without assuming which source?"
    )
    assert not answer.answered
    assert answer.total_kgCO2e is None
    assert answer.rows == ()
    assert answer.holes
    assert "material carbon, process carbon, or both" in answer.clarification


def test_unsatisfiable_filter_is_not_reported_as_answered_zero(tmp_path: Path) -> None:
    answer = service(tmp_path, UNKNOWN_CARRIER_FILTER).answer(
        "Show process carbon for unobtainium."
    )
    assert not answer.answered
    assert answer.query_status == "unsatisfiable_filter"
    assert answer.total_kgCO2e is None
    assert answer.rows == ()
    payload = answer.to_payload()
    assert payload["totalKgCO2e"] is None
    assert payload["rows"] == []
    assert "could not be resolved uniquely" in answer.clarification


def test_uncompilable_question_reports_no_value(tmp_path: Path) -> None:
    answer = service(tmp_path, "this is not a CarbonQL program").answer(
        "Tell me something about the weather."
    )
    assert not answer.answered
    assert answer.total_kgCO2e is None
    assert answer.query_status == "not_executed"
    assert answer.compiler_error
    assert "no value is reported" in answer.clarification


def test_payload_never_carries_a_total_for_an_unanswered_question(
    tmp_path: Path,
) -> None:
    for index, program in enumerate((WITHHELD_SOURCE, "not a program")):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        payload = service(case_dir, program).answer("A question.").to_payload()
        assert payload["answered"] is False
        assert payload["totalKgCO2e"] is None
        assert payload["rows"] == []


UNDERSPECIFIED_BREAKDOWN = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "GroupBy", "keys": ["component"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]
}

UNDERSPECIFIED_RANKING = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "GroupBy", "keys": ["component"]},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
        {"op": "Rank", "top_k": 3, "descending": True},
    ]
}

UNGROUPED_TOTAL = {
    "steps": [
        {"op": "SelectProject"},
        {"op": "CarbonAtoms", "source": "all"},
        {"op": "Aggregate", "metric": "sum_kgCO2e"},
    ]
}

UNNAMED_SOURCE_QUESTION = "Which component carries the most carbon?"


def test_source_dependence_is_settled_when_the_question_names_no_source(
    tmp_path: Path,
) -> None:
    answer = service(tmp_path, UNDERSPECIFIED_BREAKDOWN).answer(UNNAMED_SOURCE_QUESTION)

    dependence = answer.source_dependence
    assert dependence["evaluated"] is True
    assert dependence["decisionClass"] in {
        "view_robust",
        "view_sensitive",
        "decomposition_required",
        "source_dependent_trace",
    }
    assert [row["candidateId"] for row in dependence["candidates"]] == [
        "material",
        "process",
        "unified",
    ]
    # The three scopes must actually have been executed, not merely enumerated.
    totals = {row["candidateId"]: row["totalKgCO2e"] for row in dependence["candidates"]}
    assert totals["material"] == pytest.approx(19.0)
    assert totals["process"] == pytest.approx(24.0)
    assert totals["unified"] == pytest.approx(43.0)
    # The check costs real time, so it is reported rather than hidden inside
    # the latency of the answer itself.
    assert dependence["latencyMs"] >= 0.0


def test_ranking_answer_leads_with_the_ranked_entry_not_the_sum(
    tmp_path: Path,
) -> None:
    """A ranking question is answered by its top entry.

    The sum over all groups is true but is not what was asked, so it must not
    occupy the headline. It stays available on the line below.
    """
    from dm2c_carbonql_service import answer_payload

    answer = service(tmp_path, UNDERSPECIFIED_RANKING).answer(
        "Rank the components by carbon."
    )
    text = answer_payload(answer)["answer"]
    headline = text.splitlines()[0]

    top = answer.rows[0]
    top_value = next(value for key, value in top.items() if key.endswith("kgCO2e"))
    assert f"{top_value:,.3f}" in headline
    assert f"{answer.total_kgCO2e:,.3f}" not in headline
    assert f"total over all groups in scope: {answer.total_kgCO2e:,.3f}" in text


def test_aggregate_answer_still_leads_with_the_total(tmp_path: Path) -> None:
    from dm2c_carbonql_service import answer_payload

    answer = service(tmp_path, PROJECT_MATERIAL_TOTAL).answer(
        "How much embodied carbon does the project carry?"
    )
    text = answer_payload(answer)["answer"]

    assert text.splitlines()[0] == "19.000 kgCO2e"
    assert "total over all groups in scope" not in text


def test_source_dependence_is_skipped_when_the_question_fixes_the_source(
    tmp_path: Path,
) -> None:
    answer = service(tmp_path, MATERIAL_BREAKDOWN).answer(
        "Break the project's embodied carbon down by material."
    )

    assert answer.source_dependence["evaluated"] is False
    assert "fixes its carbon source" in answer.source_dependence["reason"]


def test_source_dependence_leaves_the_reported_answer_untouched(
    tmp_path: Path,
) -> None:
    """The verdict is diagnostic. It must not move the number the reader is given.

    The frozen benchmarks score status, total and rows, so a change here would
    silently invalidate them.
    """
    answer = service(tmp_path, UNDERSPECIFIED_RANKING).answer(
        "Rank the components by carbon."
    )

    assert answer.source_dependence["evaluated"] is True
    assert answer.query_status == "ok"
    assert answer.total_kgCO2e == pytest.approx(43.0)
    assert answer.source_scope == ("material", "process")
    assert len(answer.rows) == 3


def test_ineligible_program_gets_no_verdict_rather_than_a_guessed_one(
    tmp_path: Path,
) -> None:
    answer = service(tmp_path, UNGROUPED_TOTAL).answer(
        "What is the total carbon of the project?"
    )

    assert answer.answered
    assert answer.source_dependence["evaluated"] is False
    assert "not eligible" in answer.source_dependence["reason"]


def test_uncompiled_question_carries_no_source_dependence_verdict(
    tmp_path: Path,
) -> None:
    answer = service(tmp_path, "this is not a CarbonQL program").answer(
        UNNAMED_SOURCE_QUESTION
    )

    assert not answer.answered
    assert answer.source_dependence == {}


def test_unnamed_source_raises_a_warning_the_reader_can_act_on(
    tmp_path: Path,
) -> None:
    from dm2c_carbonql_service import answer_payload

    answer = service(tmp_path, UNDERSPECIFIED_BREAKDOWN).answer(UNNAMED_SOURCE_QUESTION)
    payload = answer_payload(answer)

    assert payload["sourceDependence"]["evaluated"] is True
    warnings = [
        flag for flag in payload["validation"]["flags"] if flag["level"] == "warning"
    ]
    assert len(warnings) == 1
    assert warnings[0]["code"] == "source_decomposition_required"
    assert "did not name a carbon source" in warnings[0]["message"]


def test_answered_question_with_a_named_source_carries_no_warning(
    tmp_path: Path,
) -> None:
    from dm2c_carbonql_service import answer_payload

    payload = answer_payload(
        service(tmp_path, PROJECT_MATERIAL_TOTAL).answer(
            "How much embodied carbon does the project carry?"
        )
    )

    assert payload["validation"]["flags"] == []


def test_graph_summary_describes_the_loaded_release(tmp_path: Path) -> None:
    summary = service(tmp_path, PROJECT_MATERIAL_TOTAL).graph_summary()
    assert summary["releaseId"]
    assert summary["emissionRecordCount"] > 0
    assert summary["syntheticEnergy"] is False


def _project(tmp_path: Path, **overrides: Any):
    import dm2c_api_server as api

    return api.Project(
        project_id="connect",
        created_at=0.0,
        upload_dir=tmp_path / "uploads",
        output_dir=tmp_path / "outputs",
        **overrides,
    )


def test_configured_release_opens_the_carbon_query_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dm2c_api_server as api

    release = write_task10_release(tmp_path / "release")
    monkeypatch.setenv(api.CARBONQL_RELEASE_ENV, str(release))
    project = _project(tmp_path, llm_client=StubLLMClient(PROJECT_MATERIAL_TOTAL))

    api.connect_carbonql_executor(project)

    assert project.canonical_query_available is True
    assert project.canonical_query_status == "carbonql_executor_connected"
    assert project.carbonql is not None
    api._require_canonical_query(project)


def test_unset_synthetic_policy_connects_a_valid_synthetic_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dm2c_api_server as api

    monkeypatch.delenv(api.CARBONQL_ALLOW_SYNTHETIC_ENV, raising=False)
    monkeypatch.setenv(api.CARBONQL_RELEASE_ENV, str(_write_synthetic_release(tmp_path)))
    project = _project(tmp_path, llm_client=StubLLMClient(PROJECT_MATERIAL_TOTAL))

    api.connect_carbonql_executor(project)

    assert project.canonical_query_available is True
    assert project.canonical_query_status == "carbonql_executor_connected"
    assert project.carbonql is not None
    assert project.carbonql.synthetic_energy is True


@pytest.mark.parametrize("explicit_value", ["0", "false", "False"])
def test_explicit_synthetic_opt_out_rejects_a_synthetic_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_value: str
) -> None:
    import dm2c_api_server as api

    monkeypatch.setenv(api.CARBONQL_ALLOW_SYNTHETIC_ENV, explicit_value)
    monkeypatch.setenv(api.CARBONQL_RELEASE_ENV, str(_write_synthetic_release(tmp_path)))
    project = _project(tmp_path, llm_client=StubLLMClient(PROJECT_MATERIAL_TOTAL))

    api.connect_carbonql_executor(project)

    assert project.canonical_query_available is False
    assert project.canonical_query_status.startswith("canonical_release_rejected")
    assert project.carbonql is None


def test_pinned_release_supplies_the_panels_not_only_the_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A release with no uploaded graph must still fill the account panels."""
    import dm2c_api_server as api

    release = write_task10_release(tmp_path / "release")
    monkeypatch.setenv(api.CARBONQL_RELEASE_ENV, str(release))
    project = _project(tmp_path, llm_client=StubLLMClient(PROJECT_MATERIAL_TOTAL))

    api.connect_carbonql_executor(project)

    assert project.canonical_graph_path is None
    assert set(project.carbon_accounts) == {"product", "material", "process"}
    graph_info = api._project_graph_info(project)
    assert graph_info["canonicalValidationLevel"] == "release_envelope"
    assert graph_info["stats"]["nodeCount"] > 0
    assert graph_info["stats"]["edgeCount"] > 0
    assert graph_info["stats"]["applicationClassCounts"]["BuildingComponent"] > 0
    coverage = graph_info["stats"]["validation"]
    assert coverage["candidateCount"] == (
        coverage["acceptedCount"] + coverage["rejectedCount"]
    )
    assert coverage["coverageValue"] == pytest.approx(
        coverage["acceptedCount"] / coverage["candidateCount"]
    )
    assert sum(coverage["rejectedByReason"].values()) == coverage["rejectedCount"]

    summary = api._account_summary(project, component_count=3)
    assert summary["components"] == 3
    assert summary["knownTotalCarbon_kgCO2e"] == pytest.approx(
        project.carbonql.measure().C_total_kgCO2e
    )


def test_pinned_release_serves_one_component_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import dm2c_api_server as api

    release = write_task10_release(tmp_path / "release")
    monkeypatch.setenv(api.CARBONQL_RELEASE_ENV, str(release))
    project = _project(tmp_path, llm_client=StubLLMClient(PROJECT_MATERIAL_TOTAL))
    api.connect_carbonql_executor(project)
    component = project.carbonql.perspective_accounts()["product"][0]["id"]

    api._projects[project.project_id] = project
    try:
        response = asyncio.run(
            api.get_project_component_subgraph(
                project.project_id, component, max_nodes=80, expand_shared=False
            )
        )
    finally:
        api._projects.pop(project.project_id, None)

    assert response["status"] == "success"
    assert response["subgraph"]["nodes"]


def test_gate_stays_closed_and_names_the_reason_without_a_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dm2c_api_server as api
    from fastapi import HTTPException

    monkeypatch.delenv(api.CARBONQL_RELEASE_ENV, raising=False)
    project = _project(tmp_path, llm_client=StubLLMClient(PROJECT_MATERIAL_TOTAL))

    api.connect_carbonql_executor(project)

    assert project.canonical_query_available is False
    assert project.canonical_query_status == "canonical_release_unavailable"
    with pytest.raises(HTTPException) as exc_info:
        api._require_canonical_query(project)
    assert exc_info.value.status_code == 409


def test_unusable_release_is_reported_rather_than_silently_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dm2c_api_server as api

    empty = tmp_path / "not-a-release"
    empty.mkdir()
    monkeypatch.setenv(api.CARBONQL_RELEASE_ENV, str(empty))
    project = _project(tmp_path, llm_client=StubLLMClient(PROJECT_MATERIAL_TOTAL))

    api.connect_carbonql_executor(project)

    assert project.canonical_query_available is False
    assert project.canonical_query_status.startswith("canonical_release_rejected")
    assert project.carbonql is None
