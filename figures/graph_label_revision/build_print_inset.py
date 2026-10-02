"""180 mm publication composition: readable subgraph plus overview inset."""
from pathlib import Path
import ast, base64, io, json, math
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont

ROOT=Path('F:/论文4')
OUT=ROOT/'outputs/graph_label_revision/print_inset'
OUT.mkdir(parents=True,exist_ok=True)
tree=ast.parse((ROOT/'figures/graph_label_revision/enlarge_labels.py').read_text(encoding='utf-8-sig'))
labels=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='LABELS' for t in n.targets))
# Retain all label strings. Move one peripheral label to leave an overview inset.
labels[24]=(labels[24][0],535,106)
W,H,S=915,740,4
DY=35
MM,PT=180,9
FS=PT*W/(MM/25.4*72)
font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',round(FS*S))
small=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',round(8*W/(MM/25.4*72)*S))
bold=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf',round(FS*S))
bg=Image.open(ROOT/'outputs/graph_label_revision/graph_without_labels.png').convert('RGB')
canvas=Image.new('RGB',(W*S,H*S),'white')
canvas.paste(bg.resize((bg.width*S,bg.height*S),Image.Resampling.LANCZOS),(0,DY*S))
overview=Image.open(ROOT/'outputs/research_experiments/m2_typea1_full_en_layerdedup_20260731_d_spread/multigranular_carbon_kg_annotated_calculation_chain_123_view_single_fluorescent_green_boundary_2x_width_4x.png').convert('RGB')
overview.thumbnail((182*S,160*S),Image.Resampling.LANCZOS)
canvas.paste(overview,(round((805-overview.width/S/2)*S),5*S))
d=ImageDraw.Draw(canvas)
d.text((805*S,178*S),'Overall graph',font=small,fill='#333333',anchor='mm')
d.text((5*S,16*S),'Calculation information subgraph',font=bold,fill='#111111',anchor='lm')
# Short neutral leader for the moved ProductionStage label.
d.line((597*S,(133+DY-12)*S,597*S,(106+DY+10)*S),fill='#888888',width=2)
NS='http://www.w3.org/2000/svg';ET.register_namespace('',NS)
svg=ET.Element(f'{{{NS}}}svg',{'width':'180mm','height':f'{MM*H/W}mm','viewBox':f'0 0 {W} {H}'})
buf=io.BytesIO();canvas.save(buf,format='PNG')
ET.SubElement(svg,f'{{{NS}}}image',{'width':str(W),'height':str(H),'href':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
boxes=[]
for text,x,y in labels:
    lines=text.split('\n')
    for i,line in enumerate(lines):
        yy=y+DY+(i-(len(lines)-1)/2)*FS*1.13
        pos=(round(x*S),round(yy*S))
        d.text(pos,line,font=font,fill='#111111',anchor='lm',stroke_width=2,stroke_fill='white')
        box=d.textbbox(pos,line,font=font,anchor='lm')
        boxes.append((line,[v/S for v in box]))
        e=ET.SubElement(svg,f'{{{NS}}}text',{'x':str(x),'y':str(yy),'font-family':'Arial, sans-serif','font-size':str(FS),'dominant-baseline':'central','fill':'#111111','stroke':'white','stroke-width':'0.5','paint-order':'stroke'})
        e.text=line
overlaps=[]
for i,(a,ab) in enumerate(boxes):
    for b,bb in boxes[i+1:]:
        if min(ab[2],bb[2])>max(ab[0],bb[0]) and min(ab[3],bb[3])>max(ab[1],bb[1]):overlaps.append([a,b])
outside=[t for t,b in boxes if b[0]<0 or b[1]<0 or b[2]>W or b[3]>H]
canvas.save(OUT/'knowledge_graph_18cm_9pt.png',dpi=(W*S/(MM/25.4),)*2)
canvas.resize((W,H),Image.Resampling.LANCZOS).save(OUT/'preview.png')
ET.ElementTree(svg).write(OUT/'knowledge_graph_18cm_9pt.svg',encoding='utf-8',xml_declaration=True)
report={'width_mm':MM,'height_mm':MM*H/W,'label_font_pt':PT,'label_font_px':FS,'label_count':len(labels),'label_overlaps':overlaps,'outside_bounds':outside,'node_positions_changed':False,'overview':'Inset in previously empty upper-right space'}
(OUT/'qa.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(report)
