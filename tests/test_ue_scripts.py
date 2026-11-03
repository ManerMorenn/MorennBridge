# SPDX-License-Identifier: GPL-3.0-or-later
"""The editor-side scripts (ue/scripts) run against tests/fake_unreal.py instead of the Unreal Editor."""

import importlib.util
import json
import os
import runpy
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import bpy

from tests import fake_unreal as U
from tests import fixtures
from tests.test_export_anim import _animated_character

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ue", "scripts")
SCRIPT_MODULES = ("mb_ue_common", "mb_ue_import", "mb_ue_export")

OPTIONS = {
    "build_nanite": False,
    "generate_lightmap_uvs": False,
    "create_physics_asset": True,
    "import_materials": True,
    "import_textures": False,
    "influence_limit": 8,
    "frame_alignment": "NONE",
    "recompute_tangents": True,
    "save": True,
    "rename_skeleton": True,
    "skeleton_prefix": "SKEL_",
    "physics_prefix": "PHYS_",
    "link_complex_collision": True,
}


class UnrealScriptCase(unittest.TestCase):
    def setUp(self):
        U.reset()
        self.saved_path = list(sys.path)
        self.saved_unreal = sys.modules.get("unreal")
        sys.modules["unreal"] = U
        for name in SCRIPT_MODULES:
            sys.modules.pop(name, None)
        self.tmp = tempfile.mkdtemp(prefix="morenn_ue_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def tearDown(self):
        for name in SCRIPT_MODULES:
            sys.modules.pop(name, None)
        if self.saved_unreal is None:
            sys.modules.pop("unreal", None)
        else:
            sys.modules["unreal"] = self.saved_unreal
        sys.path[:] = self.saved_path

    def load(self, name):
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def item(self, kind, name, assets=None, single=True, destination="/Game/MB", **extra):
        path = os.path.join(self.tmp, f"{name}.fbx")
        with open(path, "wb") as fh:
            fh.write(b"")
        item = {
            "file": f"{name}.fbx",
            "path": path,
            "kind": kind,
            "destination_path": destination,
            "single_asset": single,
            "assets": assets if assets is not None else [{"node": name, "name": name}],
        }
        item.update(extra)
        return item

    def write_manifest(self, items, importer="INTERCHANGE", **options):
        data = {"version": 1, "ue_importer": importer, "options": dict(OPTIONS, **options), "items": items}
        path = os.path.join(self.tmp, "batch.morenn.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def import_manifest(self, items, importer="INTERCHANGE", **options):
        path = self.write_manifest(items, importer, **options)
        report = self.load("mb_ue_import").run(path)
        return report, path

    def assert_no_property_warnings(self):
        problems = [m for m in U.messages("Warning") if "not found" in m or "Property" in m]
        self.assertEqual(problems, [])

    def pipeline(self, index=-1):
        return U.STATE.imports[index]["pipelines"][0]


class InterchangeImportTests(UnrealScriptCase):
    def test_static_mesh_with_explicit_pipeline_settings(self):
        item = self.item("STATIC_MESH", "SM_Crate")
        U.plan_import(item["path"], [("StaticMesh", "SM_Crate"), ("Material", "M_Test")])
        report, manifest = self.import_manifest([item])
        self.assertTrue(report["ok"])
        self.assertEqual(report["engine"], U.ENGINE_VERSION)
        self.assertIn("/Game/MB/SM_Crate", report["items"][0]["imported"])
        with open(manifest + ".result.json", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["items"][0]["file"], "SM_Crate.fbx")
        call = U.STATE.imports[0]
        self.assertEqual(call["content_path"], "/Game/MB")
        params = call["params"]
        self.assertTrue(params.get_editor_property("is_automated"))
        self.assertTrue(params.get_editor_property("replace_existing"))
        self.assertEqual(params.get_editor_property("destination_name"), "SM_Crate")
        p = self.pipeline()
        self.assertTrue(p.get_editor_property("use_source_name_for_asset"))
        common = p.get_editor_property("common_meshes_properties")
        self.assertIs(common.get_editor_property("force_all_mesh_as_type"), U.InterchangeForceMeshType.IFMT_STATIC_MESH)
        self.assertFalse(common.get_editor_property("recompute_normals"))
        self.assertTrue(common.get_editor_property("recompute_tangents"))
        self.assertFalse(common.get_editor_property("convert_statics_in_bone_hierarchy_to_skeletals"))
        mesh = p.get_editor_property("mesh_pipeline")
        self.assertTrue(mesh.get_editor_property("import_static_meshes"))
        self.assertFalse(mesh.get_editor_property("import_skeletal_meshes"))
        self.assertFalse(mesh.get_editor_property("build_nanite"))
        self.assertEqual(mesh.get_editor_property("bone_influence_limit"), 8)
        self.assertIs(
            mesh.get_editor_property("combine_static_meshes_behavior"),
            U.InterchangeCombineStaticMeshesBehavior.DO_NOT_COMBINE,
        )
        self.assertFalse(p.get_editor_property("animation_pipeline").get_editor_property("import_animations"))
        texture = p.get_editor_property("material_pipeline").get_editor_property("texture_pipeline")
        self.assertFalse(texture.get_editor_property("import_textures"))
        self.assertIn("/Game/MB/SM_Crate", U.STATE.saved)
        self.assert_no_property_warnings()

    def test_combined_file_names_assets_after_the_nodes(self):
        assets = [{"node": "SM_A", "name": "SM_A"}, {"node": "SM_B", "name": "SM_B"}]
        item = self.item("STATIC_MESH", "Props", assets=assets, single=False)
        U.plan_import(item["path"], [("StaticMesh", "SM_A"), ("StaticMesh", "SM_B")])
        report, _ = self.import_manifest([item])
        self.assertTrue(report["ok"])
        self.assertFalse(self.pipeline().get_editor_property("use_source_name_for_asset"))
        self.assertEqual(U.STATE.imports[0]["params"].get_editor_property("destination_name"), "")
        self.assertEqual(sorted(report["items"][0]["imported"]), ["/Game/MB/SM_A", "/Game/MB/SM_B"])

    def test_skeleton_and_physics_asset_get_their_prefixes(self):
        item = self.item("SKELETAL_MESH", "SKM_Hero", source_armature="Hero", skeleton=None)
        U.plan_import(
            item["path"],
            [
                ("SkeletalMesh", "SKM_Hero"),
                ("Skeleton", "SKM_Hero_Skeleton"),
                ("PhysicsAsset", "SKM_Hero_PhysicsAsset"),
            ],
        )
        report, _ = self.import_manifest([item])
        result = report["items"][0]
        self.assertTrue(result["success"])
        self.assertEqual(result["skeleton"], "/Game/MB/SKEL_Hero")
        self.assertEqual(result["source_armature"], "Hero")
        self.assertIn(["/Game/MB/SKM_Hero_PhysicsAsset", "/Game/MB/PHYS_Hero"], result["renamed"])
        self.assertTrue(U.EditorAssetLibrary.does_asset_exist("/Game/MB/SKEL_Hero"))
        common = self.pipeline().get_editor_property("common_meshes_properties")
        self.assertIs(
            common.get_editor_property("force_all_mesh_as_type"), U.InterchangeForceMeshType.IFMT_SKELETAL_MESH
        )
        mesh = self.pipeline().get_editor_property("mesh_pipeline")
        self.assertTrue(mesh.get_editor_property("import_skeletal_meshes"))
        self.assertTrue(mesh.get_editor_property("create_physics_asset"))
        self.assert_no_property_warnings()

    def test_existing_names_are_not_overwritten_by_renames(self):
        U.add_asset("Skeleton", "/Game/MB/SKEL_Hero")
        item = self.item("SKELETAL_MESH", "SKM_Hero", source_armature="Hero")
        U.plan_import(item["path"], [("SkeletalMesh", "SKM_Hero"), ("Skeleton", "SKM_Hero_Skeleton")])
        report, _ = self.import_manifest([item])
        self.assertEqual(report["items"][0]["skeleton"], "/Game/MB/SKM_Hero_Skeleton")
        self.assertTrue(any("already exists" in m for m in U.messages("Warning")))

    def test_animation_uses_the_skeleton_imported_in_the_same_batch(self):
        mesh = self.item("SKELETAL_MESH", "SKM_Hero", source_armature="Hero")
        anim = self.item("ANIMATION", "A_Hero_Run", source_armature="Hero", skeleton=None, fps=30)
        U.plan_import(mesh["path"], [("SkeletalMesh", "SKM_Hero"), ("Skeleton", "SKM_Hero_Skeleton")])
        U.plan_import(anim["path"], [("AnimSequence", "A_Hero_Run")])
        report, _ = self.import_manifest([mesh, anim])
        self.assertTrue(report["ok"])
        p = self.pipeline()
        skeletal = p.get_editor_property("common_skeletal_meshes_and_animations_properties")
        self.assertIs(skeletal.get_editor_property("skeleton"), U.load_asset("/Game/MB/SKEL_Hero"))
        self.assertTrue(skeletal.get_editor_property("import_only_animations"))
        animation = p.get_editor_property("animation_pipeline")
        self.assertTrue(animation.get_editor_property("import_animations"))
        self.assertEqual(animation.get_editor_property("custom_bone_animation_sample_rate"), 30)
        self.assertIs(animation.get_editor_property("animation_range"), U.InterchangeAnimationRange.TIMELINE)
        self.assertIs(
            p.get_editor_property("common_meshes_properties").get_editor_property("force_all_mesh_as_type"),
            U.InterchangeForceMeshType.IFMT_NONE,
        )
        self.assert_no_property_warnings()

    def test_generated_collision_mesh_becomes_the_complex_collision(self):
        assets = [
            {"node": "SM_Rock", "name": "SM_Rock", "collision_mesh": "SM_Rock_COLL", "collision_ratio": 0.1},
            {"node": "SM_Rock_COLL", "name": "SM_Rock_COLL", "role": "COLLISION_MESH"},
        ]
        item = self.item("STATIC_MESH", "SM_Rock", assets=assets, single=False)
        U.plan_import(item["path"], [("StaticMesh", "SM_Rock"), ("StaticMesh", "SM_Rock_COLL")])
        report, _ = self.import_manifest([item])
        self.assertEqual(report["items"][0]["complex_collision"], "/Game/MB/SM_Rock_COLL")
        rock = U.load_asset("/Game/MB/SM_Rock")
        self.assertIs(rock.get_editor_property("complex_collision_mesh"), U.load_asset("/Game/MB/SM_Rock_COLL"))

    def test_options_can_skip_renames_links_and_saving(self):
        item = self.item("SKELETAL_MESH", "SKM_Hero")
        U.plan_import(item["path"], [("SkeletalMesh", "SKM_Hero"), ("Skeleton", "SKM_Hero_Skeleton")])
        report, _ = self.import_manifest([item], rename_skeleton=False, save=False, link_complex_collision=False)
        self.assertEqual(report["items"][0]["renamed"], [])
        self.assertEqual(report["items"][0]["skeleton"], "/Game/MB/SKM_Hero_Skeleton")
        self.assertEqual(U.STATE.saved, [])

    def test_failed_import_is_reported(self):
        item = self.item("STATIC_MESH", "SM_Crate")
        report, _ = self.import_manifest([item])
        self.assertFalse(report["ok"])
        self.assertEqual(report["items"][0]["errors"], ["Importer reported failure"])

    def test_exception_inside_an_item_is_reported(self):
        item = self.item("STATIC_MESH", "SM_Crate")
        with mock.patch.object(U.InterchangeManager, "import_asset", side_effect=RuntimeError("boom")):
            report, _ = self.import_manifest([item])
        self.assertFalse(report["ok"])
        self.assertIn("boom", report["items"][0]["errors"][0])
        self.assertTrue(any("SM_Crate.fbx" in m for m in U.messages("Error")))


class LegacyImportTests(UnrealScriptCase):
    def test_static_mesh_task(self):
        item = self.item("STATIC_MESH", "SM_Crate")
        U.plan_import(item["path"], [("StaticMesh", "SM_Crate")])
        report, _ = self.import_manifest([item], importer="LEGACY", generate_lightmap_uvs=True)
        self.assertTrue(report["ok"])
        self.assertEqual(report["items"][0]["imported"], ["/Game/MB/SM_Crate"])
        task = U.STATE.imports[0]["task"]
        self.assertIsInstance(task.get_editor_property("factory"), U.FbxFactory)
        self.assertEqual(task.get_editor_property("destination_name"), "SM_Crate")
        self.assertTrue(task.get_editor_property("automated"))
        ui = task.get_editor_property("options")
        self.assertIs(ui.get_editor_property("mesh_type_to_import"), U.FBXImportType.FBXIT_STATIC_MESH)
        self.assertFalse(ui.get_editor_property("import_as_skeletal"))
        static = ui.get_editor_property("static_mesh_import_data")
        self.assertIs(
            static.get_editor_property("normal_import_method"),
            U.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS,
        )
        self.assertTrue(static.get_editor_property("import_mesh_lods"))
        self.assertTrue(static.get_editor_property("generate_lightmap_u_vs"))
        self.assertFalse(static.get_editor_property("auto_generate_collision"))
        self.assertTrue(static.get_editor_property("convert_scene_unit"))
        self.assertFalse(static.get_editor_property("force_front_x_axis"))
        self.assertIs(static.get_editor_property("vertex_color_import_option"), U.VertexColorImportOption.REPLACE)

    def test_skeletal_mesh_and_animation_tasks(self):
        mesh = self.item("SKELETAL_MESH", "SKM_Hero", source_armature="Hero")
        anim = self.item("ANIMATION", "A_Hero_Run", source_armature="Hero", fps=24)
        U.plan_import(mesh["path"], [("SkeletalMesh", "SKM_Hero"), ("Skeleton", "SKM_Hero_Skeleton")])
        U.plan_import(anim["path"], [("AnimSequence", "A_Hero_Run")])
        report, _ = self.import_manifest([mesh, anim], importer="LEGACY")
        self.assertTrue(report["ok"])
        mesh_ui = U.STATE.imports[0]["task"].get_editor_property("options")
        self.assertTrue(mesh_ui.get_editor_property("import_as_skeletal"))
        skeletal = mesh_ui.get_editor_property("skeletal_mesh_import_data")
        self.assertTrue(skeletal.get_editor_property("import_morph_targets"))
        self.assertTrue(skeletal.get_editor_property("import_mesh_lods"))
        self.assertFalse(skeletal.get_editor_property("import_meshes_in_bone_hierarchy"))
        anim_ui = U.STATE.imports[1]["task"].get_editor_property("options")
        self.assertIs(anim_ui.get_editor_property("mesh_type_to_import"), U.FBXImportType.FBXIT_ANIMATION)
        self.assertIs(anim_ui.get_editor_property("skeleton"), U.load_asset("/Game/MB/SKEL_Hero"))
        data = anim_ui.get_editor_property("anim_sequence_import_data")
        self.assertEqual(data.get_editor_property("custom_sample_rate"), 24)
        self.assertFalse(data.get_editor_property("use_default_sample_rate"))
        self.assertIs(
            data.get_editor_property("animation_length"), U.FBXAnimationLengthImportType.FBXALIT_EXPORTED_TIME
        )
        self.assertFalse(data.get_editor_property("snap_to_closest_frame_boundary"))


class EntryPointTests(UnrealScriptCase):
    def run_script(self, name, *args):
        with mock.patch.object(sys, "argv", [os.path.join(SCRIPTS, name), *args]):
            runpy.run_path(os.path.join(SCRIPTS, name), run_name="__main__")

    def test_import_script_writes_the_result_next_to_the_manifest(self):
        item = self.item("STATIC_MESH", "SM_Crate")
        U.plan_import(item["path"], [("StaticMesh", "SM_Crate")])
        manifest = self.write_manifest([item])
        self.run_script("mb_ue_import.py", manifest)
        with open(manifest + ".result.json", encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["ok"])

    def test_scripts_need_their_arguments(self):
        self.run_script("mb_ue_import.py")
        self.run_script("mb_ue_export.py", "")
        errors = U.messages("Error")
        self.assertTrue(any("usage: mb_ue_import.py" in m for m in errors))
        self.assertTrue(any("usage: mb_ue_export.py" in m for m in errors))

    def test_export_script_exports_the_selection(self):
        U.STATE.selected = [U.add_asset("StaticMesh", "/Game/MB/SM_Crate")]
        out = os.path.join(self.tmp, "pull")
        self.run_script("mb_ue_export.py", out)
        with open(os.path.join(out, "pull.result.json"), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["items"][0]["kind"], "STATIC_MESH")


class ExportScriptTests(UnrealScriptCase):
    def test_selected_assets_are_exported_with_fixed_options(self):
        skeleton = U.add_asset("Skeleton", "/Game/MB/SKEL_Hero")
        mesh = U.add_asset("SkeletalMesh", "/Game/MB/SKM_Hero")
        mesh._values["skeleton"] = skeleton
        anim = U.add_asset("AnimSequence", "/Game/MB/A_Hero_Run")
        anim._values["skeleton"] = skeleton
        U.STATE.selected = [
            U.add_asset("StaticMesh", "/Game/MB/SM_Crate"),
            mesh,
            anim,
            U.add_asset("Material", "/Game/MB/M_A"),
        ]
        report = self.load("mb_ue_export").run(self.tmp, [])
        kinds = [i.get("kind") for i in report["items"]]
        self.assertEqual(kinds, ["STATIC_MESH", "SKELETAL_MESH", "ANIMATION", None])
        self.assertFalse(report["ok"])
        self.assertEqual(report["items"][1]["skeleton"], "/Game/MB/SKEL_Hero")
        self.assertEqual(report["items"][2]["skeleton"], "/Game/MB/SKEL_Hero")
        self.assertIn("Unsupported class Material", report["items"][3]["errors"][0])
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "SM_Crate.fbx")))
        task = U.STATE.exports[0]
        self.assertIsInstance(task.get_editor_property("exporter"), U.StaticMeshExporterFBX)
        self.assertFalse(task.get_editor_property("prompt"))
        options = task.get_editor_property("options")
        self.assertIs(options.get_editor_property("fbx_export_compatibility"), U.FbxExportCompatibility.FBX_2013)
        self.assertFalse(options.get_editor_property("force_front_x_axis"))
        self.assertTrue(options.get_editor_property("collision"))
        self.assert_no_property_warnings()

    def test_assets_given_by_path(self):
        U.add_asset("StaticMesh", "/Game/MB/SM_Crate")
        report = self.load("mb_ue_export").run(self.tmp, ["/Game/MB/SM_Crate", "/Game/MB/Missing"])
        self.assertEqual([i["asset_path"] for i in report["items"]], ["/Game/MB/SM_Crate"])
        self.assertTrue(any("Asset not found: /Game/MB/Missing" in m for m in U.messages("Warning")))

    def test_no_selection_api(self):
        with mock.patch.object(U.EditorUtilityLibrary, "get_selected_assets", side_effect=RuntimeError("no UI")):
            report = self.load("mb_ue_export").run(self.tmp, [])
        self.assertEqual(report["items"], [])


