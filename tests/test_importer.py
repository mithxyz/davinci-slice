import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from build_slice_mapper import build_effect, inspect_layout, read_layout, select_layout, validate_effect_name, suggested_effect_name
from import_service import ensure_new_effect_name, execute_request, lua_data
from package_importer import package_importer
from test_layout import xml


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root/"map.xml"
        self.source.write_text(xml())

    def test_catalog_lists_unsupported_without_losing_supported_choices(self):
        unsupported = '<Slice><Params name="Common"><Param name="Name" value="Polygon"/></Params><InputPolygon/></Slice>'
        self.source.write_text(xml().replace('</layers>', unsupported+'</layers>'))
        catalog = inspect_layout(self.source)
        self.assertEqual(len(catalog["slices"]), 2)
        self.assertTrue(catalog["slices"][0]["supported"])
        self.assertFalse(catalog["slices"][1]["supported"])
        layout = select_layout(catalog, ["SQ-1"])
        self.assertEqual([s["name"] for s in layout["slices"]], ["SQ-1"])
        with self.assertRaisesRegex(ValueError, "framing"):
            select_layout(catalog, ["Polygon"])

    def test_screen_order_and_subset_are_preserved(self):
        second = xml()[xml().index('<Slice '):xml().index('</layers>')].replace('SQ-1','Other')
        self.source.write_text(xml().replace('</layers>',second+'</layers>'))
        layout = read_layout(self.source, ["Other", "SQ-1"])
        self.assertEqual([s["name"] for s in layout["slices"]], ["Other", "SQ-1"])
        result = build_effect(layout,"Show A",self.root/"bundles")
        text = result["setting"].read_text()
        self.assertLess(text.index('CCS_AddString = "Other"'),text.index('CCS_AddString = "SQ-1"'))

    def test_independently_named_bundles_coexist_and_round_trip(self):
        layout = read_layout(self.source,["SQ-1"])
        a = build_effect(layout,"Show A",self.root/"bundles")
        first_bytes = a["bundle"].read_bytes()
        b = build_effect(layout,"Show B",self.root/"bundles")
        self.assertTrue(a["bundle"].exists() and b["bundle"].exists())
        self.assertEqual(a["bundle"].read_bytes(),first_bytes)
        with ZipFile(b["bundle"]) as z:
            self.assertIn("Edit/Effects/Davinci Slice/",z.namelist())
            self.assertEqual(z.read("Edit/Effects/Davinci Slice/Show B.setting"),b["setting"].read_bytes())
        manifest = json.loads(b["manifest"].read_text())
        self.assertEqual(manifest["effect_name"],"Show B")
        self.assertEqual(manifest["slices"][0]["x"],383.5)

    def test_build_collision_never_overwrites_existing_artifacts(self):
        layout = read_layout(self.source,["SQ-1"])
        result = build_effect(layout,"Show A",self.root/"bundles")
        before = {p.name:p.read_bytes() for p in result["bundle"].parent.iterdir()}
        for name in ("Show A", "show a"):
            with self.assertRaises(FileExistsError):
                build_effect(layout,name,self.root/"bundles")
        self.assertEqual(before,{p.name:p.read_bytes() for p in result["bundle"].parent.iterdir()})

    def test_installed_name_collision_is_case_insensitive(self):
        installed = self.root/"templates"
        installed.mkdir()
        (installed/"Show A.drfx").write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            ensure_new_effect_name("show a",[installed])
        ensure_new_effect_name("Show B",[installed])

    def test_unsafe_effect_names_are_rejected(self):
        for name in ("", "../Other", "A/B", "A\\B", "CON", "A\x00B", "A."):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_effect_name(name)
        self.assertEqual(validate_effect_name(" Show A "),"Show A")
        self.assertEqual(suggested_effect_name("Show: A/B"),"Slice Mapper - Show- A-B")

    def test_empty_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"at least one"):
            select_layout(inspect_layout(self.source),[])

    def test_invalid_xml_entities_are_rejected(self):
        self.source.write_text('<!DOCTYPE foo [<!ENTITY x "a">]><XmlState/>')
        with self.assertRaisesRegex(ValueError,"entities"):
            inspect_layout(self.source)

    def test_bundled_script_imports_without_local_modules_or_running_ui(self):
        entry = package_importer()
        path = entry.parent/"slice_mapper_backend.py"
        spec = importlib.util.spec_from_file_location("bundled_slice_importer",path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.service_main))
        self.assertTrue(callable(module.build_effect))
        self.assertNotIn("from build_slice_mapper import",path.read_text())
        self.assertIn("local PYTHON_BACKEND = [========[",entry.read_text())

    def test_file_change_since_preview_requires_review(self):
        catalog = inspect_layout(self.source)
        self.source.write_text(xml().replace('383.5','383.75').replace('895.5','895.75'))
        with self.assertRaisesRegex(ValueError,"changed on disk"):
            execute_request(dict(action="build",path=str(self.source),name="Show A",names=["SQ-1"],
                                 expected_sha256=catalog["source_sha256"]),self.root/"output",[])

    def test_service_builds_only_selected_screens_and_serializes_data(self):
        catalog = execute_request(dict(action="inspect",path=str(self.source)))
        result = execute_request(dict(action="build",path=str(self.source),name="Show A",names=["SQ-1"],
                                      expected_sha256=catalog["catalog"]["source_sha256"]),self.root/"output",[])
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"],1)
        self.assertTrue(Path(result["bundle"]).exists())
        encoded = lua_data({"value":'quote" \\ newline\n Unicode é',"ok":True,"names":["SQ-1"]})
        self.assertIn('\\010',encoded)
        self.assertNotIn('return ',encoded)


if __name__ == "__main__":
    unittest.main()
