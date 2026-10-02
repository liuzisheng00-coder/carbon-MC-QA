"""Larger editable text over the original three-account interface layout."""

from pathlib import Path
import argparse
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch
from PIL import Image
import numpy as np


HERE = Path(__file__).resolve().parent
BASE_DPI = 96
EXPORT_DPI = 288
BODY_PX = 16
FONT = "C:/Windows/Fonts/arial.ttf"
BOLD = "C:/Windows/Fonts/arialbd.ttf"
PANELS = [
    dict(title="Product account", title_y=6, subtitle_y=35,
         subtitle="Components and modules projected from accepted canonical carbon records.",
         title_x=20, header_y=75, header_top=60, header_bottom=90,
         centres=[110,147,184], item_x=31, context_x=517,
         mat_x=742, proc_x=842, nav_y=230, active="Product",
         rows=[
             ["Floor Slab:110mm:2130995", "IfcSlab", "664.2", "88"],
             ["200x100x14 Steel Purlin:C1:2130673", "IfcColumn", "345.6", "3.9"],
             ["200x100x14 Steel Purlin:C1:2130675", "IfcColumn", "345.6", "3.9"],
         ]),
    dict(title="Material account", title_y=284, subtitle_y=314,
         subtitle="IFC materials projected from the same accepted emissions.",
         title_x=11, header_y=356, header_top=340, header_bottom=371,
         centres=[391,428,466], item_x=23, context_x=373,
         mat_x=646, total_x=766, nav_y=506, active="Material",
         rows=[
             ["Metal - Steel 43 - 355_A1", "45 component(s)", "4,176.7", "4,176.7"],
             ["Concrete, Cast-in-Place Gray", "2 component(s)", "784.6", "784.6"],
             ["Calcium silicate board", "9 component(s)", "594.8", "594.8"],
         ]),
    dict(title="Process account", title_y=552, subtitle_y=582,
         subtitle="Factory activities, energy use, and process evidence.",
         title_x=11, header_y=623, header_top=607, header_bottom=638,
         centres=[658,696,733], item_x=23, context_x=372,
         mat_x=646, total_x=766, nav_y=775, active="Process",
         rows=[
             ["Steel material intake & inspection", "diesel", "519.2", "519.2"],
             ["Post-weld repair & NDT", "electricity", "492.3", "492.3"],
             ["Steel frame welding", "electricity", "159.2", "159.2"],
         ]),
]


