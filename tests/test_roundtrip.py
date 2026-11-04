# SPDX-License-Identifier: GPL-3.0-or-later
"""Export, import and export again: what comes back has to match what went out."""

import json
import math
import os
import unittest

import bpy
from mathutils import Quaternion

from tests import fixtures
from tests.test_export_anim import _animated_character
from tests.test_export_skeletal import _rigged_character


def rounded(values, digits=3):
    return tuple(round(v, digits) + 0.0 for v in values)


def world_positions(obj):
    return sorted(rounded(obj.matrix_world @ v.co) for v in obj.data.vertices)


def corner_normals(me, matrix):
    rotation = matrix.to_3x3()
    out = {}
    for loop in me.loops:
        key = rounded(matrix @ me.vertices[loop.vertex_index].co)
        normal = (rotation @ me.corner_normals[loop.index].vector).normalized()
        out.setdefault(key, set()).add(rounded(normal, 2))
    return out


def uv_coordinates(me):
    uv = me.uv_layers.active.data
    return sorted(rounded(uv[i].uv, 4) for i in range(len(uv)))


def weights(obj):
    names = {g.index: g.name for g in obj.vertex_groups}
    out = {}
    for vertex in obj.data.vertices:
        key = rounded(obj.matrix_world @ vertex.co)
        out[key] = {names[g.group]: round(g.weight, 3) for g in vertex.groups if g.weight > 1e-4}
    return out


