"""Drive the real /ask route end to end and save the payloads for section 4.1.2.

The walkthrough goes through the FastAPI application rather than calling the
service directly, so the artifact is evidence that the endpoint answers, not
only that the library does. Every number it records must reconcile with the
frozen release's fact package; that is the point of pinning the release here
rather than letting the upload decide.

The question set is chosen to exercise the four behaviours the section claims:
a project total, a ranking, two different grouping perspectives, and a filter
the account cannot satisfy. The last one must come back without a number.

Usage:
    python scripts/capture_qa_walkthrough.py [--out DIR] [--release DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_RELEASE = (
    REPO_ROOT
    / "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)
DEFAULT_IFC = REPO_ROOT / "inputs/case_study/typed_completed_20260718.ifc"
WORKBOOK = (
    REPO_ROOT
    / "outputs/research_experiments/case_study_facts_20260715/co2e_complete_factors"
    / "Embodied_Carbon_Coefficients_Hong_Kong_CO2e_Complete_2026-07-15.xlsx"
)
DEFAULT_OUT = REPO_ROOT / "outputs/research_experiments/qa_walkthrough"

QUESTIONS = (
    "How much embodied carbon does the project carry?",
    "Which component carries the most carbon?",
    "Break the project's embodied carbon down by material.",
    "How much process carbon is there, by production stage?",
    "Show process carbon for unobtainium.",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--ifc", type=Path, default=DEFAULT_IFC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    for path in (args.release, args.ifc, WORKBOOK):
        if not path.exists():
            print(f"missing input: {path}", file=sys.stderr)
            return 1

    # Both are read when dm2c_api_server is imported, so they must be set first.
    # The fixture carries synthetic factory energy, which the server refuses by
    # default; the walkthrough has to say so out loud rather than work around it.
    os.environ["DM2C_CARBONQL_RELEASE"] = str(args.release.resolve())
    os.environ["DM2C_CARBONQL_ALLOW_SYNTHETIC"] = "1"

    from fastapi.testclient import TestClient

    import dm2c_api_server as api

    client = TestClient(api.app)

    print(f"release: {args.release.name}")
    print("creating project ...")
    started = time.perf_counter()
    with args.ifc.open("rb") as ifc_handle, WORKBOOK.open("rb") as xlsx_handle:
        response = client.post(
            "/api/projects",
            files=[
                ("design", (args.ifc.name, ifc_handle, "application/octet-stream")),
                (
                    "carbon_factors",
                    (
                        WORKBOOK.name,
                        xlsx_handle,
                        "application/vnd.openxmlformats-officedocument."
                        "spreadsheetml.sheet",
                    ),
                ),
            ],
        )
    print(f"  status {response.status_code} in {time.perf_counter() - started:.1f}s")
    if response.status_code != 200:
        print(response.text[:4000], file=sys.stderr)
        return 1

    created = response.json()
    project_id = created["project_id"]
    print(f"  project_id: {project_id}")

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "00_project_created.json").write_text(
        json.dumps(created, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    index = []
    for number, question in enumerate(QUESTIONS, start=1):
        print(f"asking [{number}] {question}")
        started = time.perf_counter()
        reply = client.post(
            f"/api/projects/{project_id}/ask", json={"question": question}
        )
        elapsed = time.perf_counter() - started
        print(f"   status {reply.status_code} in {elapsed:.1f}s")
        if reply.status_code != 200:
            print(reply.text[:2000], file=sys.stderr)
            return 1

        body = reply.json()
        payload = body.get("payload", {})
        carbon = payload.get("carbonAnswer", {})
        dependence = payload.get("sourceDependence", {})
        print(
            f"   queryStatus={carbon.get('queryStatus')} "
            f"total={carbon.get('totalKgCO2e')} "
            f"rows={len(carbon.get('rows', []))} "
            f"sourceDependence={dependence.get('decisionClass') or dependence.get('evaluated')}"
        )
        name = f"{number:02d}_ask.json"
        (args.out / name).write_text(
            json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        index.append(
            {
                "file": name,
                "question": question,
                "queryStatus": carbon.get("queryStatus"),
                "answered": carbon.get("answered"),
                "totalKgCO2e": carbon.get("totalKgCO2e"),
                "rowCount": len(carbon.get("rows", [])),
                "sourceDependenceEvaluated": dependence.get("evaluated"),
                "decisionClass": dependence.get("decisionClass"),
                "sourceDependenceLatencyMs": dependence.get("latencyMs"),
                "compileLatencyMs": carbon.get("compileLatencyMs"),
                "executeLatencyMs": carbon.get("executeLatencyMs"),
                "llmCallCount": carbon.get("llmCallCount"),
                "roundTripS": round(elapsed, 3),
            }
        )

    (args.out / "index.json").write_text(
        json.dumps(
            {
                "releaseId": args.release.name,
                "projectId": project_id,
                "model": created.get("payload", {}).get("model"),
                "asks": index,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
