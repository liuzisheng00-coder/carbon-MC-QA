"""Export the E1 independent-verification sample from a canonical-v2 release.

E1 checks that the carbon account is right, not merely self-consistent. A
reviewer takes each sampled record back to the IFC model and the factor
source, establishes the quantity, the factor and the allocation share on their
own, and computes the emission. Those independent values are then compared
against what the account reports.

Two properties of this exporter matter for that claim.

The worksheet does not carry the value the account computed. The earlier
protocol placed the system's answer next to the blank the reviewer had to
fill, which invites anchoring; here the system values go to a separate answer
key that only the scorer reads. What the worksheet does carry is the lineage a
reviewer needs to work independently: the component, the quantity and its
basis, the factor keyword and its published source, and the allocation share.

The sample is stratified and seeded. Every material present in the account
appears at least once, so a factor that is wrong for one material cannot hide
behind the others, and the remaining slots are filled in proportion to record
count under a fixed seed so that the same sample can be regenerated.

Usage:
    python scripts/export_e1_reference_sample.py [--release DIR] [--material-n 30]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_RELEASE = (
    REPO_ROOT
    / "outputs/research_experiments/m2_typed_completed_20260728_partial_allocation_final"
)
DEFAULT_OUT_ROOT = REPO_ROOT / "outputs/research_experiments"
SEED = 7

MATERIAL_COLUMNS = (
    "row_id",
    "component_global_id",
    "component_name",
    "ifc_class",
    "material_name",
    "quantity_value",
    "quantity_unit",
    "quantity_basis",
    "factor_keyword",
    "factor_value",
    "factor_unit",
    "factor_source",
    "allocation_fraction",
    "formula_code",
    "independent_kgCO2e",
    "independent_quantity_value",
    "independent_factor_value",
    "reviewer",
    "notes",
)

ENERGY_COLUMNS = (
    "row_id",
    "carrier_name",
    "production_stage",
    "manufacturing_activity",
    "attribution_mode",
    "quantity_value",
    "quantity_unit",
    "factor_keyword",
    "factor_value",
    "factor_unit",
    "factor_source",
    "attributed_fraction",
    "attributed_component_count",
    "unattributed_fraction",
    "formula_code",
    "independent_kgCO2e",
    "independent_quantity_value",
    "independent_factor_value",
    "reviewer",
    "notes",
)


def _load_graph(release: Path) -> Mapping[str, Any]:
    return json.loads(
        (release / "multigranular_carbon_kg.json").read_text(encoding="utf-8")
    )


def _stage_by_activity(graph: Mapping[str, Any]) -> dict[str, str]:
    """Resolve a stage for each activity, the way the executor does.

    Energy records hang off activities, not stages, so the stage a reviewer
    needs in order to locate the record has to be reached over `hasActivity`.
    An activity claimed by more than one stage is left unresolved rather than
    assigned arbitrarily.
    """
    owners: dict[str, set[str]] = defaultdict(set)
    for edge in graph.get("edges", ()):
        if edge.get("type") != "hasActivity":
            continue
        source, target = str(edge.get("src") or ""), str(edge.get("tgt") or "")
        if source and target:
            owners[target].add(source)
    return {
        activity: next(iter(stages))
        for activity, stages in owners.items()
        if len(stages) == 1
    }


def _prop(nodes: Mapping[str, Mapping[str, Any]], node_id: str | None, key: str) -> str:
    if not node_id:
        return ""
    node = nodes.get(str(node_id))
    if not node:
        return ""
    return str((node.get("props") or {}).get(key, ""))


def _labels(nodes: Mapping[str, Mapping[str, Any]], node_id: str | None) -> tuple[str, ...]:
    node = nodes.get(str(node_id or ""))
    return tuple(str(label) for label in (node or {}).get("labels", ()))


def _first_with_label(
    nodes: Mapping[str, Mapping[str, Any]], ids: Sequence[str], label: str
) -> str:
    for node_id in ids:
        if label in _labels(nodes, node_id):
            return str(node_id)
    return ""


def _stratified_sample(
    by_stratum: Mapping[str, list[Any]], total: int, seed: int
) -> list[Any]:
    """Cover every stratum once, then fill the rest in proportion to size."""
    rng = random.Random(seed)
    chosen: list[Any] = []
    remaining: list[Any] = []
    for stratum in sorted(by_stratum):
        members = sorted(by_stratum[stratum], key=lambda fact: fact.emission_id)
        pick = rng.choice(members)
        chosen.append(pick)
        remaining.extend(member for member in members if member is not pick)
    if len(chosen) >= total:
        return sorted(chosen, key=lambda fact: fact.emission_id)[:total]
    rng.shuffle(remaining)
    chosen.extend(remaining[: total - len(chosen)])
    return sorted(chosen, key=lambda fact: fact.emission_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, default=DEFAULT_RELEASE)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--material-n", type=int, default=30)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    if not args.release.exists():
        print(f"missing release: {args.release}", file=sys.stderr)
        return 1

    from dm2c_canonical_v2_reader import load_canonical_v2_context

    context = load_canonical_v2_context(args.release, allow_synthetic=True)
    graph = _load_graph(args.release)
    nodes = {str(node["id"]): node for node in graph["nodes"]}
    stage_of = _stage_by_activity(graph)
    release_id = str(context.manifest.get("releaseId") or args.release.name)
    out_dir = args.out or (DEFAULT_OUT_ROOT / f"e1_sample_{release_id}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # An emission can be split across many components. What a reviewer must
    # check is the share the account attributed in total, not the share of
    # whichever component happens to come first.
    # A record attributed in `direct` mode carries no explicit weight because the
    # whole of it belongs to one component; treating a missing weight as zero
    # would understate those records to nothing.
    attributed_total: dict[str, float] = defaultdict(float)
    component_of: dict[str, str] = {}
    contribution_count: dict[str, int] = defaultdict(int)
    for contribution in context.product_contributions:
        weight = contribution.normalized_weight
        attributed_total[contribution.emission_id] += (
            1.0 if weight is None else float(weight)
        )
        contribution_count[contribution.emission_id] += 1
        component_of.setdefault(contribution.emission_id, contribution.component_id)

    material_facts = [fact for fact in context.emissions if fact.material_id]
    energy_facts = [fact for fact in context.emissions if fact.carrier_id]
    print(f"release {release_id}: {len(material_facts)} material, {len(energy_facts)} energy")

    by_material: dict[str, list[Any]] = defaultdict(list)
    for fact in material_facts:
        by_material[_prop(nodes, fact.material_id, "name") or "<Unnamed>"].append(fact)
    print(f"material strata: {len(by_material)}")

    sample = _stratified_sample(by_material, args.material_n, args.seed)
    print(f"sampled {len(sample)} material records")

    answer_key: dict[str, dict[str, Any]] = {}
    material_rows: list[dict[str, str]] = []
    for index, fact in enumerate(sample, start=1):
        row_id = f"M{index:03d}"
        component_id = component_of.get(fact.emission_id, "")
        quantity_basis = _prop(nodes, fact.quantity_id, "basis") or _prop(
            nodes, fact.quantity_id, "quantityBasis"
        )
        material_rows.append(
            {
                "row_id": row_id,
                "component_global_id": _prop(nodes, component_id, "globalId"),
                "component_name": _prop(nodes, component_id, "name"),
                "ifc_class": _prop(nodes, component_id, "ifcClass"),
                "material_name": _prop(nodes, fact.material_id, "name") or "<Unnamed>",
                "quantity_value": f"{fact.quantity_value:.6f}",
                "quantity_unit": fact.quantity_unit,
                "quantity_basis": quantity_basis,
                "factor_keyword": fact.factor_keyword or "",
                "factor_value": f"{fact.factor_value:.6f}",
                "factor_unit": fact.factor_unit,
                "factor_source": fact.factor_source or "",
                "allocation_fraction": (
                    f"{attributed_total[fact.emission_id]:.6f}"
                    if fact.emission_id in attributed_total
                    else "1.000000"
                ),
                "formula_code": fact.formula_code,
                "independent_kgCO2e": "",
                "independent_quantity_value": "",
                "independent_factor_value": "",
                "reviewer": "",
                "notes": "",
            }
        )
        answer_key[row_id] = {
            "emission_id": fact.emission_id,
            "kind": "material",
            "system_kgCO2e": fact.emission_value,
        }

    energy_rows: list[dict[str, str]] = []
    for index, fact in enumerate(
        sorted(energy_facts, key=lambda item: item.emission_id), start=1
    ):
        row_id = f"E{index:03d}"
        activity_id = _first_with_label(nodes, fact.process_ids, "ManufacturingActivity")
        stage_id = _first_with_label(
            nodes, fact.process_ids, "ProductionStage"
        ) or stage_of.get(activity_id, "")
        attributed = attributed_total.get(fact.emission_id, 0.0)
        unattributed = _prop(nodes, fact.consumption_id, "unattributedFraction")
        energy_rows.append(
            {
                "row_id": row_id,
                "carrier_name": _prop(nodes, fact.carrier_id, "name"),
                "production_stage": _prop(nodes, stage_id, "stageName"),
                "manufacturing_activity": _prop(nodes, activity_id, "activityName"),
                "attribution_mode": _prop(nodes, fact.consumption_id, "attributionMode"),
                "quantity_value": f"{fact.quantity_value:.6f}",
                "quantity_unit": fact.quantity_unit,
                "factor_keyword": fact.factor_keyword or "",
                "factor_value": f"{fact.factor_value:.6f}",
                "factor_unit": fact.factor_unit,
                "factor_source": fact.factor_source or "",
                "attributed_fraction": f"{attributed:.6f}",
                "attributed_component_count": str(
                    contribution_count.get(fact.emission_id, 0)
                ),
                "unattributed_fraction": (
                    f"{float(unattributed):.6f}"
                    if unattributed
                    else f"{1.0 - attributed:.6f}"
                ),
                "formula_code": fact.formula_code,
                "independent_kgCO2e": "",
                "independent_quantity_value": "",
                "independent_factor_value": "",
                "reviewer": "",
                "notes": "",
            }
        )
        answer_key[row_id] = {
            "emission_id": fact.emission_id,
            "kind": "energy",
            "system_kgCO2e": fact.emission_value,
        }

    def _write(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            writer.writerows(rows)

    _write(out_dir / "e1_material_worksheet.csv", MATERIAL_COLUMNS, material_rows)
    _write(out_dir / "e1_energy_worksheet.csv", ENERGY_COLUMNS, energy_rows)

    # The key is written apart from the worksheet so that a reviewer filling the
    # worksheet cannot see the value they are supposed to arrive at.
    (out_dir / "e1_answer_key.json").write_text(
        json.dumps(answer_key, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "e1_sample_manifest.json").write_text(
        json.dumps(
            {
                "releaseId": release_id,
                "releaseDir": str(args.release),
                "seed": args.seed,
                "strata": "material name, one record per material then proportional fill",
                "materialSampleSize": len(material_rows),
                "materialPopulation": len(material_facts),
                "materialStrataCount": len(by_material),
                "energySampleSize": len(energy_rows),
                "energyPopulation": len(energy_facts),
                "protocol": (
                    "Independent recomputation from the IFC model and the published "
                    "factor source. Spreadsheet formulas referencing the exported "
                    "quantity and factor columns do not count as independent."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out_dir}")
    print(f"  material worksheet: {len(material_rows)} rows to fill")
    print(f"  energy worksheet:   {len(energy_rows)} rows to fill")
    print(f"  answer key held separately: {len(answer_key)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
