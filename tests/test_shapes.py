import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from build_slice_mapper import inspect_layout, select_layout, read_contour, make_setting, build_effect

FIXTURE = Path(__file__).parent / 'fixtures/ultra_input.xml'


class ShapeTests(unittest.TestCase):
    def setUp(self):
        self.catalog = inspect_layout(FIXTURE)
        self.by_name = {s['name']: s for s in self.catalog['slices']}

    def test_all_ultra_screens_are_available(self):
        self.assertEqual(len(self.by_name), 14)
        self.assertTrue(all(s['supported'] for s in self.by_name.values()))
        self.assertEqual(sum(s['shape'] == 'Polygon' for s in self.by_name.values()), 12)
        self.assertEqual(sum(s['shape'] == 'Bezier mask' for s in self.by_name.values()), 2)

    def test_concave_u_preserves_all_28_input_vertices(self):
        s = self.by_name['U OUTER']
        self.assertEqual(s['contour']['segments'], 'L'*28)
        self.assertEqual(len(s['contour']['points']), 28)
        self.assertAlmostEqual(s['contour']['points'][4]['x'], 1722.3681640625)

    def test_circle_retains_four_cubic_segments_and_closing_handles(self):
        mask = self.by_name['SR CIRCLE']['masks'][0]
        self.assertTrue(mask['keep_inside'])
        self.assertEqual(mask['contour']['segments'], 'CCCC')
        p = mask['contour']['points']
        self.assertEqual(len(p), 4)
        self.assertAlmostEqual(p[0]['x'], 1488.18359375)
        self.assertAlmostEqual(p[0]['rx'], 58.9102783203125)
        self.assertAlmostEqual(p[0]['lx'], -58.9102783203125)
        self.assertAlmostEqual(p[1]['ly'], -58.9102783203125)
        self.assertAlmostEqual(p[1]['ry'], 58.9102783203125)

    def test_off_canvas_polygon_keeps_original_coordinates(self):
        s = self.by_name['SR 1']
        self.assertLess(s['x'], 0)
        self.assertLess(s['contour']['points'][0]['x'], 0)
        self.assertEqual(s['warning'], 'Clipped at canvas edge')

    def test_invert_and_disabled_mask_flags(self):
        root = ET.parse(FIXTURE)
        slice_node = root.find('.//Slice')
        mask = slice_node.find('SliceMask')
        invert = mask.find('./Params/Param[@name="Invert"]')
        enabled = mask.find('./Params/Param[@name="Enabled"]')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'map.xml'
            invert.set('value', '0')
            root.write(path)
            s = {s['name']:s for s in inspect_layout(path)['slices']}['SR CIRCLE']
            self.assertFalse(s['masks'][0]['keep_inside'])
            setting = make_setting(select_layout(inspect_layout(path), ['SR CIRCLE']))
            self.assertIn('Invert = Input { Value = 1, }', setting)
            enabled.set('value', '0')
            root.write(path)
            s = {s['name']:s for s in inspect_layout(path)['slices']}['SR CIRCLE']
            self.assertEqual(s['shape'], 'Rectangle')
            self.assertNotIn('masks', s)

    def test_rejects_malformed_contours_without_rectangle_fallback(self):
        original = ET.parse(FIXTURE).find('.//InputContour')
        for kind in ['open', 'unknown', 'count', 'nonfinite', 'degenerate']:
            c = copy.deepcopy(original)
            if kind == 'open': c.set('closed','0')
            elif kind == 'unknown': c.find('segments').text = 'QQQQ'
            elif kind == 'count': c.find('segments').text = 'CCCC'
            elif kind == 'nonfinite': c.find('points/v').set('x','nan')
            else:
                for v in c.findall('points/v'): v.set('x','1'); v.set('y','1')
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                read_contour(c)

    def test_handles_mixed_linear_and_cubic_segments(self):
        c = ET.fromstring('<Contour closed="1"><points><v x="0" y="0"/><v x="10" y="0"/><v x="10" y="5"/><v x="5" y="10"/><v x="0" y="10"/></points><segments>LCL</segments></Contour>')
        p = read_contour(c)['points']
        self.assertEqual(len(p),3)
        self.assertEqual((p[1]['rx'],p[1]['ry']),(0,5))
        self.assertEqual((p[2]['lx'],p[2]['ly']),(5,0))

    def test_build_embeds_vectors_and_subset_without_external_assets(self):
        layout = select_layout(self.catalog, ['U OUTER','SR CIRCLE'])
        with tempfile.TemporaryDirectory() as tmp:
            result = build_effect(layout,'Complex show',Path(tmp))
            setting = result['setting'].read_text()
            self.assertIn('PolylineMask', setting)
            self.assertIn('Closed = true', setting)
            self.assertIn('LX = -0.015341218312581', setting)
            self.assertNotIn('Loader', setting)
            self.assertNotIn('CCS_AddString = "US"', setting)
            data = json.loads(result['manifest'].read_text())
            self.assertEqual(data['format_version'],3)
            self.assertEqual(len(data['slices'][0]['contour']['points']),28)


if __name__ == '__main__':
    unittest.main()
