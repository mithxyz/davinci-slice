"""Local map thumbnail and matching hit geometry, using only the standard library."""
import base64
import math
import struct
import zlib

PREVIEW_W, PREVIEW_H, PAD = 1280, 800, 24
PALETTE = [(36,174,223),(155,109,238),(244,178,65),(56,197,157),(234,111,148),(83,139,239)]
FONT = {
    '0':['01110','10001','10011','10101','11001','10001','01110'],
    '1':['00100','01100','00100','00100','00100','00100','01110'],
    '2':['01110','10001','00001','00010','00100','01000','11111'],
    '3':['11110','00001','00001','01110','00001','00001','11110'],
    '4':['00010','00110','01010','10010','11111','00010','00010'],
    '5':['11111','10000','10000','11110','00001','00001','11110'],
    '6':['01110','10000','10000','11110','10001','10001','01110'],
    '7':['11111','00001','00010','00100','01000','01000','01000'],
    '8':['01110','10001','10001','01110','10001','10001','01110'],
    '9':['01110','10001','10001','01111','00001','00001','01110'],
    'A':['01110','10001','10001','11111','10001','10001','10001'],
    'B':['11110','10001','10001','11110','10001','10001','11110'],
    'C':['01111','10000','10000','10000','10000','10000','01111'],
    'D':['11110','10001','10001','10001','10001','10001','11110'],
    'E':['11111','10000','10000','11110','10000','10000','11111'],
    'F':['11111','10000','10000','11110','10000','10000','10000'],
    'G':['01111','10000','10000','10111','10001','10001','01111'],
    'H':['10001','10001','10001','11111','10001','10001','10001'],
    'I':['01110','00100','00100','00100','00100','00100','01110'],
    'J':['00111','00010','00010','00010','10010','10010','01100'],
    'K':['10001','10010','10100','11000','10100','10010','10001'],
    'L':['10000','10000','10000','10000','10000','10000','11111'],
    'M':['10001','11011','10101','10101','10001','10001','10001'],
    'N':['10001','11001','10101','10011','10001','10001','10001'],
    'O':['01110','10001','10001','10001','10001','10001','01110'],
    'P':['11110','10001','10001','11110','10000','10000','10000'],
    'Q':['01110','10001','10001','10001','10101','10010','01101'],
    'R':['11110','10001','10001','11110','10100','10010','10001'],
    'S':['01111','10000','10000','01110','00001','00001','11110'],
    'T':['11111','00100','00100','00100','00100','00100','00100'],
    'U':['10001','10001','10001','10001','10001','10001','01110'],
    'V':['10001','10001','10001','10001','10001','01010','00100'],
    'W':['10001','10001','10001','10101','10101','10101','01010'],
    'X':['10001','10001','01010','00100','01010','10001','10001'],
    'Y':['10001','10001','01010','00100','00100','00100','00100'],
    'Z':['11111','00001','00010','00100','01000','10000','11111'],
    '-':['00000','00000','00000','11111','00000','00000','00000'],
    ' ':['00000']*7,
}


def flatten_contour(contour, tolerance):
    points = contour['points']
    out = []

    def curve(a,b,c,d,depth=0):
        # Convex hull distance to the chord bounds the cubic's screen error.
        dx,dy = d[0]-a[0],d[1]-a[1]
        length = math.hypot(dx,dy)
        error = max(abs(dx*(p[1]-a[1])-dy*(p[0]-a[0]))/length if length else math.dist(a,p) for p in (b,c))
        if error <= tolerance or depth >= 12:
            out.append(d); return
        ab,bc,cd = [tuple((u[k]+v[k])/2 for k in (0,1)) for u,v in ((a,b),(b,c),(c,d))]
        abc,bcd = [tuple((u[k]+v[k])/2 for k in (0,1)) for u,v in ((ab,bc),(bc,cd))]
        mid = tuple((abc[k]+bcd[k])/2 for k in (0,1))
        curve(a,ab,abc,mid,depth+1); curve(mid,bcd,cd,d,depth+1)

    for i,p in enumerate(points):
        q = points[(i+1)%len(points)]
        a,d = (p['x'],p['y']),(q['x'],q['y'])
        if i == 0: out.append(a)
        if contour['segments'][i] == 'C':
            curve(a,(p['x']+p['rx'],p['y']+p['ry']),
                  (q['x']+q['lx'],q['y']+q['ly']),d)
        else: out.append(d)
    return out


