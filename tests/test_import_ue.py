# SPDX-License-Identifier: GPL-3.0-or-later
import os
import unittest

import bpy

from tests import fixtures
from tests.test_export_skeletal import _rigged_character


def make_ue_style_file(tmp, name="SKM_Hero"):
    """Export a rig with X-along-bone axes, then disguise the file as an Unreal export."""
    rig, body = _rigged_character("Hero")
    done, _, report = fixtures.run_export([rig], tmp)
    assert report.ok, [str(e) for e in report.entries]
    tree = fixtures.addon_module("fbxpatch.tree")
    insp = fixtures.addon_module("fbxpatch.inspect")
    root, version = tree.load(done[0].path)
    # root bone typed 'Root' (model + attribute), like FFbxExporter::CreateSkeleton
    limbs = fixtures.fbx_models(root, b"LimbNode")
    limbs["root"].props[2] = b"Root"
    for kind, child, parent, _p, _e in insp.connections(root):
        if kind == b"OO" and parent == insp.object_id(limbs["root"]):
            attr = insp.object_by_id(root, child)
            if attr is not None and attr.id == b"NodeAttribute":
                attr.props[2] = b"Root"
    # creator header: not Blender
    header = root.find(b"FBXHeaderExtension")
    header.find(b"Creator").props[0] = b"FBX SDK/FBX Plugins version 2020.2"
    top = root.find(b"Creator")
    if top is not None:
        top.props[0] = b"FBX SDK/FBX Plugins version 2020.2"
    # drop our marker so the importer must rely on preferences
    gs = root.find(b"GlobalSettings").find(b"Properties70")
    gs.elems = [p for p in gs.elems if not (p.props and p.props[0].startswith(b"MorennBridge"))]
    out = os.path.join(tmp, f"{name}.fbx")
    tree.save(out, root, version)
    return out, rig, body


