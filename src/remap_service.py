"""Data-only service for the separate native Map Remapper window."""
import json
from pathlib import Path
import sys

from build_map_remapper import inspect_remap, select_remap, remap_setting, build_remapper, dimensions, validate_direction  # REMAPPER_IMPORT
from build_slice_mapper import suggested_effect_name, validate_effect_name  # REMAPPER_IMPORT
from import_service import ensure_new_effect_name, template_roots, lua_data  # REMAPPER_IMPORT
from map_preview import preview_model, render_preview  # REMAPPER_IMPORT


def remap_previews(catalog,screen_key,keys,output_size):
    screen=next(s for s in catalog['screens'] if s['key']==str(screen_key))
    ow,oh=dimensions(*output_size)
    result={}
    for side,w,h in [('input',catalog['width'],catalog['height']),('output',ow,oh)]:
        slices=[]
        for s in screen['slices']:
            # Rejected geometry appears as unavailable in the list, never as a valid approximation.
            if side in s:
                slices.append({**s[side],'name':s['name'],'supported':s['supported']})
        model=preview_model(dict(width=w,height=h,slices=slices))
        selected=[s['name'] for s in screen['slices'] if s['key'] in keys and s['supported']]
        result[side+'_png']=render_preview(model,selected)
    return result


def remap_request(request,output_root=None,roots=None):
    path=Path(request['path']).expanduser(); catalog=inspect_remap(path)
    action=request['action']
    if action=='inspect': return dict(ok=True,catalog=catalog)
    if action not in ('preview','setting','build'): raise ValueError('Unknown remapper action')
    if request.get('expected_sha256')!=catalog['source_sha256']:
        raise ValueError('The XML changed on disk. Click Load to review it again.')
    key=str(request['screen_key']); keys=request.get('keys',[])
    screen=next((s for s in catalog['screens'] if s['key']==key),None)
    if screen is None: raise ValueError('Choose an output Screen')
    size=request.get('output_size') or [screen['width'],screen['height']]
    dimensions(*size)
    if action=='preview':
        if keys: select_remap(catalog,key,keys,size)
        return dict(ok=True,**remap_previews(catalog,key,keys,size))
    if not screen['size_explicit'] and not request.get('output_size'):
        raise ValueError('Confirm the output Screen width and height; the XML does not store its pixel dimensions.')
    layout=select_remap(catalog,key,keys,size); direction=validate_direction(request.get('direction',0))
    if action=='setting':
        w,h=(layout['output_width'],layout['output_height']) if direction==0 else (layout['width'],layout['height'])
        return dict(ok=True,setting=remap_setting(layout,direction),count=len(layout['slices']),width=w,height=h,
                    map_name=layout['name']+' / '+layout['screen_name'],source_sha256=layout['source_sha256'],
                    manifest_json=json.dumps(layout,ensure_ascii=False))
    name=validate_effect_name(request['name'])
    ensure_new_effect_name(name,template_roots() if roots is None else roots)
    root=Path.home()/'Documents/DaVinci Slice/Remappers' if output_root is None else Path(output_root)
    return dict(ok=True,**build_remapper(layout,name,root/name,direction))


def remap_main():
    try:
        result=remap_request(json.loads(Path(sys.argv[1]).read_text()))
    except Exception as exc:
        result=dict(ok=False,error=str(exc))
    Path(sys.argv[2]).write_text(lua_data(result),encoding='utf-8')


if __name__=='__main__': remap_main()
