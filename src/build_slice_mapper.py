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


if __name__ == "__main__":
    main()
