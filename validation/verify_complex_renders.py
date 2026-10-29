"""Compare actual Resolve RGBA renders to independent XML contour rasterization."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'validation/complex'
source = ET.parse(ROOT/'tests/fixtures/ultra_input.xml')
W, H = 3840, 2160
report = []
preview = np.zeros((H,W,4), dtype=np.uint8)


def polygon(contour):
    points = [(float(v.get('x')),float(v.get('y'))) for v in contour.findall('points/v')]
    out, offset = [], 0
    for kind in contour.findtext('segments').strip():
        if kind == 'L':
            out.append(points[offset]); offset += 1
        elif kind == 'C':
            a,b,c = points[offset:offset+3]
            d = points[(offset+3) % len(points)]
            for t in np.linspace(0,1,257):
                out.append(tuple((1-t)**3*a[k]+3*(1-t)**2*t*b[k]+3*(1-t)*t*t*c[k]+t**3*d[k] for k in (0,1)))
            offset += 3
        else: raise AssertionError(kind)
    return out


for i, node in enumerate(source.findall('.//layers/*')):
    name = node.find('./Params/Param[@name="Name"]').get('value')
    expected = Image.new('L',(W,H))
    pts = [(float(v.get('x')),float(v.get('y'))) for v in node.findall('InputRect/v')]
    ImageDraw.Draw(expected).polygon(pts, fill=255)
    contour = node.find('InputContour')
    constraints = [(contour,True)] if contour is not None else []
    for mask in node.findall('SliceMask'):
        constraints.append((mask.find('./ShapeObject/Shape/Contour'),mask.find('./Params/Param[@name="Invert"]').get('value')=='1'))
    for contour, inside in constraints:
        shape = Image.new('L',(W,H))
        ImageDraw.Draw(shape).polygon(polygon(contour), fill=255)
        keep = np.array(shape)>0
        expected = Image.fromarray((np.array(expected)*(keep if inside else ~keep)).astype('uint8'))
    path = OUT/f'ultra_000{86400+i}.png'
    image = Image.open(path)
    assert image.mode == 'RGBA' and image.size == (W,H), name
    rgba = np.array(image)
    alpha = rgba[:,:,3]
    exterior = np.array(expected.filter(ImageFilter.MaxFilter(7))) == 0
    interior = np.array(expected.filter(ImageFilter.MinFilter(7))) > 0
    assert interior.any(), name
    assert not np.any(alpha[exterior]), f'{name}: pixels spill outside the contour'
    coverage = float(np.mean(alpha[interior] > 200))
    assert coverage > .995, f'{name}: interior missing ({coverage:.3%})'
    yy,xx = np.where(alpha>0)
    assert len(xx), name
    report.append(dict(screen=name, alpha_bounds=[int(xx.min()),int(yy.min()),int(xx.max()+1),int(yy.max()+1)],
                       interior_coverage=coverage, outside_contour_alpha_zero=True))
    preview = np.where((alpha>preview[:,:,3])[:,:,None],rgba,preview)

u = np.array(Image.open(OUT/'ultra_00086402.png'))[:,:,3]
assert u[300,1920]==0 and u[550,1920]>200, 'U gap or base is wrong'
for index, cx in [(8,1488),(13,2349)]:
    a = np.array(Image.open(OUT/f'ultra_000{86400+index}.png'))[:,:,3]
    assert a[564,cx]>200 and a[465,cx-99]==0, 'Circle centre/corner is wrong'

normal = np.array(Image.open(OUT/'ultra_00086413.png'))
framed = np.array(Image.open(OUT/'circle_framing_00086414.png'))
assert not np.array_equal(normal, framed), 'Framing did not change the content'
# The final loop's exterior is the independent XML reference for SL CIRCLE.
# Source resampling changes edge coverage, so compare the geometric boundary.
assert not np.any(framed[:,:,3][exterior]), 'Framing escaped the curved boundary'
for image in (normal,framed):
    yy,xx = np.where(image[:,:,3]>0)
    assert [int(xx.min()),int(yy.min()),int(xx.max()+1),int(yy.max()+1)] == report[-1]['alpha_bounds'], 'Framing moved the mask bounds'
hole = np.array(Image.open(OUT/'circle_hole_00086414.png'))[:,:,3]
assert hole[564,2349]==0 and hole[465,2250]>200, 'Inverted mask did not make a hole'
assert hole[450,2349]==0 and hole[564,2230]==0, 'Inverted mask escaped its slice rectangle'

(OUT/'report.json').write_text(json.dumps(dict(cases=report,edge_tolerance_pixels=3,framing_keeps_boundary=True,inverted_mask_verified=True),indent=2)+'\n')
Image.fromarray(preview).save(OUT/'all_screens_preview.png')
print(f'PASS: {len(report)} screen renders match the XML contours; circles, U-shaped gap and curved-screen framing verified.')