class RoundTripCase(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.import_fbx = fixtures.addon_module("core.import_fbx")
        self.prefs = fixtures.addon_module("prefs").get_prefs()
        self.Report = fixtures.addon_module("core.log").Report

    def export(self, objects, folder="a", **overrides):
        out = os.path.join(self.tmp, folder)
        done, _manifest, report = fixtures.run_export(objects, out, **overrides)
        self.assertTrue(report.ok, fixtures.report_text(report))
        return done[0].path

    def import_back(self, path, clear=True):
        if clear:
            fixtures.fresh_scene(cm=True)
        report = self.Report()
        result = self.import_fbx.import_file(bpy.context, path, self.prefs, report)
        self.assertTrue(report.ok, fixtures.report_text(report))
        bpy.context.view_layer.update()
        return result


class StaticRoundTripTests(RoundTripCase):
    def source(self):
        return fixtures.make_cube("Crate", size=100.0, location=(0, 0, 0))

    def test_positions(self):
        cube = self.source()
        expected = sorted(set(world_positions(cube)))
        result = self.import_back(self.export([cube]))
        self.assertEqual(sorted(set(world_positions(result.meshes[0]))), expected)

    def test_sharp_edges_keep_split_normals(self):
        cube = self.source()
        bpy.context.view_layer.update()
        expected = corner_normals(cube.data, cube.matrix_world)
        result = self.import_back(self.export([cube]))
        got = result.meshes[0]
        self.assertEqual(corner_normals(got.data, got.matrix_world), expected)

    def test_smooth_shading_is_preserved(self):
        ball = fixtures.make_sphere("Ball", radius=50.0, segments=12, rings=8)
        ball.data.shade_smooth()
        bpy.context.view_layer.update()
        expected = corner_normals(ball.data, ball.matrix_world)
        result = self.import_back(self.export([ball]))
        got = corner_normals(result.meshes[0].data, result.meshes[0].matrix_world)
        self.assertEqual(set(got), set(expected))
        self.assertTrue(all(len(normals) == 1 for normals in got.values()))

    def test_uvs(self):
        cube = self.source()
        expected = set(uv_coordinates(cube.data))
        result = self.import_back(self.export([cube]))
        self.assertEqual(set(uv_coordinates(result.meshes[0].data)), expected)

    def test_vertex_colors(self):
        cube = self.source()
        result = self.import_back(self.export([cube]))
        attribute = result.meshes[0].data.color_attributes[0]
        for got, expected in zip(attribute.data[0].color, (0.5, 0.25, 1.0, 1.0)):
            self.assertAlmostEqual(got, expected, delta=0.02)

    def test_material_slots(self):
        result = self.import_back(self.export([self.source()]))
        self.assertEqual([m.name for m in result.meshes[0].data.materials], ["M_Test"])

    def test_second_generation_file_matches_the_first(self):
        first_path = self.export([self.source()])
        first_root, _ = fixtures.fbx_load(first_path)
        first_model = fixtures.fbx_models(first_root)["SM_Crate"]
        first = sorted(
            rounded(v) for v in fixtures.fbx_vertices(fixtures.fbx_geometry_for_model(first_root, first_model))
        )
        result = self.import_back(first_path)
        second_path = self.export(result.meshes, folder="b")
        second_root, _ = fixtures.fbx_load(second_path)
        second_model = fixtures.fbx_models(second_root)["SM_Crate"]
        second = sorted(
            rounded(v) for v in fixtures.fbx_vertices(fixtures.fbx_geometry_for_model(second_root, second_model))
        )
        self.assertEqual(second, first)


class SkeletalRoundTripTests(RoundTripCase):
    def assert_rest_matches(self, armature, expected):
        for name, matrix in expected.items():
            got = armature.data.bones[name].matrix_local
            for i in range(4):
                fixtures.assert_vec_close(self, got[i], matrix[i], places=3)

    def test_x_along_bone(self):
        rig, _body = _rigged_character("Hero")
        expected = {b.name: b.matrix_local.copy() for b in rig.data.bones}
        result = self.import_back(self.export([rig]))
        self.assert_rest_matches(result.armatures[0], expected)

    def test_y_along_bone(self):
        rig, _body = _rigged_character("Hero")
        rig.data.morenn.bone_primary = "Y"
        rig.data.morenn.bone_secondary = "X"
        expected = {b.name: b.matrix_local.copy() for b in rig.data.bones}
        result = self.import_back(self.export([rig]))
        self.assert_rest_matches(result.armatures[0], expected)

    def test_weights(self):
        rig, body = _rigged_character("Hero")
        bpy.context.view_layer.update()
        expected = weights(body)
        result = self.import_back(self.export([rig]))
        got = weights(result.meshes[0])
        self.assertEqual(set(got), set(expected))
        for key, values in expected.items():
            self.assertEqual(set(got[key]), set(values), key)

    def test_shape_keys(self):
        rig, _body = _rigged_character("Hero")
        result = self.import_back(self.export([rig]))
        keys = result.meshes[0].data.shape_keys.key_blocks
        self.assertEqual([k.name for k in keys], ["Basis", "Puff"])
        ratio = keys["Puff"].data[0].co.length / keys["Basis"].data[0].co.length
        self.assertGreater(ratio, 1.05)

    def test_shape_key_names_longer_than_blender_allows(self):
        rig, body = _rigged_character("Hero")
        body.shape_key_add(name="Puff2", from_mix=False).data[0].co.x += 5.0
        path = self.export([rig])
        patches, tree, insp = (fixtures.addon_module(f"fbxpatch.{m}") for m in ("patches", "tree", "inspect"))
        root, version = tree.load(path)
        long_names = ["corrective_" + "a" * 52 + "_L", "corrective_" + "a" * 52 + "_R"]  # 65 bytes, same 63 first
        patches.rename_blend_shapes(root, {"SKM_Hero": dict(zip(["Puff", "Puff2"], long_names))})
        for kind, child, parent, _prop, _e in list(insp.connections(root)):  # Unreal repeats these connections
            if kind == b"OO" and insp.object_subclass(insp.object_by_id(root, child)) in (b"BlendShape", b"Shape"):
                insp.add_connection(root, child, parent)
        self.assertEqual(patches.blend_shape_names(root), {"SKM_Hero": long_names})
        tree.save(path, root, version)

        first = self.import_back(path)
        mesh = first.meshes[0]
        shortened = [k.name for k in mesh.data.shape_keys.key_blocks[1:]]
        self.assertEqual(shortened[0], long_names[0][:63])
        self.assertNotEqual(shortened[1], shortened[0])
        self.assertEqual(json.loads(mesh.morenn.shape_key_names), dict(zip(shortened, long_names)))
        root, _ = fixtures.fbx_load(self.export([first.armatures[0]], folder="b"))
        self.assertEqual(patches.blend_shape_names(root), {"SKM_Hero": long_names})

    def test_second_generation_keeps_rest_pose(self):
        rig, _body = _rigged_character("Hero")
        expected = {b.name: b.matrix_local.copy() for b in rig.data.bones}
        first = self.import_back(self.export([rig]))
        second = self.import_back(self.export([first.armatures[0]], folder="b"))
        self.assert_rest_matches(second.armatures[0], expected)

    def test_bones_keep_unit_scale_in_the_file(self):
        rig, _body = _rigged_character("Hero")
        root, _ = fixtures.fbx_load(self.export([rig]))
        insp = fixtures.addon_module("fbxpatch.inspect")
        for model in fixtures.fbx_models(root, b"LimbNode").values():
            values = insp.prop_values(model, b"Lcl Scaling")
            if values:
                fixtures.assert_vec_close(self, values[-3:], (1, 1, 1))


class AnimationRoundTripTests(RoundTripCase):
    def key_values(self, action):
        compat = fixtures.addon_module("compat")
        out = {}
        for _slot, curve in compat.iter_fcurves(action):
            out[(curve.data_path, curve.array_index)] = [rounded(k.co, 3) for k in curve.keyframe_points]
        return out

    def test_final_pose_survives_two_generations(self):
        rig, _body = _animated_character()
        scene = bpy.context.scene
        scene.frame_set(24)
        bpy.context.view_layer.update()
        expected = {b.name: b.matrix.copy() for b in rig.pose.bones}
        settings_dir = os.path.join(self.tmp, "a")
        classify = fixtures.addon_module("core.classify")
        export_fbx = fixtures.addon_module("core.export_fbx")

        def export_action(arm, folder):
            settings = fixtures.export_settings(os.path.join(self.tmp, folder))
            groups = classify.build_anim_groups([arm], self.prefs, bpy.context.scene)
            done, _manifest = export_fbx.export_groups(bpy.context, groups, settings, self.Report())
            return done[0].path

        path = export_action(rig, "a")
        self.assertTrue(path.startswith(settings_dir))
        first = self.import_back(path)
        arm = first.armatures[0]
        arm.animation_data.action.name = "Run"
        second = self.import_back(export_action(arm, "b"))
        bpy.context.scene.frame_set(24)
        bpy.context.view_layer.update()
        for name, matrix in expected.items():
            got = second.armatures[0].pose.bones[name].matrix
            fixtures.assert_vec_close(self, got.translation, matrix.translation, places=2)
            self.assertGreater(abs(got.to_quaternion().dot(matrix.to_quaternion())), 0.9999, name)

    def test_quaternion_keys_stay_continuous(self):
        rig, _body = _animated_character()
        pose_bone = rig.pose.bones["bone_01"]
        pose_bone.rotation_quaternion = Quaternion((0, 0, 1), math.radians(170))
        pose_bone.keyframe_insert("rotation_quaternion", frame=12)
        settings = fixtures.export_settings(self.tmp)
        classify = fixtures.addon_module("core.classify")
        export_fbx = fixtures.addon_module("core.export_fbx")
        groups = classify.build_anim_groups([rig], self.prefs, bpy.context.scene)
        done, _manifest = export_fbx.export_groups(bpy.context, groups, settings, self.Report())
        self.assertEqual(len(done), 1)
        result = self.import_back(done[0].path)
        arm = result.armatures[0]
        scene = bpy.context.scene
        previous = None
        for frame in range(1, 25):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            current = arm.pose.bones["bone_01"].matrix.to_quaternion()
            if previous is not None:
                self.assertGreater(abs(previous.dot(current)), 0.8, frame)
            previous = current


if __name__ == "__main__":
    unittest.main()
