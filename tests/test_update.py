import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from map_update import map_manifest,plan_update
from build_slice_mapper import read_layout,make_setting
from import_service import execute_request

FIXTURE=Path(__file__).parent/'fixtures/ultra_input.xml'

class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.layout=read_layout(FIXTURE,['DJ','US','SR CIRCLE'])
        self.previous=map_manifest(self.layout)

    def test_reordering_preserves_original_indices_and_does_not_mutate(self):
        source=copy.deepcopy(self.layout)
        source['slices'].reverse()
        before=copy.deepcopy(source)
        updated,report=plan_update(self.previous,source)
        self.assertEqual([s['name'] for s in updated['slices']],['DJ','US','SR CIRCLE'])
        self.assertEqual(report['matched'],3)
        self.assertEqual(source,before)

    def test_rename_matches_by_resolume_id(self):
        self.layout['slices'][2]['name']='SR Ring'
        updated,report=plan_update(self.previous,self.layout)
        self.assertEqual(updated['slices'][2]['name'],'SR Ring')
        self.assertEqual(report['renamed'],[{'old':'SR CIRCLE','new':'SR Ring'}])
        self.assertEqual(report['added'],[])

    def test_legacy_and_reexported_ids_match_unique_names(self):
        for s in self.previous['slices']: s.pop('id')
        self.previous['legacy']=True
        updated,report=plan_update(self.previous,self.layout)
        self.assertEqual(report['matched'],3)
        self.assertTrue(report['legacy'])
        self.assertEqual(updated['slices'],self.layout['slices'])

    def test_removed_slots_are_transparent_and_new_screens_start_off(self):
        old_us=self.layout['slices'].pop(1)
        self.layout['slices'].append(dict(old_us,name='New',id='new'))
        updated,report=plan_update(self.previous,self.layout)
        self.assertTrue(updated['slices'][1]['missing'])
        self.assertEqual(updated['slices'][3]['destination_default'],0)
        self.assertEqual(report['removed'],['US'])
        self.assertEqual(report['added'],['New'])
        setting=make_setting(updated)
        self.assertIn('Missing: US',setting)
        branch=setting.split('Screen2Clip1 = Merge',1)[1].split('Screen3Resize',1)[0]
        self.assertIn('Blend = Input { Value = 1, Expression = "0"',branch)
        self.assertIn('Send4 = Input { Value = 0',setting)

    def test_returning_screen_recovers_original_slot(self):
        partial=dict(self.layout,slices=self.layout['slices'][:1])
        removed,_=plan_update(self.previous,partial)
        restored,report=plan_update(map_manifest(removed),self.layout)
        self.assertEqual([s['name'] for s in restored['slices']],['DJ','US','SR CIRCLE'])
        self.assertFalse(any(s.get('missing') for s in restored['slices']))
        self.assertEqual(report['matched'],3)

    def test_duplicate_ids_do_not_override_name_identity(self):
        for s in self.layout['slices']: s['id']='duplicate'
        updated,report=plan_update(self.previous,self.layout)
        self.assertEqual([s['name'] for s in updated['slices']],['DJ','US','SR CIRCLE'])
        self.assertEqual(report['matched'],3)

    def test_canvas_change_is_reported(self):
        self.layout['width']=4096
        _,report=plan_update(self.previous,self.layout)
        self.assertTrue(report['canvas_changed'])

    def test_invalid_manifest_fails_before_creating_geometry(self):
        for value in (None,{},dict(slices=[]),dict(slices=[{}])):
            with self.subTest(value=value),self.assertRaises(ValueError): plan_update(value,self.layout)

    def test_service_update_uses_json_manifest_and_ignores_new_routing_default(self):
        catalog=execute_request(dict(action='inspect',path=str(FIXTURE)))['catalog']
        result=execute_request(dict(action='update',path=str(FIXTURE),names=['SR CIRCLE','US','DJ'],
            previous_json=json.dumps(self.previous),expected_sha256=catalog['source_sha256'],routing=2))
        manifest=json.loads(result['manifest_json'])
        self.assertEqual([s['name'] for s in manifest['slices']],['DJ','US','SR CIRCLE'])
        self.assertEqual(result['report']['matched'],3)
        self.assertEqual(result['count'],3)

if __name__=='__main__': unittest.main()
