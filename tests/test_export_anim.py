# SPDX-License-Identifier: GPL-3.0-or-later
import json
import math
import os
import unittest

import bpy
from mathutils import Quaternion

from tests import fixtures
from tests.test_export_skeletal import _rigged_character


def _key_bone(arm, bone, frame, rotation=None, location=None):
    pb = arm.pose.bones[bone]
    pb.rotation_mode = "QUATERNION"
    if rotation is not None:
        pb.rotation_quaternion = rotation
        pb.keyframe_insert("rotation_quaternion", frame=frame)
    if location is not None:
        pb.location = location
        pb.keyframe_insert("location", frame=frame)


def _animated_character(frames=24):
    rig, body = _rigged_character("Hero", shape_key=False)
    _key_bone(rig, "bone_01", 1, rotation=Quaternion((1, 0, 0, 0)), location=(0, 0, 0))
    _key_bone(rig, "bone_01", frames, rotation=Quaternion((1, 0, 0), math.radians(90)), location=(0, 0, 0))
    _key_bone(rig, "root", 1, location=(0, 0, 0))
    _key_bone(rig, "root", frames, location=(0, 50, 0))
    rig.animation_data.action.name = "Run"
    return rig, body


class AnimExportTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.I = fixtures.addon_module("fbxpatch.inspect")
        self.classify = fixtures.addon_module("core.classify")
        self.export_fbx = fixtures.addon_module("core.export_fbx")
        self.prefs = fixtures.addon_module("prefs").get_prefs()
        self.Report = fixtures.addon_module("core.log").Report

    def _export_anim(self, rig, source="ACTIVE_ACTION", **overrides):
        settings = fixtures.export_settings(self.tmp, **overrides)
        groups = self.classify.build_anim_groups(
            [rig], self.prefs, bpy.context.scene, source=source, destination=settings.destination_path
        )
        report = self.Report()
        done, manifest_path = self.export_fbx.export_groups(bpy.context, groups, settings, report)
        return done, manifest_path, report

    def test_active_action_export(self):
        rig, body = _animated_character()
        bpy.context.scene.frame_set(24)
        bpy.context.view_layer.update()
        end_pose = rig.pose.bones["bone_01"].matrix.copy()
        done, manifest_path, report = self._export_anim(rig)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual(os.path.basename(done[0].path), "A_Hero_Run.fbx")
        root, _ = fixtures.fbx_load(done[0].path)
        stacks = self.I.anim_stacks(root)
        self.assertEqual([self.I.object_name(s) for s in stacks], [b"A_Hero_Run"])
        self.assertNotIn("Armature", fixtures.fbx_models(root))
        self.I.find_objects(root, b"AnimationCurveNode")
        limbs = fixtures.fbx_models(root, b"LimbNode")
        for name, model in limbs.items():
            mid = self.I.object_id(model)
            props = {c[3] for c in self.I.children_of(root, mid, kind=b"OP")}
            self.assertEqual(props, {b"Lcl Translation", b"Lcl Rotation", b"Lcl Scaling"}, name)
        curves = self.I.find_objects(root, b"AnimationCurve")
        self.assertTrue(curves)
        for c in curves:
            self.assertEqual(len(c.find(b"KeyTime").props[0]), 24)
        with open(manifest_path) as fh:
            item = json.load(fh)["items"][0]
        self.assertEqual(item["kind"], "ANIMATION")
        self.assertEqual((item["frame_start"], item["frame_end"]), (1, 24))
        self.assertEqual(item["fps"], 24)
        # user rig untouched and nothing left behind
        self.assertEqual(rig.animation_data.action.name, "Run")
        ok, leftovers = fixtures.no_temp_leftovers()
        self.assertTrue(ok, leftovers)
        self.assertEqual(len(bpy.data.actions), 1)
        # round trip: import the animation back and compare the final pose
        fixtures.fresh_scene(cm=True)
        import_fbx = fixtures.addon_module("core.import_fbx")
        res = import_fbx.import_file(bpy.context, done[0].path, self.prefs, self.Report())
        arm = res.armatures[0]
        self.assertEqual([a.name for a in res.actions], ["A_Hero_Run"])
        bpy.context.scene.frame_set(24)
        bpy.context.view_layer.update()
        got = arm.pose.bones["bone_01"].matrix
        for i in range(4):
            for j in range(4):
                self.assertAlmostEqual(got[i][j], end_pose[i][j], places=3, msg=f"[{i}][{j}]")
        fixtures.assert_vec_close(
            self, arm.pose.bones["root"].matrix.translation, (0, 0, 50), places=3
        )  # bone-local Y = world Z

    def test_all_actions_and_constraints_baked(self):
        rig, body = _animated_character()
        walk = bpy.data.actions.new("Walk")
        # second action keyed through a temporary assignment
        rig.animation_data.action = walk
        _key_bone(rig, "bone_01", 1, rotation=Quaternion((1, 0, 0, 0)))
        _key_bone(rig, "bone_01", 10, rotation=Quaternion((0, 1, 0), math.radians(45)))
        rig.animation_data.action = bpy.data.actions["Run"]
        # a constraint that the exporter would otherwise ignore: bone_02 copies bone_01's rotation
        con = rig.pose.bones["bone_02"].constraints.new("COPY_ROTATION")
        con.target = rig
        con.subtarget = "bone_01"
        con.owner_space = "LOCAL"
        con.target_space = "LOCAL"
        done, _, report = self._export_anim(rig, source="ALL_ACTIONS")
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual({os.path.basename(d.path) for d in done}, {"A_Hero_Run.fbx", "A_Hero_Walk.fbx"})
        self.assertEqual(rig.animation_data.action.name, "Run")
        fixtures.fresh_scene(cm=True)
        import_fbx = fixtures.addon_module("core.import_fbx")
        res = import_fbx.import_file(
            bpy.context, [d for d in done if d.path.endswith("Run.fbx")][0].path, self.prefs, self.Report()
        )
        arm = res.armatures[0]
        bpy.context.scene.frame_set(24)
        bpy.context.view_layer.update()
        q1 = arm.pose.bones["bone_01"].matrix_basis.to_quaternion()
        q2 = arm.pose.bones["bone_02"].matrix_basis.to_quaternion()
        self.assertGreater(abs(q1.dot(q2)), 0.999)  # constraint result baked into bone_02


if __name__ == "__main__":
    unittest.main()
