"""Check native Resolve pixels against XML-derived bounds and source samples."""
from pathlib import Path
import json
import sys
import xml.etree.ElementTree as ET
import numpy as np
from PIL import Image,ImageFilter

ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'validation/remapper'
sys.path.insert(0,str(ROOT/'src'))
from map_preview import flatten_contour,row_intervals,intersect,subtract


def shape_mask(g,w,h):
    mask=np.zeros((h,w),dtype='uint8')
    contours=([dict(contour=g['contour'],keep_inside=True)] if 'contour' in g else [])+g.get('masks',[])
    contours=[(flatten_contour(m['contour'],.08),m['keep_inside']) for m in contours]
    for y in range(max(0,int(g['y'])),min(h,int(g['y']+g['height'])+1)):
        if not g['y']<=y+.5<g['y']+g['height']: continue
        intervals=[(g['x'],g['x']+g['width'])]
        for poly,inside in contours:
            cuts=row_intervals(poly,y+.5)
            intervals=intersect(intervals,cuts) if inside else subtract(intervals,cuts)
        for left,right in intervals:
            mask[y,max(0,int(np.ceil(left-.5))):min(w,int(np.ceil(right-.5)))]=255
    return mask


def check(name,layout,direction,source,minimum_match=.998):
    a=np.array(Image.open(OUT/(name+'00086400.png')))
    w,h=(layout['output_width'],layout['output_height']) if direction==0 else (layout['width'],layout['height'])
    assert a.shape==(h,w,4),(name,a.shape,(h,w))
    all_masks=[]; sample_errors=[]; alpha_errors=[]
    for s in layout['slices']:
        g,d=(s['input'],s['output']) if direction==0 else (s['output'],s['input'])
        mask=shape_mask(d,w,h);all_masks.append(mask)
        interior=np.array(Image.fromarray(mask).filter(ImageFilter.MinFilter(7)))>0
        yy,xx=np.mgrid[3:h:13,3:w:13];inside=interior[yy,xx]
        sx=(xx+.5-d['x'])*g['width']/d['width']+g['x']-.5
        sy=(yy+.5-d['y'])*g['height']/d['height']+g['y']-.5
        sx=np.rint(sx).astype(int);sy=np.rint(sy).astype(int)
        valid=inside&(sx>=4)&(sx<source.shape[1]-4)&(sy>=4)&(sy<source.shape[0]-4)
        dy,dx=yy[valid],xx[valid];sy,sx=sy[valid],sx[valid]
        if not len(dx): continue
        neighbours=np.stack([source[sy+oy,sx+ox].astype(int) for oy in range(-3,4) for ox in range(-3,4)])
        stable=np.max(np.ptp(neighbours,axis=0),axis=1)<=2
        # Opaque flat source patches verify actual sampling and orientation without
        # making the test depend on Fusion's resampling kernel or premultiplication.
        opaque=stable&(source[sy,sx,3]==255)
        rgb=np.max(np.abs(a[dy[opaque],dx[opaque],:3].astype(int)-source[sy[opaque],sx[opaque],:3].astype(int)),axis=1)
        sample_errors.extend(rgb.tolist())
        alpha_errors.extend(np.abs(a[dy[opaque],dx[opaque],3].astype(int)-255).tolist())
    mask=np.maximum.reduce(all_masks)
    allowed=np.array(Image.fromarray(mask).filter(ImageFilter.MaxFilter(7)))>0
    outside=int(np.max(a[:,:,3][~allowed],initial=0))
    assert outside==0,(name,'escaped destination',outside)
    rgb_match=float(np.mean(np.array(sample_errors)<=4)); alpha_match=float(np.mean(np.array(alpha_errors)<=4))
    assert len(sample_errors)>50,(name,'too few independent samples')
    print(name,'RGB',rgb_match,'alpha',alpha_match,'outside',outside,flush=True)
    assert rgb_match>=minimum_match,(name,'RGB source mismatch',rgb_match)
    assert alpha_match>=minimum_match,(name,'missing opaque content',alpha_match)
    im=Image.fromarray(a);im.thumbnail((1280,720));im.save(OUT/(name+'_preview.png'))
    return dict(case=name,direction=direction,dimensions=[w,h],outside_alpha_max=outside,
                flat_source_samples=len(sample_errors),rgb_match=rgb_match,alpha_match=alpha_match)


def main():
    source=np.array(Image.open(OUT/'baseline00086400.png'))
    manifest=next((ROOT/'dist/Remappers/DavinciMap/Screen 1').glob('*.remap.json'))
    davinci=json.loads(manifest.read_text());report=[]
    for name,direction in [('native_forward',0),('native_reverse',1)]:report.append(check(name,davinci,direction,source))
    subset={**davinci,'slices':[davinci['slices'][0],davinci['slices'][13]]}
    report.append(check('subset',subset,0,source))
    empty=np.array(Image.open(OUT/'native_empty00086400.png'));assert not empty[:,:,3].any()
    report.append(dict(case='native_empty',transparent=True))
    for key in ('center','sr'):
        layout=json.loads((OUT/(key+'.json')).read_text())
        for side,d in [('forward',0),('reverse',1)]:report.append(check('ultra_'+key+'_'+side,layout,d,source))
    small=json.loads((OUT/'small.json').read_text())
    for side,d in [('forward',0),('reverse',1)]:
        name='small_'+side;a=np.array(Image.open(OUT/(name+'00086400.png')))
        w,h=(small['output_width'],small['output_height']) if d==0 else (small['width'],small['height'])
        assert a.shape==(h,w,4),(name,a.shape)
        geom=small['slices'][0]['output' if d==0 else 'input'];mask=shape_mask(geom,w,h)
        allowed=np.array(Image.fromarray(mask).filter(ImageFilter.MaxFilter(7)))>0
        interior=np.array(Image.fromarray(mask).filter(ImageFilter.MinFilter(7)))>0
        assert not a[:,:,3][~allowed].any(),(name,'escaped destination')
        assert np.all(a[:,:,3][interior]>=250),(name,'missing interior')
        im=Image.fromarray(a);im.thumbnail((1280,720));im.save(OUT/(name+'_preview.png'))
        report.append(dict(case=name,direction=d,dimensions=[w,h],outside_alpha_max=0,opaque_interior=True))
    result=dict(native_render_verified=True,remapper_frames=len(report),source_baseline_verified=True,cases=report)
    (OUT/'render_report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
