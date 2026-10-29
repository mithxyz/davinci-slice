"""Bundle a native Lua UI and its standard-library Python engine into one script."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def package_importer():
    core = (ROOT/"src/build_slice_mapper.py").read_text()
    core = core.rsplit('\nif __name__ == "__main__":', 1)[0]
    service = (ROOT/"src/import_service.py").read_text()
    service = "\n".join(line for line in service.splitlines() if "# BUNDLE_IMPORT" not in line)
    preview = (ROOT/"src/map_preview.py").read_text()
    updater=(ROOT/'src/map_update.py').read_text()
    backend = core + "\n\n" + preview + "\n\n" + updater + "\n\n" + service + "\n"
    compile(backend, "slice_mapper_backend.py", "exec")
    (ROOT/"dist").mkdir(parents=True, exist_ok=True)
    (ROOT/"dist/slice_mapper_backend.py").write_text(backend)
    interface = (ROOT/"src/import_resolume_xml.lua").read_text()
    interface=interface.replace('--__APPLY_TO_FUSION__',(ROOT/'src/apply_to_fusion.lua').read_text())
    interface=interface.replace('--__UPDATE_MAP__',(ROOT/'src/update_map.lua').read_text())
    delimiter = "========"
    if "]"+delimiter+"]" in backend:
        raise ValueError("Embedded source contains the Lua string delimiter")
    source = ("-- @mcp-script name=Slice Mapper kind=custom language=lua\n"
              + interface.replace("--__PYTHON_BACKEND__", "["+delimiter+"[\n"+backend+"]"+delimiter+"]"))
    target = ROOT/"dist/Slice Mapper.lua"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    return target


if __name__ == "__main__":
    print(package_importer())
