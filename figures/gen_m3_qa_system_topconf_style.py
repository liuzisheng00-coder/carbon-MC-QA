from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import math


OUT_DIR = Path(__file__).resolve().parent
PNG_OUT = OUT_DIR / "fig_m3_qa_system_topconf_style.png"
PDF_OUT = OUT_DIR / "fig_m3_qa_system_topconf_style.pdf"

W, H = 2500, 1500
PAD = 56


def f(name, size):
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


FONT = f("arial.ttf", 24)
FONT_S = f("arial.ttf", 20)
FONT_XS = f("arial.ttf", 17)
FONT_B = f("arialbd.ttf", 26)
FONT_H = f("arialbd.ttf", 34)
FONT_PANEL = f("arialbd.ttf", 30)
FONT_MONO = f("consola.ttf", 19)

C = {
    "ink": (32, 36, 42),
    "muted": (88, 96, 108),
    "line": (93, 103, 118),
    "soft_line": (187, 193, 203),
    "bg": (255, 255, 255),
    "panel": (250, 251, 253),
    "green": (227, 246, 226),
    "green2": (195, 232, 195),
    "green_edge": (72, 147, 82),
    "blue": (223, 239, 255),
    "blue2": (196, 224, 252),
    "blue_edge": (63, 133, 207),
    "orange": (255, 236, 211),
    "orange2": (255, 216, 166),
    "orange_edge": (218, 126, 43),
    "purple": (240, 226, 253),
    "purple2": (220, 196, 243),
    "purple_edge": (134, 89, 179),
    "yellow": (255, 249, 207),
    "yellow2": (252, 232, 137),
    "yellow_edge": (196, 151, 35),
    "red": (255, 229, 229),
    "red2": (255, 199, 199),
    "red_edge": (211, 73, 73),
    "white": (255, 255, 255),
    "gray": (240, 243, 247),
}


def rr(d, box, fill, outline, w=3, r=20):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=w)


def line_text(d, xy, text, font, fill=C["ink"], max_w=None, gap=4):
    x, y = xy
    if max_w is None:
        d.text((x, y), text, font=font, fill=fill)
        return y + font.size + gap
    words = text.split()
    line = ""
    for word in words:
        test = word if not line else line + " " + word
        if d.textbbox((0, 0), test, font=font)[2] <= max_w:
            line = test
        else:
            if line:
                d.text((x, y), line, font=font, fill=fill)
                y += font.size + gap
            line = word
    if line:
        d.text((x, y), line, font=font, fill=fill)
        y += font.size + gap
    return y


def arrow(d, a, b, fill=C["line"], width=4, head=16):
    d.line([a, b], fill=fill, width=width)
    x1, y1 = a
    x2, y2 = b
    dx, dy = x2 - x1, y2 - y1
    L = max(math.hypot(dx, dy), 1)
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    p1 = (x2 - ux * head + px * head * 0.55, y2 - uy * head + py * head * 0.55)
    p2 = (x2 - ux * head - px * head * 0.55, y2 - uy * head - py * head * 0.55)
    d.polygon([b, p1, p2], fill=fill)


def dashed(d, a, b, fill=C["line"], width=3, dash=18, gap=11):
    x1, y1 = a
    x2, y2 = b
    dx, dy = x2 - x1, y2 - y1
    L = max(math.hypot(dx, dy), 1)
    ux, uy = dx / L, dy / L
    t = 0
    while t < L - dash:
        p = (x1 + ux * t, y1 + uy * t)
        q = (x1 + ux * min(t + dash, L), y1 + uy * min(t + dash, L))
        d.line([p, q], fill=fill, width=width)
        t += dash + gap
    arrow(d, (x1 + ux * max(L - dash, 0), y1 + uy * max(L - dash, 0)), b, fill, width, head=14)


def panel_header(d, xy, label, title):
    x, y = xy
    d.ellipse((x, y, x + 44, y + 44), fill=C["ink"])
    d.text((x + 14, y + 6), label, font=FONT_B, fill=C["white"])
    d.text((x + 58, y + 6), title, font=FONT_PANEL, fill=C["ink"])


