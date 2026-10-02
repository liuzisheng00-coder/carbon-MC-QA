from dm2c_reference_grounding import resolve_reference


def test_grounding_uses_explicit_ifc_class_and_emits_it() -> None:
    decision = resolve_reference(
        "all beams",
        [
            {"id": "component:c1", "name": "Beam one", "ifcClass": "IfcBeam"},
            {"id": "component:c2", "name": "Column one", "ifcClass": "IfcColumn"},
        ],
    )
    assert decision.committed_ids == ["component:c1"]
    payload = decision.to_dict()
    assert payload["candidates"][0]["ifcClass"] == "IfcBeam"
    assert "ifcType" not in repr(payload)


def test_grounding_does_not_fallback_to_generic_type_or_fuzzy_material_family() -> None:
    decision = resolve_reference(
        "all beams",
        [{"id": "component:c1", "name": "Member", "type": "IfcBeam", "materialFamily": "steel"}],
    )
    assert decision.decision == "unresolved"


def test_selected_ids_deduplicate_before_grounding_commit() -> None:
    decision = resolve_reference(
        "this component",
        [{"id": "component:c1", "name": "Member", "ifcClass": "IfcBeam"}],
        selected_ids=["component:c1", "component:c1"],
    )
    assert decision.decision == "commit"
    assert decision.committed_ids == ["component:c1"]
    assert [candidate.id for candidate in decision.candidates] == ["component:c1"]


def test_selected_ids_fail_closed_when_any_id_is_unknown() -> None:
    decision = resolve_reference(
        "these components",
        [{"id": "component:c1", "name": "Member", "ifcClass": "IfcBeam"}],
        selected_ids=["component:c1", "component:missing"],
    )
    assert decision.decision == "unresolved"
    assert decision.committed_ids == []
    assert decision.candidates == []
    assert decision.reason == "selected_component_not_found"
