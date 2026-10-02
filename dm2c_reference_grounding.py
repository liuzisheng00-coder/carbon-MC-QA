"""Deterministic BIM entity grounding for component-level carbon questions."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


TYPE_ALIASES = {
    "beam": ("beam", "girder", "ifcbeam", "梁", "钢梁"),
    "column": ("column", "post", "ifccolumn", "柱"),
    "wall": ("wall", "wall panel", "ifcwall", "墙", "墙板", "外墙板"),
    "slab": ("slab", "floor slab", "ifcslab", "楼板", "板"),
    "panel": ("panel", "cassette", "面板", "板件"),
    "door": ("door", "ifcdoor", "门"),
    "window": ("window", "ifcwindow", "窗"),
    "module": ("module", "modular unit", "modularunit", "模块"),
}

MATERIAL_ALIASES = {
    "steel": ("steel", "s355", "stainless", "钢", "钢材"),
    "concrete": ("concrete", "cement", "混凝土", "水泥"),
    "timber": ("timber", "wood", "木", "木材"),
    "aluminium": ("aluminium", "aluminum", "铝"),
    "insulation": ("insulation", "mineral wool", "保温", "岩棉"),
}

SET_TERMS = (
    "all ",
    "every ",
    "list ",
    "rank ",
    "which ",
    "highest",
    "top ",
    "compare ",
    "所有",
    "全部",
    "哪些",
    "排名",
    "最高",
)

DEICTIC_TERMS = (
    "this component",
    "that component",
    "this item",
    "that item",
    "selected component",
    "selected item",
    "chosen component",
    "这个构件",
    "该构件",
    "选中的构件",
    "刚才选中的",
)

UNSUPPORTED_SPATIAL_TERMS = (
    "next to",
    "adjacent to",
    "beside",
    "near the",
    "to the left of",
    "to the right of",
    "旁边",
    "相邻",
    "附近",
    "左边",
    "右边",
)

PROJECT_ONLY_TERMS = (
    "project total",
    "overall carbon",
    "total carbon for the project",
    "project's known carbon",
    "项目总",
    "整个项目",
    "全项目",
)

STOP_TOKENS = {
    "a",
    "an",
    "and",
    "carbon",
    "co2e",
    "component",
    "emission",
    "emissions",
    "for",
    "give",
    "is",
    "item",
    "known",
    "me",
    "of",
    "please",
    "show",
    "the",
    "this",
    "that",
    "what",
}


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = re.sub(r"[^\w\-$]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    return normalize_text(phrase) in text


def _contains_set_cue(text: str, phrase: str) -> bool:
    normalized = normalize_text(phrase)
    if not normalized:
        return False
    if not normalized.isascii():
        return normalized in text
    pattern = r"\s+".join(re.escape(token) for token in normalized.split())
    return re.search(rf"(?<!\w){pattern}(?!\w)", text) is not None


def _english_plural(phrase: str) -> str:
    words = phrase.split()
    final = words[-1]
    if final.endswith(("s", "x", "z", "ch", "sh")):
        words[-1] = f"{final}es"
    elif len(final) > 1 and final.endswith("y") and final[-2] not in "aeiou":
        words[-1] = f"{final[:-1]}ies"
    else:
        words[-1] = f"{final}s"
    return " ".join(words)


def _has_plural_type_reference(text: str) -> bool:
    aliases = {
        normalize_text(alias)
        for canonical, terms in TYPE_ALIASES.items()
        for alias in (canonical, *terms)
    }
    return any(
        alias
        and alias.isascii()
        and re.fullmatch(r"[a-z ]+", alias) is not None
        and _contains_set_cue(text, _english_plural(alias))
        for alias in aliases
    )


@dataclass
class ReferenceSelector:
    expected_cardinality: str = "none"
    type_terms: List[str] = field(default_factory=list)
    name_terms: List[str] = field(default_factory=list)
    material_terms: List[str] = field(default_factory=list)
    storey_terms: List[str] = field(default_factory=list)
    production_terms: List[str] = field(default_factory=list)
    deictic: bool = False
    unsupported_relations: List[str] = field(default_factory=list)
    required: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> "ReferenceSelector":
        data = data or {}
        selector = cls(
            expected_cardinality=str(
                data.get("expected_cardinality")
                or data.get("expectedCardinality")
                or "none"
            ),
            type_terms=_text_list(data.get("type_terms") or data.get("typeTerms")),
            name_terms=_text_list(data.get("name_terms") or data.get("nameTerms")),
            material_terms=_text_list(data.get("material_terms") or data.get("materialTerms")),
            storey_terms=_text_list(data.get("storey_terms") or data.get("storeyTerms")),
            production_terms=_text_list(data.get("production_terms") or data.get("productionTerms")),
            deictic=bool(data.get("deictic", False)),
            unsupported_relations=_text_list(
                data.get("unsupported_relations") or data.get("unsupportedRelations")
            ),
            required=bool(data.get("required", False)),
        )
        if not selector.required:
            selector.required = bool(
                selector.deictic
                or selector.type_terms
                or selector.name_terms
                or selector.material_terms
                or selector.storey_terms
                or selector.production_terms
            )
        if selector.required and selector.expected_cardinality == "none":
            selector.expected_cardinality = "singleton"
        return selector


@dataclass
class GroundingCandidate:
    id: str
    name: str
    ifc_class: str = ""
    material: str = ""
    storey: str = ""
    score: float = 0.0
    matched_constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "globalId": self.id,
            "name": self.name,
            "ifcClass": self.ifc_class,
            "material": self.material,
            "storey": self.storey,
            "score": self.score,
            "matchedConstraints": list(self.matched_constraints),
        }


@dataclass
class GroundingDecision:
    decision: str
    selector: ReferenceSelector
    candidates: List[GroundingCandidate] = field(default_factory=list)
    committed_ids: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selector": self.selector.to_dict(),
            "decision": self.decision,
            "committedIds": list(self.committed_ids),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "reason": self.reason,
        }


def _text_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, Iterable):
        return []
    out: List[str] = []
    for item in value:
        text = normalize_text(item)
        if text and text not in out:
            out.append(text)
    return out


def parse_reference_selector(question: str) -> ReferenceSelector:
    text = normalize_text(question)
    if any(_contains_phrase(text, phrase) for phrase in PROJECT_ONLY_TERMS):
        return ReferenceSelector(expected_cardinality="none", required=False)

    type_terms = [
        canonical
        for canonical, aliases in TYPE_ALIASES.items()
        if any(_contains_phrase(text, alias) for alias in aliases)
    ]
    material_terms = [
        canonical
        for canonical, aliases in MATERIAL_ALIASES.items()
        if any(_contains_phrase(text, alias) for alias in aliases)
    ]
    deictic = any(_contains_phrase(text, phrase) for phrase in DEICTIC_TERMS)
    unsupported = [
        normalize_text(phrase)
        for phrase in UNSUPPORTED_SPATIAL_TERMS
        if _contains_phrase(text, phrase)
    ]
    storey_terms = re.findall(r"\b(?:gf|\d+f|level\s*\d+|storey\s*\d+)\b", text)

    known_words = set(STOP_TOKENS)
    for aliases in TYPE_ALIASES.values():
        known_words.update(normalize_text(alias) for alias in aliases)
    for aliases in MATERIAL_ALIASES.values():
        known_words.update(normalize_text(alias) for alias in aliases)
    name_terms = []
    for token in re.findall(r"[a-z0-9_$-]+", text):
        if token in known_words:
            continue
        if any(char.isdigit() for char in token) and token not in storey_terms:
            name_terms.append(token)

    required = bool(type_terms or material_terms or name_terms or storey_terms or deictic)
    has_set_cue = any(_contains_set_cue(text, term) for term in SET_TERMS)
    cardinality = "set" if has_set_cue or _has_plural_type_reference(text) else "singleton"
    if not required:
        cardinality = "none"
    return ReferenceSelector(
        expected_cardinality=cardinality,
        type_terms=type_terms,
        name_terms=name_terms,
        material_terms=material_terms,
        storey_terms=_text_list(storey_terms),
        deictic=deictic,
        unsupported_relations=unsupported,
        required=required,
    )


def _component(candidate: Mapping[str, Any]) -> Dict[str, str]:
    raw = candidate.get("raw") if isinstance(candidate.get("raw"), Mapping) else {}
    component_id = str(
        candidate.get("id")
        or candidate.get("globalId")
        or candidate.get("componentGlobalId")
        or candidate.get("componentNodeId")
        or ""
    ).strip()
    return {
        "id": component_id,
        "name": str(candidate.get("name") or candidate.get("componentName") or component_id).strip(),
        "ifc_class": str(candidate.get("ifcClass") or "").strip(),
        "material": str(candidate.get("material") or candidate.get("materialText") or "").strip(),
        "storey": str(candidate.get("storey") or raw.get("storey") or "").strip(),
    }


def _matches_canonical(value: str, canonical: str, aliases: Mapping[str, Sequence[str]]) -> bool:
    haystack = normalize_text(value)
    terms = (canonical,) + tuple(aliases.get(canonical, ()))
    return any(normalize_text(term) in haystack for term in terms)


def _score_candidate(component: Mapping[str, str], selector: ReferenceSelector) -> Optional[GroundingCandidate]:
    matched: List[str] = []
    score = 0.0

    if selector.type_terms:
        type_haystack = f"{component['ifc_class']} {component['name']}"
        matched_types = [
            term for term in selector.type_terms if _matches_canonical(type_haystack, term, TYPE_ALIASES)
        ]
        if not matched_types:
            return None
        matched.extend(f"type:{term}" for term in matched_types)
        score += 0.5

    if selector.material_terms:
        matched_materials = [
            term
            for term in selector.material_terms
            if _matches_canonical(component["material"], term, MATERIAL_ALIASES)
        ]
        if not matched_materials:
            return None
        matched.extend(f"material:{term}" for term in matched_materials)
        score += 0.25

    if selector.name_terms:
        name = normalize_text(component["name"])
        if not all(term in name for term in selector.name_terms):
            return None
        matched.extend(f"name:{term}" for term in selector.name_terms)
        score += 0.15

    if selector.storey_terms:
        storey = normalize_text(component["storey"])
        if not all(term in storey for term in selector.storey_terms):
            return None
        matched.extend(f"storey:{term}" for term in selector.storey_terms)
        score += 0.1

    if not matched:
        return None
    return GroundingCandidate(
        id=component["id"],
        name=component["name"],
        ifc_class=component["ifc_class"],
        material=component["material"],
        storey=component["storey"],
        score=round(score, 4),
        matched_constraints=matched,
    )


def resolve_reference(
    question: str,
    components: Sequence[Mapping[str, Any]],
    selected_ids: Sequence[str] = (),
    selector_data: Optional[Mapping[str, Any]] = None,
) -> GroundingDecision:
    selector = (
        ReferenceSelector.from_dict(selector_data)
        if selector_data
        else parse_reference_selector(question)
    )
    normalized_components = [_component(candidate) for candidate in components]
    normalized_components = [candidate for candidate in normalized_components if candidate["id"]]
    by_id = {candidate["id"]: candidate for candidate in normalized_components}

    selected = list(
        dict.fromkeys(
            str(value).strip() for value in selected_ids if str(value).strip()
        )
    )
    if selected:
        if any(component_id not in by_id for component_id in selected):
            return GroundingDecision("unresolved", selector, reason="selected_component_not_found")
        matched_ids = selected
        candidates = [
            GroundingCandidate(
                id=component_id,
                name=by_id[component_id]["name"],
                ifc_class=by_id[component_id]["ifc_class"],
                material=by_id[component_id]["material"],
                storey=by_id[component_id]["storey"],
                score=1.0,
                matched_constraints=["deictic:selected_component"],
            )
            for component_id in matched_ids
        ]
        return GroundingDecision(
            "commit",
            selector,
            candidates=candidates,
            committed_ids=matched_ids,
            reason="selected_component_context",
        )

    if not selector.required:
        return GroundingDecision("not_required", selector, reason="no_component_reference")
    if selector.unsupported_relations:
        return GroundingDecision("unresolved", selector, reason="unsupported_spatial_relation")

    candidates = [
        scored
        for component in normalized_components
        if (scored := _score_candidate(component, selector)) is not None
    ]
    candidates.sort(key=lambda item: (-item.score, item.id))
    if not candidates:
        return GroundingDecision("unresolved", selector, reason="no_matching_component")
    if selector.expected_cardinality == "set":
        return GroundingDecision(
            "commit",
            selector,
            candidates=candidates,
            committed_ids=[candidate.id for candidate in candidates],
            reason="set_reference_resolved",
        )
    if len(candidates) == 1:
        return GroundingDecision(
            "commit",
            selector,
            candidates=candidates,
            committed_ids=[candidates[0].id],
            reason="unique_candidate",
        )
    return GroundingDecision(
        "clarify",
        selector,
        candidates=candidates,
        reason="multiple_matching_components",
    )