def step_box(d, box, title, body_lines, fill, edge, tag=None):
    rr(d, box, fill, edge, w=3, r=18)
    x1, y1, x2, _ = box
    if tag:
        rr(d, (x1 + 16, y1 + 15, x1 + 76, y1 + 45), C["white"], edge, w=2, r=10)
        d.text((x1 + 28, y1 + 19), tag, font=FONT_XS, fill=edge)
        title_x = x1 + 88
    else:
        title_x = x1 + 18
    d.text((title_x, y1 + 18), title, font=FONT_B, fill=C["ink"])
    y = y1 + 58
    for line in body_lines:
        y = line_text(d, (x1 + 20, y), line, FONT_S, C["ink"], x2 - x1 - 40, 4)
    return box


def chip(d, box, text, fill, edge, font=FONT_XS):
    rr(d, box, fill, edge, w=2, r=10)
    x1, y1, x2, y2 = box
    tw = d.textbbox((0, 0), text, font=font)[2]
    d.text((x1 + (x2 - x1 - tw) / 2, y1 + 7), text, font=font, fill=C["ink"])


def doc_icon(d, x, y, w=42, h=55, edge=C["line"], fill=C["white"]):
    d.rectangle((x, y, x + w, y + h), fill=fill, outline=edge, width=2)
    d.polygon([(x + w - 14, y), (x + w, y + 14), (x + w - 14, y + 14)], fill=(230, 233, 238), outline=edge)
    for i in range(3):
        d.line((x + 8, y + 20 + i * 10, x + w - 8, y + 20 + i * 10), fill=edge, width=1)


def table_icon(d, x, y, w=82, h=60, edge=C["line"]):
    d.rectangle((x, y, x + w, y + h), fill=C["white"], outline=edge, width=2)
    for i in range(1, 3):
        d.line((x + i * w / 3, y, x + i * w / 3, y + h), fill=edge, width=1)
    for j in range(1, 4):
        d.line((x, y + j * h / 4, x + w, y + j * h / 4), fill=edge, width=1)


def graph_icon(d, cx, cy, scale=1.0, edge=C["blue_edge"]):
    pts = [
        (cx - 54 * scale, cy - 26 * scale),
        (cx - 6 * scale, cy - 54 * scale),
        (cx + 50 * scale, cy - 21 * scale),
        (cx - 28 * scale, cy + 38 * scale),
        (cx + 46 * scale, cy + 46 * scale),
        (cx + 2 * scale, cy + 6 * scale),
    ]
    for a, b in [(0, 1), (1, 2), (0, 5), (2, 5), (5, 3), (5, 4), (3, 4)]:
        d.line((pts[a], pts[b]), fill=edge, width=max(2, int(4 * scale)))
    colors = [C["green2"], C["yellow2"], C["blue2"], C["orange2"], C["purple2"], C["white"]]
    for p, col in zip(pts, colors):
        r = max(6, int(13 * scale))
        d.ellipse((p[0] - r, p[1] - r, p[0] + r, p[1] + r), fill=col, outline=edge, width=max(2, int(3 * scale)))


def bot_icon(d, cx, cy, scale=1.0, edge=C["purple_edge"]):
    r = int(30 * scale)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=C["purple"], outline=edge, width=max(2, int(3 * scale)))
    d.rectangle((cx - 18 * scale, cy - 8 * scale, cx + 18 * scale, cy + 9 * scale), fill=C["white"], outline=edge, width=max(1, int(2 * scale)))
    d.ellipse((cx - 12 * scale, cy - 3 * scale, cx - 6 * scale, cy + 3 * scale), fill=edge)
    d.ellipse((cx + 6 * scale, cy - 3 * scale, cx + 12 * scale, cy + 3 * scale), fill=edge)
    d.line((cx, cy + 9 * scale, cx, cy + 21 * scale), fill=edge, width=max(1, int(2 * scale)))


