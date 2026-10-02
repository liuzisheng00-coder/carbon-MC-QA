from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


OUT_DIR = Path(__file__).resolve().parent
PNG_OUT = OUT_DIR / "fig_m3_qa_system_rje_okralong_comrag.png"
PDF_OUT = OUT_DIR / "fig_m3_qa_system_rje_okralong_comrag.pdf"


W, H = 2600, 1550
M = 80

COLORS = {
    "ink": (33, 37, 41),
    "muted": (92, 99, 112),
    "line": (118, 126, 142),
    "panel": (250, 250, 250),
    "blue": (221, 238, 255),
    "blue_edge": (79, 138, 202),
    "green": (226, 245, 226),
    "green_edge": (92, 154, 99),
    "orange": (255, 236, 213),
    "orange_edge": (218, 137, 52),
    "purple": (241, 229, 252),
    "purple_edge": (139, 98, 180),
    "red": (255, 229, 229),
    "red_edge": (206, 79, 79),
    "yellow": (255, 247, 203),
    "yellow_edge": (203, 160, 43),
    "gray": (241, 243, 245),
    "white": (255, 255, 255),
}


def font(name="arial.ttf", size=32):
    try:
        return ImageFont.truetype(name, size)
    except OSError:
        return ImageFont.load_default()


F_TITLE = font("arialbd.ttf", 40)
F_HEAD = font("arialbd.ttf", 32)
F_SUB = font("arial.ttf", 25)
F_SMALL = font("arial.ttf", 22)
F_TINY = font("arial.ttf", 18)
F_BOLD_SMALL = font("arialbd.ttf", 22)
F_MONO = font("consola.ttf", 20)


def wrap_text(draw, text, max_width, fnt):
    words = text.split()
    lines, line = [], ""
    for word in words:
        test = word if not line else f"{line} {word}"
        if draw.textbbox((0, 0), test, font=fnt)[2] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def round_rect(draw, box, fill, outline, width=3, radius=24):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text_block(draw, xy, text, max_width, fnt, fill, line_gap=6):
    x, y = xy
    for line in wrap_text(draw, text, max_width, fnt):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += fnt.size + line_gap
    return y


def arrow(draw, p1, p2, fill=None, width=5):
    if fill is None:
        fill = COLORS["line"]
    draw.line([p1, p2], fill=fill, width=width)
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    length = max((dx * dx + dy * dy) ** 0.5, 1)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    size = 18
    tip = (x2, y2)
    left = (x2 - ux * size + px * size * 0.55, y2 - uy * size + py * size * 0.55)
    right = (x2 - ux * size - px * size * 0.55, y2 - uy * size - py * size * 0.55)
    draw.polygon([tip, left, right], fill=fill)


def dashed_arrow(draw, p1, p2, fill=None, width=4, dash=18, gap=12):
    if fill is None:
        fill = COLORS["line"]
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    length = max((dx * dx + dy * dy) ** 0.5, 1)
    ux, uy = dx / length, dy / length
    t = 0
    while t < length - dash:
        a = (x1 + ux * t, y1 + uy * t)
        b = (x1 + ux * min(t + dash, length), y1 + uy * min(t + dash, length))
        draw.line([a, b], fill=fill, width=width)
        t += dash + gap
    arrow(draw, (x1 + ux * max(length - dash, 0), y1 + uy * max(length - dash, 0)), p2, fill, width)


def module(draw, box, label, body, fill, outline, tag=None):
    x1, y1, x2, y2 = box
    round_rect(draw, box, fill=fill, outline=outline, width=4, radius=28)
    if tag:
        tag_box = (x1 + 22, y1 + 18, x1 + 132, y1 + 52)
        round_rect(draw, tag_box, fill=COLORS["white"], outline=outline, width=2, radius=12)
        draw.text((x1 + 42, y1 + 23), tag, font=F_TINY, fill=outline)
        tx = x1 + 150
    else:
        tx = x1 + 28
    draw.text((tx, y1 + 20), label, font=F_HEAD, fill=COLORS["ink"])
    text_block(draw, (x1 + 30, y1 + 74), body, x2 - x1 - 60, F_SUB, COLORS["ink"], 7)


