# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
from unittest import mock

import bpy

from tests import fixtures
from tests.test_export_skeletal import _rigged_character


def add_bone(rig, name, parent=None, head=(0, 0, 0), tail=(0, 10, 0), deform=True):
    compat = fixtures.addon_module("compat")
    bpy.context.view_layer.objects.active = rig
    with bpy.context.temp_override(active_object=rig, object=rig, selected_objects=[rig], **compat.window_override()):
        bpy.ops.object.mode_set(mode="EDIT")
        bone = rig.data.edit_bones.new(name)
        bone.head, bone.tail = head, tail
        bone.use_deform = deform
        if parent:
            bone.parent = rig.data.edit_bones[parent]
        bpy.ops.object.mode_set(mode="OBJECT")


def key_bone(rig, bone="bone_01"):
    pose_bone = rig.pose.bones[bone]
    pose_bone.rotation_mode = "QUATERNION"
    pose_bone.keyframe_insert("rotation_quaternion", frame=1)
    pose_bone.keyframe_insert("rotation_quaternion", frame=10)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=True)
        self.classify = fixtures.addon_module("core.classify")
        self.validate = fixtures.addon_module("core.validate")
        self.prefs = fixtures.addon_module("prefs").get_prefs()

    def issues(self, objects, mode="AUTO"):
        groups = self.classify.build_groups(objects, self.prefs, self.scene, mode=mode)
        return self.validate.run(bpy.context, groups, self.prefs)

    def anim_issues(self, rig, source="ACTIVE_ACTION"):
        groups = self.classify.build_anim_groups([rig], self.prefs, self.scene, source=source)
        return self.validate.run(bpy.context, groups, self.prefs)

    def codes(self, objects, mode="AUTO"):
        return {issue.code for issue in self.issues(objects, mode)}

    def anim_codes(self, rig, source="ACTIVE_ACTION"):
        return {issue.code for issue in self.anim_issues(rig, source)}

    def test_clean_asset_has_no_issues(self):
        self.assertEqual(self.issues([fixtures.make_cube("Crate")]), [])

    def test_e001_several_roots(self):
        rig, _body = _rigged_character("Hero")
        add_bone(rig, "second_root")
        issue = next(i for i in self.issues([rig]) if i.code == "E001")
        self.assertEqual((issue.severity, issue.fix_id), ("ERROR", "add_root_bone"))

    def test_e001_no_exportable_bones(self):
        rig = fixtures.make_armature("Hero")
        for bone in rig.data.bones:
            bone.use_deform = False
        self.assertIn("E001", self.codes([rig]))

    def test_e002_armature_object_motion_blocks_animation_export(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        rig.keyframe_insert("location", frame=1)
        self.assertIn("E002", self.anim_codes(rig))

    def test_w002_same_rig_only_warns_for_mesh_export(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        rig.keyframe_insert("location", frame=1)
        codes = self.codes([rig])
        self.assertIn("W002", codes)
        self.assertNotIn("E002", codes)

    def test_e002_animated_parent(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        parent = fixtures.make_empty("Mover")
        parent.keyframe_insert("location", frame=1)
        rig.parent = parent
        self.assertIn("E002", self.anim_codes(rig))

    def test_e002_constraint_on_the_armature_object(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        constraint = rig.constraints.new("COPY_LOCATION")
        self.assertIn("E002", self.anim_codes(rig))
        constraint.mute = True
        self.assertNotIn("E002", self.anim_codes(rig))

    def test_e002_driver_on_the_armature_object(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        rig.driver_add("location", 0)
        self.assertIn("E002", self.anim_codes(rig))

    def test_no_e002_for_bone_keys(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        self.assertNotIn("E002", self.anim_codes(rig))

    def test_no_e002_for_nla_strips_of_bone_keys(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        action = rig.animation_data.action
        rig.animation_data.nla_tracks.new().strips.new("Walk", 1, action)
        rig.animation_data.action = None
        self.assertNotIn("E002", self.anim_codes(rig, "NLA_STRIPS"))
        self.assertEqual({"W002", "E002"} & self.codes([rig]), set())

    def test_no_e002_for_custom_properties(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        rig["mood"] = 1.0
        rig.keyframe_insert('["mood"]', frame=1)
        self.assertNotIn("E002", self.anim_codes(rig))

    def test_muted_strip_does_not_count(self):
        rig, _body = _rigged_character("Hero")
        rig.keyframe_insert("location", frame=1)
        action = rig.animation_data.action
        track = rig.animation_data.nla_tracks.new()
        track.strips.new("Move", 1, action)
        rig.animation_data.action = None
        self.assertIn("W002", self.codes([rig]))
        track.mute = True
        self.assertNotIn("W002", self.codes([rig]))

    def test_e003_mirrored_mesh(self):
        cube = fixtures.make_cube("Crate")
        cube.scale = (-1, 1, 1)
        bpy.context.view_layer.update()
        issue = next(i for i in self.issues([cube]) if i.code == "E003")
        self.assertEqual(issue.fix_id, "apply_scale")

    def test_e003_mirrored_satellites(self):
        cube = fixtures.make_cube("Crate")
        for name in ("UCX_Crate_01", "Crate_LOD1"):
            fixtures.make_cube(name).scale = (-1, 1, 1)
        bpy.context.view_layer.update()
        self.assertEqual(sum(i.code == "E003" for i in self.issues([cube])), 2)

    def test_e003_mirrored_armature(self):
        rig, _body = _rigged_character("Hero")
        rig.scale = (-1, -1, -1)
        bpy.context.view_layer.update()
        self.assertIn("E003", self.codes([rig]))

    def test_w003_non_uniform_scale(self):
        cube = fixtures.make_cube("Crate")
        cube.scale = (1, 2, 3)
        bpy.context.view_layer.update()
        self.assertIn("W003", self.codes([cube]))

    def test_e004_mesh_parented_to_a_bone(self):
        rig = fixtures.make_armature("Hero")
        prop = fixtures.make_cube("Sword")
        prop.parent = rig
        prop.parent_type = "BONE"
        prop.parent_bone = "bone_01"
        issue = next(i for i in self.issues([rig]) if i.code == "E004")
        self.assertEqual((issue.fix_id, issue.bone_name), ("rigid_bind", "bone_01"))

    def test_w005_no_uv_map(self):
        self.assertIn("W005", self.codes([fixtures.make_cube("Crate", uv=False)]))

    def test_i006_ngons(self):
        me = bpy.data.meshes.new("Ngon")
        me.from_pydata([(0, 0, 0), (1, 0, 0), (2, 1, 0), (1, 2, 0), (0, 2, 0)], [], [(0, 1, 2, 3, 4)])
        self.assertIn("I006", self.codes([fixtures.link(bpy.data.objects.new("Ngon", me))]))

    def test_w007_no_material_or_empty_slot(self):
        self.assertIn("W007", self.codes([fixtures.make_cube("Bare", material=False)]))
        empty_slot = fixtures.make_cube("Slot", material=False)
        empty_slot.data.materials.append(None)
        self.assertIn("W007", self.codes([empty_slot]))

    def test_no_w007_for_object_linked_material(self):
        cube = fixtures.make_cube("Crate", material=False)
        cube.data.materials.append(None)
        cube.material_slots[0].link = "OBJECT"
        cube.material_slots[0].material = bpy.data.materials.new("OnObject")
        self.assertNotIn("W007", self.codes([cube]))

    def test_e008_duplicate_asset_names_list_the_objects(self):
        first = fixtures.make_cube("Crate")
        second = fixtures.make_cube("SM_Crate")
        issue = next(i for i in self.issues([first, second]) if i.code == "E008")
        self.assertIn("Crate", issue.message)
        self.assertIn("SM_Crate", issue.message)

    def test_non_latin_names_are_not_duplicates(self):
        self.assertNotIn("E008", self.codes([fixtures.make_cube("木箱"), fixtures.make_cube("鉄箱")]))

    def test_e010_bone_names_unreal_would_rewrite(self):
        rig, _body = _rigged_character("Hero")
        rig.data.bones["bone_01"].name = "spine.001"
        issue = next(i for i in self.issues([rig]) if i.code == "E010")
        self.assertEqual(issue.fix_id, "rename_bones_ue_safe")

    def test_i009_too_many_influences(self):
        rig, body = _rigged_character("Hero", bones=10)
        for group in body.vertex_groups:
            group.add([0], 0.1, "REPLACE")
        self.assertIn("I009", self.codes([rig]))

    def test_i011_renamed_on_export(self):
        self.assertIn("I011", self.codes([fixtures.make_cube("Crate.001")]))

    def test_w012_shape_keys_with_topology_modifier(self):
        cube = fixtures.make_cube("Face")
        cube.shape_key_add(name="Basis")
        cube.shape_key_add(name="Smile")
        cube.modifiers.new("Sub", "SUBSURF")
        self.assertIn("W012", self.codes([cube]))

    def test_e013_armature_with_non_uniform_scale(self):
        rig, _body = _rigged_character("Hero")
        rig.scale = (1, 2, 1)
        bpy.context.view_layer.update()
        issue = next(i for i in self.issues([rig]) if i.code == "E013")
        self.assertEqual(issue.fix_id, "apply_scale")

    def test_w014_weights_on_bones_that_are_not_exported(self):
        rig, _body = _rigged_character("Hero")
        rig.data.bones["bone_02"].morenn.export = "EXCLUDE"
        rig.data.bones["bone_02"].use_deform = False
        self.assertIn("W014", self.codes([rig]))

    def test_w015_second_armature_modifier(self):
        rig, body = _rigged_character("Hero")
        other = fixtures.make_armature("Other")
        body.modifiers.new("Second", "ARMATURE").object = other
        self.assertIn("W015", self.codes([rig]))

    def test_w016_scene_not_in_centimetres(self):
        fixtures.fresh_scene(cm=False)
        issue = next(i for i in self.issues([fixtures.make_cube("Crate")]) if i.code == "W016")
        self.assertEqual(issue.fix_id, "scene_setup")

    def test_w017_armature_without_meshes(self):
        self.assertIn("W017", self.codes([fixtures.make_armature("Hero")]))

    def test_w018_several_colour_attributes(self):
        cube = fixtures.make_cube("Crate")
        cube.data.color_attributes.new("Second", "BYTE_COLOR", "CORNER")
        self.assertIn("W018", self.codes([cube]))

    def test_e019_no_faces(self):
        obj = fixtures.link(bpy.data.objects.new("Nothing", bpy.data.meshes.new("Nothing")))
        self.assertIn("E019", self.codes([obj]))

    def test_e019_faces_removed_by_modifiers(self):
        cube = fixtures.make_cube("Gone")
        fixtures.add_empty_geometry_modifier(cube)
        self.assertIn("E019", self.codes([cube]))

    def test_no_e019_when_nodes_generate_instances(self):
        cube = fixtures.make_cube("Host")
        fixtures.add_instance_modifier(cube)
        self.assertNotIn("E019", self.codes([cube]))

    def test_w021_unweighted_vertices(self):
        rig, body = _rigged_character("Hero")
        for group in body.vertex_groups:
            group.remove([0])
        self.assertIn("W021", self.codes([rig]))

    def test_e022_empty_frame_range(self):
        rig, _body = _rigged_character("Hero")
        key_bone(rig)
        groups = self.classify.build_anim_groups([rig], self.prefs, self.scene)
        groups[0].frame_range = (10, 1)
        self.assertIn("E022", {i.code for i in self.validate.run(bpy.context, groups, self.prefs)})

    def test_w025_non_mesh_objects_in_a_collection_instance(self):
        instance = fixtures.make_collection_instance("Kit")
        curve = fixtures.make_curve("Pipe")
        bpy.context.scene.collection.objects.unlink(curve)
        instance.instance_collection.objects.link(curve)
        self.assertIn("W025", self.codes([instance]))

    def test_curve_only_gets_transform_checks(self):
        curve = fixtures.make_curve("Pipe")
        curve.scale = (-1, 1, 1)
        bpy.context.view_layer.update()
        self.assertEqual(self.codes([curve]), {"E003"})


class ExportTimeCodeTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()

    def test_e012_modifier_changes_the_vertex_count_between_shape_keys(self):
        cube = fixtures.make_cube("Face", size=2.0)
        cube.shape_key_add(name="Basis")
        far = cube.shape_key_add(name="Far")
        for point in far.data:
            point.co = point.co * 100.0
        cube.modifiers.new("Weld", "WELD").merge_threshold = 10.0
        done, _manifest, report = fixtures.run_export([cube], self.tmp)
        self.assertEqual(done, [])
        self.assertIn("E012", fixtures.report_text(report))
        self.assertTrue(fixtures.no_temp_leftovers()[0])

    def test_shape_keys_are_read_as_stored_without_other_modifiers(self):
        """Fast path (no modifier besides Armature) and evaluated path (one key at a time) give the same keys."""
        staging = fixtures.addon_module("core.staging")
        cube = fixtures.make_cube("Face", size=2.0)
        cube.shape_key_add(name="Basis")
        smile = cube.shape_key_add(name="Smile")
        for point in smile.data:
            point.co.z += 0.5
        with staging.Staging(bpy.context, fixtures.addon_module("core.log").Report()) as st:
            _mesh, direct = st.evaluate_source_mesh(cube)
            cube.modifiers.new("Nothing", "DISPLACE").strength = 0.0  # moves nothing, forces one evaluation per key
            _mesh, evaluated = st.evaluate_source_mesh(cube)
        self.assertEqual([s["name"] for s in direct], ["Basis", "Smile"])
        self.assertEqual(len(direct[1]["co"]), len(evaluated[1]["co"]))
        for a, b in zip(direct[1]["co"], evaluated[1]["co"]):
            self.assertAlmostEqual(float(a), float(b), places=5)
        self.assertTrue(fixtures.no_temp_leftovers()[0])

    def test_e020_combined_file_with_the_legacy_importer(self):
        cubes = [fixtures.make_cube("A"), fixtures.make_cube("B")]
        done, _manifest, report = fixtures.run_export(cubes, self.tmp, file_mode="COMBINED", ue_importer="LEGACY")
        self.assertEqual(done, [])
        self.assertIn("E020", fixtures.report_text(report))

    def test_e025_collision_mesh_in_the_same_file_with_the_legacy_importer(self):
        done, _manifest, report = fixtures.run_export(
            [fixtures.make_cube("A")],
            self.tmp,
            generate_collision_mesh=True,
            collision_mesh_file="SAME",
            ue_importer="LEGACY",
        )
        self.assertEqual(done, [])
        self.assertIn("E025", fixtures.report_text(report))

    def test_e008_caught_before_writing(self):
        first = fixtures.make_cube("Crate")
        first.morenn.export_name = "Same"
        second = fixtures.make_cube("Other")
        second.morenn.export_name = "Same"
        done, _manifest, report = fixtures.run_export([first, second], self.tmp)
        self.assertEqual(done, [])
        self.assertIn("E008", fixtures.report_text(report))


class FixerTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=True)
        self.classify = fixtures.addon_module("core.classify")
        self.validate = fixtures.addon_module("core.validate")
        self.prefs = fixtures.addon_module("prefs").get_prefs()

    def issues(self, objects):
        groups = self.classify.build_groups(objects, self.prefs, self.scene)
        return self.validate.run(bpy.context, groups, self.prefs)

    def fix(self, objects, code):
        issue = next(i for i in self.issues(objects) if i.code == code)
        return self.validate.apply_fix(bpy.context, issue)

    def codes(self, objects):
        bpy.context.view_layer.update()
        return {i.code for i in self.issues(objects)}

    def test_apply_scale(self):
        cube = fixtures.make_cube("Crate")
        cube.scale = (-2, 2, 2)
        bpy.context.view_layer.update()
        changed, _message = self.fix([cube], "E003")
        self.assertTrue(changed)
        fixtures.assert_vec_close(self, cube.scale, (1, 1, 1))
        self.assertNotIn("E003", self.codes([cube]))

    def test_apply_scale_on_shared_mesh_data(self):
        cube = fixtures.make_cube("Crate")
        other = fixtures.link(bpy.data.objects.new("Other", cube.data))
        cube.scale = (-1, 1, 1)
        bpy.context.view_layer.update()
        changed, message = self.fix([cube], "E003")
        self.assertTrue(changed, message)
        self.assertNotEqual(cube.data, other.data)
        fixtures.assert_vec_close(self, other.scale, (1, 1, 1))

    def test_assign_default_material_fills_empty_slots(self):
        cube = fixtures.make_cube("Crate", material=False)
        cube.data.materials.append(None)
        self.assertTrue(self.fix([cube], "W007")[0])
        self.assertEqual(cube.data.materials[0].name, "M_Default")

    def test_rename_bones_like_unreal(self):
        rig, body = _rigged_character("Hero")
        rig.data.bones["bone_01"].name = "spine.001"
        self.assertEqual(body.vertex_groups[1].name, "spine.001")
        self.assertTrue(self.fix([rig], "E010")[0])
        self.assertIn("spine_001", rig.data.bones)
        self.assertEqual(body.vertex_groups[1].name, "spine_001")
        rig.data.bones["bone_02"].name = "mixamorig:Head"  # Unreal drops FBX namespaces
        self.assertTrue(self.fix([rig], "E010")[0])
        self.assertIn("Head", rig.data.bones)

    def test_rename_bones_avoids_collisions(self):
        rig, _body = _rigged_character("Hero")
        rig.data.bones["bone_01"].name = "a.b"
        rig.data.bones["bone_02"].name = "a_b"
        self.assertTrue(self.fix([rig], "E010")[0])
        self.assertEqual(len({b.name for b in rig.data.bones}), 3)
        self.assertNotIn("E010", self.codes([rig]))

    def test_add_root_bone(self):
        rig, _body = _rigged_character("Hero")
        add_bone(rig, "second_root")
        self.assertTrue(self.fix([rig], "E001")[0])
        roots = [b.name for b in rig.data.bones if b.parent is None]
        self.assertEqual(len(roots), 1)
        self.assertNotIn("E001", self.codes([rig]))

    def test_add_root_bone_when_root_is_taken(self):
        rig = fixtures.make_armature("Hero", bones=2)
        add_bone(rig, "other")
        self.assertTrue(self.fix([rig], "E001")[0])
        self.assertEqual([b.name for b in rig.data.bones if b.parent is None], ["root_bone"])

    def test_rigid_bind(self):
        rig = fixtures.make_armature("Hero")
        prop = fixtures.make_cube("Sword", location=(5, 0, 0))
        prop.parent = rig
        prop.parent_type = "BONE"
        prop.parent_bone = "bone_01"
        bpy.context.view_layer.update()
        world = prop.matrix_world.copy()
        self.assertTrue(self.fix([rig], "E004")[0])
        bpy.context.view_layer.update()
        self.assertEqual(prop.parent_type, "OBJECT")
        self.assertEqual([m.type for m in prop.modifiers], ["ARMATURE"])
        self.assertEqual(prop.vertex_groups[0].name, "bone_01")
        fixtures.assert_vec_close(self, prop.matrix_world.translation, world.translation)
        self.assertNotIn("E004", self.codes([rig]))

    def test_scene_setup(self):
        fixtures.fresh_scene(cm=False)
        cube = fixtures.make_cube("Crate")
        self.assertTrue(self.fix([cube], "W016")[0])
        self.assertTrue(fixtures.addon_module("core.units").is_cm_scene(bpy.context.scene))

    def test_fixers_do_nothing_for_missing_objects(self):
        for fix_id, (_label, fix) in self.validate.FIXERS.items():
            if fix_id == "scene_setup":
                continue
            issue = self.validate.Issue("X", "ERROR", "", object_name="Missing", fix_id=fix_id)
            self.assertFalse(fix(bpy.context, issue), fix_id)

    def test_failed_fix_is_reported_not_raised(self):
        def explode(_context, _issue):
            raise RuntimeError("Error: cannot do that\n")

        issue = self.validate.Issue("X", "ERROR", "", object_name="Crate", fix_id="explode")
        with mock.patch.dict(self.validate.FIXERS, {"explode": ("Explode", explode)}):
            changed, message = self.validate.apply_fix(bpy.context, issue)
        self.assertFalse(changed)
        self.assertEqual(message, "Explode failed on 'Crate': Error: cannot do that")

    def test_unknown_fix(self):
        issue = self.validate.Issue("X", "ERROR", "", fix_id="nope")
        self.assertFalse(self.validate.apply_fix(bpy.context, issue)[0])


if __name__ == "__main__":
    unittest.main()
