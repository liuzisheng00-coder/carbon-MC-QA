"""Enlarge text in the original calculation graph interface without moving nodes."""

from pathlib import Path
import json
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image, ImageFont
import numpy as np

from enlarge_screenshot_text import QUESTIONS, ANSWERS

HERE=Path(__file__).resolve().parent
FONT='C:/Windows/Fonts/arial.ttf'
BOLD='C:/Windows/Fonts/arialbd.ttf'
BASE_DPI=62.4
SCALE=3
CHAT_PX=10.4


def render():
    source=Image.open(HERE/'source_graph_recovered.png').convert('RGB')
    width,height=source.size
    raw=np.asarray(source)
    clean=raw.copy()
    plt.rcParams.update({'svg.fonttype':'none','pdf.fonttype':42})
    node_specs=[]

    def node(x,y,w,h,kind,identifier,value,light=False):
        # Interior replacement leaves node boxes, borders and all edges intact.
        colour=raw[round(y+3),round(x+w-4)]
        clean[int(y+2):int(y+h-2),int(x+2):int(x+w-2)]=colour
        node_specs.append((x,y,w,h,kind,identifier,value,light))

    node(22,133,104,35,'ModularUnit','Type-A Full Model (En...','ModularUnit:0f923aaafa62d...',True)
    node(127,133,111,35,'BuildingComponent','Basic Wall:Calcium Si...',None)
    node(127,171,111,35,'ComponentType','Basic Wall:Calcium Si...','ComponentType:ccff8c5cf7a...')
    energy_ids=['240...','47f...','480...','63a...','a42...']
    energy_details=[
        'allocated | SYN_E_LINE_A_001',
        'allocated | SYN_WS_05_001',
        'allocated | SYN_E_MODULE_003',
        'allocated | SYN_WS_01_001',
        'allocated | SYN_E_FACTORY...',
    ]
    for y,ident,value in zip([133,171,209,247,285],energy_ids,energy_details):
        node(253,y,111,35,'EnergyConsumption','EnergyConsumption:'+ident,value)
    node(253,323,111,35,'MaterialConsumption','MaterialConsumption:7...',
         'area_thickness_density | ...')
    carbon_ids=['1335b5...','2945ff...','5d1b8c...','5f39ae...','7d7c0f...','c61ad...']
    emissions=['15.53','38.676','207.055','33.715','492.308','155.922']
    for y,ident,value in zip([133,171,209,247,285,323],carbon_ids,emissions):
        node(378,y,111,35,'CarbonEmission','CarbonEmission:'+ident,value+' kgCO2e')
    quantities=['952.608 kWh','74.838 kWh','65.238 kWh','301.707 kWh','266.034 kg','30.05 kWh']
    quantity_ids=['1...','2...','2...','7...','c...','d...']
    for y,ident,value in zip([133,171,209,247,285,323],quantity_ids,quantities):
        node(503,y,111,35,'ConsumptionQuantity','ConsumptionQuantity:'+ident,value,True)
    node(503,362,111,35,'DesignQuantity','DesignQuantity:10d738...','0.048 m2',True)
    node(503,400,111,35,'DesignQuantity','DesignQuantity:820635...','5.939 m',True)
    # The lowest card is partially outside the source viewport; retain that crop.
    clean[441:453,505:612]=raw[443,610]

    # Keep the exact source component identifier strip inside its node.
    clean[160:166,135:235]=raw[160:166,135:235]
    # Source IDs have intentionally truncated suffixes; enlargement does not infer them.
    for y in [484,500,515,531,546,562]:
        clean[y:y+10,23:630]=raw[y:y+10,620:621]
    clean[469:481,23:630]=[255,255,255]
    clean[65:102,22:639]=[255,255,255]
    clean[17:34,450:552]=[253,253,253]

    fig=plt.figure(figsize=((width+.001)/BASE_DPI,(height+.001)/BASE_DPI),dpi=BASE_DPI)
    ax=fig.add_axes([0,0,1,1])
    ax.set(xlim=(0,width),ylim=(height,0)); ax.axis('off')
    ax.imshow(clean,extent=(0,width,height,0),interpolation='none')
    texts=[]

    def text(x,y,s,px=8.5,bold=False,color='#303030',ha='left',box=None):
        item=ax.text(x,y,s,va='top',ha=ha,color=color,
                     fontproperties=FontProperties(fname=BOLD if bold else FONT),
                     fontsize=px*72/BASE_DPI,zorder=5)
        texts.append((item,box))
        return item

    def rect(x,y,w,h,color='white',radius=0):
        if radius:
            ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle=f'round,pad=0,rounding_size={radius}',
                         facecolor=color,edgecolor='none',zorder=3))
        else:
            ax.add_patch(Rectangle((x,y),w,h,facecolor=color,edgecolor='none',zorder=3))

    for x,y,w,h,kind,identifier,value,light in node_specs:
        if kind=='ModularUnit':
            face,edge='#e8e5dc','#b3afa2'
        elif kind in ['BuildingComponent','ComponentType']:
            face,edge='#e64d4d','#a43939'
        elif kind in ['EnergyConsumption','MaterialConsumption']:
            face,edge='#7668c9','#5b51ac'
        elif kind=='CarbonEmission':
            face,edge='#d76552','#ad4f3e'
        else:
            face,edge='#eaf2ee','#82ae99'
        ax.add_patch(FancyBboxPatch((x-.25,y-.7),w+.5,h+1.4,
                     boxstyle='round,pad=0,rounding_size=2.5',facecolor=face,
                     edgecolor=edge,linewidth=.7,zorder=3))
        color='#284839' if light else 'white'
        b=(x+3,y+1,x+w-3,y+h-1)
        text(x+6,y+4,kind,8.4,True,color,box=b)
        text(x+6,y+15,identifier,7.2,True,color,box=b)
        if value:
            text(x+5,y+25,value,6.8,False,'#617b6f' if light else '#ffffff',box=b)
        elif kind=='BuildingComponent':
            ax.imshow(source.crop((135,160,235,166)),extent=(x+6,x+106,y+33,y+26),
                      interpolation='none',zorder=4)
    text(509,442,'DesignQuantity',8.4,True,'#284839')

    text(22,66,'BIM to carbon graph',11,True,'#202020')
    # ID itself is preserved as raster to avoid guessing visually ambiguous glyphs.
    ax.imshow(source.crop((22,78,151,86)),extent=(22,190,91,81),interpolation='none',zorder=4)
    text(22,92,'Root BuildingComponent:1c72eb7b15172...',8.0,True)
    text(422,67,'Selected subgraph',9.5,True)
    text(523,67,'Project mapping',9.5,color='#777777')
    ax.plot([422,514],[82,82],color='#242424',linewidth=1.2,zorder=4)
    text(638,91,'34 nodes | 44 edges | canonical component closure',8,color='#808080',ha='right')
    text(453,20,'DM2C Carbon',10.5,True)

    xs=[24,180,324,480]
    for x,title in zip(xs,['Source','Relation','Target','Occurrence']):
        text(x,470,title,8.8,True,'#666666')
    suffixes=['ccff8...','10d7...','8206...','9cb7...','a6cc...','acd3...']
    for i,(y,suffix) in enumerate(zip([485,501,516,532,547,563],suffixes)):
        text(xs[0],y,'BuildingComponent:1...',8)
        text(xs[1],y,'hasComponentType' if i==0 else 'hasDesignQuantity',8,True,'#5546af')
        text(xs[2],y,('ComponentType:' if i==0 else 'DesignQuantity:')+suffix,8)
        text(xs[3],y,'ComponentTypeAssign...' if i==0 else 'DesignQuantity:'+suffix,8)

    def wrap(s,width,px=CHAT_PX):
        font=ImageFont.truetype(FONT,round(px*10)); result=[]
        for para in s.split('\n'):
            line=''
            for word in para.split():
                attempt=(line+' '+word).strip()
                if line and font.getlength(attempt)/10>width:
                    result.append(line);line=word
                else: line=attempt
            result.append(line)
        return result

    def bubble(x,y,w,s,question=False,badge=False):
        lines=wrap(s,w-16)
        extra=16 if badge else 0
        h=10+len(lines)*11.5+extra
        rect(x,y,w,h,'#0070e8' if question else '#f1f1f3',5)
        if badge:
            ax.imshow(source.crop((734,438,859,449)),
                      extent=(x+8,x+151,y+19,y+6),interpolation='none',zorder=4)
        for i,line in enumerate(lines):
            text(x+8,y+4+extra+i*11.5,line,CHAT_PX,color='white' if question else '#363636',
                 box=(x+4,y+2,x+w-3,y+h))
        return y+h

    rect(664,55,260,25)
    text(672,62,'Ask DM2C',10.4,True)
    ax.plot(865,67,marker='o',markersize=3,color='#238944',zorder=5)
    text(873,62,'Grounded',8.5,True,'#238944')
    rect(664,81,252,488)
    intro=('Components: 321\nGraph nodes: 2993\nGraph edges: 5613\n'
           'Select a component in the model, or ask about this project carbon assessment.')
    y=bubble(672,84,237,intro)
    for i,(q,a) in enumerate(zip(QUESTIONS,ANSWERS)):
        y=max(y+8,[168,293,430][i])
        y=bubble(685 if i<2 else 701,y,225 if i<2 else 209,q,True,badge=i==2)
        y+=6
        y=bubble(672,y,243,a)
        y+=5
        ax.plot([673,676,673],[y+3,y+6,y+9],color='#0070e8',linewidth=.8,zorder=5)
        text(681,y,'Show evidence',9.6,True,'#0070e8')
        y+=12
    if y>569:
        raise RuntimeError(f'Chat overflows source panel: {y} > 569')

    # Enlarge navigation labels in their original positions and active Graph tab.
    for x,end,label,active in [(38,63,'Model',False),(83,110,'Product',False),
                              (132,160,'Material',False),(181,209,'Process',False),
                              (229,257,'Graph',True)]:
        rect(x-1,607,end-x+1,12,'#1c1c1c' if active else 'white')
        text(x,607,label,7.8,True,'white' if active else '#64666b')
    rect(676,603,188,16)
    text(678,605,'Ask about the selected component...',8.6,color='#888888')

    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    for item,box in texts:
        b=item.get_window_extent(renderer).transformed(ax.transData.inverted())
        x0,x1=sorted([b.x0,b.x1]);y0,y1=sorted([b.y0,b.y1])
        if not(-.1<=x0<x1<=width+.1 and -.1<=y0<y1<=height+.1):
            raise RuntimeError('Outside canvas: '+item.get_text())
        if box:
            left,top,right,bottom=box
            if x0<left-.2 or x1>right+.2 or y0<top-.2 or y1>bottom+.2:
                raise RuntimeError('Outside box: '+item.get_text()+f' {x0,x1,y0,y1} {box}')
    out=HERE/'original_layout_graph_text_enlarged'
    for ext in ['png','svg','pdf']:
        fig.savefig(out.with_suffix('.'+ext),dpi=BASE_DPI*SCALE,bbox_inches=None,pad_inches=0)
    out.with_suffix('.json').write_text(json.dumps({
        'source_size':[width,height],'output_pixels':[width*SCALE,height*SCALE],
        'chat_font_native_pixels':CHAT_PX,'graph_type_font_native_pixels':8.4,
        'emissions':emissions,'quantities':quantities,'last_chat_y':y,
        'notes':'Recovered the matching original embedded screenshot; enlarged text within original node positions; original edges retained; identifiers remain abbreviated.'
    },indent=2),encoding='utf-8')
    plt.close(fig)
    print(json.dumps({'output':str(out),'chat_bottom':y,'layout_check':'passed'}))


if __name__=='__main__':
    render()
