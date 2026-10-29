"""Data-only local helper for the native Lua importer window."""
import json
import os
from pathlib import Path
import sys

from build_slice_mapper import inspect_layout, select_layout, build_effect, make_setting, validate_routing, validate_effect_name, suggested_effect_name  # BUNDLE_IMPORT
from map_preview import preview_model, render_preview  # BUNDLE_IMPORT
from map_update import map_manifest, plan_update  # BUNDLE_IMPORT


def template_roots():
    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates",
                Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Templates")]
    if sys.platform.startswith("win"):
        return [Path(os.environ.get("APPDATA", str(home))) / "Blackmagic Design/DaVinci Resolve/Support/Fusion/Templates",
                Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Blackmagic Design/DaVinci Resolve/Fusion/Templates"]
    return [home / ".local/share/DaVinciResolve/Fusion/Templates", Path("/opt/resolve/Fusion/Templates")]


def ensure_new_effect_name(name, roots):
    wanted = validate_effect_name(name).casefold()
    for root in roots:
        if root.exists():
            for path in root.rglob("*"):
                if path.suffix.lower() in (".setting", ".drfx") and path.stem.casefold() == wanted:
                    raise FileExistsError("That effect name is already installed. Choose a new name, such as adding a show date or v2.")


def execute_request(request, output_root=None, roots=None):
    path = Path(request["path"]).expanduser()
    catalog = inspect_layout(path)
    if request["action"] == "inspect":
        model=preview_model(catalog)
        return dict(ok=True, catalog=catalog, suggested_name=suggested_effect_name(catalog["name"]),
                    preview=model, preview_png=render_preview(model,[s['name'] for s in catalog['slices'] if s['supported']]))
    if request["action"] not in ("build", "preview", "setting", "update"):
        raise ValueError("Unknown importer action")
    if request.get("expected_sha256") != catalog["source_sha256"]:
        raise ValueError("The XML changed on disk. Click Load to review its screen list again.")
    if request['action'] == 'preview':
        names=request.get('names', [])
        if names: select_layout(catalog,names)
        model=preview_model(catalog,request.get('view'))
        return dict(ok=True,preview=model,preview_png=render_preview(model,names))
    if request['action'] == 'setting':
        layout=select_layout(catalog,request.get('names',[]))
        return dict(ok=True,setting=make_setting(layout,request.get('routing',0)),count=len(layout['slices']),
                    map_name=layout['name'],width=layout['width'],height=layout['height'],
                    manifest_json=json.dumps(map_manifest(layout),ensure_ascii=False))
    if request['action']=='update':
        layout=select_layout(catalog,request.get('names',[]))
        previous=json.loads(request['previous_json']) if request.get('previous_json') else request.get('previous')
        layout,report=plan_update(previous,layout)
        return dict(ok=True,setting=make_setting(layout),count=sum(not s.get('missing') for s in layout['slices']),
                    map_name=layout['name'],width=layout['width'],height=layout['height'],report=report,
                    manifest_json=json.dumps(map_manifest(layout),ensure_ascii=False))
    name = validate_effect_name(request["name"])
    ensure_new_effect_name(name, template_roots() if roots is None else roots)
    layout = select_layout(catalog, request.get("names", []))
    save_root = Path.home()/"Documents/DaVinci Slice/Maps" if output_root is None else Path(output_root)
    result = build_effect(layout, name, save_root/name,routing=request.get('routing',0))
    return dict(ok=True, name=name, count=len(layout["slices"]),
                bundle=str(result["bundle"]), manifest=str(result["manifest"]),
                width=layout["width"], height=layout["height"])


def lua_data(value):
    """Serialize plain data for bmd.readfile; never generate executable Lua."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        chars = []
        for c in value:
            if c in ('"', '\\'):
                chars.append('\\'+c)
            elif ord(c) < 32 or ord(c) == 127:
                chars.append('\\'+format(ord(c), '03d'))
            else:
                chars.append(c)
        return '"'+''.join(chars)+'"'
    pairs = enumerate(value, 1) if isinstance(value, list) else value.items()
    return "{ " + ", ".join(f"[{lua_data(k)}] = {lua_data(v)}" for k, v in pairs) + " }"


def service_main():
    try:
        request = json.loads(Path(sys.argv[1]).read_text())
        result = execute_request(request)
    except Exception as exc:
        result = dict(ok=False, error=str(exc))
    Path(sys.argv[2]).write_text(lua_data(result), encoding="utf-8")


if __name__ == "__main__":
    service_main()