class CommonHelperTests(UnrealScriptCase):
    def test_setp_tries_alternatives_and_warns_when_nothing_fits(self):
        C = self.load("mb_ue_common")
        options = U.FbxExportOption()
        self.assertTrue(C.setp(options, "no_such_name", True, alternatives=("ascii",)))
        self.assertTrue(options.get_editor_property("ascii"))
        self.assertFalse(C.setp(options, "no_such_name", True))
        self.assertTrue(any("no_such_name" in m for m in U.messages("Warning")))
        self.assertFalse(C.setp(options, "other_missing", True, optional=True))
        self.assertIsNone(C.getp(options, "other_missing"))

    def test_result_file_problems_are_warnings(self):
        C = self.load("mb_ue_common")
        C.set_result({"ok": True}, os.path.join(self.tmp, "missing", "dir", "r.json"))
        self.assertEqual(json.loads(C.last_result_json()), {"ok": True})
        self.assertTrue(any("Could not write result file" in m for m in U.messages("Warning")))

    def test_save_and_list_failures_are_survivable(self):
        C = self.load("mb_ue_common")
        asset = U.add_asset("StaticMesh", "/Game/MB/SM_Crate")
        with mock.patch.object(U.EditorAssetLibrary, "save_loaded_asset", side_effect=RuntimeError("locked")):
            C.save_asset(asset)
        self.assertTrue(any("Could not save SM_Crate" in m for m in U.messages("Warning")))
        with mock.patch.object(U.EditorAssetLibrary, "list_assets", side_effect=RuntimeError("bad path")):
            self.assertEqual(C.list_assets("/Game/MB"), [])
        with mock.patch.object(U.EditorAssetLibrary, "rename_asset", return_value=False):
            self.assertEqual(C.rename_asset(asset, "SM_Box"), "/Game/MB/SM_Crate")
        self.assertEqual(C.rename_asset(asset, "SM_Crate"), "/Game/MB/SM_Crate")
        with mock.patch.object(U, "load_asset", side_effect=RuntimeError("bad path")):
            self.assertIsNone(C.load("/Game/MB/SM_Crate"))


