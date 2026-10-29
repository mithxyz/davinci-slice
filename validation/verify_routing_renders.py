"""Verify actual renders from the mapper pasted directly by the importer."""
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'validation/live_apply'
old=[np.array(Image.open(ROOT/'validation/complex'/f'ultra_000{86400+i}.png'))[:,:,3] for i in range(14)]
all_screens=np.maximum.reduce(old)
two_screens=np.maximum(old[1],old[8])
cases=[('single_us',old[1]),('repeat_all',all_screens),('span_all',all_screens),
       ('repeat_two',two_screens),('span_two',two_screens),('empty',None),
       ('single_circle',old[8]),('repeat_framed',all_screens)]
images=[]; report=[]
for i,(name,reference) in enumerate(cases):
    image=Image.open(OUT/f'routing_000{86400+i}.png')
    assert image.mode=='RGBA' and image.size==(3840,2160), name
    data=np.array(image); images.append(data); a=data[:,:,3]
    if reference is None:
        assert not np.any(a), 'No destinations must produce transparent output'
        coverage=None
    else:
        mask=Image.fromarray((reference>0).astype('uint8')*255)
        allowed=np.array(mask.filter(ImageFilter.MaxFilter(7)))>0
        assert not np.any(a[~allowed]), f'{name}: content escaped the chosen destinations'
        interior=np.array(mask.filter(ImageFilter.MinFilter(7)))>0
        coverage=float(np.mean(a[interior]>200))
        if name!='repeat_framed': assert coverage>.995, f'{name}: missing screen content'
        assert np.any(a), name
    report.append(dict(case=name,outside_destinations_alpha_zero=True,interior_coverage=coverage))

assert not np.array_equal(images[1],images[2]), 'Repeat and span rendered identically'
assert not np.array_equal(images[3],images[4]), 'Two-screen repeat and span rendered identically'
assert not np.array_equal(images[1],images[7]), 'Framing did not change the content'
# The importer only inserted a native group: no template installation or runtime file is required.
(OUT/'report.json').write_text(json.dumps(dict(cases=report,direct_apply_rendered=True),indent=2)+'\n')
for index,label in [(1,'repeat'),(2,'span')]:
    im=Image.fromarray(images[index]); im.thumbnail((1280,720)); im.save(OUT/f'{label}_preview.png')
print('PASS: 8 direct-apply Resolve renders; repeat/span differ; destination toggles, empty output and framing verified.')
