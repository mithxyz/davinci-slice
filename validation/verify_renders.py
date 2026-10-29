"""Check full-resolution RGBA output actually rendered by Resolve."""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
layout = json.loads((ROOT / "dist/layout.json").read_text())
screens = {s["name"]: s for s in layout["slices"]}
cases = [
    ("main_fill", "Main", "Fill"),
    ("square_fill", "SQ-1", "Fill"),
    ("square_fit", "SQ-1", "Fit"),
    ("square_framing", "SQ-1", "Framing"),
    ("column_fill", "Col-L1", "Fill"),
    ("column_fit", "Col-L1", "Fit"),
    ("column_stretch", "Col-L1", "Stretch"),
]
report = []
for name, screen, mode in cases:
    im = Image.open(ROOT / "validation" / f"{name}00086400.png")
    assert im.mode == "RGBA" and im.size == (layout["width"], layout["height"]), name
    a = np.array(im)
    alpha = a[:, :, 3]
    s = screens[screen]
    left, top = math.floor(s["x"]), math.floor(s["y"])
    right, bottom = math.ceil(s["x"]+s["width"]), math.ceil(s["y"]+s["height"])
    allowed = np.zeros(alpha.shape, dtype=bool)
    allowed[top:bottom, left:right] = True
    assert not np.any(alpha[~allowed]), f"{name}: alpha leaks beyond the screen"
    yy, xx = np.where(alpha > 0)
    actual = [int(xx.min()), int(yy.min()), int(xx.max()+1), int(yy.max()+1)]
    expected = [left, top, right, bottom]
    if mode == "Fit":
        scale = min(s["width"]/1920, s["height"]/1080)
        fit_w, fit_h = math.floor(1920*scale+0.5), math.floor(1080*scale+0.5)
        x, y = s["x"]+(s["width"]-fit_w)/2, s["y"]+(s["height"]-fit_h)/2
        expected = [math.floor(x), math.floor(y), math.ceil(x+fit_w), math.ceil(y+fit_h)]
    assert actual == expected, f"{name}: expected {expected}, got {actual}"
    if name == "square_fill":
        assert 0 < alpha[100, left] < 255 and 0 < alpha[100, right-1] < 255
    report.append(dict(case=name, screen=screen, alpha_bounds=actual, outside_alpha_zero=True))

normal = np.array(Image.open(ROOT / "validation/square_fill00086400.png"))
framed = np.array(Image.open(ROOT / "validation/square_framing00086400.png"))
assert not np.array_equal(normal, framed), "Framing controls did not change the rendered image"
(ROOT / "validation/report.json").write_text(json.dumps(dict(resolve_version="21.0.2.4", cases=report), indent=2)+"\n")
print(f"PASS: {len(report)} Resolve RGBA renders; exact alpha bounds; no spill outside screens; framing changes content.")
