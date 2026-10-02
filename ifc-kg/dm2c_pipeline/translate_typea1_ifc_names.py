"""Create an English-named copy of the Type A1 IFC model.

Only user-facing component/product names (including their type definitions and
opening names) and IfcMaterial strings are translated.  Geometry, GlobalIds,
relationships, quantities, and property sets are copied verbatim.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


# Longest/multi-word terms come first to prevent partial replacements from
# obscuring the intended construction term.
TRANSLATIONS = {
    "AR_FST-H-08_外墙板门窗套_L145.6": "AR_FST-H-08_Exterior Wall Panel Door/Window Trim_L145.6",
    "AR_FST-M-10_200外墙板": "AR_FST-M-10_200 Exterior Wall Panel",
    "AR_FST-M-33_顶外墙板": "AR_FST-M-33_Top Exterior Wall Panel",
    "AR_FST-103_底外墙板": "AR_FST-103_Bottom Exterior Wall Panel",
    "ST_L50X50X3_ER068(横-带角度切)": "ST_L50X50X3_ER068 (Horizontal - Angle Cut)",
    "125×80×10mm 空调架紧固角码": "125x80x10mm Air-Conditioner Bracket Fixing Angle",
    "50X50X50X2mm U型槽钢副框": "50x50x50x2mm U-Channel Steel Secondary Frame",
    "50x50x50x2 U型槽钢副框柱": "50x50x50x2 U-Channel Steel Secondary Frame Column",
    "50X50X50X2mm U型槽钢副框": "50x50x50x2mm U-Channel Steel Secondary Frame",
    "100x50x3 门窗副框柱": "100x50x3 Door/Window Secondary Frame Column",
    "M36X80普通六角头螺母": "M36x80 Standard Hex-Head Nut",
    "新型材40_门窗套扣盖": "New Profile 40_Door/Window Trim Snap Cover",
    "外墙板底 FST-80": "Bottom Exterior Wall Panel FST-80",
    "70x50x5mm RHS 钢通斜支撑": "70x50x5mm RHS Steel Purlin Diagonal Brace",
    "100x100x5mm RHS 钢通": "100x100x5mm RHS Steel Purlin",
    "100x50x5mm RHS 钢通": "100x50x5mm RHS Steel Purlin",
    "100x50x3mm RHS 钢通": "100x50x3mm RHS Steel Purlin",
    "150x100x6mm RHS 钢通": "150x100x6mm RHS Steel Purlin",
    "200x100x14 钢通": "200x100x14 Steel Purlin",
    "100x100x4/5 钢通": "100x100x4/5 Steel Purlin",
    "内装门龙骨": "Interior Door Stud",
    "内装龙骨": "Interior Stud",
    "厕所瓷砖地板": "Toilet Tile Floor",
    "推拉门-单扇-1": "Sliding Door - Single Leaf - 1",
    "基本墙": "Basic Wall",
    "厕所内贴面": "Toilet Interior Finish",
    "硅钙板": "Calcium Silicate Board",
    "防火板": "Fire-Resistant Board",
    "楼板": "Floor Slab",
    "岩棉": "Mineral Wool",
    "常规": "Generic",
    "管井门": "Shaft Door",
    "包钢-管井": "Steel Cladding - Shaft",
    "包钢": "Steel Cladding",
    "门槛石": "Threshold Stone",
    "双开窗": "Double Casement Window",
    "固定窗": "Fixed Window",
    "外墙板": "Exterior Wall Panel",
    "底": "Bottom",
    "顶": "Top",
    "横": "Horizontal",
    "竖": "Vertical",
    "带角度切": "Angle Cut",
    "扁管": "Rectangular Tube",
    "U型槽钢": "U-Channel Steel",
    "钢通": "Steel Purlin",
    "斜支撑": "Diagonal Brace",
    "门窗套": "Door/Window Trim",
    "门窗": "Door/Window",
    "副框": "Secondary Frame",
    "空调架": "Air-Conditioner Bracket",
    "紧固角码": "Fixing Angle",
    "普通六角头螺母": "Standard Hex-Head Nut",
    "地胶": "Vinyl Flooring",
    "族": "Family",
    "二层百叶": "Second-Floor Louvers",
    "其他": "Other",
    "基板": "Substrate",
    "基础砂浆": "Base Mortar",
    "木材": "Timber",
    "混凝土，现场浇注灰色": "Concrete, Cast-in-Place Gray",
    "混凝土": "Concrete",
    "玻璃-门": "Door Glass",
    "窗框": "Window Frame",
    "系统": "System",
    "金属 - 钢": "Metal - Steel",
    "金属 - 铝 - 白色": "Metal - Aluminum - White",
    "金属": "Metal",
    "防火门": "Fire-Rated Door",
    # These compensate for the earlier generic \"楼板\" substitution when
    # translating Revit's default material name.
    "默认Floor Slab": "Default Floor Slab",
    "默认墙": "Default Wall",
    "默认楼板": "Default Floor Slab",
}

PRODUCT_CLASSES = {
    "IFCBEAM", "IFCCOLUMN", "IFCSLAB", "IFCWALL", "IFCDOOR", "IFCWINDOW",
    "IFCBUILDINGELEMENTPROXY", "IFCOPENINGELEMENT",
}

ENTITY_RE = re.compile(r"#\d+=(IFC[A-Z0-9_]+)\((.*?)\);", re.DOTALL)
STEP_X2_RE = re.compile(r"\\X2\\([0-9A-Fa-f]+)\\X0\\")
STEP_STRING_RE = re.compile(r"'((?:''|[^'])*)'")


def decode_step(value: str) -> str:
    return STEP_X2_RE.sub(lambda m: bytes.fromhex(m.group(1)).decode("utf-16-be"), value).replace("''", "'")


def encode_step(value: str) -> str:
    """Encode a Unicode value as a valid ISO-10303-21 string payload."""
    chunks: list[str] = []
    ascii_buffer: list[str] = []

    def flush_ascii() -> None:
        if ascii_buffer:
            chunks.append("".join(ascii_buffer))
            ascii_buffer.clear()

    for char in value:
        if ord(char) < 128:
            ascii_buffer.append("''" if char == "'" else char)
        else:
            flush_ascii()
            chunks.append("\\X2\\" + char.encode("utf-16-be").hex().upper() + "\\X0\\")
    flush_ascii()
    return "".join(chunks)


def contains_chinese(value: str) -> bool:
    return any(0x4E00 <= ord(char) <= 0x9FFF for char in value)


def translate_value(value: str, replacements: Counter[str]) -> str:
    translated = value
    for chinese, english in TRANSLATIONS.items():
        if chinese in translated:
            count = translated.count(chinese)
            translated = translated.replace(chinese, english)
            replacements[chinese] += count
    return translated


def translate_entity(match: re.Match[str], replacements: Counter[str], untranslated: set[str]) -> str:
    entity_class, arguments = match.groups()
    is_product_or_type = entity_class in PRODUCT_CLASSES or entity_class.endswith("TYPE")
    if entity_class != "IFCMATERIAL" and not is_product_or_type:
        return match.group(0)

    def replace_string(string_match: re.Match[str]) -> str:
        original = decode_step(string_match.group(1))
        translated = translate_value(original, replacements)
        if contains_chinese(translated):
            untranslated.add(translated)
        return "'" + encode_step(translated) + "'"

    return f"{match.group(0).split('(', 1)[0]}(" + STEP_STRING_RE.sub(replace_string, arguments) + ");"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("typea1_full.ifc"))
    parser.add_argument("--output", type=Path, default=Path("typea1_full_en.ifc"))
    parser.add_argument("--report", type=Path, default=Path("typea1_full_en_translation_report.json"))
    args = parser.parse_args()

    source = args.input.read_text(encoding="utf-8")
    replacements: Counter[str] = Counter()
    untranslated: set[str] = set()
    translated = ENTITY_RE.sub(lambda m: translate_entity(m, replacements, untranslated), source)

    if untranslated:
        examples = "\n".join(sorted(untranslated)[:20])
        raise ValueError(f"Untranslated Chinese text remains in targeted names:\n{examples}")
    if source.count("#") != translated.count("#"):
        raise ValueError("Entity count changed; refusing to write output.")

    args.output.write_text(translated, encoding="utf-8", newline="\n")
    report = {
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "translated_terms": dict(replacements),
        "replacement_count": sum(replacements.values()),
        "targeted_chinese_remaining": 0,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
