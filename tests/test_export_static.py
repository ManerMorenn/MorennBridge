# SPDX-License-Identifier: GPL-3.0-or-later
import json
import math
import os
import unittest

import bpy
from mathutils import Euler

from tests import fixtures


class StaticExportTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.I = fixtures.addon_module("fbxpatch.inspect")
        self.patches = fixtures.addon_module("fbxpatch.patches")

    def _gs(self, root, name):
        return self.I.global_setting_values(root, name)

    def test_header_units_names_and_transform(self):
        cube = fixtures.make_cube("Crate", size=100.0, location=(10, 20, 30))
        cube.rotation_euler = Euler((0, 0, math.radians(90)))
        done, manifest_path, report = fixtures.run_export([cube], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual(len(done), 1)
        self.assertEqual(os.path.basename(done[0].path), "SM_Crate.fbx")
        root, version = fixtures.fbx_load(done[0].path)
        self.assertEqual(self._gs(root, b"UpAxis")[0], 2)
        self.assertEqual(self._gs(root, b"UpAxisSign")[0], 1)
        self.assertEqual(self._gs(root, b"FrontAxis")[0], 1)
        self.assertEqual(self._gs(root, b"FrontAxisSign")[0], -1)
        self.assertEqual(self._gs(root, b"CoordAxis")[0], 0)
        self.assertEqual(self._gs(root, b"CoordAxisSign")[0], 1)
        self.assertAlmostEqual(self._gs(root, b"UnitScaleFactor")[0], 1.0)
        models = fixtures.fbx_models(root, b"Mesh")
        self.assertEqual(list(models), ["SM_Crate"])
        model = models["SM_Crate"]
        for name, default in (
            (b"Lcl Translation", (0, 0, 0)),
            (b"Lcl Rotation", (0, 0, 0)),
            (b"Lcl Scaling", (1, 1, 1)),
        ):
            values = self.I.prop_values(model, name) or list(default)
            for v, d in zip(values, default):
                self.assertAlmostEqual(v, d, places=5)
        geom = fixtures.fbx_geometry_for_model(root, model)
        self.assertIsNotNone(geom)
        self.assertEqual(fixtures.fbx_polygon_count(geom), 12)  # triangulated cube
        self.assertIsNotNone(geom.find(b"LayerElementSmoothing"))
        self.assertIsNotNone(geom.find(b"LayerElementColor"))
        self.assertIsNotNone(geom.find(b"LayerElementUV"))
        verts = geom.find(b"Vertices").props[0]
        xs = [verts[i] for i in range(0, len(verts), 3)]
        self.assertAlmostEqual(max(xs) - min(xs), 100.0, places=3)  # rotation applied, no translation
        self.assertAlmostEqual((max(xs) + min(xs)) / 2, 0.0, places=3)
        self.assertEqual(self.patches.get_globalsettings_string(root, "MorennBridge|BoneAxes"), "XY")
        with open(manifest_path) as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["items"][0]["assets"][0]["name"], "SM_Crate")
        self.assertTrue(manifest["items"][0]["single_asset"])
        ok, leftovers = fixtures.no_temp_leftovers()
        self.assertTrue(ok, leftovers)
        self.assertEqual(bpy.context.scene.name, "Scene")

    def test_metre_scene_exports_centimetres(self):
        fixtures.fresh_scene(cm=False)
        cube = fixtures.make_cube("Crate", size=1.0)  # 1 m
        done, _, report = fixtures.run_export([cube], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _ = fixtures.fbx_load(done[0].path)
        model = fixtures.fbx_models(root, b"Mesh")["SM_Crate"]
        scaling = self.I.prop_values(model, b"Lcl Scaling") or [1, 1, 1]
        for v in scaling:
            self.assertAlmostEqual(v, 1.0, places=6)
        geom = fixtures.fbx_geometry_for_model(root, model)
        verts = geom.find(b"Vertices").props[0]
        xs = [verts[i] for i in range(0, len(verts), 3)]
        self.assertAlmostEqual(max(xs) - min(xs), 100.0, places=3)
        self.assertAlmostEqual(self._gs(root, b"UnitScaleFactor")[0], 1.0)
        self.assertAlmostEqual(bpy.context.scene.unit_settings.scale_length, 1.0)  # user scene untouched

    def test_satellites_and_lod_naming(self):
        crate = fixtures.make_cube("Crate", size=100.0, location=(50, 0, 0))
        bpy.context.view_layer.update()
        col = fixtures.make_cube("UCX_Crate", size=110.0, location=(50, 0, 0), uv=False, color=False, material=False)
        col.parent = crate
        col.matrix_parent_inverse = crate.matrix_world.inverted()
        fixtures.make_cube("Crate_LOD1", size=100.0, location=(50, 0, 0))
        sock = fixtures.make_empty("SOCKET_Lid", location=(50, 0, 60), parent=crate)
        sock.matrix_parent_inverse = crate.matrix_world.inverted()
        done, manifest_path, report = fixtures.run_export([crate], self.tmp)
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual(len(done), 1)
        root, _ = fixtures.fbx_load(done[0].path)
        names = set(fixtures.fbx_models(root))
        self.assertEqual(names, {"LOD0_SM_Crate", "LOD1_SM_Crate", "UCX_LOD0_SM_Crate_01", "SOCKET_Lid"})
        models = fixtures.fbx_models(root)
        # socket is a child of the LOD0 node, at the right local position (cm)
        sock_id = self.I.object_id(models["SOCKET_Lid"])
        parents = [p for p in self.I.parents_of(root, sock_id, b"OO")]
        self.assertEqual(parents[0][2], self.I.object_id(models["LOD0_SM_Crate"]))
        t = self.I.prop_values(models["SOCKET_Lid"], b"Lcl Translation")
        fixtures.assert_vec_close(self, t, (0, 0, 60), places=3)
        geom = fixtures.fbx_geometry_for_model(root, models["UCX_LOD0_SM_Crate_01"])
        self.assertIsNone(geom.find(b"LayerElementUV"))
        with open(manifest_path) as fh:
            item = json.load(fh)["items"][0]
        self.assertEqual(item["assets"][0]["lods"], 2)
        self.assertEqual(item["assets"][0]["collisions"], 1)
        self.assertEqual(item["assets"][0]["sockets"], 1)

    def test_lodgroup_nodes_mode_reimports(self):
        crate = fixtures.make_cube("Crate", size=100.0)
        fixtures.make_cube("Crate_LOD1", size=90.0)
        fixtures.make_cube("Crate_LOD2", size=80.0)
        done, _, report = fixtures.run_export([crate], self.tmp, lod_mode="LODGROUP_NODES")
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _ = fixtures.fbx_load(done[0].path)
        groups = self.patches.lod_groups(root)
        self.assertEqual(groups, [("SM_Crate", ["SM_Crate_LOD0", "SM_Crate_LOD1", "SM_Crate_LOD2"])])
        self.assertEqual(self.I.definition_count(root, b"Model"), len(self.I.find_objects(root, b"Model")))
        # Blender's own (ufbx) importer must accept the patched file
        fixtures.fresh_scene(cm=True)
        compat = fixtures.addon_module("compat")
        with bpy.context.temp_override(**compat.window_override()):
            bpy.ops.wm.fbx_import(filepath=done[0].path, global_scale=100.0)
        names = {o.name for o in bpy.data.objects}
        self.assertIn("SM_Crate", names)
        self.assertIn("SM_Crate_LOD2", names)
        group = bpy.data.objects["SM_Crate"]
        self.assertEqual({c.name for c in group.children}, {"SM_Crate_LOD0", "SM_Crate_LOD1", "SM_Crate_LOD2"})

    def test_combined_file_names_nodes(self):
        a = fixtures.make_cube("Crate", size=100.0)
        b = fixtures.make_cube("Barrel", size=50.0, location=(200, 0, 0))
        done, manifest_path, report = fixtures.run_export([a, b], self.tmp, file_mode="COMBINED")
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual(len(done), 1)
        self.assertEqual(os.path.basename(done[0].path), "batch.fbx")
        root, _ = fixtures.fbx_load(done[0].path)
        self.assertEqual(set(fixtures.fbx_models(root, b"Mesh")), {"SM_Crate", "SM_Barrel"})
        with open(manifest_path) as fh:
            item = json.load(fh)["items"][0]
        self.assertFalse(item["single_asset"])
        self.assertEqual({a["name"] for a in item["assets"]}, {"SM_Crate", "SM_Barrel"})

    def test_generated_collision_mesh(self):
        ball = fixtures.make_sphere("Ball", segments=32, rings=16, radius=50.0)
        len(ball.data.polygons)
        done, manifest_path, report = fixtures.run_export(
            [ball], self.tmp, generate_collision_mesh=True, collision_ratio=0.5
        )
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        root, _ = fixtures.fbx_load(done[0].path)
        models = fixtures.fbx_models(root, b"Mesh")
        self.assertEqual(set(models), {"SM_Ball", "SM_Ball_COLL"})
        render_faces = fixtures.fbx_polygon_count(fixtures.fbx_geometry_for_model(root, models["SM_Ball"]))
        coll_geom = fixtures.fbx_geometry_for_model(root, models["SM_Ball_COLL"])
        coll_faces = fixtures.fbx_polygon_count(coll_geom)
        self.assertGreater(render_faces, 0)
        self.assertTrue(0.3 * render_faces <= coll_faces <= 0.7 * render_faces, (render_faces, coll_faces))
        self.assertIsNone(coll_geom.find(b"LayerElementUV"))
        self.assertIsNone(coll_geom.find(b"LayerElementMaterial"))
        with open(manifest_path) as fh:
            item = json.load(fh)["items"][0]
        self.assertFalse(item["single_asset"])
        self.assertEqual(item["assets"][0]["collision_mesh"], "SM_Ball_COLL")
        ok, leftovers = fixtures.no_temp_leftovers()
        self.assertTrue(ok, leftovers)
        self.assertEqual(len(bpy.data.objects), 1)  # nothing added to the user's scene

    def test_generated_collision_mesh_separate_file_and_min_faces(self):
        cube = fixtures.make_cube("Tiny", size=10.0)
        done, _, report = fixtures.run_export(
            [cube], self.tmp, generate_collision_mesh=True, collision_ratio=0.1, collision_mesh_file="SEPARATE"
        )
        self.assertTrue(report.ok, [str(e) for e in report.entries])
        self.assertEqual({os.path.basename(d.path) for d in done}, {"SM_Tiny.fbx", "SM_Tiny_COLL.fbx"})
        self.assertTrue(any("W024" in e.message for e in report.warnings))
        coll = [d for d in done if d.path.endswith("_COLL.fbx")][0]
        root, _ = fixtures.fbx_load(coll.path)
        geom = fixtures.fbx_geometry_for_model(root, fixtures.fbx_models(root, b"Mesh")["SM_Tiny_COLL"])
        self.assertEqual(fixtures.fbx_polygon_count(geom), 12)


if __name__ == "__main__":
    unittest.main()
