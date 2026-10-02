# -*- coding: utf-8 -*-
"""Apply the XL10 to XL13 additions to the reviewed manuscript.

Input : manuscript_rev by yue.docx (reviewer comments preserved)
Output: 原文修改稿_XL10-13_20260929.docx

New or replaced text is red. Every change carries a Word comment (author "Cursor Agent")
that states what was replaced or inserted and where the numbers come from, so nothing
else is added to the manuscript body. Appendix C itself is a separate document
(mm_appendix_doc.py); the manuscript refers to it.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mm_common import ROOT  # noqa: E402
from mm_content import FIGURE_PNG, build_content  # noqa: E402
from mm_docx import RED, add_math_runs, paragraph_after, style_run, table_after  # noqa: E402

SRC = ROOT / "manuscript_rev by yue.docx"
OUT = ROOT / "原文修改稿_XL10-13_20260929.docx"
AUTHOR, INITIALS = "Cursor Agent", "CA"
DATA_NOTE = "数据来源 outputs/research_experiments/multi_module_20260928/MULTI_MODULE_REPORT.md；脚本 scripts/multi_module/mm_apply_to_manuscript.py 可重新生成。"


class Editor:
    def __init__(self, doc):
        self.doc = doc
        self.body_tmpl = self.find(startswith="The knowledge graph construction pipeline integrated")
        self.heading_tmpl = self.find(startswith="4.1.2 Knowledge graph construction and quality")
        self.caption_tmpl = self.find(startswith="Table 1. Detailed element information")
        self.fig_caption_tmpl = self.find(startswith="Figure 9. Overview of the case knowledge graph")
        self.body_rpr = deepcopy(self.body_tmpl.runs[0]._r.rPr) if self.body_tmpl.runs and self.body_tmpl.runs[0]._r.rPr is not None else None
        self.heading_rpr = deepcopy(self.heading_tmpl.runs[0]._r.rPr) if self.heading_tmpl.runs[0]._r.rPr is not None else None
        self.log = []

    # ---------------------------------------------------------------- lookup
    def find(self, startswith=None, contains=None):
        for p in self.doc.paragraphs:
            t = p.text.strip()
            if startswith is not None and t.startswith(startswith):
                return p
            if contains is not None and contains in p.text:
                return p
        raise LookupError(startswith or contains)

    # ---------------------------------------------------------------- runs
    def _styled_run(self, p, text, rpr, color=RED, bold=None):
        run = p.add_run(text)
        if rpr is not None:
            run._r.insert(0, deepcopy(rpr))
        style_run(run, color=color, bold=bold)
        return run

    def comment(self, runs, text):
        if not isinstance(runs, (list, tuple)):
            runs = [runs]
        self.doc.add_comment(runs, text=text, author=AUTHOR, initials=INITIALS)
        self.log.append(text.split("。")[0])

    def _inherit_font(self, run):
        """Copy font family and size from the body template into a run that already has an rPr."""
        if self.body_rpr is None:
            return
        rpr = run._r.get_or_add_rPr()
        for tag in ("w:rFonts", "w:sz", "w:szCs"):
            src = self.body_rpr.find(qn(tag))
            if src is not None and rpr.find(qn(tag)) is None:
                rpr.insert(0, deepcopy(src))

    def red_paragraph_after(self, anchor, text, note=None, math=False):
        p = paragraph_after(anchor, template=self.body_tmpl)
        if math:
            runs = add_math_runs(p, text, color=RED)
            for r in runs:
                self._inherit_font(r)
            first = runs[0]
        else:
            first = self._styled_run(p, text, self.body_rpr)
        if note:
            self.comment(first, note)
        return p

    def heading_after(self, anchor, text, note=None):
        p = paragraph_after(anchor, template=self.heading_tmpl)
        run = self._styled_run(p, text, self.heading_rpr, bold=True)
        if note:
            self.comment(run, note)
        return p

    def equation_after(self, anchor, markup, number):
        p = paragraph_after(anchor, template=self.body_tmpl)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        runs = add_math_runs(p, markup, color=RED)
        tail = p.add_run(f"        ({number})")
        style_run(tail, color=RED)
        for r in runs + [tail]:
            self._inherit_font(r)
        return p, runs[0]

    def caption_after(self, anchor, text, template=None, note=None):
        p = paragraph_after(anchor, template=template or self.caption_tmpl)
        run = self._styled_run(p, text, self.body_rpr)
        if note:
            self.comment(run, note)
        return p

    def table_after(self, anchor, tbl):
        return table_after(self.doc, anchor, tbl.headers, tbl.rows, color=RED, size=9)

    def figure_after(self, anchor, png: Path, width_cm=16.0):
        p = paragraph_after(anchor, template=self.body_tmpl)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(str(png), width=Cm(width_cm))
        return p

    def replace_sentence(self, p, old, new, note):
        """Replace `old` inside `p` by a red run, keeping the surrounding text black.

        Handles a target inside one run and a target spanning consecutive runs.
        """
        runs = p.runs
        for i, run in enumerate(runs):
            if old in run.text:
                before, after = run.text.split(old, 1)
                run.text = before
                new_run = self._styled_run(p, new, run._r.rPr)
                run._r.addnext(new_run._r)
                if after:
                    tail = p.add_run(after)
                    if run._r.rPr is not None:
                        tail._r.insert(0, deepcopy(run._r.rPr))
                    new_run._r.addnext(tail._r)
                self.comment(new_run, note)
                return new_run
        # spanning case: find the run window whose joined text contains `old`
        for i in range(len(runs)):
            joined = ""
            for j in range(i, len(runs)):
                joined += runs[j].text
                if old in joined:
                    start = joined.index(old)
                    before = joined[:start]
                    after = joined[start + len(old):]
                    runs[i].text = before
                    for k in range(i + 1, j + 1):
                        p._p.remove(runs[k]._r)
                    new_run = self._styled_run(p, new, runs[i]._r.rPr)
                    runs[i]._r.addnext(new_run._r)
                    if after:
                        tail = p.add_run(after)
                        if runs[i]._r.rPr is not None:
                            tail._r.insert(0, deepcopy(runs[i]._r.rPr))
                        new_run._r.addnext(tail._r)
                    self.comment(new_run, note)
                    return new_run
                if j - i >= 8:
                    break
        raise LookupError(f"sentence not found: {old[:60]}")

    def append_text(self, p, text, note):
        run = self._styled_run(p, " " + text, self.body_rpr)
        self.comment(run, note)
        return run

    def replace_paragraph(self, p, text, note):
        for r in list(p._p.findall(qn("w:r"))):
            p._p.remove(r)
        run = self._styled_run(p, text, self.body_rpr)
        self.comment(run, note)
        return run


def main() -> None:
    C = build_content()
    L = C.labels
    doc = Document(SRC)
    ed = Editor(doc)

    # ------------------------------------------------------------ abstract
    ed.replace_sentence(
        ed.find(startswith="This study develops a knowledge graph-based framework"),
        "The multi-perspective carbon question answering system is validated in a case study of a volumetric module.",
        C.abstract,
        "[意见 1] 摘要案例句由单模块改为三模块，并加入零样本迁移结果。替换原句 “The multi-perspective carbon question answering system is validated in a case study of a volumetric module.”")

    # ------------------------------------------------------------ 3.1.3
    p64 = ed.find(startswith="In Equations 3 and 4,")
    cur = ed.red_paragraph_after(p64, C.sec313["para1"],
                                 note=f"[意见 2] 新增分配政策定义与对账关系。两式标为 Equation {L.eq_a}、{L.eq_b}，避免与现有 Equation 9、10 冲突；如改为顺序编号，请同时改 4.1.3 与 {L.app} 的引用。请用公式编辑器重排两式。")
    cur, _ = ed.equation_after(cur, C.sec313["eq_a"], L.eq_a)
    cur, _ = ed.equation_after(cur, C.sec313["eq_b"], L.eq_b)
    cur = ed.red_paragraph_after(cur, C.sec313["para2"], math=True)

    # ------------------------------------------------------------ 4.1.1
    p121 = ed.find(startswith="The case study uses a student residence")
    ed.replace_sentence(p121, C.sec411["old_focus"], C.sec411["replace_focus"],
                        "[意见 1] 案例由单一 Type A 改为三种模块类型。原句中“自重 5 到 6 吨”已删：图中已接受材料质量为 9.2 吨（混凝土楼板 5.9 吨），与 5–6 吨口径不一致，请核对后决定是否恢复。")
    p125 = ed.find(startswith="The design model was developed in Autodesk Revit 2024")
    ed.replace_sentence(p125, C.sec411["old_objects"], C.sec411["replace_objects"],
                        "[意见 1] 补充 Type B、Type D 模型的构件数与预处理（剥离机电、按 Type A 类型规则补材料、结构楼板改类）。")
    p126 = ed.find(startswith="Factory energy data include measured monthly electricity")
    ed.append_text(p126, C.sec411["append_factory"],
                   "[意见 2] 说明 Type B、Type D 过程能耗的质量缩放代理（比例 0.934、1.107）。上下界见 4.1.3 与 " + L.app + ".1。")

    # ------------------------------------------------------------ 4.1.2
    p135 = ed.find(startswith="The accuracy of carbon calculations depends on the design properties")
    from mm_docx import paragraph_before
    p_new = paragraph_before(p135, template=ed.body_tmpl)
    first = ed._styled_run(p_new, C.sec412["para"], ed.body_rpr)
    ed.comment(first, f"[意见 1、3] 新增三模块图统计段与 {L.tab_graph}（原 X1）。原 Table 2（4.3.3 措辞敏感性）顺延为 {L.old_table2_new}。" + DATA_NOTE)
    cap = ed.caption_after(p_new, C.sec412["table"].caption)
    ed.table_after(cap, C.sec412["table"])

    # ------------------------------------------------------------ 4.1.3 (new subsection after Figure 10 caption)
    p138 = ed.find(startswith="Figure 10. IFC knowledge extraction accuracy")
    cur = ed.heading_after(p138, C.sec413["heading"], note="[意见 1、2、4] 新增小节：三模块账目、对账、Figure X 与指向 " + L.app + " 的结果句。若采用压缩稿删除 Figure 10，本小节位置不变。")
    cur = ed.red_paragraph_after(cur, C.sec413["para"])
    cur = ed.caption_after(cur, C.sec413["table"].caption, note=f"{L.tab_acc}（原 X2）。" + DATA_NOTE)
    cur = ed.table_after(cur, C.sec413["table"])
    cur = ed.figure_after(cur, FIGURE_PNG)
    cur = ed.caption_after(cur, C.figure_caption, template=ed.fig_caption_tmpl,
                           note="编号待定：若保留 Figure 10，本图为 Figure 11，其后 Figure 11–14 顺延为 12–15；若采用压缩稿删除 Figure 10，本图为 Figure 10，顺延取消。矢量版 PDF/SVG/TIFF 在 outputs/paper_figures/process_attribution_20260929/。")
    cur = ed.red_paragraph_after(cur, C.sec413["fig_para"])
    cur = ed.red_paragraph_after(cur, C.sec413["appendix_para"])

    # ------------------------------------------------------------ 4.3.5 (after last paragraph of 4.3.4)
    p181 = ed.find(startswith="These results distinguish the contribution of each system layer")
    cur = ed.heading_after(p181, C.sec435["heading"], note="[意见 1、3] 新增短小节，完整留出集结果与开销表在 " + L.app + ".6、" + L.app + ".7。")
    cur = ed.red_paragraph_after(cur, C.sec435["para"])

    # ------------------------------------------------------------ 5.1 (after the last discussion paragraph)
    p190 = ed.find(startswith="Questions from the product perspective had lower pass rates")
    cur = ed.red_paragraph_after(p190, C.sec51["bridge"],
                                 note="[意见 3] 模块到建筑的论证段，来自压缩修改稿 5.1 的红字段落，首句按三模块案例改写。若之后套用压缩稿 5.1，请勿重复插入。")
    cur = ed.red_paragraph_after(cur, C.sec51["eq_intro"], math=True,
                                 note=f"[意见 3] 建筑级账目 Equation {L.eq_bldg}。N_A、N_B、N_D 待项目提供，填入 outputs/research_experiments/multi_module_20260928/E2/module_counts.json 后运行 mm_e2_accounting.py 得建筑总量。")
    cur, _ = ed.equation_after(cur, C.sec51["eq"], L.eq_bldg)
    cur = ed.red_paragraph_after(cur, C.sec51["eq_after"])
    cur = ed.red_paragraph_after(cur, C.sec51["uncertainty"], note="[意见 4] 不确定性来源排序，数字来自 " + L.app + ".1–C.5。")

    # ------------------------------------------------------------ 5.2
    p194 = ed.find(startswith="First, fine-grained energy records were unavailable")
    ed.replace_paragraph(p194, C.sec52["para"],
                         "[意见 1–4] 替换原局限段（能耗记录粒度、单项目两点）。新段覆盖单项目单工厂、跨模块而非跨项目的迁移证据、质量缩放代理、合成拆分、并发未测、数据质量缺口。")

    # ------------------------------------------------------------ 6
    p198 = ed.find(startswith="The case evaluation demonstrates that reliable quantitative access")
    ed.append_text(p198, C.conclusion, "[意见 1、2、4] 结论补一句三模块结果。")

    # ------------------------------------------------------------ renumber the existing Table 2
    for p in (ed.find(contains="Table 2 reports the outcome of each"), ed.find(startswith="Table 2. Per-question phrasing sensitivity")):
        ed.replace_sentence(p, "Table 2", L.old_table2_new, f"原 Table 2 顺延为 {L.old_table2_new}，因 4.1.2 与 4.1.3 新增 {L.tab_graph}、{L.tab_acc}。")

    doc.save(OUT)
    print("written:", OUT)
    print("changes:", len(ed.log))


if __name__ == "__main__":
    main()
