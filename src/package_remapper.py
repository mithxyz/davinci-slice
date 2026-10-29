"""Package the separate Map Remapper as a self-contained Resolve utility."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def package_remapper():
    parts=[]
    for filename in ['build_slice_mapper.py','map_preview.py','map_update.py','import_service.py','build_map_remapper.py','remap_service.py']:
        text=(ROOT/'src'/filename).read_text().rsplit('\nif __name__',1)[0]
        text='\n'.join(line for line in text.splitlines() if not line.startswith('from __future__') and '# BUNDLE_IMPORT' not in line and '# REMAPPER_IMPORT' not in line)
        parts.append(text)
    backend='from __future__ import annotations\n'+'\n\n'.join(parts)+'\nremap_main()\n'
    compile(backend,'map_remapper_backend.py','exec')
    ui=(ROOT/'src/import_map_remapper.lua').read_text()
    apply=(ROOT/'src/apply_to_fusion.lua').read_text().replace('Slice Mapper','Map Remapper').replace('DavinciSlice.','DavinciRemap.')
    apply=apply.replace('Use Update Map to revise it, or use another clip for a new mapper.','Use another clip or remove that remapper before adding a new one.')
    ui=ui.replace('--__APPLY_TO_FUSION__',apply)
    delimiter='========'
    assert ']'+delimiter+']' not in backend
    ui=ui.replace('--__PYTHON_BACKEND__','['+delimiter+'[\n'+backend+']'+delimiter+']')
    (ROOT/'dist/map_remapper_backend.py').write_text(backend)
    target=ROOT/'dist/Map Remapper.lua'
    target.write_text('-- @mcp-script name=Map Remapper kind=custom language=lua\n'+ui)
    return target


if __name__=='__main__': print(package_remapper())
