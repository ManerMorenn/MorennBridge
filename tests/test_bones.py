# SPDX-License-Identifier: GPL-3.0-or-later
import math
import unittest

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

from tests import fixtures
from tests.test_export_skeletal import _rigged_character


class AxisMathTests(unittest.TestCase):
    def setUp(self):
        self.bones = fixtures.addon_module("core.bones")

    def test_correction_matrix_is_a_rotation(self):
        for primary in self.bones.AXES:
            secondary = self.bones.default_secondary(primary)
            matrix = self.bones.correction_matrix(primary, secondary)
            self.assertAlmostEqual(matrix.to_3x3().determinant(), 1.0, places=6)

    def test_correction_and_import_matrices_are_inverse(self):
        for primary, secondary in (("X", "Y"), ("-X", "Y"), ("Z", "X"), ("-Z", "Y")):
            product = self.bones.import_matrix(primary, secondary) @ self.bones.correction_matrix(primary, secondary)
            for i in range(3):
                fixtures.assert_vec_close(self, product[i], Matrix.Identity(4)[i])

    def test_signed_permutation(self):
        matrix = self.bones.correction_matrix("X", "Y").to_3x3()
        perm, sign = self.bones.signed_permutation(matrix)
        self.assertEqual(sorted(perm), [0, 1, 2])
        self.assertTrue(all(abs(s) == 1 for s in sign))

    def test_axes_map_text_round_trip(self):
        default = ("X", "Y")
        axes = {"root": ("X", "Y"), "arm_r": ("-X", "Y"), "pelvis": ("Z", "Y")}
        text = self.bones.serialize_axes_map(axes, default)
        self.assertNotIn("root", text)
        self.assertEqual(self.bones.parse_axes_map(text), {"arm_r": ("-X", "Y"), "pelvis": ("Z", "Y")})
        self.assertEqual(self.bones.parse_axes_map(""), {})


class DetectionTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.bones = fixtures.addon_module("core.bones")

    def test_vote_counts_bones_pointing_at_their_child(self):
        rig = fixtures.make_armature("Rig", bones=4, bone_length=10.0)
        self.assertEqual(self.bones.vote_bones_point_to_children(rig), 1.0)

    def test_vote_without_chains(self):
        rig = fixtures.make_armature("Rig", bones=1)
        self.assertIsNone(self.bones.vote_bones_point_to_children(rig))

    def test_stored_axes_round_trip(self):
        rig = fixtures.make_armature("Rig", bones=3)
        axes = {b.name: ("X", "Y") for b in rig.data.bones}
        axes["bone_01"] = ("-X", "Y")
        self.bones.store_bone_axes(rig, axes, ("X", "Y"))
        self.assertEqual(rig.data.bones["bone_01"].morenn.axis_primary, "-X")
        self.assertEqual(rig.data.bones["root"].morenn.axis_primary, "DEFAULT")
        self.assertEqual(self.bones.axes_map_from_stored(rig, ("X", "Y"))["bone_01"], ("-X", "Y"))
        self.bones.clear_bone_axes(rig)
        self.assertEqual(rig.data.bones["bone_01"].morenn.axis_primary, "DEFAULT")


class BoneLengthTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.bones = fixtures.addon_module("core.bones")
        self.prefs = fixtures.addon_module("prefs").get_prefs()

    def lengths(self, rig, **kwargs):
        self.bones.recompute_bone_lengths(bpy.context, rig, self.prefs, **kwargs)
        return {b.name: round(b.length, 3) for b in rig.data.bones}

    def test_chain_uses_child_distance_and_half_length_for_the_leaf(self):
        rig = fixtures.make_armature("Rig", bones=3, bone_length=10.0)
        self.assertEqual(self.lengths(rig), {"root": 10.0, "bone_01": 10.0, "bone_02": 5.0})

    def test_lone_root_gets_the_preference_length(self):
        rig = fixtures.make_armature("Rig", bones=1, bone_length=33.0)
        self.assertEqual(self.lengths(rig)["root"], round(self.prefs.import_root_bone_length, 3))

    def test_helper_bones_get_the_helper_length(self):
        from tests.test_validate import add_bone

        rig = fixtures.make_armature("Rig", bones=2, bone_length=10.0)
        add_bone(rig, "ik_foot", parent="root", head=(0, 0, 10), tail=(0, 40, 10))
        self.assertEqual(self.lengths(rig)["ik_foot"], round(self.prefs.import_helper_bone_length, 3))

    def test_unweighted_leaf_gets_the_helper_length(self):
        rig = fixtures.make_armature("Rig", bones=3, bone_length=10.0)
        lengths = self.lengths(rig, deform_names={"root", "bone_01"})
        self.assertEqual(lengths["bone_02"], round(self.prefs.import_helper_bone_length, 3))

    def test_connect_option(self):
        rig = fixtures.make_armature("Rig", bones=2, bone_length=10.0)
        bpy.context.view_layer.objects.active = rig
        compat = fixtures.addon_module("compat")
        with bpy.context.temp_override(active_object=rig, object=rig, **compat.window_override()):
            bpy.ops.object.mode_set(mode="EDIT")
            rig.data.edit_bones["bone_01"].use_connect = False
            bpy.ops.object.mode_set(mode="OBJECT")
        self.bones.recompute_bone_lengths(bpy.context, rig, self.prefs, connect=True)
        self.assertTrue(rig.data.bones["bone_01"].use_connect)