class ImportUETests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.import_fbx = fixtures.addon_module("core.import_fbx")
        self.prefs = fixtures.addon_module("prefs").get_prefs()
        self.Report = fixtures.addon_module("core.log").Report

    def test_root_bone_restored_axes_converted_and_tidy(self):
        path, rig, body = make_ue_style_file(self.tmp)
        original = {b.name: (b.head_local.copy(), fixtures.bone_dir(b), b.length) for b in rig.data.bones}
        fixtures.fresh_scene(cm=True)
        report = self.Report()
        res = self.import_fbx.import_file(bpy.context, path, self.prefs, report)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual(len(res.armatures), 1)
        arm = res.armatures[0]
        self.assertEqual(arm.name, "SKM_Hero")
        self.assertEqual({b.name for b in arm.data.bones}, {"root", "bone_01", "bone_02"})
        self.assertIsNone(arm.data.bones["root"].parent)
        self.assertEqual(arm.data.bones["bone_01"].parent.name, "root")
        # bones point to their children again (Blender Y along the chain, +Z in world)
        for name in ("root", "bone_01"):
            b = arm.data.bones[name]
            fixtures.assert_vec_close(self, fixtures.bone_dir(b), (0, 0, 1), places=4)
            fixtures.assert_vec_close(self, b.head_local, original[name][0], places=3)
            self.assertAlmostEqual(b.length, 30.0, places=3)
        self.assertAlmostEqual(arm.data.bones["bone_02"].length, 15.0, places=3)  # leaf = half of parent
        self.assertTrue(arm.data.morenn.axes_converted)
        self.assertEqual(arm.data.morenn.bone_primary, "X")
        self.assertEqual(arm.data.morenn.bone_secondary, "Y")
        meshes = [o for o in res.objects if o.type == "MESH"]
        self.assertEqual(len(meshes), 1)
        self.assertEqual(meshes[0].name, "SKM_Hero_Mesh")
        self.assertEqual(meshes[0].parent, arm)
        self.assertTrue(any(m.type == "ARMATURE" and m.object == arm for m in meshes[0].modifiers))
        self.assertEqual(
            {vg.name for vg in meshes[0].vertex_groups} & {"root", "bone_01", "bone_02"}, {"root", "bone_01", "bone_02"}
        )
        self.assertIn("Deform", [c.name for c in arm.data.collections])
        # rest pose is identical to the original rig: skinning deforms nothing
        for pb in arm.pose.bones:
            fixtures.assert_vec_close(self, pb.matrix_basis.translation, (0, 0, 0), places=4)
        self.assertFalse([o for o in bpy.data.objects if o.name.startswith("morenn_")])

    def test_blender_marker_round_trip_matrices(self):
        rig, body = _rigged_character("Hero")
        done, _, report = fixtures.run_export([rig], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        original = {b.name: b.matrix_local.copy() for b in rig.data.bones}
        verts = [body.matrix_world @ v.co for v in body.data.vertices]
        fixtures.fresh_scene(cm=True)
        res = self.import_fbx.import_file(bpy.context, done[0].path, self.prefs, self.Report())
        arm = res.armatures[0]
        for name, m in original.items():
            got = arm.data.bones[name].matrix_local
            for i in range(4):
                for j in range(4):
                    self.assertAlmostEqual(got[i][j], m[i][j], places=4, msg=f"{name} [{i}][{j}]")
        mesh = [o for o in res.objects if o.type == "MESH"][0]
        got_verts = sorted((mesh.matrix_world @ v.co).to_tuple(3) for v in mesh.data.vertices)
        self.assertEqual(got_verts, sorted(v.to_tuple(3) for v in verts))

    def test_metre_scene_rescales(self):
        path, rig, body = make_ue_style_file(self.tmp)
        fixtures.fresh_scene(cm=False)
        res = self.import_fbx.import_file(bpy.context, path, self.prefs, self.Report())
        arm = res.armatures[0]
        self.assertAlmostEqual(arm.data.bones["bone_01"].head_local.z, 0.30, places=4)
        mesh = [o for o in res.objects if o.type == "MESH"][0]
        zs = [(mesh.matrix_world @ v.co).z for v in mesh.data.vertices]
        self.assertAlmostEqual(max(zs) - min(zs), 0.90, places=4)


if __name__ == "__main__":
    unittest.main()


class MirroredChainTests(unittest.TestCase):
    """A chain whose middle bone points away from its child (the UE4/ALS mannequin right arm / left leg case)."""

    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.import_fbx = fixtures.addon_module("core.import_fbx")
        self.bones = fixtures.addon_module("core.bones")
        self.prefs = fixtures.addon_module("prefs").get_prefs()
        self.Report = fixtures.addon_module("core.log").Report
        self.insp = fixtures.addon_module("fbxpatch.inspect")

    def _mirrored_rig(self):
        """root 0→30 up; bone_01 and bone_02 point DOWN toward their parents (X-toward-parent chain, as the
        right arm / left leg of the UE4 mannequin): bone_01 head (0,0,30), bone_02 head (0,0,60)."""
        compat = fixtures.addon_module("compat")
        arm = bpy.data.armatures.new("Mirror")
        rig = bpy.data.objects.new("Mirror", arm)
        fixtures.link(rig)
        bpy.context.view_layer.objects.active = rig
        with bpy.context.temp_override(
            active_object=rig, object=rig, selected_objects=[rig], **compat.window_override()
        ):
            bpy.ops.object.mode_set(mode="EDIT")
            root = arm.edit_bones.new("root")
            root.head, root.tail = (0, 0, 0), (0, 0, 30)
            b1 = arm.edit_bones.new("bone_01")
            b1.head, b1.tail = (0, 0, 30), (0, 0, 20)
            b1.parent = root
            b2 = arm.edit_bones.new("bone_02")
            b2.head, b2.tail = (0, 0, 60), (0, 0, 30)
            b2.parent = b1
            bpy.ops.object.mode_set(mode="OBJECT")
        body = fixtures.make_cube("Mirror_Body", size=20.0, location=(0, 0, 45))
        mod = body.modifiers.new("Armature", "ARMATURE")
        mod.object = rig
        body.parent = rig
        for name in ("root", "bone_01", "bone_02"):
            body.vertex_groups.new(name=name)
        for v in body.data.vertices:
            body.vertex_groups[v.index % 3].add([v.index], 1.0, "REPLACE")
        return rig, body

    def _bone_lcl(self, path):
        root, _ = fixtures.fbx_load(path)
        out = {}
        for name, model in fixtures.fbx_models(root, b"LimbNode").items():
            t = self.insp.prop_values(model, b"Lcl Translation") or [0.0, 0.0, 0.0]
            r = self.insp.prop_values(model, b"Lcl Rotation") or [0.0, 0.0, 0.0]
            out[name] = (tuple(round(v, 3) for v in t), tuple(round(v, 3) for v in r))
        return out

    def test_detection_conversion_and_exact_round_trip(self):
        rig, body = self._mirrored_rig()
        done, _, report = fixtures.run_export([rig], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        original_lcl = self._bone_lcl(done[0].path)
        # in the file, bone_02 sits on bone_01's -X axis (mirrored side), like upperarm_r in the mannequin
        self.assertAlmostEqual(original_lcl["bone_02"][0][0], -30.0, places=2)
        # disguise as an Unreal export (Root type, non-Blender creator, no marker)
        tree = fixtures.addon_module("fbxpatch.tree")
        root, version = tree.load(done[0].path)
        limbs = fixtures.fbx_models(root, b"LimbNode")
        limbs["root"].props[2] = b"Root"
        for kind, child, parent, _p, _e in self.insp.connections(root):
            if kind == b"OO" and parent == self.insp.object_id(limbs["root"]):
                attr = self.insp.object_by_id(root, child)
                if attr is not None and attr.id == b"NodeAttribute":
                    attr.props[2] = b"Root"
        root.find(b"FBXHeaderExtension").find(b"Creator").props[0] = b"FBX SDK/FBX Plugins version 2020.2"
        gs = root.find(b"GlobalSettings").find(b"Properties70")
        gs.elems = [p for p in gs.elems if not (p.props and p.props[0].startswith(b"MorennBridge"))]
        ue_path = os.path.join(self.tmp, "SKM_Mirror.fbx")
        tree.save(ue_path, root, version)

        fixtures.fresh_scene(cm=True)
        res = self.import_fbx.import_file(bpy.context, ue_path, self.prefs, self.Report())
        arm = res.armatures[0]
        b = arm.data.bones
        self.assertEqual(b["bone_01"].morenn.axis_primary, "-X")
        self.assertEqual(b["bone_02"].morenn.axis_primary, "-X")  # leaf inherits its parent's convention
        self.assertEqual(b["root"].morenn.axis_primary, "DEFAULT")
        for name in ("root", "bone_01", "bone_02"):
            fixtures.assert_vec_close(self, fixtures.bone_dir(b[name]), (0, 0, 1), places=4)
        fixtures.assert_vec_close(self, b["bone_01"].head_local, (0, 0, 30), places=3)
        self.assertAlmostEqual(b["bone_01"].length, 30.0, places=3)
        for pb in arm.pose.bones:
            fixtures.assert_vec_close(self, pb.matrix_basis.translation, (0, 0, 0), places=4)

        # exact round trip: re-export reproduces the Unreal-side bone transforms
        done2, _, report2 = fixtures.run_export([arm], self.tmp)
        self.assertTrue(report2.ok, [str(e) for e in report2.entries])
        again = self._bone_lcl(done2[0].path)
        for name in ("root", "bone_01", "bone_02"):
            fixtures.assert_vec_close(self, again[name][0], original_lcl[name][0], places=2)
            fixtures.assert_vec_close(self, again[name][1], original_lcl[name][1], places=2)
        root2, _ = fixtures.fbx_load(done2[0].path)
        patches = fixtures.addon_module("fbxpatch.patches")
        self.assertIn("bone_01:-X,Y", patches.get_globalsettings_string(root2, "MorennBridge|BoneAxesMap"))

        # our own file (with marker + map) imports back to the converted Blender layout
        fixtures.fresh_scene(cm=True)
        res3 = self.import_fbx.import_file(bpy.context, done2[0].path, self.prefs, self.Report())
        b3 = res3.armatures[0].data.bones
        fixtures.assert_vec_close(self, fixtures.bone_dir(b3["bone_01"]), (0, 0, 1), places=4)
        self.assertEqual(b3["bone_01"].morenn.axis_primary, "-X")

    def test_animation_round_trip_on_mirrored_bone(self):
        import math

        from mathutils import Quaternion

        rig, body = self._mirrored_rig()
        done, _, _ = fixtures.run_export([rig], self.tmp)
        fixtures.fresh_scene(cm=True)
        # import our file but pretend it is foreign so per-bone detection runs
        tree = fixtures.addon_module("fbxpatch.tree")
        root, version = tree.load(done[0].path)
        root.find(b"FBXHeaderExtension").find(b"Creator").props[0] = b"FBX SDK/FBX Plugins version 2020.2"
        gs = root.find(b"GlobalSettings").find(b"Properties70")
        gs.elems = [p for p in gs.elems if not (p.props and p.props[0].startswith(b"MorennBridge"))]
        ue_path = os.path.join(self.tmp, "SKM_Mirror2.fbx")
        tree.save(ue_path, root, version)
        res = self.import_fbx.import_file(bpy.context, ue_path, self.prefs, self.Report())
        arm = res.armatures[0]
        self.assertEqual(arm.data.bones["bone_01"].morenn.axis_primary, "-X")
        pb = arm.pose.bones["bone_01"]
        pb.rotation_mode = "QUATERNION"
        pb.rotation_quaternion = Quaternion((1, 0, 0, 0))
        pb.keyframe_insert("rotation_quaternion", frame=1)
        pb.rotation_quaternion = Quaternion((0, 1, 0), math.radians(60))
        pb.keyframe_insert("rotation_quaternion", frame=5)
        arm.animation_data.action.name = "Wave"
        bpy.context.scene.frame_set(5)
        bpy.context.view_layer.update()
        expected = arm.pose.bones["bone_02"].matrix.copy()  # child's world pose follows the rotated parent
        classify = fixtures.addon_module("core.classify")
        export_fbx = fixtures.addon_module("core.export_fbx")
        settings = fixtures.export_settings(self.tmp)
        groups = classify.build_anim_groups(
            [arm], self.prefs, bpy.context.scene, source="ACTIVE_ACTION", destination=settings.destination_path
        )
        report = self.Report()
        done2, _ = export_fbx.export_groups(bpy.context, groups, settings, report)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        fixtures.fresh_scene(cm=True)
        res2 = self.import_fbx.import_file(bpy.context, done2[0].path, self.prefs, self.Report())
        arm2 = res2.armatures[0]
        bpy.context.scene.frame_set(5)
        bpy.context.view_layer.update()
        got = arm2.pose.bones["bone_02"].matrix
        for i in range(4):
            for j in range(4):
                self.assertAlmostEqual(got[i][j], expected[i][j], places=3, msg=f"[{i}][{j}]")
