"""Enlarge the 39 original labels without re-laying out the graph."""

from __future__ import annotations

import base64
import io
import json
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SOURCE = Path('C:/Users/liuzi/AppData/Local/Temp/codex-clipboard-5be2a6ea-4041-4cd4-b6a1-84d8521ea92e.png')
OUT = Path('F:/论文4/outputs/graph_label_revision')
OUT.mkdir(parents=True, exist_ok=True)
SCALE = 4
SOURCE_DPI = 150
ORIGINAL_ESTIMATED_PX = 11.0
FONT_PX = ORIGINAL_ESTIMATED_PX + 2 * SOURCE_DPI / 72
FONT_PATH = 'C:/Windows/Fonts/arial.ttf'

# Positions use the supplied panel's coordinate system; only label anchors move.
LABELS = [
    ('EmissionFactor', 224, 24),
    ('CarbonEmission', 108, 48),
    ('ConsumptionQuantity', 278, 80),
    ('MaterialConsumption', 232, 133),
    ('Calcium silicate board', 120, 190),
    ('DesignQuantity', 374, 113),
    ('DesignQuantity', 447, 149),
    ('DesignQuantity', 310, 163),
    ('DesignQuantity', 325, 186),
    ('DesignQuantity', 247, 218),
    ('DesignQuantity', 398, 217),
    ('Default Wall', 91, 295),
    ('Basic Wall:Calcium Silicate Board:2136584', 325, 251),
    ('Basic Wall:Calcium Silicate Board', 237, 321),
    ('DesignQuantity', 411, 299),
    ('Type-A Full Model (English)', 356, 341),
    ('DesignQuantity', 304, 367),
    ('EnergyConsumption', 499, 278),
    ('EnergyConsumption', 471, 361),
    ('EnergyConsumption', 241, 415),
    ('EnergyConsumption', 322, 448),
    ('EnergyConsumption', 457, 469),
    ('EnergyConsumption', 265, 642),
    ('electricity', 395, 399),
    ('ProductionStage', 614, 133),
    ('ManufacturingActivity', 588, 235),
    ('Spray coating equipment', 668, 207),
    ('ManufacturingActivity', 600, 416),
    ('Hand tools / installation\nscaffold and ladders', 680, 373),
    ('ProductionStage', 647, 475),
    ('ManufacturingActivity', 499, 545),
    ('Plate/pipe cutter and sandblaster', 561, 596),
    ('ProductionStage', 478, 636),
    ('ManufacturingActivity', 290, 563),
    ('MiC welding workstation / robot', 345, 667),
    ('ProductionStage', 184, 613),
    ('ManufacturingActivity', 139, 458),
    ('Weld NDT (MT/UT)', 80, 507),
    ('ProductionStage', 52, 414),
]

im = Image.open(SOURCE).convert('RGB')
original = np.asarray(im).copy()
clean = original.copy()
spread = original.max(axis=2).astype(int) - original.min(axis=2).astype(int)
# The source uses black/grey only for text. Colored graph pixels are retained.
text_pixels = (spread <= 3) & (original.min(axis=2) < 255)
clean[text_pixels] = 255
# Recover colored strokes darkened by the old black text without moving them.
# For each palette color, solve observed = k*white + k*a*(color-white).
# k is black-text transparency; a is the original edge-antialias coverage.
palette = np.array([(23,190,207),(31,119,180),(188,189,34),(148,103,189),
                    (255,127,14),(214,39,40),(140,86,75),(44,160,44)], dtype=float)
yy,xx = np.where(spread > 3)
observed = original[yy,xx].astype(float)
best_err = np.full(len(xx), np.inf)
restored = observed.copy()
for color in palette:
    basis = np.column_stack((np.full(3,255.), color-255.))
    coeff = observed @ np.linalg.pinv(basis).T
    k,a = coeff[:,0],coeff[:,1]
    predicted = coeff @ basis.T
    err = np.linalg.norm(predicted-observed,axis=1)
    valid = (k>.025)&(k<.98)&(a>0)&(a<=k*1.04)&(err<3)&(err<best_err)
    corrected = 255.+np.clip(a/np.maximum(k,.001),0,1)[:,None]*(color-255.)
    restored[valid] = corrected[valid]
    best_err[valid] = err[valid]
clean[yy,xx] = np.rint(np.clip(restored,0,255)).astype(np.uint8)
background = Image.fromarray(clean)
background.save(OUT / 'graph_without_labels.png')

font = ImageFont.truetype(FONT_PATH, round(FONT_PX * SCALE))
canvas = background.resize((im.width*SCALE, im.height*SCALE), Image.Resampling.LANCZOS)
draw = ImageDraw.Draw(canvas)
boxes = []
for text, x, y in LABELS:
    lines = text.split('\n')
    line_height = FONT_PX * 1.13
    for i, line in enumerate(lines):
        yy = y + (i-(len(lines)-1)/2)*line_height
        pos = (round(x*SCALE), round(yy*SCALE))
        draw.text(pos, line, fill='#111111', font=font, anchor='lm')
        bbox = draw.textbbox(pos, line, font=font, anchor='lm')
        boxes.append((line, tuple(round(v/SCALE, 2) for v in bbox)))

canvas.save(OUT / 'graph_labels_larger.png', dpi=(SOURCE_DPI*SCALE, SOURCE_DPI*SCALE))
canvas.resize(im.size, Image.Resampling.LANCZOS).save(OUT / 'graph_labels_larger_preview.png', dpi=(SOURCE_DPI, SOURCE_DPI))

# The same lettering remains individually editable in the companion SVG.
NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', NS)
root = ET.Element(f'{{{NS}}}svg', {'width':f'{im.width/SOURCE_DPI}in', 'height':f'{im.height/SOURCE_DPI}in', 'viewBox':f'0 0 {im.width} {im.height}'})
buf = io.BytesIO()
background.save(buf, format='PNG')
ET.SubElement(root, f'{{{NS}}}image', {'width':str(im.width), 'height':str(im.height), 'href':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
for text, x, y in LABELS:
    lines = text.split('\n')
    for i,line in enumerate(lines):
        yy = y+(i-(len(lines)-1)/2)*FONT_PX*1.13
        node = ET.SubElement(root, f'{{{NS}}}text', {'x':str(x),'y':str(yy),'font-family':'Arial, sans-serif','font-size':str(FONT_PX),'fill':'#111111','dominant-baseline':'central'})
        node.text = line
ET.ElementTree(root).write(OUT/'graph_labels_larger.svg', encoding='utf-8', xml_declaration=True)

counts = Counter(t.replace('\n',' ') for t,_,_ in LABELS)
assert len(LABELS) == 39
assert counts['DesignQuantity']==8 and counts['EnergyConsumption']==6
overlaps=[]
for i,(a,ab) in enumerate(boxes):
    for b,bb in boxes[i+1:]:
        if min(ab[2],bb[2])>max(ab[0],bb[0]) and min(ab[3],bb[3])>max(ab[1],bb[1]):
            overlaps.append([a,b])
report={'label_count':len(LABELS),'counts':dict(counts),'source_dpi':SOURCE_DPI,'font_px':FONT_PX,'approximate_increase_pt':2,'font_size_basis':'Original raster font size estimated as 11 px; 2 pt added at source DPI.','graph_geometry':'Original colored raster retained; old black lettering removed, including deblending where it crossed colored strokes. No node or edge repositioned.','text_bbox_overlaps':overlaps,'outside_bounds':[t for t,b in boxes if b[0]<0 or b[1]<0 or b[2]>im.width or b[3]>im.height]}
(OUT/'qa.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
