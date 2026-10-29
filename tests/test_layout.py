import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_slice_mapper import read_layout


def xml(rect='<v x="383.5" y="0"/><v x="895.5" y="0"/><v x="895.5" y="512"/><v x="383.5" y="512"/>', copies=1):
    node = f'''<Slice uniqueId="1"><Params name="Common"><Param name="Name" value="SQ-1"/><Param name="Enabled" value="1"/></Params><InputRect orientation="0">{rect}</InputRect><OutputRect><v x="0" y="1080"/></OutputRect></Slice>'''
    return f'''<XmlState name="Test"><ScreenSetup><CurrentCompositionTextureSize width="3840" height="2160"/><screens><Screen><layers>{node * copies}</layers></Screen></screens></ScreenSetup></XmlState>'''


class LayoutTests(unittest.TestCase):
    def parse(self, content, names=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.xml"
            path.write_text(content)
            return read_layout(path, names or ["SQ-1"])

    def test_preserves_fractional_input_geometry_not_output_position(self):
        layout = self.parse(xml())
        self.assertEqual((layout["width"], layout["height"]), (3840, 2160))
        self.assertEqual({k: layout["slices"][0][k] for k in ("x", "y", "width", "height")},
                         dict(x=383.5, y=0, width=512, height=512))

    def test_rejects_missing_name(self):
        with self.assertRaisesRegex(ValueError, "not found"):
            self.parse(xml(), ["Main"])

    def test_rejects_duplicate_slice_name(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.parse(xml(copies=2))

    def test_rejects_skewed_input_instead_of_using_its_bounding_box(self):
        with self.assertRaisesRegex(ValueError, "Nonrectangular"):
            self.parse(xml().replace('x="895.5" y="0"', 'x="800" y="0"'))

    def test_rejects_disabled_slice(self):
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.parse(xml().replace('name="Enabled" value="1"', 'name="Enabled" value="0"'))


if __name__ == "__main__":
    unittest.main()
