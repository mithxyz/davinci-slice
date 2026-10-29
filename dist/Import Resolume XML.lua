-- @mcp-script name=Import Resolume XML kind=custom language=lua
-- Native Resolve Studio importer. Python backend is embedded by package_importer.py.
local PYTHON_BACKEND = [========[
#!/usr/bin/env python3
"""Generate a self-contained Fusion Edit effect from Resolume input geometry.

Uses only the Python standard library. Output geometry is intentionally not used:
the rendered composition is meant to feed the original Resolume Advanced Output.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import io
import json
import math
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]


def read_points(element):
    if element is None:
        raise ValueError("Missing input geometry")
    points = [(float(v.attrib['x']), float(v.attrib['y'])) for v in element.findall('v')]
    if not points or not all(math.isfinite(n) for p in points for n in p):
        raise ValueError("Expected finite input points")
    return points


def read_contour(element):
    """Resolume L uses one point; C uses an anchor and two cubic controls."""
    if element is None or element.get('closed') != '1':
        raise ValueError("A closed input contour is required")
    points = read_points(element.find('points'))
    segments = (element.findtext('segments') or '').strip()
    if not segments or set(segments) - set('LC'):
        raise ValueError("Only linear and cubic Bezier input contours are supported")
    if len(points) != sum(1 if s == 'L' else 3 for s in segments):
        raise ValueError("Input contour point count does not match its segments")
    if len(segments) < 2 or len(points) > 4096:
        raise ValueError("Input contour has too few segments or too many points")
    anchors, offset = [], 0
    for segment in segments:
        x, y = points[offset]
        anchors.append(dict(x=x, y=y, lx=0., ly=0., rx=0., ry=0.))
        offset += 1 if segment == 'L' else 3
    offset = 0
    for i, segment in enumerate(segments):
        if segment == 'C':
            a, b = anchors[i], anchors[(i+1) % len(anchors)]
            c1, c2 = points[offset+1:offset+3]
            a.update(rx=c1[0]-a['x'], ry=c1[1]-a['y'])
            b.update(lx=c2[0]-b['x'], ly=c2[1]-b['y'])
        offset += 1 if segment == 'L' else 3
    if len(set(points)) < 3:
        raise ValueError("Input contour is degenerate")
    if set(segments) == {'L'}:
        area = sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1]))
        if abs(area) < 1e-8:
            raise ValueError("Input contour has zero area")
    return dict(points=anchors, segments=segments)


def input_geometry(node, canvas_width, canvas_height):
    if node.tag not in ('Slice', 'Polygon'):
        raise ValueError("Only rectangle slices, polygon slices and slice masks are supported")
    rect = node.find('InputRect')
    if rect is None or rect.get('orientation', '0') != '0':
        raise ValueError("Rotated input framing rectangles are not supported")
    pts = read_points(rect)
    if len(pts) != 4:
        raise ValueError("Expected four input rectangle corners")
    x, y = pts[0]
    w, h = pts[2][0]-x, pts[2][1]-y
    if w <= 0 or h <= 0 or pts != [(x,y), (x+w,y), (x+w,y+h), (x,y+h)]:
        raise ValueError("Nonrectangular InputRect framing is unsupported")
    if x >= canvas_width or y >= canvas_height or x+w <= 0 or y+h <= 0:
        raise ValueError("Slice is entirely outside the composition")
    result = dict(x=x, y=y, width=w, height=h, shape='Rectangle')
    if x < 0 or y < 0 or x+w > canvas_width or y+h > canvas_height:
        result['warning'] = 'Clipped at canvas edge'
    if node.tag == 'Polygon':
        result.update(shape='Polygon', contour=read_contour(node.find('InputContour')))
    masks = []
    for mask in node.findall('SliceMask'):
        enabled = mask.find('./Params/Param[@name="Enabled"]')
        if enabled is not None and enabled.get('value') == '0':
            continue
        contours = mask.findall('./ShapeObject/Shape/Contour')
        if len(contours) != 1:
            raise ValueError("Each slice mask must contain one closed contour")
        invert = mask.find('./Params/Param[@name="Invert"]')
        # Resolume Invert=1 keeps the inside (as in both Ultra circles).
        masks.append(dict(contour=read_contour(contours[0]),
                          keep_inside=invert is not None and invert.get('value') == '1'))
    if masks:
        result['masks'] = masks
        if node.tag == 'Slice':
            result['shape'] = 'Bezier mask' if any('C' in m['contour']['segments'] for m in masks) else 'Polygon mask'
    return result


def inspect_layout(path: Path) -> dict:
    """Describe every slice, retaining reasons a slice cannot be selected."""
    path = Path(path)
    data = path.read_bytes()
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("XML is larger than the 16 MB import limit")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("XML document types and entities are not supported")
    root = ET.fromstring(data)
    size = root.find(".//CurrentCompositionTextureSize")
    if size is None:
        raise ValueError("Missing Resolume composition size")
    width, height = int(size.get("width", "0")), int(size.get("height", "0"))
    if min(width, height) <= 0:
        raise ValueError("Invalid composition size")
    entries = []
    for screen in root.findall(".//screens/Screen"):
        enabled = screen.find('./Params/Param[@name="Enabled"]')
        screen_enabled = enabled is None or enabled.get("value") != "0"
        for node in screen.findall("./layers/*"):
            name_param = node.find('./Params[@name="Common"]/Param[@name="Name"]')
            name = name_param.get("value") if name_param is not None else None
            enabled = node.find('./Params[@name="Common"]/Param[@name="Enabled"]')
            record = dict(name=name or "(unnamed)", id=node.get("uniqueId"),
                          screen=screen.get("name", "Screen"), supported=False, reason="")
            try:
                if not name:
                    raise ValueError("Slice has no name")
                if not screen_enabled or (enabled is not None and enabled.get("value") == "0"):
                    raise ValueError(f"Selected slice is disabled: {name}")
                record.update(input_geometry(node, width, height), supported=True)
            except (ValueError, KeyError, TypeError) as exc:
                record["reason"] = str(exc)
            entries.append(record)
    counts = Counter(s["name"] for s in entries)
    for s in entries:
        if counts[s["name"]] > 1:
            s.update(supported=False, reason=f"Ambiguous duplicate slice name: {s['name']}")
    return dict(name=root.get("name", path.stem), width=width, height=height,
                coordinate_space="Resolume input; top-left origin; pixels",
                source_sha256=hashlib.sha256(data).hexdigest(), slices=entries)


def select_layout(catalog: dict, names: list[str]) -> dict:
    if not names or len(names) != len(set(names)):
        raise ValueError("Select at least one screen, without duplicate names")
    found = {s["name"]: s for s in catalog["slices"]}
    missing = set(names) - set(found)
    if missing:
        raise ValueError(f"Slices not found: {', '.join(sorted(missing))}")
    for name in names:
        if not found[name]["supported"]:
            raise ValueError(found[name]["reason"])
    return {**catalog, "slices": [{k: v for k, v in found[n].items()
                                   if k not in ("supported", "reason", "screen")} for n in names]}


def read_layout(path: Path, names: list[str]) -> dict:
    return select_layout(inspect_layout(path), names)


def validate_effect_name(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).strip()
    if not name or len(name) > 100:
        raise ValueError("Effect name must contain between 1 and 100 characters")
    if re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', name) or name.endswith(".") or name in (".", ".."):
        raise ValueError('Effect name cannot contain path separators, control characters, or <>:"|?*; or end with a dot')
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)]}:
        raise ValueError("Choose a different effect name; this is a reserved filename")
    return name


def suggested_effect_name(map_name: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "-", unicodedata.normalize("NFKC", map_name)).strip(" .")
    return "Slice Mapper - " + (clean or "Untitled Map")[:80]


def validate_routing(value):
    if type(value) is not int or value not in (0,1,2):
        raise ValueError('Routing must be Single screen (0), Repeat (1), or Span full map (2)')
    return value


def build_effect(layout: dict, effect_name: str, output_dir: Path, overwrite=False, routing=0) -> dict:
    """Write independently named portable artifacts, refusing collisions by default."""
    name = validate_effect_name(effect_name)
    if not layout.get("slices"):
        raise ValueError("Select at least one screen")
    output_dir = Path(output_dir)
    routing=validate_routing(routing)
    setting = make_setting(layout,routing)
    manifest_name = "layout.json" if name == "Slice Mapper Prototype" else f"{name}.layout.json"
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as z:
        for directory in ("Edit/", "Edit/Effects/", "Edit/Effects/Davinci Slice/"):
            z.writestr(directory, "")
        z.writestr(f"Edit/Effects/Davinci Slice/{name}.setting", setting)
    manifest = {**layout, "effect_name": name, "format_version": 3,
                "initial_routing": ['single','repeat','span'][routing]}
    payloads = {
        f"{name}.setting": setting.encode("utf-8"),
        f"{name}.drfx": buffer.getvalue(),
        manifest_name: (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    }
    if output_dir.exists() and not overwrite:
        occupied = {p.name.casefold() for p in output_dir.iterdir()}
        if any(n.casefold() in occupied for n in payloads):
            raise FileExistsError(f"An effect with this name already exists in {output_dir}. Choose a different name.")
    output_dir.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for filename, data in payloads.items():
            path = output_dir / filename
            with path.open("wb" if overwrite else "xb") as stream:
                created.append(path)
                stream.write(data)
    except Exception:
        if not overwrite:
            for path in created:
                path.unlink(missing_ok=True)
        raise
    return dict(name=name, setting=output_dir/f"{name}.setting", bundle=output_dir/f"{name}.drfx",
                manifest=output_dir/manifest_name)


def quote(value: str) -> str:
    # JSON string escaping is compatible with the strings emitted here in .setting.
    return json.dumps(value, ensure_ascii=False)


def number(n: float) -> str:
    return format(n, ".14g")


def selected(values: list[float]) -> str:
    result = number(values[-1])
    for i in reversed(range(len(values)-1)):
        result = f"iif(Controls.NumberIn1 < {i+0.5}, {number(values[i])}, {result})"
    return result


def input_value(value: str, expression: str | None = None) -> str:
    ex = f", Expression = {quote(expression)}" if expression else ""
    return f"Input {{ Value = {value}{ex}, }}"


def control(name: str, kind: str, default: float, minimum=0, maximum=1,
            choices: list[str] | None = None, integer=False) -> str:
    entries = [f"LINKS_Name = {quote(name)}", 'LINKID_DataType = "Number"',
               f"INPID_InputControl = {quote(kind)}", 'ICS_ControlPage = "Controls"',
               f"INP_Default = {number(default)}", f"INP_MinScale = {number(minimum)}",
               f"INP_MaxScale = {number(maximum)}", "ICD_Width = 1"]
    if integer:
        entries.append("INP_Integer = true")
    if kind == "CheckboxControl":
        entries.append("CBC_TriState = false")
    for choice in choices or []:
        entries.append(f"{{ CCS_AddString = {quote(choice)}, }}")
    return "{ " + ", ".join(entries) + ", }"


def contour_setting(contour, width, height):
    """Native Fusion Bezier points are center-relative; handles are offsets."""
    points = []
    for p in contour['points']:
        linear = 'Linear = true, ' if not any(p[k] for k in ('lx','ly','rx','ry')) else ''
        values = dict(X=p['x']/width-.5, Y=.5-p['y']/height,
                      LX=p['lx']/width, LY=-p['ly']/height,
                      RX=p['rx']/width, RY=-p['ry']/height)
        points.append('{ '+linear+', '.join(k+' = '+number(v) for k,v in values.items())+', }')
    return 'Polyline { Closed = true, Points = { '+', '.join(points)+', }, }'


def complex_screen_tools(layout, cx, cy):
    """Intersect native masks, then composite only the selected screen branch."""
    W, H = layout['width'], layout['height']
    tools, previous = [], 'Canvas'
    common = f'''MaskWidth = Input {{ Value = {W}, }}, MaskHeight = Input {{ Value = {H}, }},
        UseFrameFormatSettings = Input {{ Value = 0, }}, PixelAspect = Input {{ Value = {{ 1, 1 }}, }},
        SoftEdge = Input {{ Value = 0, }},'''
    for i, s in enumerate(layout['slices']):
        prefix = f'Screen{i+1}'
        rect = prefix+'Rect'
        center = '{ '+number((s['x']+s['width']/2)/W)+', '+number(1-(s['y']+s['height']/2)/H)+' }'
        tools.append(f'''{rect} = RectangleMask {{ Inputs = {{ {common}
            Center = {input_value(center)}, Width = {input_value(number(s['width']/W))},
            Height = {input_value(number(s['height']/H))}, }}, }},''')
        boundaries = [rect]
        contours = ([dict(contour=s['contour'], keep_inside=True)] if 'contour' in s else []) + s.get('masks', [])
        for j, mask in enumerate(contours):
            name = prefix+f'Contour{j+1}'
            tools.append(f'''{name} = PolylineMask {{ Inputs = {{ {common}
                Invert = Input {{ Value = {0 if mask['keep_inside'] else 1}, }},
                Polyline = Input {{ Value = {contour_setting(mask['contour'], W, H)}, }},
                Solid = Input {{ Value = 1, }}, }}, }},''')
            boundaries.append(name)
        branch = 'ContentFraming'
        for j, mask_name in enumerate(boundaries):
            final = j == len(boundaries)-1
            name = 'ScreenOutput' if final and i == len(layout['slices'])-1 else prefix+f'Clip{j+1}'
            background = previous if final else 'Canvas'
            position = f'Point({cx}, {cy})' if j == 0 else None
            tools.append(f'''{name} = Merge {{ Inputs = {{
                Background = Input {{ SourceOp = "{background}", Source = "Output", }},
                Foreground = Input {{ SourceOp = "{branch}", Source = "Output", }},
                EffectMask = Input {{ SourceOp = "{mask_name}", Source = "Mask", }},
                Center = {input_value('{ 0.5, 0.5 }', position)},
                Blend = {input_value('1', f'iif(abs(Controls.NumberIn1 - {i}) < 0.5, 1, 0)' if final else None)},
                PerformDepthMerge = Input {{ Value = 0, }}, }}, }},''')
            branch = name
        previous = branch
    return '\n'.join(tools)


def routed_screen_tools(layout):
    """One source, with an independent framing branch per destination."""
    W,H=layout['width'],layout['height']
    tools=[]; previous='Canvas'
    common=f'''MaskWidth = Input {{ Value = {W}, }}, MaskHeight = Input {{ Value = {H}, }},
        UseFrameFormatSettings = Input {{ Value = 0, }}, PixelAspect = Input {{ Value = {{1,1}}, }}, SoftEdge = Input {{ Value = 0, }},'''
    ratio='(self.Input.OriginalWidth / max(self.Input.OriginalHeight,1))'
    for i,s in enumerate(layout['slices']):
        p=f'Screen{i+1}'
        sw=f'iif(Controls.DestinationMode > 1.5, {W}, {number(s["width"])})'
        sh=f'iif(Controls.DestinationMode > 1.5, {H}, {number(s["height"])})'
        rw=f'max(1,floor(iif(Controls.NumberIn2 > 1.5, {sw}, iif(Controls.NumberIn2 < 0.5, max({sw},({sh})*{ratio}), min({sw},({sh})*{ratio})))+0.5))'
        rh=f'max(1,floor(iif(Controls.NumberIn2 > 1.5, {sh}, iif(Controls.NumberIn2 < 0.5, max({sh},({sw})/{ratio}), min({sh},({sw})/{ratio})))+0.5))'
        tools.append(f'''{p}Resize = BetterResize {{ Inputs = {{
            Input = Input {{ SourceOp = "SourceResize", Source = "Output", }},
            Width = {input_value('1920',rw)}, Height = {input_value('1080',rh)},
            KeepAspect = Input {{ Value = 0, }}, UseFrameFormatSettings = Input {{ Value = 0, }},
            HiQOnly = Input {{ Value = 0, }}, PixelAspect = Input {{ Value = {{1,1}}, }}, }}, }},
        {p}Framing = Transform {{ Inputs = {{
            Input = Input {{ SourceOp = "{p}Resize", Source = "Output", }},
            Center = {input_value('{0.5,0.5}','Point(0.5 + Controls.NumberIn4 / max(self.Input.OriginalWidth,1), 0.5 + Controls.NumberIn5 / max(self.Input.OriginalHeight,1))')},
            Size = {input_value('1','max(Controls.NumberIn3,0.001)')}, Angle = {input_value('0','Controls.NumberIn6')},
            FlipHoriz = {input_value('0','Controls.NumberIn7')}, FlipVert = {input_value('0','Controls.NumberIn8')}, }}, }},''')
        center='{'+number((s['x']+s['width']/2)/W)+','+number(1-(s['y']+s['height']/2)/H)+'}'
        tools.append(f'''{p}Rect = RectangleMask {{ Inputs = {{ {common}
            Center = {input_value(center)}, Width = {input_value(number(s['width']/W))}, Height = {input_value(number(s['height']/H))}, }}, }},''')
        masks=[p+'Rect']
        contours=([dict(contour=s['contour'],keep_inside=True)] if 'contour' in s else [])+s.get('masks',[])
        for j,mask in enumerate(contours):
            name=p+f'Contour{j+1}'
            tools.append(f'''{name} = PolylineMask {{ Inputs = {{ {common}
                Invert = Input {{ Value = {0 if mask['keep_inside'] else 1}, }},
                Polyline = Input {{ Value = {contour_setting(mask['contour'],W,H)}, }}, Solid = Input {{ Value = 1, }}, }}, }},''')
            masks.append(name)
        branch=p+'Framing'
        for j,mask in enumerate(masks):
            final=j==len(masks)-1
            name='ScreenOutput' if final and i==len(layout['slices'])-1 else p+f'Clip{j+1}'
            pos=None
            if j==0:
                pos=f'Point(iif(Controls.DestinationMode > 1.5, 0.5, {number((s["x"]+s["width"]/2)/W)}), iif(Controls.DestinationMode > 1.5, 0.5, {number(1-(s["y"]+s["height"]/2)/H)}))'
            blend=f'iif(Controls.DestinationMode < 0.5, iif(abs(Controls.NumberIn1 - {i}) < 0.5, 1, 0), Controls.Send{i+1})' if final else None
            tools.append(f'''{name} = Merge {{ Inputs = {{
                Background = Input {{ SourceOp = "{previous if final else 'Canvas'}", Source = "Output", }},
                Foreground = Input {{ SourceOp = "{branch}", Source = "Output", }},
                EffectMask = Input {{ SourceOp = "{mask}", Source = "Mask", }},
                Center = {input_value('{0.5,0.5}',pos)}, Blend = {input_value('1',blend)},
                PerformDepthMerge = Input {{ Value = 0, }}, }}, }},''')
            branch=name
        previous=branch
    return '\n'.join(tools)


def make_setting(layout: dict, routing=0) -> str:
    routing=validate_routing(routing)
    W, H = layout["width"], layout["height"]
    slices = layout["slices"]
    sw = selected([s["width"] for s in slices])
    sh = selected([s["height"] for s in slices])
    cx = selected([(s["x"]+s["width"]/2)/W for s in slices])
    cy = selected([1-(s["y"]+s["height"]/2)/H for s in slices])
    # Resize is before framing. The final Merge's mask stays in canvas space.
    ratio = "(self.Input.OriginalWidth / max(self.Input.OriginalHeight, 1))"
    rw = f"max(1, floor(iif(Controls.NumberIn2 > 1.5, {sw}, iif(Controls.NumberIn2 < 0.5, max({sw}, ({sh}) * {ratio}), min({sw}, ({sh}) * {ratio}))) + 0.5))"
    rh = f"max(1, floor(iif(Controls.NumberIn2 > 1.5, {sh}, iif(Controls.NumberIn2 < 0.5, max({sh}, ({sw}) / {ratio}), min({sh}, ({sw}) / {ratio}))) + 0.5))"
    controls = [
        ("Screen", "ComboControl", 0, 0, len(slices)-1, [s["name"] for s in slices], True),
        ("Scaling", "ComboControl", 0, 0, 2, ["Fill", "Fit", "Stretch"], True),
        ("Zoom", "SliderControl", 1, 0.1, 4, None, False),
        ("Offset X (px)", "SliderControl", 0, -960, 960, None, False),
        ("Offset Y (px, up)", "SliderControl", 0, -540, 540, None, False),
        ("Rotation", "ScrewControl", 0, -180, 180, None, False),
        ("Flip Horizontal", "CheckboxControl", 0, 0, 1, None, True),
        ("Flip Vertical", "CheckboxControl", 0, 0, 1, None, True),
    ]
    published, usercontrols, values = [], [], []
    keys = ["Screen", "Scaling", "Zoom", "OffsetX", "OffsetY", "Rotation", "FlipH", "FlipV"]
    for i, (key, cfg) in enumerate(zip(keys, controls), 1):
        name, kind, default, mn, mx, choices, integer = cfg
        published.append(f'{key} = InstanceInput {{ SourceOp = "Controls", Source = "NumberIn{i}", Name = {quote(name)}, Default = {number(default)}, }},')
        usercontrols.append(f"NumberIn{i} = {control(name, kind, default, mn, mx, choices, integer)},")
        values.append(f"NumberIn{i} = {input_value(number(default))},")
    published.insert(0,f'DestinationMode = InstanceInput {{ SourceOp = "Controls", Source = "DestinationMode", Name = "Routing", Default = {routing}, }},')
    usercontrols.insert(0,'DestinationMode = '+control('Routing','ComboControl',routing,0,2,['Single screen','Repeat to checked screens','Span full map'],True)+',')
    values.append('DestinationMode = '+input_value(str(routing))+',')
    for i,s in enumerate(slices,1):
        label='Send to: '+s['name']
        published.append(f'Send{i} = InstanceInput {{ SourceOp = "Controls", Source = "Send{i}", Name = {quote(label)}, Default = 1, }},')
        usercontrols.append(f'Send{i} = '+control(label,'CheckboxControl',1,0,1,integer=True)+',')
        values.append(f'Send{i} = '+input_value('1')+',')
    setting = f'''{{
    Tools = ordered() {{
        SliceMapper = GroupOperator {{
            NameSet = true,
            CtrlWZoom = false,
            Inputs = ordered() {{
                {chr(10).join(published)}
                MainInput1 = InstanceInput {{ SourceOp = "SourceResize", Source = "Input", }},
            }},
            Outputs = {{ MainOutput1 = InstanceOutput {{ SourceOp = "ScreenOutput", Source = "Output", }}, }},
            ViewInfo = GroupInfo {{ Pos = {{ 220, 50 }} }},
            Tools = ordered() {{
                Controls = Custom {{
                    NameSet = true,
                    Inputs = {{ {chr(10).join(values)} }},
                    UserControls = ordered() {{ {chr(10).join(usercontrols)} }},
                    ViewInfo = OperatorInfo {{ Pos = {{ 0, -66 }} }},
                }},
                SourceResize = BetterResize {{
                    Inputs = {{
                        Width = {input_value('1920', rw)},
                        Height = {input_value('1080', rh)},
                        KeepAspect = Input {{ Value = 0, }},
                        UseFrameFormatSettings = Input {{ Value = 0, }},
                        HiQOnly = Input {{ Value = 0, }},
                        PixelAspect = Input {{ Value = {{ 1, 1 }}, }},
                    }},
                    ViewInfo = OperatorInfo {{ Pos = {{ 0, 33 }} }},
                }},
                ContentFraming = Transform {{
                    Inputs = {{
                        Input = Input {{ SourceOp = "SourceResize", Source = "Output", }},
                        Center = {input_value('{ 0.5, 0.5 }', 'Point(0.5 + Controls.NumberIn4 / max(self.Input.OriginalWidth,1), 0.5 + Controls.NumberIn5 / max(self.Input.OriginalHeight,1))')},
                        Size = {input_value('1', 'max(Controls.NumberIn3, 0.001)')},
                        Angle = {input_value('0', 'Controls.NumberIn6')},
                        FlipHoriz = {input_value('0', 'Controls.NumberIn7')},
                        FlipVert = {input_value('0', 'Controls.NumberIn8')},
                    }},
                    ViewInfo = OperatorInfo {{ Pos = {{ 110, 33 }} }},
                }},
                Canvas = Background {{
                    Inputs = {{
                        Width = Input {{ Value = {W}, }}, Height = Input {{ Value = {H}, }},
                        UseFrameFormatSettings = Input {{ Value = 0, }},
                        PixelAspect = Input {{ Value = {{ 1, 1 }}, }},
                        TopLeftAlpha = Input {{ Value = 0, }},
                    }},
                    ViewInfo = OperatorInfo {{ Pos = {{ 110, 99 }} }},
                }},
                ScreenBoundary = RectangleMask {{
                    Inputs = {{
                        MaskWidth = Input {{ Value = {W}, }}, MaskHeight = Input {{ Value = {H}, }},
                        UseFrameFormatSettings = Input {{ Value = 0, }},
                        PixelAspect = Input {{ Value = {{ 1, 1 }}, }},
                        Center = {input_value('{ 0.5, 0.5 }', f'Point({cx}, {cy})')},
                        Width = {input_value('0.5', f'({sw}) / {W}')},
                        Height = {input_value('0.5', f'({sh}) / {H}')},
                        SoftEdge = Input {{ Value = 0, }},
                    }},
                    ViewInfo = OperatorInfo {{ Pos = {{ 220, -33 }} }},
                }},
                ScreenOutput = Merge {{
                    Inputs = {{
                        Background = Input {{ SourceOp = "Canvas", Source = "Output", }},
                        Foreground = Input {{ SourceOp = "ContentFraming", Source = "Output", }},
                        EffectMask = Input {{ SourceOp = "ScreenBoundary", Source = "Mask", }},
                        Center = {input_value('{ 0.5, 0.5 }', f'Point({cx}, {cy})')},
                        PerformDepthMerge = Input {{ Value = 0, }},
                    }},
                    ViewInfo = OperatorInfo {{ Pos = {{ 220, 33 }} }},
                }},
            }},
        }},
    }},
    ActiveTool = "SliceMapper",
}}
'''
    # Retain the published image input while sharing the original image across
    # branches. Resizing happens separately for each destination, after this input.
    start=setting.index('                SourceResize = BetterResize')
    end=setting.index('                Canvas = Background',start)
    setting=setting[:start]+'SourceResize = Transform { Inputs = { Size = Input { Value = 1, }, }, },\n'+setting[end:]
    start=setting.index('                ScreenBoundary = RectangleMask')
    end=setting.index('\n            },\n        },',start)
    setting=setting[:start]+routed_screen_tools(layout)+setting[end:]
    return setting


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xml", type=Path)
    parser.add_argument("--screens", nargs="+", default=["Main", "SQ-1", "Col-L1"])
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--name", default="Slice Mapper Prototype")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing build files explicitly")
    parser.add_argument('--routing',choices=['single','repeat','span'],default='single')
    args = parser.parse_args()
    if not args.screens or len(args.screens) != len(set(args.screens)):
        parser.error("Select at least one screen, without duplicate names")
    layout = read_layout(args.xml, args.screens)
    build_effect(layout, args.name, args.output, args.overwrite, ['single','repeat','span'].index(args.routing))
    print(f"Built {len(layout['slices'])} screens on {layout['width']} x {layout['height']}: {args.output}")



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


"""Data-only local helper for the native Lua importer window."""
import json
import os
from pathlib import Path
import sys



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
    if request["action"] not in ("build", "preview", "setting"):
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
                    map_name=layout['name'],width=layout['width'],height=layout['height'])
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
]========]
local fusion = fusion or fu or bmd.scriptapp("Fusion")
local ui = fusion.UIManager
local dispatcher = bmd.UIDispatcher(ui)
local WINDOW_ID = "com.davincislice.ImportResolumeXML"
local existing = ui:FindWindow(WINDOW_ID)
if existing and existing.Visible then existing:Show(); existing:Raise(); return end
local PAGE_SIZE = 10
-- Insert a self-contained mapper before the single output of an open Fusion comp.
local function apply_to_fusion(setting_text, metadata, temporary_path)
    local resolve=bmd.scriptapp('Resolve')
    if not resolve or resolve:GetCurrentPage()~='fusion' then
        error('Open the target clip on the Fusion page, then click Add to Fusion.')
    end
    local comp=fusion:GetCurrentComp()
    if not comp then error('No open Fusion composition. Open a clip on the Fusion page first.') end
    local outputs=comp:GetToolList(false,'MediaOut')
    local output,count=nil,0
    for _,tool in pairs(outputs) do output=tool; count=count+1 end
    if count~=1 then error('Add to Fusion needs a composition with exactly one MediaOut.') end
    local target=output:FindMainInput(1)
    local source=target and target:GetConnectedOutput()
    if not source then error('Connect your content to MediaOut before adding the mapper.') end
    local upstream=source:GetTool()
    if upstream:GetData('DavinciSlice.SourceHash') then
        error('A Slice Mapper is already connected to MediaOut. Use another clip, or remove that mapper before adding a new mapping.')
    end
    local file=assert(io.open(temporary_path,'wb')); file:write(setting_text); file:close()
    local settings=bmd.readfile(temporary_path); os.remove(temporary_path)
    if not settings then error('Could not read the generated mapping settings.') end
    local before={}
    for _,tool in pairs(comp:GetToolList(false)) do before[tool:GetAttrs().TOOLI_ID]=true end
    comp:StartUndo('Add Slice Mapper')
    local mapper
    local ok,message=pcall(function()
        if not comp:Paste(settings) then error('Fusion did not paste the mapping group.') end
        for _,tool in pairs(comp:GetToolList(false,'GroupOperator')) do
            if not before[tool:GetAttrs().TOOLI_ID] then
                if mapper then error('More than one mapping group was created.') end
                mapper=tool
            end
        end
        if not mapper then error('Fusion did not create a mapping group.') end
        local input=mapper:FindMainInput(1)
        local out=mapper:FindMainOutput(1)
        if not input or not out then error('The mapping group has no image input or output.') end
        if not input:ConnectTo(source) then error('Could not connect the source to the mapper.') end
        if not target:ConnectTo(out) then error('Could not connect the mapper to MediaOut.') end
        mapper:SetData('DavinciSlice.SourceHash',metadata.source_sha256)
        mapper:SetData('DavinciSlice.MapName',metadata.map_name)
        mapper.Comments='Slice Mapper: '..metadata.map_name..' | '..metadata.count..' screens | '..metadata.width..' x '..metadata.height
        comp:SetActiveTool(mapper)
    end)
    if not ok then
        target:ConnectTo(source)
        -- Roll back only newly pasted tools; preserve every pre-existing tool.
        for _,tool in pairs(comp:GetToolList(false)) do
            pcall(function()
                if not before[tool:GetAttrs().TOOLI_ID] then comp:DeleteTool(tool) end
            end)
        end
    end
    comp:EndUndo(ok)
    if not ok then error(message) end
    return mapper:GetAttrs().TOOLS_Name
end


local function shell_quote(s)
    return "'" .. tostring(s):gsub("'", "'\\''") .. "'"
end

local function json(value)
    local kind = type(value)
    if kind == "string" then
        return '"' .. value:gsub('[%z\1-\31\\"]', function(c)
            if c == '"' or c == '\\' then return '\\' .. c end
            return string.format('\\u%04x', c:byte())
        end) .. '"'
    elseif kind == "number" or kind == "boolean" then return tostring(value)
    elseif kind == "table" then
        local fields = {}
        if #value > 0 then
            for _, v in ipairs(value) do fields[#fields+1] = json(v) end
            return '[' .. table.concat(fields, ',') .. ']'
        end
        for k, v in pairs(value) do fields[#fields+1] = json(k) .. ':' .. json(v) end
        return '{' .. table.concat(fields, ',') .. '}'
    end
    return 'null'
end

local base = os.tmpname()
local helper = base .. '.py'
local helperFile = assert(io.open(helper, 'wb'))
helperFile:write(PYTHON_BACKEND); helperFile:close()
os.remove(base)

local function service(request)
    local requestPath, responsePath, logPath = base .. '.json', base .. '.setting', base .. '.log'
    local file = assert(io.open(requestPath, 'wb'))
    file:write(json(request)); file:close()
    -- macOS ships the Python launcher used by this local build; no Resolve API
    -- Python module or third-party package is required by the helper.
    local python = os.getenv('DAVINCI_SLICE_PYTHON') or '/usr/bin/python3'
    os.execute(shell_quote(python) .. ' ' .. shell_quote(helper) .. ' ' .. shell_quote(requestPath)
        .. ' ' .. shell_quote(responsePath) .. ' >' .. shell_quote(logPath) .. ' 2>&1')
    local result = bmd.readfile(responsePath)
    if not result then
        local log = io.open(logPath, 'rb')
        local message = log and log:read('*a') or 'Could not start the local import engine.'
        if log then log:close() end
        result = {ok=false, error=message}
    end
    os.remove(requestPath); os.remove(responsePath); os.remove(logPath)
    return result
end

local screenRows = {Spacing=4}
for i=1,PAGE_SIZE do
    screenRows[#screenRows+1] = ui:HGroup{Weight=0,
        ui:CheckBox{ID='Screen'..i, Text='', Enabled=false, Weight=2, Events={Toggled=true}},
        ui:Label{ID='Metrics'..i, Text='', Weight=2},
        ui:Label{ID='Availability'..i, Text='', Weight=3}}
end
local win = dispatcher:AddWindow({ID=WINDOW_ID, WindowTitle='Import Resolume XML', Geometry={80,50,1200,740}},
    ui:VGroup({Spacing=8,
        ui:Label{Text='Create a Slice Mapper effect', Weight=0, Font=ui:Font{PointSize=16, Bold=true}},
        ui:HGroup{Weight=0,
            ui:LineEdit{ID='XMLPath', PlaceholderText='Resolume Advanced Output XML…'},
            ui:Button{ID='BrowseXML', Text='Browse…', Weight=0},
            ui:Button{ID='LoadXML', Text='Load', Weight=0}},
        ui:Label{ID='MapInfo', Text='No map loaded.', WordWrap=true, Weight=0},
        ui:HGroup{Weight=0,
            ui:Label{Text='Effect name', Weight=0},
            ui:LineEdit{ID='EffectName', PlaceholderText='e.g. Slice Mapper - Melbourne 2026', MaxLength=100},
            ui:Label{Text='Routing', Weight=0},
            ui:ComboBox{ID='InitialRouting', MinimumSize={190,22}, Weight=0,
                ToolTip='Repeat fits a copy into every included screen. Span fits one image to the full map and reveals it through included screens.'}},
        ui:HGroup{Spacing=16,
            ui:VGroup{Weight=1.3,
                ui:HGroup{Weight=0, Spacing=5,
                    ui:Button{ID='ZoomOut', Text='−', FixedSize={28,22}, Enabled=false},
                    ui:Label{ID='ZoomInfo', Text='100%', FixedSize={44,22}},
                    ui:Button{ID='ZoomIn', Text='+', FixedSize={28,22}, Enabled=false},
                    ui:Button{ID='FitView', Text='Fit', Weight=0, Enabled=false},
                    ui:CheckBox{ID='PanMode', Text='Pan', Enabled=false},
                    ui:Label{Text='Colour = included  •  Grey = excluded'}},
                ui:Button{ID='MapPreview', Text='Load an XML map to preview its screens.', Flat=true,
                    FixedSize={560,350}, IconSize={556,346}, MouseTracking=true,
                    StyleSheet='QPushButton { background-color: #0e1219; border: 1px solid #465166; border-radius: 4px; padding: 0px; }',
                    Events={MousePress=true, MouseMove=true, MouseRelease=true, Wheel=true, Leave=true}},
                ui:Label{ID='PreviewInfo', Text='Click to select. Wheel to zoom. Pan: middle/right drag, or enable Pan.', WordWrap=true,
                    MinimumSize={0,38}, MaximumSize={16777215,44}, Weight=0}},
            ui:VGroup{Weight=1,
                ui:HGroup{Weight=0, ui:Label{Text='Include / screen', Weight=2},
                    ui:Label{Text='Size (px)', Weight=2}, ui:Label{Text='Shape / status', Weight=3}},
                ui:VGroup(screenRows),
                ui:HGroup{Weight=0,
                    ui:Button{ID='Previous', Text='Previous', Enabled=false, Weight=0},
                    ui:Button{ID='Next', Text='Next', Enabled=false, Weight=0},
                    ui:Label{ID='PageInfo', Text='No map loaded'}}}},
        ui:HGroup{Weight=0,
            ui:Button{ID='SelectAll', Text='Select all', Enabled=false, Weight=0},
            ui:Button{ID='SelectNone', Text='Select none', Enabled=false, Weight=0},
            ui:Label{ID='SelectionInfo', Text='0 screens selected'}},
        ui:Label{Text='Use a timeline matching the map’s canvas. Existing clips keep their current mappings.', WordWrap=true, Weight=0},
        ui:Label{Text='Bundles are saved in Documents/DaVinci Slice/Maps.', Weight=0},
        ui:TextEdit{ID='Status', ReadOnly=true, MinimumSize={0,42}, MaximumSize={16777215,56}, Weight=0},
        ui:HGroup{Weight=0,
            ui:Button{ID='CloseButton', Text='Close', Weight=0}, ui:HGap(0,1),
            ui:Button{ID='ApplyFusion', Text='Add to Fusion', Enabled=false, Weight=0,
                ToolTip='Open your clip on the Fusion page. Adds a mapper before MediaOut immediately; controls live on the Fusion page.'},
            ui:Button{ID='CreateBundle', Text='Create Bundle', Enabled=false, Weight=0},
            ui:Button{ID='CreateInstall', Text='Create & Install', Enabled=false, Weight=0}}
    }))
local items = win:GetItems()
items.InitialRouting:AddItems({'Single screen','Repeat to checked screens','Span full map'})
local state = {selected={}, page=1, busy=false, preview_files={}, preview_revision=0, view={zoom=1,cx=.5,cy=.5}}

local function status(text) items.Status.PlainText = text end
local function pixels(value)
    return string.format('%.3f', value):gsub('0+$', ''):gsub('%.$', '')
end
local function selected_names()
    local result = {}
    for _, entry in ipairs(state.catalog and state.catalog.slices or {}) do
        if entry.supported and state.selected[entry.name] then result[#result+1] = entry.name end
    end
    return result
end
local function decode_base64(encoded)
    local alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
    local result,value,bits={},0,0
    for i=1,#encoded do
        local n=alphabet:find(encoded:sub(i,i),1,true)
        if n then
            value=value*64+n-1; bits=bits+6
            if bits>=8 then
                bits=bits-8; result[#result+1]=string.char(math.floor(value/2^bits)%256)
                value=value%2^bits
            end
        end
    end
    return table.concat(result)
end
local function display_preview(png)
    state.preview_revision=state.preview_revision+1
    local path=base..'_preview_'..state.preview_revision..'.png'
    local file=assert(io.open(path,'wb')); file:write(decode_base64(png)); file:close()
    state.preview_files[#state.preview_files+1]=path
    items.MapPreview.Text=''; items.MapPreview.Icon=ui:Icon{File=path}
    if #state.preview_files>3 then os.remove(table.remove(state.preview_files,1)) end
end
local function refresh_preview()
    if not state.catalog or not state.preview then return end
    local names=selected_names()
    local key=json{names=names,view=state.view}
    if state.preview_key==key then return end
    local response=service{action='preview',path=state.path,names=names,view=state.view,expected_sha256=state.catalog.source_sha256}
    if not response.ok then
        state.preview_error=true
        items.CreateBundle.Enabled=false; items.CreateInstall.Enabled=false
        items.ApplyFusion.Enabled=false
        items.PreviewInfo.Text='Preview is out of date. Reload the XML to continue.'
        status('Could not update preview: '..tostring(response.error)); return
    end
    state.preview=response.preview; state.view=response.preview.view
    display_preview(response.preview_png); state.preview_key=json{names=names,view=state.view}
    items.ZoomInfo.Text=math.floor(state.view.zoom*100+.5)..'%'
    items.ZoomOut.Enabled=state.view.zoom>1; items.ZoomIn.Enabled=state.view.zoom<16
end
local function update_selection()
    if state.busy then return end
    local count = #selected_names()
    items.SelectionInfo.Text = count .. (count == 1 and ' screen selected' or ' screens selected')
    items.CreateBundle.Enabled = state.catalog ~= nil and count > 0 and not state.preview_error
    items.CreateInstall.Enabled = state.catalog ~= nil and count > 0 and not state.preview_error
    items.ApplyFusion.Enabled = state.catalog ~= nil and count > 0 and not state.preview_error
    refresh_preview()
end
local function show_page()
    state.busy=true
    local entries = state.catalog and state.catalog.slices or {}
    local pages = math.max(1, math.ceil(#entries/PAGE_SIZE))
    for i=1,PAGE_SIZE do
        local entry = entries[(state.page-1)*PAGE_SIZE+i]
        local check, metrics, availability = items['Screen'..i], items['Metrics'..i], items['Availability'..i]
        check.Text=entry and (((state.page-1)*PAGE_SIZE+i)..'  '..entry.name) or ''; check.Enabled=entry and entry.supported or false
        check.Checked=entry and state.selected[entry.name] or false
        metrics.Text=entry and entry.width and (pixels(entry.width)..' × '..pixels(entry.height)) or ''
        metrics.ToolTip=entry and entry.width and ('Position: '..pixels(entry.x)..', '..pixels(entry.y)..' px') or ''
        availability.Text=entry and (entry.supported and ((entry.shape or 'Rectangle') .. (entry.warning and ' • clipped' or '')) or 'Unavailable') or ''
        availability.ToolTip=entry and (entry.warning or entry.reason) or ''
        check.ToolTip=entry and (entry.supported and (entry.screen .. (entry.warning and ' • '..entry.warning or '')) or entry.reason) or ''
    end
    items.Previous.Enabled=state.page>1
    items.Next.Enabled=state.page<pages
    items.PageInfo.Text=#entries>0 and ('Page '..state.page..' of '..pages) or 'No map loaded'
    state.busy=false
    update_selection()
end
local function reset_preview()
    state.catalog=nil; state.path=nil; state.selected={}; state.page=1; state.busy=true
    items.MapInfo.Text='No map loaded.'
    items.SelectAll.Enabled=false; items.SelectNone.Enabled=false
    state.preview=nil; state.preview_key=nil; state.preview_error=nil
    state.view={zoom=1,cx=.5,cy=.5}; state.drag=nil
    items.ZoomInfo.Text='100%'; items.ZoomOut.Enabled=false; items.ZoomIn.Enabled=false
    items.FitView.Enabled=false; items.PanMode.Enabled=false
    items.MapPreview.Icon=ui:Icon{File=''}; items.MapPreview.Text='Load an XML map to preview its screens.'
    items.PreviewInfo.Text='Click to select. Wheel to zoom. Pan: middle/right drag, or enable Pan.'
    state.busy=false; show_page()
end
local function load_xml()
    reset_preview()
    local path = items.XMLPath.Text:match('^%s*(.-)%s*$')
    items.XMLPath.Text=path
    local response = service{action='inspect', path=path}
    if not response.ok then status('Could not load XML: ' .. tostring(response.error)); return end
    state.catalog=response.catalog; state.path=path; state.busy=true
    local supported = 0
    for _, entry in ipairs(state.catalog.slices) do
        state.selected[entry.name]=entry.supported
        if entry.supported then supported=supported+1 end
    end
    state.busy=false
    local c = state.catalog
    items.EffectName.Text = response.suggested_name
    state.preview=response.preview; state.view=response.preview.view
    state.preview_key=json{names=selected_names(),view=state.view}
    display_preview(response.preview_png)
    items.ZoomIn.Enabled=true; items.FitView.Enabled=true; items.PanMode.Enabled=true
    items.MapInfo.Text = c.width .. ' × ' .. c.height .. ' canvas  •  ' .. supported .. ' of ' .. #c.slices .. ' slices available'
    items.SelectAll.Enabled=supported>0; items.SelectNone.Enabled=supported>0
    status(supported>0 and 'Click screens in the map or use the checkboxes. Coloured screens will be included in the effect.'
        or 'No supported input geometry found. Hover over an unavailable screen for its reason.')
    show_page()
end
local function in_rect(rect,x,y)
    return x>=rect[1] and x<rect[1]+rect[3] and y>=rect[2] and y<rect[2]+rect[4]
end
local function in_polygon(points,x,y)
    local inside=false
    local a=points[#points]
    for _,b in ipairs(points) do
        if (a[2]>y)~=(b[2]>y) and x<(b[1]-a[1])*(y-a[2])/(b[2]-a[2])+a[1] then inside=not inside end
        a=b
    end
    return inside
end
local function preview_point(pos)
    if not state.preview or not pos then return nil end
    local size=items.MapPreview:Size()
    local w,h=size[1] or size.Width or size.width, size[2] or size.Height or size.height
    local px,py=pos[1] or pos.x or pos.X, pos[2] or pos.y or pos.Y
    if not w or not h or not px or not py then return nil end
    local scale=math.min((w-4)/state.preview.width,(h-4)/state.preview.height)
    if scale<=0 then return nil end
    local x=(px-(w-state.preview.width*scale)/2)/scale
    local y=(py-(h-state.preview.height*scale)/2)/scale
    return x,y
end
local function preview_hit(pos)
    local x,y=preview_point(pos)
    if not x or not in_rect(state.preview.canvas,x,y) then return nil end
    for i=#state.preview.shapes,1,-1 do
        local shape=state.preview.shapes[i]
        local hit=in_rect(shape.rect,x,y)
        if hit then
            for _,c in ipairs(shape.constraints) do
                if in_polygon(c.points,x,y)~=c.inside then hit=false; break end
            end
        end
        if hit then return shape.supported and shape.index or nil end
    end
end
local function zoom_view(factor,pos)
    if not state.preview or state.preview_error then return end
    local zoom=math.min(16,math.max(1,state.view.zoom*factor))
    local x,y=preview_point(pos)
    x,y=x or state.preview.width/2,y or state.preview.height/2
    local c=state.preview.canvas
    local nx,ny=(x-c[1])/c[3],(y-c[2])/c[4]
    local ratio=zoom/state.view.zoom
    state.view={zoom=zoom,cx=math.min(1,math.max(0,nx-(x-state.preview.width/2)/(c[3]*ratio))),
        cy=math.min(1,math.max(0,ny-(y-state.preview.height/2)/(c[4]*ratio)))}
    if zoom==1 then state.view.cx=.5; state.view.cy=.5 end
    refresh_preview()
end
local function pan_view(pos)
    local x,y=preview_point(pos)
    if not x or not state.drag then return end
    local d=state.drag
    state.view.cx=math.min(1,math.max(0,d.cx-(x-d.x)/d.width))
    state.view.cy=math.min(1,math.max(0,d.cy-(y-d.y)/d.height))
    if state.view.zoom==1 then state.view.cx=.5; state.view.cy=.5 end
    refresh_preview()
end
local function describe_preview(index)
    local entry=index and state.catalog.slices[index]
    if entry then
        items.PreviewInfo.Text=index..'  '..entry.name..'  •  '..(state.selected[entry.name] and 'Included' or 'Excluded')..'  •  Click to toggle'
    else items.PreviewInfo.Text='Click to select. Wheel to zoom. Pan: middle/right drag, or enable Pan.' end
end
local function select_all(checked)
    state.busy=true
    for _, entry in ipairs(state.catalog and state.catalog.slices or {}) do
        state.selected[entry.name]=entry.supported and checked
    end
    state.busy=false; show_page()
end
local function create_effect(install)
    if not state.catalog then status('Load an XML map first.'); return end
    if state.path ~= items.XMLPath.Text then status('The XML path changed. Click Load to review it first.'); return end
    local result = service{action='build', path=state.path, name=items.EffectName.Text,
        names=selected_names(), routing=items.InitialRouting.CurrentIndex, expected_sha256=state.catalog.source_sha256}
    if not result.ok then status('Could not create effect: ' .. tostring(result.error)); return end
    status('Created ' .. result.name .. ' with ' .. result.count .. ' screens.\n' .. result.bundle
        .. '\nDouble-click the bundle to install. Restart Resolve if the effect does not appear.')
    print('[Slice Mapper] Created ' .. result.bundle)
    if install then state.install=result.bundle; dispatcher:ExitLoop() end
end
local function add_to_fusion()
    if not state.catalog then status('Load an XML map first.'); return end
    if state.path~=items.XMLPath.Text then status('The XML path changed. Click Load first.'); return end
    local resolve=bmd.scriptapp('Resolve')
    if not resolve or resolve:GetCurrentPage()~='fusion' then
        status('Open your target clip on the Fusion page, then click Add to Fusion. The mapping will be inserted before MediaOut.'); return
    end
    local result=service{action='setting',path=state.path,names=selected_names(),routing=items.InitialRouting.CurrentIndex,expected_sha256=state.catalog.source_sha256}
    if not result.ok then status('Could not prepare mapping: '..tostring(result.error)); return end
    result.source_sha256=state.catalog.source_sha256
    local ok,name=pcall(apply_to_fusion,result.setting,result,base..'_map.setting')
    if not ok then status('Could not add mapping: '..tostring(name)); return end
    status('Added '..result.map_name..' ('..result.count..' screens) as '..name..'. Adjust its controls in the Fusion Inspector. Undo: Add Slice Mapper.')
end
win.On[WINDOW_ID].Close = function(ev) dispatcher:ExitLoop() end
win.On.CloseButton.Clicked = function(ev) dispatcher:ExitLoop() end
win.On.BrowseXML.Clicked = function(ev)
    local start = (os.getenv('HOME') or '') .. '/Documents/Resolume Arena/Presets/Advanced Output/'
    local path = fusion:RequestFile(start, '', {FReqS_Filter='XML Files (*.xml)|*.xml', FReqB_Multi=false})
    if path then items.XMLPath.Text=tostring(path); load_xml() end
end
win.On.LoadXML.Clicked = load_xml
win.On.XMLPath.ReturnPressed = load_xml
win.On.MapPreview.MousePress=function(ev)
    if state.busy or state.preview_error then return end
    if items.PanMode.Checked or ev.Button==2 or ev.Button==4 or ev.Button=='RightButton' or ev.Button=='MiddleButton' then
        local x,y=preview_point(ev.Pos)
        if x then state.drag={x=x,y=y,cx=state.view.cx,cy=state.view.cy,width=state.preview.canvas[3],height=state.preview.canvas[4]} end
        return
    end
    if ev.Button and ev.Button~=1 and ev.Button~='LeftButton' then return end
    local index=preview_hit(ev.Pos)
    if index then
        local entry=state.catalog.slices[index]
        state.selected[entry.name]=not state.selected[entry.name]
        state.page=math.ceil(index/PAGE_SIZE)
        show_page()
    end
    describe_preview(index)
end
win.On.MapPreview.MouseMove=function(ev)
    if state.drag then pan_view(ev.Pos) else describe_preview(preview_hit(ev.Pos)) end
end
win.On.MapPreview.MouseRelease=function(ev)
    if state.drag then pan_view(ev.Pos); state.drag=nil end
end
win.On.MapPreview.Wheel=function(ev)
    local a=ev.AngleDelta
    local delta=type(a)=='table' and (a[2] or a.y or a.Y) or ev.Delta
    if type(delta)~='number' or delta==0 then
        local p=ev.PixelDelta
        delta=type(p)=='table' and (p[2] or p.y or p.Y) or 0
    end
    if type(delta)=='number' and delta~=0 then zoom_view(1.25^(math.min(480,math.max(-480,delta))/120),ev.Pos) end
end
win.On.MapPreview.Leave=function(ev) if not state.drag then describe_preview(nil) end end
win.On.ZoomIn.Clicked=function(ev) zoom_view(1.5) end
win.On.ZoomOut.Clicked=function(ev) zoom_view(1/1.5) end
win.On.FitView.Clicked=function(ev)
    state.view={zoom=1,cx=.5,cy=.5}; state.drag=nil; refresh_preview()
end
for i=1,PAGE_SIZE do
    local index=i
    win.On['Screen'..i].Toggled=function(ev)
        if state.busy or not state.catalog then return end
        local entry=state.catalog.slices[(state.page-1)*PAGE_SIZE+index]
        if entry and entry.supported then state.selected[entry.name]=items['Screen'..index].Checked end
        update_selection()
    end
end
win.On.Previous.Clicked=function(ev) state.page=state.page-1; show_page() end
win.On.Next.Clicked=function(ev) state.page=state.page+1; show_page() end
win.On.SelectAll.Clicked = function(ev) select_all(true) end
win.On.SelectNone.Clicked = function(ev) select_all(false) end
win.On.CreateBundle.Clicked = function(ev) create_effect(false) end
win.On.CreateInstall.Clicked = function(ev) create_effect(true) end
win.On.ApplyFusion.Clicked = add_to_fusion
status('Choose a Resolume Advanced Output XML to begin.')
win:Show(); dispatcher:RunLoop(); win:Hide(); os.remove(helper)
for _,path in ipairs(state.preview_files) do os.remove(path) end
if state.install then
    local ok = os.execute('open ' .. shell_quote(state.install) .. ' >/dev/null 2>&1 &')
    print('[Slice Mapper] Opening Resolve installer: ' .. state.install)
end