def store_icon(d, x, y, edge, label):
    rr(d, (x, y, x + 210, y + 96), C["white"], edge, w=3, r=14)
    d.ellipse((x + 18, y + 20, x + 68, y + 42), fill=C["white"], outline=edge, width=2)
    d.rectangle((x + 18, y + 31, x + 68, y + 67), fill=C["white"], outline=edge, width=2)
    d.ellipse((x + 18, y + 56, x + 68, y + 78), fill=C["white"], outline=edge, width=2)
    d.text((x + 82, y + 22), label, font=FONT_S, fill=C["ink"])


def draw_main_flow(d):
    panel_header(d, (82, 60), "A", "Query-driven BIM carbon QA pipeline")
    d.rounded_rectangle((75, 116, 2425, 390), radius=24, outline=C["soft_line"], width=2, fill=(255, 255, 255))

    q = (115, 160, 405, 345)
    rr(d, q, C["green"], C["green_edge"], 3, 20)
    d.text((140, 184), "Question", font=FONT_B, fill=C["ink"])
    line_text(d, (140, 225), "What is the embodied carbon of module M_i?", FONT_S, C["ink"], 230, 4)
    d.text((140, 300), "user", font=FONT_XS, fill=C["muted"])

    retr = (500, 150, 790, 355)
    step_box(d, retr, "Primary retrieval", ["BIM objects", "EF records", "process logs"], C["blue"], C["blue_edge"])
    graph_icon(d, 730, 274, 0.62)

    ana = (880, 150, 1180, 355)
    step_box(d, ana, "Analyzer", ["intent: carbon", "scope: module", "status: slots"], C["purple"], C["purple_edge"], "3.1")
    chip(d, (918, 280, 1055, 315), "semantic slots", C["white"], C["purple_edge"])

    org = (1270, 150, 1570, 355)
    step_box(d, org, "Organizer", ["graph path", "formula plan", "evidence route"], C["orange"], C["orange_edge"], "3.2")
    chip(d, (1308, 280, 1458, 315), "Plan pi_Q", C["white"], C["orange_edge"])

    exe = (1660, 150, 2025, 355)
    rr(d, exe, C["blue"], C["blue_edge"], 3, 20)
    d.text((1690, 174), "Executor", font=FONT_B, fill=C["ink"])
    chip(d, (1690, 222, 1845, 257), "Graph query", C["white"], C["blue_edge"])
    chip(d, (1858, 222, 1996, 257), "Compute C", C["white"], C["blue_edge"])
    chip(d, (1760, 285, 1940, 320), "LLM grounding", C["white"], C["blue_edge"])
    bot_icon(d, 1978, 178, 0.65, C["blue_edge"])

    ans = (2125, 168, 2385, 336)
    rr(d, ans, C["green"], C["green_edge"], 3, 20)
    d.text((2160, 194), "Answer", font=FONT_B, fill=C["ink"])
    line_text(d, (2160, 234), "value + formula + evidence trace + uncertainty", FONT_S, C["ink"], 180, 3)

    for a, b in [((405, 252), (500, 252)), ((790, 252), (880, 252)), ((1180, 252), (1270, 252)), ((1570, 252), (1660, 252)), ((2025, 252), (2125, 252))]:
        arrow(d, a, b)

    # Evidence sources, aligned with primary retrieval.
    d.text((125, 365), "BIM graph, carbon factors, and process records are converted to queryable evidence.", font=FONT_XS, fill=C["muted"])


