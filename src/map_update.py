"""Plan identity-preserving updates without reindexing animated destinations."""
from collections import Counter
import json


def map_manifest(layout):
    return dict(version=1,width=layout['width'],height=layout['height'],
                slices=[dict(name=s['name'],id=s.get('id'),missing=bool(s.get('missing')))
                        for s in layout['slices']])


def plan_update(previous,layout):
    if not isinstance(previous,dict) or not isinstance(previous.get('slices'),list):
        raise ValueError('The existing mapper has no readable screen list')
    old=previous['slices']; new=layout['slices']
    if not old or len(old)>4096 or any(not isinstance(s,dict) or not isinstance(s.get('name'),str) or not s['name'] for s in old):
        raise ValueError('The existing mapper screen list is invalid')
    ids=Counter(str(s['id']) for s in new if s.get('id'))
    names=Counter(s['name'] for s in new)
    used=set(); slots=[]; report=dict(matched=0,renamed=[],removed=[],added=[],legacy=bool(previous.get('legacy')))
    for s in old:
        target=None
        if s.get('id') and ids[str(s['id'])]==1:
            target=next((i for i,n in enumerate(new) if n.get('id') and str(n['id'])==str(s['id']) and i not in used),None)
        if target is None and names[s['name']]==1:
            target=next((i for i,n in enumerate(new) if n['name']==s['name'] and i not in used),None)
        if target is None:
            slots.append(dict(name=s['name'],id=s.get('id'),missing=True,destination_default=0,
                              x=0,y=0,width=1,height=1,shape='Rectangle'))
            if not s.get('missing'): report['removed'].append(s['name'])
        else:
            n=dict(new[target]); used.add(target); slots.append(n); report['matched']+=1
            if s['name']!=n['name']: report['renamed'].append(dict(old=s['name'],new=n['name']))
    for i,s in enumerate(new):
        if i not in used:
            slots.append(dict(s,destination_default=0)); report['added'].append(s['name'])
    report['canvas_changed']=(previous.get('width'),previous.get('height'))!=(layout['width'],layout['height'])
    return dict(layout,slices=slots),report
