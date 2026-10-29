import base64
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from build_slice_mapper import inspect_layout
from import_service import execute_request
from map_preview import preview_model, render_preview, hit_test, intersect, subtract, normalize_view
from test_layout import xml

FIXTURE=Path(__file__).parent/'fixtures/ultra_input.xml'


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.catalog=inspect_layout(FIXTURE)
        self.model=preview_model(self.catalog)

    def project(self,x,y):
        a,b,w,h=self.model['canvas']
        return a+x*w/self.catalog['width'],b+y*h/self.catalog['height']

    def test_polygon_circle_and_concavity_hit_their_actual_boundaries(self):
        for point,expected in [((1920,1000),2),((1920,550),3),((1800,250),None),
                               ((1920,200),4),((1488,564),9),((1385,460),None),
                               ((2349,564),14),((2250,465),None),((100,1800),None),
                               ((-1,800),None),((4000,1000),None)]:
            with self.subTest(point=point):
                self.assertEqual(hit_test(self.model,*self.project(*point)),expected)

    def test_mask_inversion_makes_a_hole_without_expanding_the_rectangle(self):
        catalog=copy.deepcopy(self.catalog)
        s=catalog['slices'][-1]
        s['masks'][0]['keep_inside']=False
        model=preview_model(catalog)
        self.assertIsNone(hit_test(model,*self.project(2349,564)))
        self.assertEqual(hit_test(model,*self.project(2250,465)),14)
        self.assertIsNone(hit_test(model,*self.project(2230,564)))

    def test_overlap_hits_topmost_and_unavailable_shapes_cannot_be_clicked_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'map.xml'; path.write_text(xml())
            c=inspect_layout(path)
        top=copy.deepcopy(c['slices'][0]); top['name']='Top'
        c['slices'].append(top)
        m=preview_model(c)
        x,y,w,h=m['shapes'][0]['rect']
        self.assertEqual(hit_test(m,x+w/2,y+h/2),2)
        m['shapes'][1]['supported']=False
        self.assertIsNone(hit_test(m,x+w/2,y+h/2))

    def test_preview_keeps_canvas_aspect_and_embeds_a_valid_local_png(self):
        x,y,w,h=self.model['canvas']
        self.assertAlmostEqual(w/h,3840/2160)
        self.assertGreaterEqual(x,0); self.assertGreaterEqual(y,0)
        image=base64.b64decode(render_preview(self.model,[]))
        self.assertEqual(image[:8],b'\x89PNG\r\n\x1a\n')
        self.assertEqual(struct.unpack('>II',image[16:24]),(1280,800))
        offset=8; data=b''
        while offset<len(image):
            n=struct.unpack('>I',image[offset:offset+4])[0]
            if image[offset+4:offset+8]==b'IDAT': data+=image[offset+8:offset+8+n]
            offset+=n+12
        self.assertEqual(len(zlib.decompress(data)),800*(1280*3+1))

    def test_selection_changes_highlights_without_changing_hit_geometry(self):
        before=json.dumps(self.model,sort_keys=True)
        a=render_preview(self.model,[])
        b=render_preview(self.model,['US','SR CIRCLE'])
        self.assertNotEqual(a,b)
        self.assertEqual(before,json.dumps(self.model,sort_keys=True))

    def test_portrait_canvas_is_centered_with_letterboxing(self):
        c=copy.deepcopy(self.catalog); c.update(width=2160,height=3840)
        m=preview_model(c)
        x,y,w,h=m['canvas']
        self.assertAlmostEqual(w/h,2160/3840)
        self.assertAlmostEqual(x,(m['width']-w)/2)
        self.assertAlmostEqual(y,(m['height']-h)/2)
        self.assertIsNone(hit_test(m,0,0))

    def test_interval_intersections_and_holes_preserve_disjoint_spans(self):
        self.assertEqual(intersect([(0,10),(20,30)],[(5,25)]),[(5,10),(20,25)])
        self.assertEqual(subtract([(0,30)],[(5,10),(15,20)]),[(0,5),(10,15),(20,30)])

    def test_preview_service_allows_empty_selection_and_checks_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'map.xml'; path.write_text(xml())
            info=execute_request(dict(action='inspect',path=str(path)))
            self.assertIn('preview_png',info)
            request=dict(action='preview',path=str(path),names=[],expected_sha256=info['catalog']['source_sha256'])
            result=execute_request(request)
            self.assertTrue(result['ok'])
            path.write_text(xml().replace('383.5','384').replace('895.5','896'))
            with self.assertRaisesRegex(ValueError,'changed on disk'): execute_request(request)

    def test_zoom_and_pan_keep_the_same_screen_under_its_projected_point(self):
        before=json.dumps(self.catalog,sort_keys=True)
        for view in [dict(zoom=4,cx=.3875,cy=.261),dict(zoom=16,cx=.6118,cy=.261),dict(zoom=2,cx=.1,cy=.8)]:
            m=preview_model(self.catalog,view)
            for raw,index in [((1488,564),9),((2349,564),14),((1920,1000),2)]:
                a,b,w,h=m['canvas']
                self.assertEqual(hit_test(m,a+raw[0]*w/3840,b+raw[1]*h/2160),index)
            self.assertEqual(m['width'],1280)
        self.assertEqual(before,json.dumps(self.catalog,sort_keys=True))

    def test_zoom_is_bounded_and_fit_always_recentres(self):
        self.assertEqual(normalize_view(dict(zoom=.1,cx=0,cy=1)),dict(zoom=1,cx=.5,cy=.5))
        self.assertEqual(normalize_view(dict(zoom=100,cx=-9,cy=4)),dict(zoom=16,cx=0,cy=1))
        with self.assertRaisesRegex(ValueError,'finite'): normalize_view(dict(zoom=float('nan')))

    def test_zoomed_preview_rerenders_into_a_fixed_size_image(self):
        model=preview_model(self.catalog,dict(zoom=8,cx=.3875,cy=.261))
        png=base64.b64decode(render_preview(model,['SR CIRCLE']))
        self.assertEqual(struct.unpack('>II',png[16:24]),(1280,800))
        self.assertNotEqual(png,base64.b64decode(render_preview(self.model,['SR CIRCLE'])))


if __name__=='__main__': unittest.main()