class BoneCollectionTests(unittest.TestCase):
    def test_bones_are_sorted_into_collections(self):
        from tests.test_validate import add_bone

        fixtures.fresh_scene(cm=True)
        bones = fixtures.addon_module("core.bones")
        rig = fixtures.make_armature("Rig", bones=2)
        add_bone(rig, "ik_foot", parent="root")
        add_bone(rig, "helper", parent="root", deform=False)
        bones.assign_bone_collections(rig, {"root", "bone_01"})
        groups = {c.name: {b.name for b in c.bones} for c in rig.data.collections}
        self.assertEqual(groups, {"Deform": {"root", "bone_01"}, "IK": {"ik_foot"}, "Helpers": {"helper"}})


class FCurveConversionTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.compat = fixtures.addon_module("compat")

    def child_head(self, rig, frame):
        bpy.context.scene.frame_set(frame)
        bpy.context.view_layer.update()
        return rig.pose.bones["bone_02"].head.copy()

    def convert(self, rig):
        bpy.context.view_layer.objects.active = rig
        self.assertEqual(bpy.ops.morenn.convert_bone_axes(direction="TO_UNREAL"), {"FINISHED"})

    def test_quaternion_keys_keep_the_pose(self):
        rig, _body = _rigged_character("Hero")
        pose_bone = rig.pose.bones["bone_01"]
        pose_bone.rotation_mode = "QUATERNION"
        pose_bone.rotation_quaternion = Quaternion((1, 0, 0), math.radians(40))
        pose_bone.keyframe_insert("rotation_quaternion", frame=10)
        expected = self.child_head(rig, 10)
        self.convert(rig)
        fixtures.assert_vec_close(self, self.child_head(rig, 10), expected, places=3)

    def test_euler_keys_keep_the_pose(self):
        rig, _body = _rigged_character("Hero")
        pose_bone = rig.pose.bones["bone_01"]
        pose_bone.rotation_mode = "ZXY"
        pose_bone.rotation_euler = Euler((math.radians(30), 0, math.radians(50)), "ZXY")
        pose_bone.keyframe_insert("rotation_euler", frame=1)
        pose_bone.rotation_euler = Euler((math.radians(60), math.radians(20), 0), "ZXY")
        pose_bone.keyframe_insert("rotation_euler", frame=10)
        expected = {frame: self.child_head(rig, frame) for frame in (1, 10)}
        self.convert(rig)
        self.assertEqual(rig.pose.bones["bone_01"].rotation_mode, "QUATERNION")
        paths = {fc.data_path for _slot, fc in self.compat.iter_fcurves(rig.animation_data.action)}
        self.assertEqual(paths, {'pose.bones["bone_01"].rotation_quaternion'})
        for frame, head in expected.items():
            fixtures.assert_vec_close(self, self.child_head(rig, frame), head, places=3)

    def test_location_keys_on_different_frames_are_resampled(self):
        rig, _body = _rigged_character("Hero")
        pose_bone = rig.pose.bones["root"]  # connected bones ignore location keys
        pose_bone.location = (1, 2, 3)
        pose_bone.keyframe_insert("location", index=0, frame=1)
        pose_bone.keyframe_insert("location", index=1, frame=5)
        pose_bone.keyframe_insert("location", index=2, frame=10)
        expected = self.child_head(rig, 5)
        self.convert(rig)
        fixtures.assert_vec_close(self, self.child_head(rig, 5), expected, places=3)
        frames = {
            tuple(k.co.x for k in fc.keyframe_points)
            for _slot, fc in self.compat.iter_fcurves(rig.animation_data.action)
        }
        self.assertEqual(frames, {(1.0, 5.0, 10.0)})


class DirectionTests(unittest.TestCase):
    def test_bone_direction_is_in_armature_space(self):
        fixtures.fresh_scene(cm=True)
        bones = fixtures.addon_module("core.bones")
        rig = fixtures.make_armature("Rig", bones=2)
        fixtures.assert_vec_close(self, bones.bone_direction(rig.data.bones["bone_01"]), Vector((0, 0, 1)))


if __name__ == "__main__":
    unittest.main()
