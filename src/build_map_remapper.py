#!/usr/bin/env python3
"""Build a portable, bidirectional map-to-map Fusion effect from Resolume XML."""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import io
import hashlib
import json
import math
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

from build_slice_mapper import inspect_layout, input_geometry, read_points, quote, number, control, input_value, contour_setting, validate_effect_name  # REMAPPER_IMPORT

DIRECTIONS = ['Input → Output', 'Output → Input']


def dimensions(width, height):
    if type(width) is not int or type(height) is not int or not (1 <= width <= 16384 and 1 <= height <= 16384):
        raise ValueError('Canvas dimensions must be whole pixels between 1 and 16384')
    return width, height


def rect_points(g):
    x, y, w, h = (g[k] for k in ('x', 'y', 'width', 'height'))
    return [(x,y), (x+w,y), (x+w,y+h), (x,y+h)]


def transform_geometry(g, sx, sy, tx, ty):
    result = copy.deepcopy(g)
    result.update(x=g['x']*sx+tx, y=g['y']*sy+ty, width=g['width']*sx, height=g['height']*sy)
    result.pop('warning', None)
    for contour in ([result['contour']] if 'contour' in result else []) + [m['contour'] for m in result.get('masks',[])]:
        for p in contour['points']:
            p.update(x=p['x']*sx+tx, y=p['y']*sy+ty,
                     lx=p['lx']*sx, ly=p['ly']*sy, rx=p['rx']*sx, ry=p['ry']*sy)
    return result