def mini_box(draw, box, title, lines, fill, outline):
    round_rect(draw, box, fill=fill, outline=outline, width=3, radius=18)
    x1, y1, x2, _ = box
    draw.text((x1 + 20, y1 + 16), title, font=F_BOLD_SMALL, fill=COLORS["ink"])
    y = y1 + 54
    for line in lines:
        text_block(draw, (x1 + 22, y), line, x2 - x1 - 44, F_TINY, COLORS["muted"], 3)
        y += 48


def icon_graph(draw, cx, cy, scale=1.0, color=(63, 136, 197)):
    pts = [
        (cx - 45 * scale, cy - 20 * scale),
        (cx, cy - 45 * scale),
        (cx + 45 * scale, cy - 10 * scale),
        (cx - 15 * scale, cy + 38 * scale),
        (cx + 45 * scale, cy + 45 * scale),
    ]
    edges = [(0, 1), (1, 2), (0, 3), (2, 3), (3, 4)]
    for a, b in edges:
        draw.line([pts[a], pts[b]], fill=color, width=int(5 * scale))
    for p in pts:
        r = int(11 * scale)
        draw.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=COLORS["white"], outline=color, width=int(4 * scale))


def icon_llm(draw, cx, cy, scale=1.0, color=(119, 93, 170)):
    r = int(36 * scale)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=COLORS["purple"], outline=color, width=int(4 * scale))
    draw.rectangle([cx - 20 * scale, cy - 9 * scale, cx + 20 * scale, cy + 9 * scale], fill=COLORS["white"], outline=color, width=int(3 * scale))
    draw.ellipse([cx - 14 * scale, cy - 4 * scale, cx - 7 * scale, cy + 4 * scale], fill=color)
    draw.ellipse([cx + 7 * scale, cy - 4 * scale, cx + 14 * scale, cy + 4 * scale], fill=color)
    draw.line([(cx, cy + 9 * scale), (cx, cy + 22 * scale)], fill=color, width=int(3 * scale))


def icon_store(draw, cx, cy, scale=1.0, color=(78, 151, 96)):
    w, h = 70 * scale, 82 * scale
    x1, y1 = cx - w / 2, cy - h / 2
    x2, y2 = cx + w / 2, cy + h / 2
    draw.ellipse([x1, y1, x2, y1 + 26 * scale], fill=COLORS["white"], outline=color, width=int(4 * scale))
    draw.rectangle([x1, y1 + 13 * scale, x2, y2 - 13 * scale], fill=COLORS["white"], outline=color, width=int(4 * scale))
    draw.ellipse([x1, y2 - 26 * scale, x2, y2], fill=COLORS["white"], outline=color, width=int(4 * scale))
    for yy in [y1 + 30 * scale, y1 + 52 * scale]:
        draw.arc([x1, yy - 13 * scale, x2, yy + 13 * scale], 0, 180, fill=color, width=int(3 * scale))


