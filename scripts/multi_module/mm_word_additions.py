# -*- coding: utf-8 -*-
"""Stand-alone additions document for reviewer comments 1 to 4 (XL10 to XL13).

Red runs are new manuscript text, grey bracketed notes (Chinese) give the insertion point
and provenance and are not part of the manuscript. The same text is applied to the
manuscript by mm_apply_to_manuscript.py and rendered as a stand-alone Appendix C by
mm_appendix_doc.py; all three read mm_content.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import ROOT  # noqa: E402
from mm_content import build_content  # noqa: E402
from mm_docx import BLACK, GREY, RED, add_math_runs, fill_table, style_run  # noqa: E402

OUT = ROOT / "稿件增补_XL10-13_三模块实验_v2.docx"


def para(doc, text, color=RED, bold=False, size=None):
    p = doc.add_paragraph()
    style_run(p.add_run(text), color=color, bold=bold, size=size)
    return p


def note(doc, text):
    return para(doc, f"[{text}]", color=GREY)


def heading(doc, text):
    return para(doc, text, color=BLACK, bold=True)


def math_para(doc, text):
    p = doc.add_paragraph()
    add_math_runs(p, text, color=RED)
    return p


def equation(doc, markup, number):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_math_runs(p, markup, color=RED)
    style_run(p.add_run(f"        ({number})"), color=RED)
    return p


def table(doc, tbl):
    para(doc, tbl.caption)
    fill_table(doc.add_table(rows=1, cols=len(tbl.headers)), tbl.headers, tbl.rows, color=RED, size=9)
    if tbl.note:
        para(doc, tbl.note, size=9)
    else:
        doc.add_paragraph()


def main() -> None:
    C = build_content()
    L = C.labels
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(11)

    para(doc, "Manuscript additions for reviewer comments 1 to 4 (XL10, XL11, XL12, XL13)", color=BLACK, bold=True, size=14)
    note(doc, f"v2，2026-09-29。红字为新增或替换的稿件正文，灰色方括号为插入位置与说明，不属于稿件。正文只保留方法（3.1.3）、案例描述（4.1.1、4.1.2）、三模块账目（4.1.3）和指向附录的结果句；验证、情景、被拒记录、数据质量、留出集完整结果和开销表在 {L.app}。对账两式标为 Equation {L.eq_a}、{L.eq_b}，避免与稿件现有 Equation 9、10 冲突；建筑级账目为 Equation {L.eq_bldg}；新表编号 {L.tab_graph}、{L.tab_acc}，原 Table 2 顺延为 {L.old_table2_new}；图暂为 {L.fig}。同一内容已直接套用到原稿：原文修改稿_XL10-13_20260929.docx；附录单独成文：附录C_多模块验证与开销_20260929.docx。")
    note(doc, "四条意见与落点：意见 1 → 4.1.1、4.1.2、4.1.3、4.3.5、5.2、C.2、C.6；意见 2 → 3.1.3、4.1.1、4.1.3、Figure X、C.1；意见 3 → 4.1.2、4.3.5、5.1、5.2、C.7；意见 4 → 4.1.3 末句、5.1、5.2、C.3–C.5。")

    heading(doc, "3.1.3 Carbon emission calculation models across multiple perspectives")
    note(doc, "意见 2。插入在解释 Equation 3 和 4 的段落之后。请用公式编辑器重排两式。")
    para(doc, C.sec313["para1"])
    equation(doc, C.sec313["eq_a"], L.eq_a)
    equation(doc, C.sec313["eq_b"], L.eq_b)
    math_para(doc, C.sec313["para2"])

    heading(doc, "4.1.1 Case background and data sources")
    note(doc, f"意见 1、2。替换原句 “{C.sec411['old_focus'][:60]}…”。原句的自重 5–6 吨与图中材料质量 9.2 吨口径不一致，已删，请核对。")
    para(doc, C.sec411["replace_focus"])
    note(doc, f"替换原句 “{C.sec411['old_objects']}”")
    para(doc, C.sec411["replace_objects"])
    note(doc, "追加在工厂能耗数据段末尾。")
    para(doc, C.sec411["append_factory"])

    heading(doc, "4.1.2 Knowledge graph construction and quality")
    note(doc, "意见 1、3。插入在 Table 1 之后、IFC 解析对比段之前。")
    para(doc, C.sec412["para"])
    table(doc, C.sec412["table"])

    heading(doc, C.sec413["heading"])
    note(doc, f"意见 1、2、4。新增小节，位于 4.1.2 之后。{L.fig} 见 修改prompt_FigX_过程归因敏感性图.md 与 outputs/paper_figures/process_attribution_20260929/。")
    para(doc, C.sec413["para"])
    table(doc, C.sec413["table"])
    note(doc, f"{L.fig} 插在此处。")
    para(doc, C.figure_caption)
    para(doc, C.sec413["fig_para"])
    para(doc, C.sec413["appendix_para"])

    heading(doc, C.sec435["heading"])
    note(doc, f"意见 1、3。新增短小节，位于 4.3.4 之后。完整结果在 {L.app}.6、{L.app}.7。")
    para(doc, C.sec435["para"])

    heading(doc, "5.1 Discussion")
    note(doc, "意见 3。追加在 5.1 末尾。第一段来自压缩修改稿 5.1 的红字段落，首句按三模块案例改写；若已套用压缩稿，只需替换首句。模块数量 N_A、N_B、N_D 待项目提供。")
    para(doc, C.sec51["bridge"])
    math_para(doc, C.sec51["eq_intro"])
    equation(doc, C.sec51["eq"], L.eq_bldg)
    para(doc, C.sec51["eq_after"])
    note(doc, "意见 4。追加为 5.1 的最后一段。")
    para(doc, C.sec51["uncertainty"])

    heading(doc, "5.2 Limitations")
    note(doc, "意见 1 到 4。替换原局限段。")
    para(doc, C.sec52["para"])

    heading(doc, "Abstract and Section 6, one sentence each")
    note(doc, "摘要中 “The multi-perspective carbon question answering system is validated in a case study of a volumetric module.” 改为下句；结论第二段末尾追加第二句。")
    para(doc, C.abstract)
    para(doc, C.conclusion)

    doc.add_page_break()
    heading(doc, C.appendix["title"])
    note(doc, "新增附录，位于 Appendix A（基准构建与实验设置）和 Appendix B（CarbonQL 规范）之后。单独成文见 附录C_多模块验证与开销_20260929.docx。")
    para(doc, C.appendix["intro"])
    for section in C.appendix["sections"]:
        heading(doc, section["heading"])
        for block in section["blocks"]:
            if block[0] == "para":
                para(doc, block[1])
            else:
                table(doc, block[1])

    try:
        doc.save(OUT)
        print("written:", OUT)
    except PermissionError:
        alt = OUT.with_name(OUT.stem + "_regen.docx")
        doc.save(alt)
        print(f"{OUT.name} is open in Word; written:", alt)


if __name__ == "__main__":
    main()