def render(source):
    raw = Image.open(source).convert("RGB")
    if raw.size != (915,798):
        raise ValueError("Expected the supplied 915 x 798 three-panel screenshot")
    original = np.asarray(raw)
    clean = original.copy()
    width,height = raw.size

    def erase(box, sample_x):
        x0,y0,x1,y1 = box
        clean[y0:y1,x0:x1] = original[y0:y1,sample_x:sample_x+1]

    # Erase only source text and status badges; keep frames, row rules and icons.
    for panel in PANELS:
        x = panel['title_x']
        erase((x,panel['title_y']-2,600,panel['subtitle_y']+17),800)
        erase((panel['item_x']-2,panel['header_top']+4,
               868 if panel['active']=='Product' else 878,
               panel['header_bottom']-2),860)
        for y in panel['centres']:
            erase((panel['item_x']-2,y-15,886,y+15),880)

    nav_items = [
        # label, ordinary x, selected x, right edge of the original label
        ("Model",45,45,78), ("Product",108,106,150),
        ("Material",179,178,224), ("Process",250,252,292),
        ("Graph",318,318,354),
    ]
    for panel in PANELS:
        y = panel['nav_y']
        for label,x,selected_x,end in nav_items:
            # Sample a uniform patch within each original button.
            active = label == panel['active']
            if active:
                left = {'Product':81,'Material':154,'Process':222}[label]
                colour = original[y-12,left+4]
            else:
                colour = np.array([255,255,255],dtype=np.uint8)
            clean[y-8:y+9,x-2:end] = colour

    clean[757:792,222:298] = [255,255,255]

    plt.rcParams.update({'svg.fonttype':'none','pdf.fonttype':42})
    fig = plt.figure(figsize=(width/BASE_DPI,height/BASE_DPI),dpi=BASE_DPI)
    ax = fig.add_axes([0,0,1,1])
    ax.set(xlim=(0,width),ylim=(height,0))
    ax.axis('off')
    ax.imshow(clean,extent=(0,width,height,0),interpolation='none')
    # The longer enlarged Process label needs 5 px more inside the active tab.
    ax.add_patch(FancyBboxPatch((226,759),74,31,
                 boxstyle='round,pad=0,rounding_size=4',facecolor='#1e1e1e',
                 edgecolor='none',zorder=2))
    ax.imshow(raw.crop((230,766,245,784)),extent=(234,249,784,766),
              interpolation='none',zorder=2.5)
    checks=[]

    def label(x,y,s,size=BODY_PX,bold=False,color='#303030',ha='left',va='center',box=None):
        item=ax.text(x,y,s,fontproperties=FontProperties(fname=BOLD if bold else FONT),
                     fontsize=size*72/BASE_DPI,color=color,ha=ha,va=va,zorder=3)
        checks.append((item,box))
        return item

    for p in PANELS:
        label(p['title_x'],p['title_y'],p['title'],24,True,'#202020',va='top')
        label(p['title_x'],p['subtitle_y'],p['subtitle'],14,color='#757575',va='top')
        hy=p['header_y']
        label(p['item_x'],hy,'ITEM',13,True,'#65676c')
        label(p['context_x'],hy,'CONTEXT',13,True,'#65676c')
        if p['active']=='Product':
            label(724,hy,'C_MAT',13,True,'#65676c',ha='center')
            label(827,hy,'C_PROC',13,True,'#65676c',ha='center')
        else:
            label(628,hy,'C_MAT' if p['active']=='Material' else 'C_PROC',
                  13,True,'#65676c',ha='center')
            # Two lines retain the full original field name in its original column.
            label(732,hy-7,'KNOWN',12,True,'#65676c',ha='center')
            label(732,hy+7,'TOTAL',12,True,'#65676c',ha='center')
            label(788,hy,'STATUS',13,True,'#65676c')

        for y,row in zip(p['centres'],p['rows']):
            label(p['item_x'],y,row[0],bold=True,
                  box=(p['item_x'],y-17,p['context_x']-10,y+17))
            label(p['context_x'],y,row[1],
                  box=(p['context_x'],y-17,600 if p['active']!='Product' else 699,y+17))
            label(p['mat_x'],y,row[2],ha='right',
                  box=(580 if p['active']!='Product' else 701,y-17,p['mat_x']+1,y+17))
            last_x=p['proc_x'] if p['active']=='Product' else p['total_x']
            label(last_x,y,row[3],ha='right',bold=p['active']!='Product')
            if p['active']!='Product':
                ax.add_patch(FancyBboxPatch((787,y-12),72,24,
                             boxstyle='round,pad=0,rounding_size=12',facecolor='#f0f8f1',
                             edgecolor='#41a25e',linewidth=.6,zorder=2))
                label(823,y,'measured',12,True,'#188234',ha='center')

        for s,x,selected_x,end in nav_items:
            active=s==p['active']
            label(selected_x if active else x,p['nav_y'],s,11.5,True,
                  'white' if active else '#64666a')

    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    for obj,box in checks:
        b=obj.get_window_extent(renderer).transformed(ax.transData.inverted())
        x0,x1=sorted([b.x0,b.x1]);y0,y1=sorted([b.y0,b.y1])
        if not(-.1<=x0<x1<=width+.1 and -.1<=y0<y1<=height+.1):
            raise RuntimeError('Text outside figure: '+obj.get_text())
        if box is not None:
            left,top,right,bottom=box
            if x0<left-.1 or x1>right+.1 or y0<top or y1>bottom:
                raise RuntimeError('Text exceeds assigned cell: '+obj.get_text())

    out=HERE/'original_layout_accounts_text_enlarged'
    for ext in ['png','svg','pdf']:
        fig.savefig(out.with_suffix('.'+ext),dpi=EXPORT_DPI,bbox_inches=None,pad_inches=0)
    report={'source_size':[width,height],'png_size':[width*3,height*3],
            'table_font_native_pixels':BODY_PX,'table_font_points_at_natural_size':12,
            'natural_width_cm':width/BASE_DPI*2.54,'panels':PANELS,
            'notes':'Original UI composition, rules and icons retained; text redrawn; partially hidden product column left as supplied.'}
    out.with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    plt.close(fig)
    print(json.dumps({'output':str(out),'font_native_pixels':BODY_PX,'layout_check':'passed'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    render(parser.parse_args().source)