def main():
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    # Light guide rows.
    d.rounded_rectangle((M, M, W - M, H - M), radius=28, outline=(210, 214, 220), width=2, fill=(255, 255, 255))
    d.text((M + 20, M + 18), "BIM-enabled carbon emission QA system", font=F_TITLE, fill=COLORS["ink"])
    d.text((M + 20, M + 66), "adapted from retrieval-judgment-exploration, analyzer-organizer-executor, and QA-memory update patterns", font=F_SMALL, fill=COLORS["muted"])

    # Input query and data foundation.
    query_box = (130, 185, 545, 445)
    round_rect(d, query_box, fill=COLORS["green"], outline=COLORS["green_edge"], width=4, radius=26)
    d.text((160, 213), "User query", font=F_HEAD, fill=COLORS["ink"])
    text_block(
        d,
        (160, 258),
        "How much embodied carbon is caused by module M_i, and which BIM objects, factors, and process records support the answer?",
        345,
        F_SUB,
        COLORS["ink"],
        8,
    )

    data_box = (130, 490, 545, 790)
    round_rect(d, data_box, fill=COLORS["gray"], outline=(150, 156, 166), width=3, radius=22)
    d.text((160, 518), "Evidence sources", font=F_HEAD, fill=COLORS["ink"])
    items = [
        "BIM object graph",
        "Emission-factor table",
        "Production/process logs",
        "Transport and assembly records",
    ]
    y = 570
    for item in items:
        d.rounded_rectangle((160, y, 512, y + 42), radius=12, fill=COLORS["white"], outline=(190, 196, 206), width=2)
        d.text((180, y + 9), item, font=F_SMALL, fill=COLORS["ink"])
        y += 56

    # Main modules.
    analyzer = (650, 185, 1080, 445)
    organizer = (1180, 185, 1610, 445)
    executor = (1710, 185, 2140, 445)
    answer = (2240, 185, 2470, 445)
    module(
        d,
        analyzer,
        "Analyzer",
        "M3.1 semantic parsing: intent, perspective, object scope, metric type, and missing slots.",
        COLORS["purple"],
        COLORS["purple_edge"],
        "M3.1",
    )
    module(
        d,
        organizer,
        "Organizer",
        "M3.2 query planning: map slots to graph paths, select formulas, and route evidence.",
        COLORS["orange"],
        COLORS["orange_edge"],
        "M3.2",
    )
    module(
        d,
        executor,
        "Executor",
        "M3.3 grounded synthesis: execute query, compute carbon, cite evidence, and state uncertainty.",
        COLORS["blue"],
        COLORS["blue_edge"],
        "M3.3",
    )
    round_rect(d, answer, fill=COLORS["green"], outline=COLORS["green_edge"], width=4, radius=24)
    d.text((2274, 213), "Answer", font=F_HEAD, fill=COLORS["ink"])
    text_block(d, (2270, 265), "value + equation + evidence trace + status", 160, F_SUB, COLORS["ink"], 8)

    arrow(d, (545, 315), (650, 315))
    arrow(d, (1080, 315), (1180, 315))
    arrow(d, (1610, 315), (1710, 315))
    arrow(d, (2140, 315), (2240, 315))

    # Evidence retrieval and judgment row.
    retrieval = (650, 545, 1080, 785)
    judgment = (1180, 545, 1610, 785)
    explore = (1710, 545, 2140, 785)
    mini_box(
        d,
        retrieval,
        "Primary retrieval",
        ["Retrieve candidate BIM-carbon subgraph.", "Collect factor, quantity, and process evidence."],
        COLORS["blue"],
        COLORS["blue_edge"],
    )
    icon_graph(d, 1030, 665, 0.78, COLORS["blue_edge"])
    mini_box(
        d,
        judgment,
        "Evidence judgment",
        ["Check computability and trace completeness.", "If evidence is insufficient, trigger exploration."],
        COLORS["yellow"],
        COLORS["yellow_edge"],
    )
    mini_box(
        d,
        explore,
        "Targeted exploration",
        ["Expand graph neighborhood or retrieve logs.", "Ask for missing process/transport data if needed."],
        COLORS["red"],
        COLORS["red_edge"],
    )
    icon_llm(d, 2070, 660, 0.74, COLORS["red_edge"])
    arrow(d, (545, 640), (650, 635))
    arrow(d, (1080, 665), (1180, 665))
    dashed_arrow(d, (1610, 665), (1710, 665), fill=COLORS["red_edge"])
    dashed_arrow(d, (1925, 545), (1500, 445), fill=COLORS["red_edge"])
    d.text((1632, 633), "insufficient", font=F_TINY, fill=COLORS["red_edge"])

    # Formula and evidence object row.
    formula_box = (650, 855, 1610, 1105)
    round_rect(d, formula_box, fill=(252, 252, 252), outline=(160, 166, 176), width=3, radius=24)
    d.text((690, 885), "Evidence-constrained carbon computation", font=F_HEAD, fill=COLORS["ink"])
    formulas = [
        r"C_mat = sum_i q_i * EF_i",
        r"C_proc = sum_j E_j * EF_j",
        r"C_MM = C_mat + C_proc + C_trans + C_asm",
    ]
    y = 941
    for f in formulas:
        d.rounded_rectangle((690, y, 1260, y + 46), radius=10, fill=COLORS["white"], outline=(200, 205, 212), width=2)
        d.text((710, y + 10), f, font=F_MONO, fill=COLORS["ink"])
        y += 58
    d.rounded_rectangle((1310, 942, 1572, 990), radius=10, fill=COLORS["white"], outline=(200, 205, 212), width=2)
    d.text((1325, 954), "Z_Q={obj,EF,rec,status}", font=F_MONO, fill=COLORS["ink"])
    text_block(d, (1310, 1010), "Only evidence in Z_Q may support the final response.", 245, F_SMALL, COLORS["muted"], 6)

    # QA memory/update row, ComRAG-inspired.
    memory = (1710, 855, 2470, 1105)
    round_rect(d, memory, fill=COLORS["gray"], outline=(160, 166, 176), width=3, radius=24)
    d.text((1750, 885), "QA memory and feedback update", font=F_HEAD, fill=COLORS["ink"])
    high = (1760, 950, 2055, 1070)
    low = (2110, 950, 2405, 1070)
    round_rect(d, high, fill=COLORS["green"], outline=COLORS["green_edge"], width=3, radius=18)
    round_rect(d, low, fill=COLORS["red"], outline=COLORS["red_edge"], width=3, radius=18)
    icon_store(d, 1798, 1010, 0.55, COLORS["green_edge"])
    icon_store(d, 2148, 1010, 0.55, COLORS["red_edge"])
    d.text((1840, 977), "High-confidence QA", font=F_BOLD_SMALL, fill=COLORS["ink"])
    d.text((1840, 1011), "reusable answers", font=F_TINY, fill=COLORS["muted"])
    d.text((2190, 977), "Low-confidence QA", font=F_BOLD_SMALL, fill=COLORS["ink"])
    d.text((2190, 1011), "issues to revisit", font=F_TINY, fill=COLORS["muted"])
    dashed_arrow(d, (2355, 445), (2355, 855), fill=COLORS["green_edge"])
    d.text((2378, 640), "score + update", font=F_TINY, fill=COLORS["green_edge"])
    dashed_arrow(d, (1840, 855), (1840, 810), fill=COLORS["purple_edge"])
    dashed_arrow(d, (1840, 810), (900, 810), fill=COLORS["purple_edge"])
    dashed_arrow(d, (900, 810), (900, 445), fill=COLORS["purple_edge"])
    d.text((1210, 780), "historical QA context", font=F_TINY, fill=COLORS["purple_edge"])

    # Lower example lane.
    ex_box = (130, 1160, 2470, 1410)
    round_rect(d, ex_box, fill=(252, 252, 252), outline=(205, 210, 218), width=2, radius=22)
    d.text((170, 1190), "Example execution trace", font=F_HEAD, fill=COLORS["ink"])
    stages = [
        ("Parse", "module=M_i; perspective=material + process; metric=C_MM"),
        ("Retrieve", "BIM quantity set + matching EF records + available process logs"),
        ("Judge", "transport distance missing -> status: partially executable"),
        ("Answer", "report known carbon, show formula, cite records, mark missing inputs"),
    ]
    x = 190
    for idx, (head, body) in enumerate(stages):
        bx = (x, 1250, x + 500, 1360)
        fill = [COLORS["purple"], COLORS["blue"], COLORS["yellow"], COLORS["green"]][idx]
        edge = [COLORS["purple_edge"], COLORS["blue_edge"], COLORS["yellow_edge"], COLORS["green_edge"]][idx]
        round_rect(d, bx, fill=fill, outline=edge, width=3, radius=18)
        d.text((x + 22, 1270), head, font=F_BOLD_SMALL, fill=COLORS["ink"])
        text_block(d, (x + 22, 1302), body, 445, F_TINY, COLORS["ink"], 3)
        if idx < len(stages) - 1:
            arrow(d, (x + 500, 1305), (x + 565, 1305), width=4)
        x += 565

    # Footer design note in figure itself is kept minimal for traceability.
    d.text((M + 20, H - M - 36), "Design pattern: RJE-style evidence judgment/exploration + OkraLong-style analyzer-organizer-executor + ComRAG-style QA memory update.", font=F_TINY, fill=COLORS["muted"])

    img.save(PNG_OUT, quality=96)
    img.save(PDF_OUT, "PDF", resolution=300.0)
    print(PNG_OUT)
    print(PDF_OUT)


if __name__ == "__main__":
    main()
