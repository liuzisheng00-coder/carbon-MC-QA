"""Print-sized composition with individually editable 9 pt labels."""
from pathlib import Path
import base64
import io
import json
import math
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont
import numpy as np

ROOT=Path('F:/论文4')
OUT=ROOT/'outputs/graph_label_revision/print_size'
OUT.mkdir(parents=True,exist_ok=True)
BG=ROOT/'outputs/graph_label_revision/graph_without_labels.png'
OVERVIEW=ROOT/'outputs/research_experiments/m2_typea1_full_en_layerdedup_20260731_d_spread/multigranular_carbon_kg_annotated_calculation_chain_123_view_single_fluorescent_green_boundary_2x_width_4x.png'
W,H=1260,795
MM=180
S=4
PT=9
FS=PT*W/(MM/25.4*72)
FONT='C:/Windows/Fonts/arialn.ttf'
F=ImageFont.truetype(FONT,round(FS*S))
FB=ImageFont.truetype('C:/Windows/Fonts/arialnb.ttf',round(10*W/(MM/25.4*72)*S))
OX,OY=300,60
# Exact original node anchors. Only labels are reflowed.
items=[
 ('EmissionFactor',209,24,224,24),
 ('CarbonEmission',150,88,5,42),
 ('ConsumptionQuantity',262,85,278,78),
 ('MaterialConsumption',217,134,5,134),
 ('Calcium silicate\nboard',164,175,25,185),
 ('DesignQuantity',358,114,374,113),
 ('DesignQuantity',432,149,447,149),
 ('DesignQuantity',295,164,240,137),
 ('DesignQuantity',309,185,325,187),
 ('DesignQuantity',234,215,70,231),
 ('DesignQuantity',382,216,398,215),
 ('Default Wall',153,274,69,293),
 ('Basic Wall:\nCalcium Silicate\nBoard: 2136584',309,263,65,325),
 ('Basic Wall:\nCalcium Silicate Board',231,301,45,325),
 ('DesignQuantity',395,294,411,300),
 ('Type-A Full Model\n(English)',340,340,358,343),
 ('DesignQuantity',289,358,302,380),
 ('EnergyConsumption',482,277,499,277),
 ('EnergyConsumption',454,360,471,361),
 ('EnergyConsumption',226,417,235,410),
 ('EnergyConsumption',306,452,320,449),
 ('EnergyConsumption',440,466,457,469),
 ('EnergyConsumption',249,642,260,638),
 ('electricity',379,402,394,399),
 ('ProductionStage',597,133,614,132),
 ('ManufacturingActivity',572,222,588,237),
 ('Spray coating equipment',651,207,668,200),
 ('ManufacturingActivity',584,394,600,418),
 ('Hand tools / installation\nscaffold and ladders',664,389,681,373),
 ('ProductionStage',630,474,647,475),
 ('ManufacturingActivity',482,546,499,544),
 ('Plate/pipe cutter\nand sandblaster',545,595,561,596),
 ('ProductionStage',460,635,478,636),
 ('ManufacturingActivity',273,562,290,563),
 ('MiC welding workstation / robot',329,665,345,680),
 ('ProductionStage',169,616,10,615),
 ('Manufacturing\nActivity',123,452,140,459),
 ('Weld NDT (MT/UT)',64,506,5,541),
 ('ProductionStage',36,419,10,395),
]
items=[(t.replace('DesignQuantity','Design\nQuantity'),nx,ny,px,py) for t,nx,ny,px,py in items]

bg=Image.open(BG).convert('RGB')
ink=(np.asarray(bg).min(axis=2)<230).astype(float)
integral=np.pad(ink.cumsum(axis=0).cumsum(axis=1),((1,0),(1,0)))
def occupancy(b):
    x1,y1,x2,y2=[int(v) for v in b]
    x1,x2=max(0,x1),min(bg.width,x2);y1,y2=max(0,y1),min(bg.height,y2)
    if x2<=x1 or y2<=y1:return 0
    return integral[y2,x2]-integral[y1,x2]-integral[y2,x1]+integral[y1,x1]
def intersect(a,b,pad=3):
    return min(a[2],b[2])+pad>max(a[0],b[0]) and min(a[3],b[3])+pad>max(a[1],b[1])
def dimensions(t):
    ls=t.split('\n');w=max(F.getlength(x) for x in ls)/S;h=len(ls)*FS*1.03
    return w,h
node_boxes=[]
for t,x,y,*_ in items:
    r=28 if t=='CarbonEmission' else 12
    node_boxes.append((x-r,y-r,x+r,y+r))
def nearest(node,b):
    x,y=node;return min(max(x,b[0]),b[2]),min(max(y,b[1]),b[3])
