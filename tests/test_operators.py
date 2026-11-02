# SPDX-License-Identifier: GPL-3.0-or-later
"""Every operator through ``bpy.ops``, the way the panels call them."""

import os
import unittest

import bpy
from mathutils import Vector

from tests import fixtures
from tests.test_export_skeletal import _rigged_character
from tests.test_validate import add_bone, key_bone


class OperatorCase(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.scene.morenn.export_dir = self.tmp

    def fbx_files(self):
        return sorted(f for f in os.listdir(self.tmp) if f.endswith(".fbx"))

    def override(self, **kwargs):
        compat = fixtures.addon_module("compat")
        return bpy.context.temp_override(**compat.window_override(), **kwargs)


class ExportOperatorTests(OperatorCase):
    def test_selected_objects(self):
        cube = fixtures.make_cube("Crate")
        fixtures.make_cube("NotSelected")
        fixtures.select_only(cube)
        self.assertEqual(bpy.ops.morenn.export_ue(), {"FINISHED"})
        self.assertEqual(self.fbx_files(), ["SM_Crate.fbx"])
        self.assertTrue(self.scene.morenn.last_manifest.endswith(".morenn.json"))

    def test_active_collection_scope(self):
        fixtures.make_cube("A")
        fixtures.make_cube("B")
        fixtures.select_only()
        self.assertEqual(bpy.ops.morenn.export_ue(scope="COLLECTION"), {"FINISHED"})
        self.assertEqual(self.fbx_files(), ["SM_A.fbx", "SM_B.fbx"])

    def test_collection_scope_skips_hidden_assets(self):
        fixtures.make_cube("Visible")
        fixtures.make_cube("Hidden").hide_set(True)
        fixtures.select_only()
        bpy.ops.morenn.export_ue(scope="COLLECTION")
        self.assertEqual(self.fbx_files(), ["SM_Visible.fbx"])
        self.assertIn("Hidden, not exported: Hidden", fixtures.log_text())

    def test_nothing_selected_is_cancelled_with_a_message(self):
        fixtures.select_only()
        self.assertEqual(fixtures.call(bpy.ops.morenn.export_ue), {"CANCELLED"})
        self.assertIn("Nothing to export", fixtures.log_text())

    def test_curve_without_surface_is_logged(self):
        cube = fixtures.make_cube("Crate")
        path = fixtures.make_curve("Path", bevel=0.0)
        fixtures.select_only(cube, path)
        bpy.ops.morenn.export_ue()
        self.assertEqual(self.fbx_files(), ["SM_Crate.fbx"])
        self.assertIn("No surface to export (add a bevel or extrusion): Path", fixtures.log_text())

    def test_unsupported_object_is_logged(self):
        points = fixtures.link(bpy.data.objects.new("Points", bpy.data.pointclouds.new("Points")))
        fixtures.select_only(fixtures.make_cube("Crate"), points)
        bpy.ops.morenn.export_ue()
        self.assertIn("Cannot be exported as meshes: Points", fixtures.log_text())

    def test_errors_abort_and_export_anyway_goes_through(self):
        cube = fixtures.make_cube("Crate")
        cube.scale = (-1, 1, 1)
        bpy.context.view_layer.update()
        fixtures.select_only(cube)
        self.assertEqual(fixtures.call(bpy.ops.morenn.export_ue), {"CANCELLED"})
        self.assertEqual(self.fbx_files(), [])
        self.assertIn("E003", [issue.code for issue in self.scene.morenn.issues])
        self.assertEqual(bpy.ops.morenn.export_ue(force=True), {"FINISHED"})
        self.assertEqual(self.fbx_files(), ["SM_Crate.fbx"])

    def test_animation_mode_is_remembered_for_export_anyway(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        rig.keyframe_insert("location", frame=1)
        fixtures.select_only(rig)
        self.assertEqual(fixtures.call(bpy.ops.morenn.export_ue, mode="ANIMATION"), {"CANCELLED"})
        self.assertIn("E002", [issue.code for issue in self.scene.morenn.issues])
        self.assertEqual(self.scene.morenn.last_export_mode, "ANIMATION")

    def test_animation_mode(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        rig.animation_data.action.name = "Walk"
        fixtures.select_only(rig)
        self.assertEqual(bpy.ops.morenn.export_ue(mode="ANIMATION"), {"FINISHED"})
        self.assertEqual(self.fbx_files(), ["A_Hero_Walk.fbx"])

    def test_animation_mode_without_action(self):
        rig, _body = _rigged_character("Hero")
        fixtures.select_only(rig)
        self.assertEqual(fixtures.call(bpy.ops.morenn.export_ue, mode="ANIMATION"), {"CANCELLED"})
        self.assertIn("No animation to export", fixtures.log_text())

    def test_static_and_skeletal_modes(self):
        rig, body = _rigged_character("Hero")
        fixtures.select_only(body)
        bpy.ops.morenn.export_ue(mode="STATIC")
        self.assertEqual(self.fbx_files(), ["SM_Hero_Body.fbx"])
        bpy.ops.morenn.export_ue(mode="SKELETAL")
        self.assertIn("SKM_Hero.fbx", self.fbx_files())

    def test_push_after_export_without_an_editor_warns(self):
        cube = fixtures.make_cube("Crate")
        fixtures.select_only(cube)
        self.scene.morenn.push_after_export = True
        self.assertEqual(bpy.ops.morenn.export_ue(), {"FINISHED"})
        self.assertIn("Not pushed to Unreal", fixtures.log_text())

    def test_unsaved_file_exports_to_the_temp_folder(self):
        export_ops = fixtures.addon_module("ops.export_ops")
        report = fixtures.addon_module("core.log").Report()
        path = export_ops.resolve_export_dir("//export/", report)
        self.assertTrue(path.endswith("morennbridge_export"))
        self.assertIn("not saved", fixtures.report_text(report))

    def test_poll_needs_object_mode(self):
        rig = fixtures.make_armature("Hero")
        fixtures.select_only(rig)
        compat = fixtures.addon_module("compat")
        with bpy.context.temp_override(active_object=rig, object=rig, **compat.window_override()):
            bpy.ops.object.mode_set(mode="EDIT")
            self.assertFalse(bpy.ops.morenn.export_ue.poll())
            bpy.ops.object.mode_set(mode="OBJECT")


class ImportOperatorTests(OperatorCase):
    def export_cube(self, name):
        fixtures.select_only(fixtures.make_cube(name))
        bpy.ops.morenn.export_ue()
        return os.path.join(self.tmp, f"SM_{name}.fbx")

    def test_single_file(self):
        path = self.export_cube("Crate")
        fixtures.fresh_scene(cm=True)
        self.assertEqual(bpy.ops.morenn.import_ue(filepath=path), {"FINISHED"})
        self.assertIn("SM_Crate", bpy.data.objects)

    def test_several_files_with_one_failure(self):
        good = self.export_cube("Crate")
        with open(os.path.join(self.tmp, "broken.fbx"), "wb") as fh:
            fh.write(b"not an fbx")
        fixtures.fresh_scene(cm=True)
        files = [{"name": os.path.basename(good)}, {"name": "broken.fbx"}]
        fixtures.call(bpy.ops.morenn.import_ue, directory=self.tmp, files=files)
        self.assertIn("SM_Crate", bpy.data.objects)
        self.assertIn("'broken.fbx' is not an FBX file", fixtures.log_text())

    def test_all_failures_cancel(self):
        path = os.path.join(self.tmp, "broken.fbx")
        with open(path, "wb") as fh:
            fh.write(b"not an fbx")
        self.assertEqual(fixtures.call(bpy.ops.morenn.import_ue, filepath=path), {"CANCELLED"})


class SceneOperatorTests(OperatorCase):
    def test_scene_setup(self):
        scene = fixtures.fresh_scene(cm=False)
        self.assertEqual(bpy.ops.morenn.scene_setup(), {"FINISHED"})
        self.assertTrue(fixtures.addon_module("core.units").is_cm_scene(scene))

    def test_convert_units(self):
        scene = fixtures.fresh_scene(cm=False)
        cube = fixtures.make_cube("Crate", size=2.0)
        self.assertEqual(bpy.ops.morenn.scene_convert_units(), {"FINISHED"})
        self.assertTrue(fixtures.addon_module("core.units").is_cm_scene(scene))
        self.assertAlmostEqual(max(v.co.x for v in cube.data.vertices), 100.0, places=3)


class ValidateOperatorTests(OperatorCase):
    def mirrored_cube(self, name="Crate", material=True):
        cube = fixtures.make_cube(name, material=material)
        cube.scale = (-1, 1, 1)
        return cube

    def test_validate_lists_issues(self):
        cube = self.mirrored_cube()
        bpy.context.view_layer.update()
        fixtures.select_only(cube)
        fixtures.call(bpy.ops.morenn.validate)
        self.assertEqual([i.code for i in self.scene.morenn.issues], ["E003"])

    def test_validate_clean_selection(self):
        fixtures.select_only(fixtures.make_cube("Crate"))
        self.assertEqual(bpy.ops.morenn.validate(), {"FINISHED"})
        self.assertEqual(len(self.scene.morenn.issues), 0)

    def test_fix_issue(self):
        cube = self.mirrored_cube()
        bpy.context.view_layer.update()
        fixtures.select_only(cube)
        fixtures.call(bpy.ops.morenn.validate)
        self.assertEqual(bpy.ops.morenn.fix_issue(index=0), {"FINISHED"})
        fixtures.assert_vec_close(self, cube.scale, (1, 1, 1))
        self.assertEqual(len(self.scene.morenn.issues), 0)

    def test_fix_issue_with_a_bad_index(self):
        self.assertEqual(bpy.ops.morenn.fix_issue(index=5), {"CANCELLED"})

    def test_fix_all_continues_after_a_failure(self):
        mirrored = self.mirrored_cube("Locked")
        bare = fixtures.make_cube("Bare", material=False)
        bare.data.materials.append(None)
        bpy.context.view_layer.update()
        fixtures.select_only(mirrored, bare)
        fixtures.call(bpy.ops.morenn.validate)
        validate = fixtures.addon_module("core.validate")

        def explode(_context, _issue):
            raise RuntimeError("Error: cannot apply\n")

        original = validate.FIXERS["apply_scale"]
        validate.FIXERS["apply_scale"] = ("Apply scale", explode)
        try:
            self.assertEqual(bpy.ops.morenn.fix_all(), {"FINISHED"})
        finally:
            validate.FIXERS["apply_scale"] = original
        self.assertEqual(bare.data.materials[0].name, "M_Default")

    def test_select_offenders(self):
        cube = self.mirrored_cube()
        other = fixtures.make_cube("Other")
        bpy.context.view_layer.update()
        fixtures.select_only(cube)
        fixtures.call(bpy.ops.morenn.validate)
        fixtures.select_only(other)
        self.assertEqual(bpy.ops.morenn.select_offenders(), {"FINISHED"})
        self.assertTrue(cube.select_get())
        self.assertFalse(other.select_get())

    def test_add_root_bone_through_fix_all(self):
        rig, _body = _rigged_character("Hero")
        add_bone(rig, "second_root")
        fixtures.select_only(rig)
        fixtures.call(bpy.ops.morenn.validate)
        bpy.ops.morenn.fix_all()
        self.assertEqual(len([b for b in rig.data.bones if b.parent is None]), 1)


class HelperOperatorTests(OperatorCase):
    def setUp(self):
        super().setUp()
        self.cube = fixtures.make_cube("Crate", size=10.0)
        fixtures.select_only(self.cube)

    def test_add_socket(self):
        self.assertEqual(bpy.ops.morenn.add_socket(name="Lid Top"), {"FINISHED"})
        socket = bpy.data.objects["SOCKET_Lid-Top"]
        self.assertEqual(socket.parent, self.cube)

    def test_add_box_collision_fits_the_bounds(self):
        bpy.ops.morenn.add_collision(kind="UBX")
        box = bpy.data.objects["UBX_Crate_01"]
        self.assertEqual(box.parent, self.cube)
        self.assertEqual(box.morenn.collision_of, self.cube)
        self.assertEqual(box.display_type, "WIRE")
        self.assertAlmostEqual(max(v.co.x for v in box.data.vertices), 5.0, places=4)

    def test_add_box_collision_with_padding(self):
        bpy.ops.morenn.add_collision(kind="UBX", padding=1.0)
        self.assertAlmostEqual(max(v.co.x for v in bpy.data.objects["UBX_Crate_01"].data.vertices), 6.0, places=4)

    def test_add_convex_collision(self):
        bpy.ops.morenn.add_collision(kind="UCX")
        self.assertEqual(len(bpy.data.objects["UCX_Crate_01"].data.vertices), 8)

    def test_add_sphere_and_capsule_collision(self):
        bpy.ops.morenn.add_collision(kind="USP")
        bpy.ops.morenn.add_collision(kind="UCP")
        self.assertIn("USP_Crate_01", bpy.data.objects)
        self.assertIn("UCP_Crate_02", bpy.data.objects)

    def test_added_collision_is_exported(self):
        bpy.ops.morenn.add_collision(kind="UBX")
        fixtures.select_only(self.cube)
        bpy.ops.morenn.export_ue()
        root, _version = fixtures.fbx_load(os.path.join(self.tmp, "SM_Crate.fbx"))
        self.assertIn("UBX_SM_Crate_01", fixtures.fbx_models(root))

    def test_make_lod_chain(self):
        lod_b = fixtures.make_cube("B")
        lod_a = fixtures.make_cube("A")
        with self.override(active_object=self.cube, selected_objects=[self.cube, lod_b, lod_a]):
            self.assertEqual(bpy.ops.morenn.make_lod_chain(), {"FINISHED"})
        self.assertEqual((lod_a.morenn.lod_index, lod_b.morenn.lod_index), (1, 2))
        self.assertEqual(lod_a.morenn.lod_of, self.cube)

    def test_set_role(self):
        with self.override(selected_objects=[self.cube]):
            self.assertEqual(bpy.ops.morenn.set_role(role="COLLISION"), {"FINISHED"})
        self.assertEqual(self.cube.morenn.role, "COLLISION")

    def test_convert_bone_axes_both_ways(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        before = {b.name: b.matrix_local.copy() for b in rig.data.bones}
        directions = {b.name: fixtures.bone_dir(b).copy() for b in rig.data.bones}
        bpy.context.view_layer.objects.active = rig
        self.assertEqual(bpy.ops.morenn.convert_bone_axes(direction="TO_UNREAL"), {"FINISHED"})
        self.assertFalse(rig.data.morenn.axes_converted)
        moved = any((fixtures.bone_dir(b) - directions[b.name]).length > 1e-3 for b in rig.data.bones)
        self.assertTrue(moved)
        self.assertEqual(bpy.ops.morenn.convert_bone_axes(direction="TO_BLENDER"), {"FINISHED"})
        self.assertTrue(rig.data.morenn.axes_converted)
        for bone in rig.data.bones:
            for i in range(4):
                fixtures.assert_vec_close(self, bone.matrix_local[i], before[bone.name][i])

    def test_convert_bone_axes_polls_for_an_armature(self):
        bpy.context.view_layer.objects.active = self.cube
        self.assertFalse(bpy.ops.morenn.convert_bone_axes.poll())


class LogOperatorTests(OperatorCase):
    def fill(self):
        fixtures.addon_module("core.log").add_scene_entry(self.scene, "INFO", "hello", "/tmp/file.fbx")

    def test_clear(self):
        self.fill()
        self.assertEqual(bpy.ops.morenn.log_clear(), {"FINISHED"})
        self.assertEqual(len(self.scene.morenn.log), 0)

    def test_copy(self):
        self.fill()
        self.assertEqual(bpy.ops.morenn.log_copy(), {"FINISHED"})

    def test_open_polls_for_a_path(self):
        self.assertFalse(bpy.ops.morenn.log_open.poll())
        self.fill()
        self.assertTrue(bpy.ops.morenn.log_open.poll())

    def test_log_is_capped(self):
        log = fixtures.addon_module("core.log")
        for i in range(log.MAX_LOG_ENTRIES + 5):
            log.add_scene_entry(self.scene, "INFO", f"line {i}")
        entries = self.scene.morenn.log
        self.assertEqual(len(entries), log.MAX_LOG_ENTRIES)
        self.assertEqual(entries[0].message, "line 5")
        self.assertEqual(self.scene.morenn.log_index, log.MAX_LOG_ENTRIES - 1)


class ErrorTextTests(unittest.TestCase):
    def setUp(self):
        self.describe = fixtures.addon_module("core.log").describe_error

    def test_file_errors(self):
        self.assertEqual(self.describe(PermissionError(13, "Permission denied", "/a")), "permission denied for '/a'")
        self.assertEqual(self.describe(FileNotFoundError(2, "No such file", "/a")), "'/a' does not exist")
        self.assertEqual(self.describe(OSError(28, "No space left on device", "/a")), "No space left on device ('/a')")
        self.assertEqual(self.describe(OSError(28, "No space left on device")), "No space left on device")

    def test_value_errors_are_already_readable(self):
        self.assertEqual(self.describe(ValueError("broken file")), "broken file")

    def test_unexpected_errors_name_their_type(self):
        text = self.describe(KeyError("x"))
        self.assertIn("unexpected KeyError", text)
        self.assertIn("system console", text)


class BoundsHelperTests(unittest.TestCase):
    def test_collision_is_centred_on_the_bounds(self):
        fixtures.fresh_scene(cm=True)
        cube = fixtures.make_cube("Crate", size=10.0)
        for vertex in cube.data.vertices:
            vertex.co += Vector((20, 0, 0))
        cube.data.update()
        bpy.context.view_layer.update()
        fixtures.select_only(cube)
        bpy.ops.morenn.add_collision(kind="UBX")
        xs = [v.co.x for v in bpy.data.objects["UBX_Crate_01"].data.vertices]
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, 20.0, places=4)


if __name__ == "__main__":
    unittest.main()