def draw_evidence_control(d):
    panel_header(d, (82, 430), "B", "Evidence sufficiency control")
    outer = (75, 486, 1635, 1060)
    d.rounded_rectangle(outer, radius=24, outline=C["soft_line"], width=2, fill=(255, 255, 255))

    # Lane 1.
    d.rounded_rectangle((120, 530, 1588, 685), radius=16, outline=C["soft_line"], width=2, fill=C["panel"])
    d.text((145, 545), "Q1: total material carbon of module M_i", font=FONT_B, fill=C["ink"])
    d.text((145, 585), "Retrieval", font=FONT_S, fill=C["muted"])
    graph_icon(d, 250, 625, 0.45, C["blue_edge"])
    table_icon(d, 330, 593, 70, 52, C["orange_edge"])
    doc_icon(d, 420, 591, 38, 52, C["green_edge"])
    rr(d, (520, 574, 945, 662), C["white"], C["soft_line"], 2, 12)
    d.text((540, 594), "objects + quantities + emission factors", font=FONT_S, fill=C["ink"])
    arrow(d, (470, 620), (520, 620), C["line"], 3)
    rr(d, (1015, 552, 1265, 667), C["yellow"], C["yellow_edge"], 3, 14)
    d.text((1040, 574), "Judgment", font=FONT_B, fill=C["ink"])
    d.text((1040, 612), "complete evidence", font=FONT_S, fill=C["green_edge"])
    arrow(d, (945, 620), (1015, 620), C["line"], 3)
    rr(d, (1340, 563, 1548, 657), C["green"], C["green_edge"], 3, 14)
    d.text((1370, 589), "Generate", font=FONT_B, fill=C["ink"])
    d.text((1370, 621), "answer", font=FONT_S, fill=C["muted"])
    arrow(d, (1265, 620), (1340, 620), C["green_edge"], 3)

    # Lane 2.
    d.rounded_rectangle((120, 725, 1588, 1018), radius=16, outline=C["soft_line"], width=2, fill=C["panel"])
    d.text((145, 740), "Q2: multi-perspective carbon including process and transport", font=FONT_B, fill=C["ink"])
    d.text((145, 780), "Retrieval", font=FONT_S, fill=C["muted"])
    graph_icon(d, 240, 825, 0.42, C["blue_edge"])
    doc_icon(d, 315, 793, 38, 52, C["green_edge"])
    doc_icon(d, 360, 793, 38, 52, C["red_edge"])
    d.text((345, 855), "missing", font=FONT_XS, fill=C["red_edge"])
    rr(d, (470, 772, 850, 870), C["white"], C["soft_line"], 2, 12)
    d.text((492, 790), "available: objects + EF", font=FONT_S, fill=C["ink"])
    d.text((492, 826), "missing: transport distance", font=FONT_S, fill=C["red_edge"])
    arrow(d, (410, 825), (470, 825), C["line"], 3)
    rr(d, (925, 767, 1218, 877), C["yellow"], C["yellow_edge"], 3, 14)
    d.text((950, 787), "Judgment", font=FONT_B, fill=C["ink"])
    d.text((950, 825), "insufficient evidence", font=FONT_S, fill=C["red_edge"])
    arrow(d, (850, 825), (925, 825), C["line"], 3)

    # Exploration subgraph.
    rr(d, (925, 910, 1548, 995), C["red"], C["red_edge"], 3, 14)
    d.text((950, 930), "Targeted exploration", font=FONT_B, fill=C["ink"])
    d.text((950, 965), "expand neighbourhood / retrieve logs / request missing input", font=FONT_XS, fill=C["muted"])
    dashed(d, (1070, 877), (1070, 910), C["red_edge"], 3)
    dashed(d, (1548, 954), (1588, 954), C["red_edge"], 3)
    dashed(d, (1588, 954), (1588, 706), C["red_edge"], 3)
    d.text((1460, 898), "re-plan", font=FONT_XS, fill=C["red_edge"])

    # Small formula block in this panel.
    rr(d, (145, 910, 800, 995), C["white"], C["soft_line"], 2, 12)
    d.text((165, 925), "Z_Q = {objects, factors, formulas, records, status}", font=FONT_MONO, fill=C["ink"])
    d.text((165, 958), "C_MM = C_mat + C_proc + C_trans + C_asm", font=FONT_MONO, fill=C["ink"])