placements={}
# Place constrained dense cluster first, then peripheral branches.
priority=[0,1,2,3,4,37,36,12,13,15]+list(range(24,36))+[38]
order=priority+[i for i in range(len(items)) if i not in priority]
for i in order:
    t,nx,ny,px,py=items[i];tw,th=dimensions(t)
    candidates=[(px,py-th/2)]
    for dy in [0,-24,24,-48,48,-72,72,-100,100,-135,135,-170,170]:
        for dx in [16,-tw-16,-tw/2,40,-tw-40,80,-tw-80,130,-tw-130]:
            candidates.append((nx+dx,ny+dy-th/2))
    best=None
    for x,y in candidates:
        b=(x,y,x+tw,y+th)
        if x<2 or y<1 or b[2]>950 or b[3]>723:continue
        if any(intersect(b,p['box'],5) for p in placements.values()):continue
        if any(intersect(b,nb,3) for nb in node_boxes):continue
        ex,ey=nearest((nx,ny),b)
        dist=math.hypot(ex-nx,ey-ny)
        other_distances=[]
        for j,(_,other_x,other_y,*_) in enumerate(items):
            if j==i:continue
            other_end=nearest((other_x,other_y),b)
            other_distances.append(math.hypot(other_end[0]-other_x,other_end[1]-other_y))
        wrong_near=min(other_distances)
        ambiguity=200 if wrong_near<25 and wrong_near<dist*.7 else 0
        preference=math.hypot(x-px,(y+th/2)-py)
        score=dist*1.2+preference*.65+occupancy(b)*.12+ambiguity
        if best is None or score<best[0]:best=(score,b,dist,(ex,ey))
    if best is None:raise RuntimeError(f'No nonoverlapping placement for {i}: {t}')
    _,b,dist,endpoint=best
    placements[i]={'text':t,'node':(nx,ny),'box':b,'leader':dist>24,'endpoint':endpoint}

canvas=Image.new('RGB',(W*S,H*S),'white')
canvas.paste(bg.resize((bg.width*S,bg.height*S),Image.Resampling.LANCZOS),(OX*S,OY*S))
full=Image.open(OVERVIEW).convert('RGB')
full.thumbnail((275*S,310*S),Image.Resampling.LANCZOS)
leftx,lefty=10*S,245*S
canvas.paste(full,(leftx,lefty))
d=ImageDraw.Draw(canvas)
# Small neutral enlargement cue in the central gap.
cx,cy,rr=284,352,12
d.line((cx*S,cy*S,304*S,374*S),fill='#59758A',width=5*S)
d.line((cx*S,cy*S,304*S,374*S),fill='#EB8E4B',width=3*S)
d.ellipse(((cx-rr)*S,(cy-rr)*S,(cx+rr)*S,(cy+rr)*S),fill='#BAE6F5',outline='#59758A',width=2*S)
d.text((OX*S+475*S,23*S),'Calculation Information Subgraph',font=FB,fill='#111111',anchor='mm')

svg_ns='http://www.w3.org/2000/svg';ET.register_namespace('',svg_ns)
svg=ET.Element(f'{{{svg_ns}}}svg',{'width':'180mm','height':f'{MM*H/W}mm','viewBox':f'0 0 {W} {H}'})
# Raster graph geometry and editable lettering remain separate.
base=canvas.copy()
buf=io.BytesIO();base.save(buf,format='PNG')
ET.SubElement(svg,f'{{{svg_ns}}}image',{'width':str(W),'height':str(H),'href':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
for p in placements.values():
    nx,ny=p['node'];x,y,x2,y2=p['box'];ex,ey=p['endpoint']
    if p['leader']:
        dist=math.hypot(ex-nx,ey-ny);radius=28 if p['text']=='CarbonEmission' else 13
        sx=nx+(ex-nx)*radius/dist;sy=ny+(ey-ny)*radius/dist
        coords=((OX+sx)*S,(OY+sy)*S,(OX+ex)*S,(OY+ey)*S)
        d.line(coords,fill='#999999',width=max(1,round(.7*S)))
        ET.SubElement(svg,f'{{{svg_ns}}}line',{'x1':str(OX+sx),'y1':str(OY+sy),'x2':str(OX+ex),'y2':str(OY+ey),'stroke':'#999999','stroke-width':'.7'})
for p in placements.values():
    x,y,x2,y2=p['box']
    for k,line in enumerate(p['text'].split('\n')):
        yy=y+(k+.5)*FS*1.03
        d.text(((OX+x)*S,(OY+yy)*S),line,font=F,fill='#111111',anchor='lm',stroke_width=S,stroke_fill='white')
        q=ET.SubElement(svg,f'{{{svg_ns}}}text',{'x':str(OX+x),'y':str(OY+yy),'font-family':'Arial Narrow,Arial,sans-serif','font-size':str(FS),'dominant-baseline':'central','fill':'#111111','stroke':'white','stroke-width':'1.2','paint-order':'stroke'})
        q.text=line
ET.ElementTree(svg).write(OUT/'knowledge_graph_print_9pt.svg',encoding='utf-8',xml_declaration=True)
dpi=W*S/(MM/25.4)
canvas.save(OUT/'knowledge_graph_print_9pt.png',dpi=(dpi,dpi))
canvas.resize((W,H),Image.Resampling.LANCZOS).save(OUT/'preview.png')
report={'width_mm':MM,'height_mm':MM*H/W,'label_font_pt':PT,'render_font_px':FS,'labels':len(items),'short_leaders':sum(p['leader'] for p in placements.values()),'node_positions_changed':False,'placements':placements}
(OUT/'qa.json').write_text(json.dumps(report,indent=2),encoding='utf8')
print({k:v for k,v in report.items() if k!='placements'})
