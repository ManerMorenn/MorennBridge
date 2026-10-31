# SPDX-License-Identifier: GPL-3.0-or-later
import json
import math
import os
import unittest

import bpy
from mathutils import Matrix, Vector

from tests import fixtures


def _rigged_character(name="Hero", bones=3, bone_length=30.0, shape_key=True):
    """Armature + skinned box; each vertex weighted to the nearest bone, one extra group with 9 influences."""
    rig = fixtures.make_armature(name, bones=bones, bone_length=bone_length)
    body = fixtures.make_cube(f"{name}_Body", size=20.0, location=(0, 0, bones * bone_length / 2))
    for v in body.data.vertices:  # stretch the box along the whole chain
        v.co.z *= (bones * bone_length) / 20.0
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    body.parent = rig
    bone_names = [b.name for b in rig.data.bones]
    groups = {n: body.vertex_groups.new(name=n) for n in bone_names}
    for v in body.data.vertices:  # every bone gets weights (round-robin), good enough for round-trip tests
        groups[bone_names[v.index % bones]].add([v.index], 1.0, "REPLACE")
    if shape_key:
        body.shape_key_add(name="Basis", from_mix=False)
        sk = body.shape_key_add(name="Puff", from_mix=False)
        for p in sk.data:
            p.co = p.co * 1.5
    return rig, body


class SkeletalExportTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.I = fixtures.addon_module("fbxpatch.inspect")
        self.patches = fixtures.addon_module("fbxpatch.patches")

    def _bone_matrix(self, root, model):
        """Local FBX transform (Lcl T/R Euler XYZ degrees) of a model as a Matrix."""
        t = self.I.prop_values(model, b"Lcl Translation") or [0, 0, 0]
        r = self.I.prop_values(model, b"Lcl Rotation") or [0, 0, 0]
        rot = (
            Matrix.Rotation(math.radians(r[2]), 4, "Z")
            @ Matrix.Rotation(math.radians(r[1]), 4, "Y")
            @ Matrix.Rotation(math.radians(r[0]), 4, "X")
        )
        return Matrix.Translation(Vector(t)) @ rot

    def test_skeleton_layout_and_bone_axes(self):
        rig, body = _rigged_character()
        done, manifest_path, report = fixtures.run_export([rig], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual(os.path.basename(done[0].path), "SKM_Hero.fbx")
        root, _ = fixtures.fbx_load(done[0].path)
        models = fixtures.fbx_models(root)
        self.assertNotIn("Armature", models)
        limbs = fixtures.fbx_models(root, b"LimbNode")
        self.assertEqual(set(limbs), {"root", "bone_01", "bone_02"})
        # the root bone hangs from the scene root
        root_id = self.I.object_id(limbs["root"])
        model_parents = [
            p[2]
            for p in self.I.parents_of(root, root_id, b"OO")
            if p[2] == 0 or self.I.object_by_id(root, p[2]).id == b"Model"
        ]
        self.assertEqual(model_parents, [0])
        # mesh node named after the asset, also under the scene root
        self.assertIn("SKM_Hero", fixtures.fbx_models(root, b"Mesh"))
        # X-along-bone convention: the child bone's local translation lies on +X of the parent
        child_local = self._bone_matrix(root, limbs["bone_01"])
        t = child_local.translation
        self.assertAlmostEqual(t.x, 30.0, places=3)
        self.assertAlmostEqual(t.y, 0.0, places=3)
        self.assertAlmostEqual(t.z, 0.0, places=3)
        # no leaf bones, marker present, definitions consistent, no dangling connections
        self.assertFalse([n for n in models if n.endswith("_end")])
        self.assertEqual(self.patches.get_globalsettings_string(root, "MorennBridge|BoneAxes"), "XY")
        self.assertEqual(self.I.definition_count(root, b"Model"), len(self.I.find_objects(root, b"Model")))
        ids = {self.I.object_id(e) for e in self.I.objects(root)} | {0}
        for kind, child, parent, _p, _e in self.I.connections(root):
            self.assertIn(child, ids)
            self.assertIn(parent, ids)
        # bind pose has no entry for the removed null
        for pose in self.I.find_objects(root, b"Pose"):
            nb = pose.find(b"NbPoseNodes").props[0]
            self.assertEqual(nb, len(pose.find_all(b"PoseNode")))
        # blend shape exported
        self.assertTrue(self.I.find_objects(root, b"Deformer", subclass=b"BlendShape"))
        with open(manifest_path) as fh:
            item = json.load(fh)["items"][0]
        self.assertEqual(item["kind"], "SKELETAL_MESH")
        self.assertEqual(item["root_bone"], "root")
        self.assertTrue(item["morph_targets"])
        self.assertEqual(item["bone_axes"], "XY")
        ok, leftovers = fixtures.no_temp_leftovers()
        self.assertTrue(ok, leftovers)
        self.assertEqual(rig.data.bones["bone_01"].length, 30.0)  # user data untouched

    def test_blender_native_axes_keep_y_along_bone(self):
        rig, body = _rigged_character(shape_key=False)
        done, _, report = fixtures.run_export([rig], self.tmp, bone_primary="Y", bone_secondary="X")
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _ = fixtures.fbx_load(done[0].path)
        limbs = fixtures.fbx_models(root, b"LimbNode")
        t = self._bone_matrix(root, limbs["bone_01"]).translation
        self.assertAlmostEqual(t.y, 30.0, places=3)
        self.assertEqual(self.patches.get_globalsettings_string(root, "MorennBridge|BoneAxes"), "YX")

    def test_keep_armature_null_when_disabled(self):
        rig, body = _rigged_character(shape_key=False)
        done, _, report = fixtures.run_export([rig], self.tmp, strip_armature_null=False)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _ = fixtures.fbx_load(done[0].path)
        models = fixtures.fbx_models(root, b"Null")
        self.assertIn("Armature", models)
        self.assertTrue(self.patches.is_identity_model(models["Armature"]))

    def test_influence_limit_and_merge(self):
        rig, body = _rigged_character(shape_key=False)
        # add a non-exported helper bone's group with weights and give one vertex many influences
        helper = body.vertex_groups.new(name="not_a_bone")
        helper.add([0], 0.5, "REPLACE")
        done, _, report = fixtures.run_export([rig], self.tmp, influence_limit=1)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _ = fixtures.fbx_load(done[0].path)
        clusters = self.I.find_objects(root, b"Deformer", subclass=b"Cluster")
        self.assertEqual(len(clusters), 3)
        total = {}
        for c in clusters:
            idx = c.find(b"Indexes")
            w = c.find(b"Weights")
            if idx is None:
                continue
            for i, ww in zip(idx.props[0], w.props[0]):
                total[i] = total.get(i, 0.0) + ww
                self.assertAlmostEqual(ww, 1.0, places=5)  # limit 1 -> single full weight
        for i, s in total.items():
            self.assertAlmostEqual(s, 1.0, places=5)
        # user's mesh still has its helper group
        self.assertIn("not_a_bone", [vg.name for vg in body.vertex_groups])

    def test_multiple_roots_rejected(self):
        rig, body = _rigged_character(shape_key=False)
        compat = fixtures.addon_module("compat")
        bpy.context.view_layer.objects.active = rig
        with bpy.context.temp_override(
            active_object=rig, object=rig, selected_objects=[rig], **compat.window_override()
        ):
            bpy.ops.object.mode_set(mode="EDIT")
            eb = rig.data.edit_bones.new("extra_root")
            eb.head, eb.tail = (10, 0, 0), (10, 0, 10)
            bpy.ops.object.mode_set(mode="OBJECT")
        done, _, report = fixtures.run_export([rig], self.tmp)
        self.assertEqual(done, [])
        self.assertTrue(any("E001" in e.message for e in report.errors))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "SKM_Hero.fbx")))


if __name__ == "__main__":
    unittest.main()
