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
            blend=('0' if s.get('missing') else f'iif(Controls.DestinationMode < 0.5, iif(abs(Controls.NumberIn1 - {i}) < 0.5, 1, 0), Controls.Send{i+1})') if final else None
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
        ("Screen", "ComboControl", 0, 0, len(slices)-1, [('Missing: ' if s.get('missing') else '')+s["name"] for s in slices], True),
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
        label=('Missing: ' if s.get('missing') else 'Send to: ')+s['name']
        default=s.get('destination_default',1)
        published.append(f'Send{i} = InstanceInput {{ SourceOp = "Controls", Source = "Send{i}", Name = {quote(label)}, Default = {default}, }},')
        usercontrols.append(f'Send{i} = '+control(label,'CheckboxControl',default,0,1,integer=True)+',')
        values.append(f'Send{i} = '+input_value(str(default))+',')
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
