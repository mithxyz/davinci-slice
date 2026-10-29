import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from build_slice_mapper import inspect_layout,select_layout,make_setting
from import_service import execute_request

FIXTURE=Path(__file__).parent/'fixtures/ultra_input.xml'


class RoutingTests(unittest.TestCase):
    def test_routes_share_one_source_with_independent_framing_and_destination_toggles(self):
        layout=select_layout(inspect_layout(FIXTURE),['US','SR CIRCLE'])
        text=make_setting(layout)
        for name in ['Single screen','Repeat to checked screens','Span full map','Send to: US','Send to: SR CIRCLE']:
            self.assertIn(name,text)
        self.assertIn('Screen1Resize = BetterResize',text)
        self.assertIn('Screen2Resize = BetterResize',text)
        self.assertIn('Controls.Send1',text)
        self.assertIn('Controls.Send2',text)
        self.assertEqual(text.count('SourceOp = "SourceResize", Source = "Output"'),2)
        self.assertIn('DestinationMode = Input { Value = 0',text)
        self.assertIn('SourceOp = "ScreenOutput"',text)

    def test_direct_setting_is_portable_and_creates_no_bundle_or_library_entry(self):
        catalog=inspect_layout(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=execute_request(dict(action='setting',path=str(FIXTURE),names=['US','SR CIRCLE'],
                                        expected_sha256=catalog['source_sha256']),root,[])
            self.assertTrue(result['ok'])
            self.assertEqual(result['count'],2)
            self.assertIn('PolylineMask',result['setting'])
            self.assertEqual(list(root.iterdir()),[])
            self.assertEqual((result['width'],result['height']),(3840,2160))

    def test_importer_can_start_in_either_multi_screen_mode(self):
        catalog=inspect_layout(FIXTURE)
        for routing in (1,2):
            result=execute_request(dict(action='setting',path=str(FIXTURE),names=['US','SR CIRCLE'],routing=routing,
                expected_sha256=catalog['source_sha256']))
            self.assertIn(f'DestinationMode = Input {{ Value = {routing},',result['setting'])
            self.assertIn(f'Name = "Routing", Default = {routing}',result['setting'])


if __name__=='__main__': unittest.main()