def normalize_view(view=None):
    view=view or {}
    values={k:float(view.get(k,default)) for k,default in [('zoom',1),('cx',.5),('cy',.5)]}
    if not all(math.isfinite(v) for v in values.values()):
        raise ValueError('Preview view must contain finite numbers')
    values['zoom']=min(16,max(1,values['zoom']))
    for key in ('cx','cy'):
        values[key]=.5 if values['zoom']==1 else min(1,max(0,values[key]))
    return values


def preview_model(catalog,view=None):
    width,height = catalog['width'],catalog['height']
    view=normalize_view(view)
    scale = min((PREVIEW_W-2*PAD)/width,(PREVIEW_H-2*PAD)/height)*view['zoom']
    left,top = PREVIEW_W/2-width*scale*view['cx'],PREVIEW_H/2-height*scale*view['cy']
    shapes = []
    for index,s in enumerate(catalog['slices'],1):
        if not all(k in s for k in ('x','y','width','height')):
            continue
        def project(p): return [left+p[0]*scale,top+p[1]*scale]
        constraints = []
        if 'contour' in s:
            constraints.append(dict(points=[project(p) for p in flatten_contour(s['contour'],.25/scale)],inside=True))
        for m in s.get('masks',[]):
            constraints.append(dict(points=[project(p) for p in flatten_contour(m['contour'],.25/scale)],inside=m['keep_inside']))
        shapes.append(dict(index=index,name=s['name'],supported=s['supported'],
                           rect=[left+s['x']*scale,top+s['y']*scale,s['width']*scale,s['height']*scale],
                           constraints=constraints))
    return dict(width=PREVIEW_W,height=PREVIEW_H,view=view,
                canvas=[left,top,width*scale,height*scale],shapes=shapes)


def intersect(a,b):
    out=[]; i=j=0
    while i<len(a) and j<len(b):
        lo,hi=max(a[i][0],b[j][0]),min(a[i][1],b[j][1])
        if hi>lo: out.append((lo,hi))
        if a[i][1]<b[j][1]: i+=1
        else: j+=1
    return out


def subtract(a,b):
    result=[]
    for lo,hi in a:
        for bl,bh in b:
            if bh<=lo: continue
            if bl>=hi: break
            if bl>lo: result.append((lo,min(bl,hi)))
            lo=max(lo,bh)
            if lo>=hi: break
        if lo<hi: result.append((lo,hi))
    return result


def row_intervals(points,y):
    xs=[]
    for a,b in zip(points,points[1:]+points[:1]):
        if (a[1]>y)!=(b[1]>y):
            xs.append(a[0]+(y-a[1])*(b[0]-a[0])/(b[1]-a[1]))
    xs.sort()
    return list(zip(xs[::2],xs[1::2]))


def shape_row(shape,y,canvas):
    x,t,w,h=shape['rect']; cx,cy,cw,ch=canvas
    if not (t<=y<t+h and cy<=y<cy+ch): return []
    out=intersect([(x,x+w)],[(cx,cx+cw)])
    for c in shape['constraints']:
        cuts=row_intervals(c['points'],y)
        out=intersect(out,cuts) if c['inside'] else subtract(out,cuts)
    return out


def point_in_polygon(points,x,y):
    return any(a<=x<b for a,b in row_intervals(points,y))


def hit_test(model,x,y):
    """Last drawn available shape wins; gaps and mask holes remain unclickable."""
    for s in reversed(model['shapes']):
        if any(a<=x<b for a,b in shape_row(s,y,model['canvas'])):
            return s['index'] if s['supported'] else None
    return None