def output_geometry(node, source):
    rect = node.find('OutputRect')
    if rect is None or rect.get('orientation', '0') != '0':
        raise ValueError('Missing or rotated output framing rectangle')
    points = read_points(rect)
    if len(points) != 4:
        raise ValueError('Expected four output rectangle corners')
    x,y = points[0]; w,h = points[2][0]-x, points[2][1]-y
    if w <= 0 or h <= 0 or points != [(x,y),(x+w,y),(x+w,y+h),(x,y+h)]:
        raise ValueError('Deformed output rectangles need a mesh remapper')
    # Never quietly replace a stored warp by its bounding rectangle.
    for flip in node.findall('./Params[@name="Output"]/Param[@name="Flip"]') + node.findall('./Warper/Params/Param[@name="Flip"]'):
        if flip.get('value', '0') != '0':
            raise ValueError('Output flips are not supported by this remapper version')
    warper = node.find('Warper')
    if warper is not None:
        mode=warper.find('./Params/ParamChoice[@name="Point Mode"]')
        if mode is not None and mode.get('value') != 'PM_LINEAR':
            raise ValueError('Bezier output warping needs a mesh remapper')
        src,dst=warper.find('Homography/src'),warper.find('Homography/dst')
        if (src is None) != (dst is None) or (src is not None and read_points(src) != read_points(dst)):
            raise ValueError('Perspective output warping needs a mesh remapper')
        mesh=warper.find('BezierWarper')
        if mesh is not None:
            cols,rows=int(mesh.get('controlWidth','0')),int(mesh.get('controlHeight','0'))
            vertices=read_points(mesh.find('vertices'))
            if min(cols,rows)<2 or len(vertices)!=cols*rows:
                raise ValueError('Malformed output mesh')
            if any(math.dist(p,(x+w*(i%cols)/(cols-1),y+h*(i//cols)/(rows-1)))>.01 for i,p in enumerate(vertices)):
                raise ValueError('Deformed output mesh needs a mesh remapper')
        if any(child.tag not in ('Params','BezierWarper','Homography') for child in warper):
            raise ValueError('Unknown output warper geometry')
    sx,sy=w/source['width'],h/source['height']
    result=transform_geometry(source,sx,sy,x-source['x']*sx,y-source['y']*sy)
    if node.tag == 'Polygon':
        from_contour=node.find('InputContour'); to_contour=node.find('OutputContour')
        if to_contour is None or to_contour.get('closed')!='1' or from_contour.findtext('segments')!=to_contour.findtext('segments'):
            raise ValueError('Output polygon must match the input contour topology')
        a,b=read_points(from_contour.find('points')),read_points(to_contour.find('points'))
        if len(a)!=len(b) or any(math.dist(((p[0]-source['x'])*sx+x,(p[1]-source['y'])*sy+y),q)>.01 for p,q in zip(a,b)):
            raise ValueError('Individually warped polygon vertices need a mesh remapper')
    return result


def inspect_remap(path):
    # Reuse the size/entity checks and disabled/unsupported input diagnostics.
    data=Path(path).read_bytes(); catalog=inspect_layout(Path(path))
    if hashlib.sha256(data).hexdigest()!=catalog['source_sha256']:
        raise ValueError('The XML changed while loading. Load it again.')
    root=ET.fromstring(data)
    screens=[]; offset=0
    for index,screen in enumerate(root.findall('.//screens/Screen')):
        records=[]
        nodes=screen.findall('./layers/*')
        local_names=Counter((n.find('./Params/Param[@name="Name"]').get('value') if n.find('./Params/Param[@name="Name"]') is not None else None) for n in nodes)
        for node,original in zip(nodes,catalog['slices'][offset:offset+len(nodes)]):
            record=copy.deepcopy(original)
            record['key']=str(offset+len(records))
            if record['reason'].startswith('Ambiguous duplicate slice name:') and local_names[record['name']]==1:
                enabled=screen.find('./Params/Param[@name="Enabled"]')
                layer_enabled=node.find('./Params[@name="Common"]/Param[@name="Enabled"]')
                disabled=any(p is not None and p.get('value')=='0' for p in (enabled,layer_enabled))
                record.update(supported=not disabled,reason='Slice is disabled' if disabled else '')
            if record['supported']:
                try:
                    record['input']=input_geometry(node,catalog['width'],catalog['height'])
                    record['output']=output_geometry(node,record['input'])
                except (ValueError,TypeError,KeyError) as exc:
                    record.update(supported=False,reason=str(exc))
            records.append(record)
        offset+=len(nodes)
        device=screen.find('OutputDevice')
        child=next(iter(device),None) if device is not None else None
        ow,oh=(child.get('width'),child.get('height')) if child is not None else (None,None)
        explicit=ow is not None and oh is not None
        if explicit: ow,oh=dimensions(int(ow),int(oh))
        else: ow,oh=catalog['width'],catalog['height']
        screens.append(dict(key=str(index),name=screen.get('name','Screen'),id=screen.get('uniqueId'),
                            width=ow,height=oh,size_explicit=explicit,slices=records))
    return dict(name=catalog['name'],width=catalog['width'],height=catalog['height'],
                source_sha256=catalog['source_sha256'],screens=screens)


def select_remap(catalog,screen_key,keys=None,output_size=None):
    screen=next((s for s in catalog['screens'] if s['key']==str(screen_key)),None)
    if screen is None: raise ValueError('Choose an output Screen')
    if keys is None: keys=[s['key'] for s in screen['slices'] if s['supported']]
    if not keys or len(set(keys))!=len(keys): raise ValueError('Choose at least one slice, without duplicates')
    found={s['key']:s for s in screen['slices']}
    if any(k not in found for k in keys): raise ValueError('Selected slice does not belong to this output Screen')
    for k in keys:
        if not found[k]['supported']: raise ValueError(found[k]['reason'])
    ow,oh=dimensions(*(output_size or (screen['width'],screen['height'])))
    return dict(name=catalog['name'],source_sha256=catalog['source_sha256'],
                width=catalog['width'],height=catalog['height'],output_width=ow,output_height=oh,
                screen_name=screen['name'],screen_id=screen['id'],slices=[copy.deepcopy(found[k]) for k in keys])


def validate_direction(direction):
    if type(direction) is not int or direction not in (0,1): raise ValueError('Direction must be Input → Output (0) or Output → Input (1)')
    return direction


def remap_setting(layout,direction=0):
    direction=validate_direction(direction)
    iw,ih=dimensions(layout['width'],layout['height']); ow,oh=dimensions(layout['output_width'],layout['output_height'])
    if not layout['slices']: raise ValueError('Choose at least one slice')
    W,H=max(iw,ow),max(ih,oh)
    def node(name,kind,inputs):
        return f'{name} = {kind} {{ Inputs = {{ {inputs} }}, }},'
    def link(name,port='Output'): return f'Input {{ SourceOp = "{name}", Source = "{port}", }}'
    def val(value,ex=None): return input_value(str(value),ex)
    def point(x,y): return '{'+number(x/W)+','+number(1-y/H)+'}'
    def background(name):
        return node(name,'Background',f'Width={val(W)}, Height={val(H)}, UseFrameFormatSettings={val(0)}, PixelAspect={val("{1,1}")}, TopLeftAlpha={val(0)},')
    controls=['Direction = '+control('Direction','ComboControl',direction,0,1,DIRECTIONS,True)+',']
    values=['Direction = '+val(direction)+',']
    published=[f'Direction = InstanceInput {{ SourceOp="Controls", Source="Direction", Name="Direction", Default={direction}, }},']
    for i,s in enumerate(layout['slices'],1):
        label='Include: '+s['name']
        controls.append(f'Enable{i} = '+control(label,'CheckboxControl',1,0,1,integer=True)+',')
        values.append(f'Enable{i} = '+val(1)+',')
        published.append(f'Enable{i} = InstanceInput {{ SourceOp="Controls", Source="Enable{i}", Name={quote(label)}, Default=1, }},')
    tools=[f'Controls = Custom {{ Inputs = {{ {"".join(values)} }}, UserControls = ordered() {{ {"".join(controls)} }}, }},',
           background('Canvas'),
           node('SourceResize','BetterResize',f'Width={val(iw,f"iif(Controls.Direction < 0.5,{iw},{ow})")}, Height={val(ih,f"iif(Controls.Direction < 0.5,{ih},{oh})")}, KeepAspect={val(0)}, UseFrameFormatSettings={val(0)}, PixelAspect={val("{1,1}")}, HiQOnly={val(0)},'),
           node('SourceFrame','Merge',f'Background={link("Canvas")}, Foreground={link("SourceResize")}, Center={val("{.5,.5}",f"Point(iif(Controls.Direction < 0.5,{iw},{ow})/{2*W},1-iif(Controls.Direction < 0.5,{ih},{oh})/{2*H})")}, PerformDepthMerge={val(0)},')]
    previous='Canvas'
    common=f'MaskWidth={val(W)}, MaskHeight={val(H)}, UseFrameFormatSettings={val(0)}, PixelAspect={val("{1,1}")}, SoftEdge={val(0)},'
    for i,s in enumerate(layout['slices'],1):
        for d in (0,1):
            p=f'Slice{i}{"Forward" if d==0 else "Reverse"}'
            a,b=(s['input'],s['output']) if d==0 else (s['output'],s['input'])
            tools.append(node(p+'Rect','RectangleMask',common+f'Center={val(point(a["x"]+a["width"]/2,a["y"]+a["height"]/2))}, Width={val(number(a["width"]/W))}, Height={val(number(a["height"]/H))},'))
            masks=[p+'Rect']
            for j,m in enumerate(([dict(contour=a['contour'],keep_inside=True)] if 'contour' in a else [])+a.get('masks',[])):
                name=p+f'Mask{j}'
                tools.append(node(name,'PolylineMask',common+f'Invert={val(0 if m["keep_inside"] else 1)}, Polyline={val(contour_setting(m["contour"],W,H))}, Solid={val(1)},'))
                masks.append(name)
            branch='SourceFrame'
            for j,m in enumerate(masks):
                name=p+f'Clip{j}'
                tools.append(node(name,'Merge',f'Background={link("Canvas")}, Foreground={link(branch)}, EffectMask={link(m,"Mask")}, PerformDepthMerge={val(0)},'))
                branch=name
            sx,sy=b['width']/a['width'],b['height']/a['height']
            tx,ty=b['x']-a['x']*sx,b['y']-a['y']*sy
            # Independent scales around the top-left origin retain subpixel geometry.
            # A native affine transform avoids unnecessary full-frame perspective math.
            center='{'+number(.5+tx/W)+','+number(.5-ty/H)+'}'
            tools.append(node(p+'Warp','Transform',f'Input={link(branch)}, UseSizeAndAspect={val(0)}, XSize={val(number(sx))}, YSize={val(number(sy))}, Pivot={val("{0,1}")}, Center={val(center)}, FlattenTransform={val(1)},'))
            name=p+'Merge'
            blend=f'iif(Controls.Direction {"<" if d==0 else ">="} 0.5,Controls.Enable{i},0)'
            tools.append(node(name,'Merge',f'Background={link(previous)}, Foreground={link(p+"Warp")}, Blend={val(1,blend)}, PerformDepthMerge={val(0)},'))
            previous=name
    tools.append(node('MapOutput','Crop',f'Input={link(previous)}, XSize={val(ow,f"iif(Controls.Direction < 0.5,{ow},{iw})")}, YSize={val(oh,f"iif(Controls.Direction < 0.5,{oh},{ih})")}, XOffset={val(0)}, YOffset={val(H-oh,f"{H}-iif(Controls.Direction < 0.5,{oh},{ih})")}, KeepCentered={val(0)},'))
    return f'''{{ Tools = ordered() {{ MapRemapper = GroupOperator {{ NameSet=true, CtrlWZoom=false,
        Inputs = ordered() {{ {''.join(published)} MainInput1 = InstanceInput {{ SourceOp="SourceResize", Source="Input", }}, }},
        Outputs = {{ MainOutput1 = InstanceOutput {{ SourceOp="MapOutput", Source="Output", }}, }},
        ViewInfo = GroupInfo {{ Pos = {{220,50}} }}, Tools = ordered() {{ {''.join(tools)} }},
    }}, }}, ActiveTool="MapRemapper", }}\n'''


def build_remapper(layout,name,output_dir,direction=0,overwrite=False):
    name=validate_effect_name(name); direction=validate_direction(direction)
    setting=remap_setting(layout,direction); output_dir=Path(output_dir)
    archive=io.BytesIO()
    with ZipFile(archive,'w',ZIP_DEFLATED) as z:
        z.writestr(f'Edit/Effects/Davinci Slice/{name}.setting',setting)
    manifest={**layout, 'effect_name':name,'format_version':1,'effect_type':'map_remapper','initial_direction':direction,
              'geometry_support':'axis-aligned affine; 0.01 px XML tolerance'}
    payloads={name+'.setting':setting.encode(),name+'.drfx':archive.getvalue(),name+'.remap.json':(json.dumps(manifest,indent=2)+'\n').encode()}
    if output_dir.exists() and not overwrite:
        occupied={p.name.casefold() for p in output_dir.iterdir()}
        if any(n.casefold() in occupied for n in payloads): raise FileExistsError('That remapper already exists. Choose a new effect name.')
    output_dir.mkdir(parents=True,exist_ok=True); created=[]
    try:
        for filename,data in payloads.items():
            path=output_dir/filename
            with path.open('wb' if overwrite else 'xb') as stream:
                created.append(path); stream.write(data)
    except Exception:
        if not overwrite:
            for p in created: p.unlink(missing_ok=True)
        raise
    return dict(name=name,setting=str(output_dir/(name+'.setting')),bundle=str(output_dir/(name+'.drfx')),manifest=str(output_dir/(name+'.remap.json')))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('xml',type=Path); parser.add_argument('--screen',required=True,help='Output Screen name')
    parser.add_argument('--slices',nargs='+'); parser.add_argument('--output-size',nargs=2,type=int,metavar=('WIDTH','HEIGHT'))
    parser.add_argument('--direction',choices=['forward','reverse'],default='forward')
    parser.add_argument('--name',required=True); parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'dist')
    parser.add_argument('--overwrite',action='store_true'); args=parser.parse_args()
    catalog=inspect_remap(args.xml); screens=[s for s in catalog['screens'] if s['name']==args.screen]
    if len(screens)!=1: parser.error('Output Screen name must identify exactly one Screen')
    screen=screens[0]; keys=None
    if args.slices:
        by_name={s['name']:s['key'] for s in screen['slices']}
        if any(n not in by_name for n in args.slices): parser.error('Slice not found in this Screen')
        keys=[by_name[n] for n in args.slices]
    if not screen['size_explicit'] and not args.output_size: parser.error('XML has no output pixel size. Supply --output-size WIDTH HEIGHT')
    layout=select_remap(catalog,screen['key'],keys,args.output_size)
    print(json.dumps(build_remapper(layout,args.name,args.output,args.direction=='reverse' and 1 or 0,args.overwrite),indent=2))


if __name__=='__main__': main()