def draw_memory(d):
    panel_header(d, (1705, 430), "C", "QA memory and feedback update")
    outer = (1698, 486, 2425, 1060)
    d.rounded_rectangle(outer, radius=24, outline=C["soft_line"], width=2, fill=(255, 255, 255))

    q = (1745, 545, 1955, 665)
    rr(d, q, C["green"], C["green_edge"], 3, 18)
    d.text((1770, 565), "New QA", font=FONT_B, fill=C["ink"])
    line_text(d, (1770, 604), "query and generated answer", FONT_XS, C["muted"], 140, 3)

    mech = (2040, 535, 2180, 675)
    rr(d, mech, C["blue"], C["blue_edge"], 3, 18)
    d.text((2060, 555), "Scorer", font=FONT_B, fill=C["ink"])
    bot_icon(d, 2110, 622, 0.65, C["blue_edge"])
    arrow(d, (1955, 605), (2040, 605), C["line"], 3)

    store_icon(d, 2235, 520, C["green_edge"], "High QA")
    store_icon(d, 2235, 650, C["red_edge"], "Low QA")
    arrow(d, (2180, 590), (2235, 568), C["green_edge"], 3)
    arrow(d, (2180, 635), (2235, 700), C["red_edge"], 3)
    d.text((2208, 535), "s >= tau", font=FONT_XS, fill=C["green_edge"])
    d.text((2208, 714), "s < tau", font=FONT_XS, fill=C["red_edge"])

    rr(d, (1750, 780, 2375, 922), C["gray"], C["soft_line"], 2, 18)
    d.text((1775, 804), "Memory-guided reuse", font=FONT_B, fill=C["ink"])
    d.text((1775, 844), "similar high-confidence QA pairs are retrieved as context", font=FONT_S, fill=C["muted"])
    dashed(d, (2240, 618), (2060, 780), C["green_edge"], 3)
    dashed(d, (1885, 780), (1885, 705), C["purple_edge"], 3)
    d.text((1905, 714), "historical QA context", font=FONT_XS, fill=C["purple_edge"])

    rr(d, (1750, 955, 2375, 1020), C["red"], C["red_edge"], 2, 16)
    d.text((1775, 974), "Low-confidence cases trigger factor/log correction or manual data completion.", font=FONT_S, fill=C["ink"])


def draw_example_trace(d):
    d.rounded_rectangle((75, 1112, 2425, 1425), radius=24, outline=C["soft_line"], width=2, fill=(255, 255, 255))
    d.text((105, 1140), "Example trace", font=FONT_PANEL, fill=C["ink"])
    steps = [
        ("Parse", C["purple"], C["purple_edge"], "intent=C_MM; scope=module M_i; perspective=material/process/transport"),
        ("Retrieve", C["blue"], C["blue_edge"], "BIM quantities + EF table + process records"),
        ("Judge", C["yellow"], C["yellow_edge"], "transport distance missing; status=partial"),
        ("Explore", C["red"], C["red_edge"], "retrieve route log or request input"),
        ("Answer", C["green"], C["green_edge"], "known C + equation + evidence trace + missing item"),
    ]
    x = 130
    for i, (title, fill, edge, body) in enumerate(steps):
        box = (x, 1215, x + 395, 1348)
        rr(d, box, fill, edge, 3, 16)
        d.text((x + 22, 1235), title, font=FONT_B, fill=C["ink"])
        line_text(d, (x + 22, 1274), body, FONT_XS, C["ink"], 345, 3)
        if i < len(steps) - 1:
            arrow(d, (x + 395, 1280), (x + 455, 1280), C["line"], 3)
        x += 455


def main():
    img = Image.new("RGB", (W, H), C["bg"])
    d = ImageDraw.Draw(img)
    draw_main_flow(d)
    draw_evidence_control(d)
    draw_memory(d)
    draw_example_trace(d)
    img.save(PNG_OUT, quality=96)
    img.save(PDF_OUT, "PDF", resolution=300.0)
    print(PNG_OUT)
    print(PDF_OUT)


if __name__ == "__main__":
    main()