def png_rgb(width,height,pixels):
    def chunk(tag,data):
        return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    rows=b''.join(b'\0'+pixels[y*width*3:(y+1)*width*3] for y in range(height))
    return (b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
            +chunk(b'IDAT',zlib.compress(rows,6))+chunk(b'IEND',b''))


def render_preview(model,names):
    """Return a PNG thumbnail; bitmap labels keep the installed helper portable."""
    selected=set(names); width,height=model['width'],model['height']
    pixels=bytearray(bytes((14,18,25))*(width*height))
    cx,cy,cw,ch=model['canvas']
    labels=[]
    for shape in model['shapes']:
        active=shape['supported'] and shape['name'] in selected
        color=PALETTE[(shape['index']-1)%len(PALETTE)] if active else (48,58,73)
        edge=tuple(min(255,int(c*.6+100)) for c in color)
        x,t,w,h=shape['rect']; rows=[]; previous=[]
        for row in range(max(0,int(t)),min(height,math.ceil(t+h))):
            spans=shape_row(shape,row+.5,model['canvas'])
            ints=[(max(0,math.ceil(a-.5)),min(width,math.ceil(b-.5))) for a,b in spans]
            ints=[(a,b) for a,b in ints if a<b]
            rows.append((row,spans))
            for a,b in ints:
                start=(row*width+a)*3
                pixels[start:start+(b-a)*3]=bytes(color)*(b-a)
                pixels[start:start+3]=bytes(edge)
                pixels[(row*width+b-1)*3:(row*width+b)*3]=bytes(edge)
            for a,b in subtract(ints,previous):
                start=(row*width+a)*3; pixels[start:start+(b-a)*3]=bytes(edge)*(b-a)
            for a,b in subtract(previous,ints):
                if row>0:
                    start=((row-1)*width+a)*3; pixels[start:start+(b-a)*3]=bytes(edge)*(b-a)
            previous=ints
        for a,b in previous:
            row=min(height-1,math.ceil(t+h)-1)
            start=(row*width+a)*3; pixels[start:start+(b-a)*3]=bytes(edge)*(b-a)
        # Prefer the bounding-box centre; for a concavity choose a wide filled row.
        label_row=None
        for a,b in shape_row(shape,t+h/2,model['canvas']):
            a,b=max(0,a),min(width,b)
            if a<=x+w/2<b and 0<=t+h/2<height:
                label_row=(float('inf'),x+w/2,t+h/2,b-a)
        for row,spans in rows:
            for a,b in spans:
                a,b=max(0,a),min(width,b)
                if a>=b: continue
                score=(b-a)-abs(row-(t+h/2))*.25
                if label_row is None or score>label_row[0]: label_row=(score,(a+b)/2,row,b-a)
        if label_row:
            _,lx,ly,space=label_row
            full=f"{shape['index']} {shape['name']}".upper()
            label=full if space>=len(full)*24+12 and all(c in FONT for c in full) else str(shape['index'])
            labels.append((lx,ly,label))
    def box(x,y,w,h,color):
        a,b=max(0,x),min(width,x+w)
        if a>=b: return
        for row in range(max(0,y),min(height,y+h)):
            start=(row*width+a)*3; pixels[start:start+(b-a)*3]=bytes(color)*(b-a)
    for lx,ly,label in labels:
        size=4; w=len(label)*6*size-size; h=7*size
        x,y=round(lx-w/2),round(ly-h/2)
        box(x-5,y-5,w+10,h+10,(16,23,33))
        for i,c in enumerate(label):
            for row,line in enumerate(FONT[c]):
                for col,bit in enumerate(line):
                    if bit=='1': box(x+i*6*size+col*size,y+row*size,size,size,(244,248,255))
    return base64.b64encode(png_rgb(width,height,pixels)).decode('ascii')
