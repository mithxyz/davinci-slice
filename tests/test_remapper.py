import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from build_map_remapper import inspect_remap, select_remap, output_geometry, transform_geometry, remap_setting, build_remapper
from remap_service import remap_request
from package_remapper import package_remapper

XML='''<XmlState name="Test"><CurrentCompositionTextureSize width="640" height="360"/><screens>
<Screen name="Processor A" uniqueId="A"><OutputDevice><OutputDeviceVirtual width="800" height="400"/></OutputDevice><layers>
<Slice uniqueId="1"><Params name="Common"><Param name="Name" value="Left"/></Params>
<InputRect><v x="10.5" y="20"/><v x="110.5" y="20"/><v x="110.5" y="70"/><v x="10.5" y="70"/></InputRect>
<OutputRect><v x="300" y="100"/><v x="500" y="100"/><v x="500" y="250"/><v x="300" y="250"/></OutputRect></Slice>
</layers></Screen><Screen name="Processor B" uniqueId="B"><layers>
<Slice uniqueId="2"><Params name="Common"><Param name="Name" value="Right"/></Params>
<InputRect><v x="200" y="20"/><v x="300" y="20"/><v x="300" y="70"/><v x="200" y="70"/></InputRect>
<OutputRect><v x="300" y="100"/><v x="500" y="100"/><v x="500" y="250"/><v x="300" y="250"/></OutputRect></Slice>
</layers></Screen></screens></XmlState>'''


class RemapperTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'test.xml'; self.path.write_text(XML)
        self.catalog=inspect_remap(self.path)
        self.layout=select_remap(self.catalog,'0')

    def test_distinct_screen_spaces_and_fractional_pixels(self):
        self.assertEqual(len(self.catalog['screens']),2)
        self.assertEqual(self.layout['slices'][0]['input']['x'],10.5)
        self.assertEqual(self.layout['slices'][0]['output']['width'],200)
        self.assertEqual((self.layout['output_width'],self.layout['output_height']),(800,400))
        with self.assertRaisesRegex(ValueError,'belong'): select_remap(self.catalog,'0',['1'])
        self.assertFalse(self.catalog['screens'][1]['size_explicit'])

    def test_affine_inverse_preserves_bezier_handles_and_holes(self):
        g=dict(x=10.5,y=20,width=100,height=50,shape='Bezier mask',masks=[dict(keep_inside=False,contour=dict(segments='CCCC',points=[dict(x=11,y=25,lx=-2,ly=3,rx=4,ry=-5)]))])
        moved=transform_geometry(g,2,3,279,40)
        restored=transform_geometry(moved,.5,1/3,-139.5,-40/3)
        for key in ('x','y','width','height'): self.assertAlmostEqual(g[key],restored[key])
        for key,value in g['masks'][0]['contour']['points'][0].items(): self.assertAlmostEqual(value,restored['masks'][0]['contour']['points'][0][key])
        self.assertFalse(moved['masks'][0]['keep_inside'])
        self.assertEqual(g['x'],10.5)

    def test_rejects_actual_warp_without_bbox_fallback(self):
        root=ET.fromstring(XML); node=root.find('.//Slice')
        ET.SubElement(node,'Warper').append(ET.fromstring('<Homography><src><v x="0" y="0"/></src><dst><v x="1" y="0"/></dst></Homography>'))
        ET.ElementTree(root).write(self.path)
        c=inspect_remap(self.path)
        self.assertFalse(c['screens'][0]['slices'][0]['supported'])
        with self.assertRaisesRegex(ValueError,'Perspective'): select_remap(c,'0',['0'])

    def test_deformed_mesh_and_output_flips_are_unavailable(self):
        for extra in ['<Warper><BezierWarper controlWidth="2" controlHeight="2"><vertices><v x="300" y="100"/><v x="500" y="100"/><v x="300" y="250"/><v x="501" y="250"/></vertices></BezierWarper></Warper>', '<Params name="Output"><Param name="Flip" value="1"/></Params>']:
            root=ET.fromstring(XML);root.find('.//Slice').append(ET.fromstring(extra));ET.ElementTree(root).write(self.path)
            self.assertFalse(inspect_remap(self.path)['screens'][0]['slices'][0]['supported'])

    def test_duplicate_names_across_screens_are_independent(self):
        self.path.write_text(XML.replace('value="Right"','value="Left"'))
        catalog=inspect_remap(self.path)
        self.assertTrue(catalog['screens'][0]['slices'][0]['supported'])
        self.assertTrue(catalog['screens'][1]['slices'][0]['supported'])
        self.assertNotEqual(select_remap(catalog,'0')['slices'][0]['id'],select_remap(catalog,'1')['slices'][0]['id'])

    def test_polygon_output_cannot_silently_change_shape(self):
        root=ET.fromstring(XML); node=root.find('.//Slice');node.tag='Polygon'
        for side in ('Input','Output'):
            contour=ET.SubElement(node,side+'Contour',closed='1')
            points=ET.SubElement(contour,'points')
            for point in node.find(side+'Rect'): points.append(copy.deepcopy(point))
            ET.SubElement(contour,'segments').text='LLLL'
        ET.ElementTree(root).write(self.path)
        self.assertTrue(inspect_remap(self.path)['screens'][0]['slices'][0]['supported'])
        node.find('OutputContour/points/v').set('x','301')
        ET.ElementTree(root).write(self.path)
        record=inspect_remap(self.path)['screens'][0]['slices'][0]
        self.assertFalse(record['supported']);self.assertIn('polygon vertices',record['reason'])

    def test_stale_xml_and_missing_output_size(self):
        request=dict(action='setting',path=str(self.path),expected_sha256=self.catalog['source_sha256'],screen_key='1',keys=['1'])
        with self.assertRaisesRegex(ValueError,'Confirm'): remap_request(request)
        request.update(screen_key='0',keys=['0'])
        self.path.write_text(XML+'\n')
        with self.assertRaisesRegex(ValueError,'changed'): remap_request(request)

    def test_direction_and_size_validation(self):
        for direction in (-1,2,True,'0'):
            with self.assertRaises(ValueError): remap_setting(self.layout,direction)
        for size in [(0,100),(1.5,100),(16400,100)]:
            with self.assertRaises(ValueError): select_remap(self.catalog,'0',output_size=size)
        self.assertIn('Slice1ReverseWarp',remap_setting(self.layout,1))
        self.assertIn('iif(Controls.Direction < 0.5,800,640)',remap_setting(self.layout))

    def test_portable_bundle_and_collision(self):
        out=Path(self.temp.name)/'out'; result=build_remapper(self.layout,'Remap',out)
        with ZipFile(result['bundle']) as z:
            content=z.read('Edit/Effects/Davinci Slice/Remap.setting').decode()
        self.assertEqual(content,Path(result['setting']).read_text())
        self.assertNotIn('Loader',content)
        self.assertEqual(json.loads(Path(result['manifest']).read_text())['effect_type'],'map_remapper')
        with self.assertRaises(FileExistsError): build_remapper(self.layout,'Remap',out)

    def test_packaged_backend_runs_on_its_own(self):
        package_remapper(); req=Path(self.temp.name)/'req.json'; res=Path(self.temp.name)/'res.setting'
        req.write_text(json.dumps(dict(action='setting',path=str(self.path),expected_sha256=self.catalog['source_sha256'],screen_key='0',keys=['0'],direction=1)))
        subprocess.run([sys.executable,str(ROOT/'dist/map_remapper_backend.py'),str(req),str(res)],check=True)
        self.assertIn('["ok"] = true',res.read_text());self.assertIn('MapRemapper',res.read_text())


if __name__=='__main__': unittest.main()