class ManifestContractTests(UnrealScriptCase):
    """A real export from Blender, imported by the editor script."""

    def setUp(self):
        super().setUp()
        self.scene = fixtures.fresh_scene(cm=True)

    def test_static_skeletal_and_animation_export(self):
        cube = fixtures.make_cube("Rock", size=40.0)
        rig, _body = _animated_character()
        prefs = fixtures.addon_module("prefs").get_prefs()
        classify = fixtures.addon_module("core.classify")
        export_fbx = fixtures.addon_module("core.export_fbx")
        settings = fixtures.export_settings(self.tmp, generate_collision_mesh=True, collision_ratio=0.5)
        groups = classify.build_groups([cube, rig], prefs, self.scene, destination=settings.destination_path)
        groups += classify.build_anim_groups(
            [rig], prefs, self.scene, source="ACTIVE_ACTION", destination=settings.destination_path
        )
        log = fixtures.addon_module("core.log").Report("test")
        _done, manifest_path = export_fbx.export_groups(bpy.context, groups, settings, log)
        self.assertTrue(log.ok, fixtures.report_text(log))
        with open(manifest_path, encoding="utf-8") as fh:
            items = {item["kind"]: item for item in json.load(fh)["items"]}
        destination = items["STATIC_MESH"]["destination_path"]
        U.plan_import(items["STATIC_MESH"]["path"], [("StaticMesh", "SM_Rock"), ("StaticMesh", "SM_Rock_COLL")])
        U.plan_import(items["SKELETAL_MESH"]["path"], [("SkeletalMesh", "SKM_Hero"), ("Skeleton", "SKM_Hero_Skeleton")])
        U.plan_import(items["ANIMATION"]["path"], [("AnimSequence", "A_Hero_Run")])
        result = self.load("mb_ue_import").run(manifest_path)
        self.assertTrue(result["ok"], result)
        by_file = {r["file"]: r for r in result["items"]}
        self.assertEqual(by_file["SM_Rock.fbx"]["complex_collision"], f"{destination}/SM_Rock_COLL")
        self.assertEqual(by_file["A_Hero_Run.fbx"]["source_armature"], rig.name)
        skeletal = self.pipeline().get_editor_property("common_skeletal_meshes_and_animations_properties")
        self.assertIs(skeletal.get_editor_property("skeleton"), U.load_asset(f"{destination}/SKEL_Hero"))
        self.assertEqual(
            self.pipeline()
            .get_editor_property("animation_pipeline")
            .get_editor_property("custom_bone_animation_sample_rate"),
            24,
        )
        self.assert_no_property_warnings()


if __name__ == "__main__":
    unittest.main()
