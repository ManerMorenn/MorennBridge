# SPDX-License-Identifier: GPL-3.0-or-later
import math
import os
import unittest
from unittest import mock

import addon_utils
import bpy
from mathutils import Euler

from tests import fixtures


class GeometrySourceTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()

    def export(self, objects, **overrides):
        done, _manifest, report = fixtures.run_export(objects, self.tmp, **overrides)
        self.assertTrue(report.ok, fixtures.report_text(report))
        self.assertTrue(fixtures.no_temp_leftovers()[0], fixtures.no_temp_leftovers()[1])
        return done

    def model(self, asset):
        root, _ = fixtures.fbx_load(os.path.join(self.tmp, f"{asset}.fbx"))
        return root, fixtures.fbx_geometry_for_model(root, fixtures.fbx_models(root)[asset])

    def test_geometry_nodes_instances_become_geometry(self):
        host = fixtures.make_cube("Host", size=1.0)
        fixtures.add_instance_modifier(host)
        self.export([host])
        _root, geometry = self.model("SM_Host")
        self.assertEqual(fixtures.fbx_polygon_count(geometry), 120)  # 10 cubes, 12 triangles each
        self.assertEqual([m.name for m in host.modifiers], ["GN"])

    def test_realized_instances_keep_materials(self):
        host = fixtures.make_cube("Host", size=1.0)
        fixtures.add_instance_modifier(host)
        self.export([host])
        root, _geometry = self.model("SM_Host")
        self.assertEqual(fixtures.fbx_materials(root), {"M_Test"})

    def test_mesh_that_evaluates_to_nothing_is_an_error(self):
        cube = fixtures.make_cube("Gone")
        fixtures.add_empty_geometry_modifier(cube)
        done, _manifest, report = fixtures.run_export([cube], self.tmp)
        self.assertEqual(done, [])
        self.assertIn("E019", fixtures.report_text(report))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "SM_Gone.fbx")))
        self.assertTrue(fixtures.no_temp_leftovers()[0])

    def test_empty_mesh_with_generated_geometry_exports(self):
        me = bpy.data.meshes.new("Procedural")
        obj = fixtures.link(bpy.data.objects.new("Procedural", me))
        group, _source, output = fixtures.new_geometry_group()
        cube = group.nodes.new("GeometryNodeMeshCube")
        group.links.new(cube.outputs[0], output.inputs[0])
        obj.modifiers.new("GN", "NODES").node_group = group
        self.export([obj])
        self.assertEqual(fixtures.fbx_polygon_count(self.model("SM_Procedural")[1]), 12)

    def test_collection_instance_exports_as_one_mesh(self):
        instance = fixtures.make_collection_instance("Kit", members=2)
        self.export([instance])
        self.assertEqual(fixtures.fbx_polygon_count(self.model("SM_Kit")[1]), 24)

    def test_collection_instance_keeps_transform_and_offset(self):
        instance = fixtures.make_collection_instance("Kit", offset=(5, 0, 0), location=(100, 0, 0))
        instance.rotation_euler = Euler((0, 0, math.radians(90)))
        self.export([instance])
        vertices = fixtures.fbx_vertices(self.model("SM_Kit")[1])
        centre = [sum(v[i] for v in vertices) / len(vertices) for i in range(3)]
        # member at x=10, collection offset 5, rotated 90 degrees; the instancer position becomes the pivot
        fixtures.assert_vec_close(self, centre, (0, 5, 0))

    def test_collection_instance_keeps_materials(self):
        self.export([fixtures.make_collection_instance("Kit")])
        self.assertEqual(fixtures.fbx_materials(self.model("SM_Kit")[0]), {"M_Test"})

    def test_collision_child_of_a_collection_instance(self):
        instance = fixtures.make_collection_instance("Kit")
        fixtures.make_cube("UCX_Kit_01", size=4.0).parent = instance
        self.export([instance])
        self.assertIn("UCX_SM_Kit_01", fixtures.fbx_models(self.model("SM_Kit")[0]))

    def test_curve_text_and_metaball_are_triangulated_meshes(self):
        objects = [fixtures.make_curve("Pipe"), fixtures.make_text("Sign"), fixtures.make_metaball("Blob")]
        self.export(objects)
        for asset in ("SM_Pipe", "SM_Sign", "SM_Blob"):
            geometry = self.model(asset)[1]
            self.assertGreater(fixtures.fbx_polygon_count(geometry), 0, asset)
            indices = geometry.find(b"PolygonVertexIndex").props[0]
            self.assertEqual(sum(1 for i in indices if i < 0) * 3, len(indices), asset)

    def test_object_linked_material_is_exported(self):
        cube = fixtures.make_cube("Crate", material=False)
        cube.data.materials.append(bpy.data.materials.new("OnData"))
        cube.material_slots[0].link = "OBJECT"
        cube.material_slots[0].material = bpy.data.materials.new("OnObject")
        self.export([cube])
        self.assertEqual(fixtures.fbx_materials(self.model("SM_Crate")[0]), {"OnObject"})

    def test_hidden_collision_keeps_its_modifiers(self):
        cube = fixtures.make_cube("Crate")
        for hide in ("eye", "monitor", "excluded"):
            collision = fixtures.make_cube(f"UCX_Crate_{hide}", size=2.0, material=False)
            collision.modifiers.new("Sub", "SUBSURF").levels = 1
            if hide == "eye":
                collision.hide_set(True)
            elif hide == "monitor":
                collision.hide_viewport = True
            else:
                collection = bpy.data.collections.new("Off")
                bpy.context.scene.collection.children.link(collection)
                bpy.context.scene.collection.objects.unlink(collision)
                collection.objects.link(collision)
                bpy.context.view_layer.layer_collection.children["Off"].exclude = True
        self.export([cube])
        root, _geometry = self.model("SM_Crate")
        models = fixtures.fbx_models(root)
        for index in (1, 2, 3):
            collision = models[f"UCX_SM_Crate_{index:02d}"]
            self.assertEqual(fixtures.fbx_polygon_count(fixtures.fbx_geometry_for_model(root, collision)), 48)

    def test_source_modifiers_are_restored(self):
        cube = fixtures.make_cube("Crate")
        cube.modifiers.new("Bevel", "BEVEL")
        self.export([cube])
        self.assertEqual([m.name for m in cube.modifiers], ["Bevel"])

    def test_replacing_a_file_is_logged(self):
        cube = fixtures.make_cube("Crate")
        fixtures.run_export([cube], self.tmp)
        _done, _manifest, report = fixtures.run_export([cube], self.tmp)
        self.assertIn("Replaced SM_Crate.fbx", fixtures.report_text(report))

    def test_non_latin_names_reach_the_file(self):
        self.export([fixtures.make_cube("木箱"), fixtures.make_cube("Caisse_été")])
        self.assertIn("SM_木箱.fbx", os.listdir(self.tmp))
        self.assertIn("SM_木箱", fixtures.fbx_models(fixtures.fbx_load(os.path.join(self.tmp, "SM_木箱.fbx"))[0]))
        self.assertIn("SM_Caisse_été.fbx", os.listdir(self.tmp))


class ExportFailureTests(unittest.TestCase):
    def setUp(self):
        fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.export_fbx = fixtures.addon_module("core.export_fbx")

    def assert_clean(self, cube):
        self.assertTrue(fixtures.no_temp_leftovers()[0], fixtures.no_temp_leftovers()[1])
        self.assertEqual(len(bpy.data.scenes), 1)
        self.assertEqual(len(bpy.data.objects), 1)
        self.assertEqual(len(cube.modifiers), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "SM_Crate.fbx")))

    def test_exporter_failure_leaves_nothing_behind(self):
        cube = fixtures.make_cube("Crate")
        with mock.patch.object(self.export_fbx, "run_exporter", side_effect=RuntimeError("boom")):
            done, _manifest, report = fixtures.run_export([cube], self.tmp)
        self.assertEqual(done, [])
        self.assertIn("boom", fixtures.report_text(report))
        self.assert_clean(cube)

    def test_post_pass_failure_leaves_nothing_behind(self):
        cube = fixtures.make_cube("Crate")
        with mock.patch.object(self.export_fbx, "post_process", side_effect=ValueError("bad tree")):
            done, _manifest, report = fixtures.run_export([cube], self.tmp)
        self.assertEqual(done, [])
        self.assertIn("bad tree", fixtures.report_text(report))
        self.assert_clean(cube)

    def test_one_failure_does_not_stop_the_batch(self):
        good = fixtures.make_cube("Good")
        bad = fixtures.make_cube("Bad")
        fixtures.add_empty_geometry_modifier(bad)
        done, manifest, report = fixtures.run_export([bad, good], self.tmp)
        self.assertEqual([os.path.basename(d.path) for d in done], ["SM_Good.fbx"])
        self.assertIn("E019", fixtures.report_text(report))
        self.assertIsNotNone(manifest)

    def test_disabled_fbx_addon_gets_a_clear_message(self):
        cube = fixtures.make_cube("Crate")
        addon_utils.disable("io_scene_fbx")
        try:
            done, _manifest, report = fixtures.run_export([cube], self.tmp)
        finally:
            addon_utils.enable("io_scene_fbx")
        self.assertEqual(done, [])
        self.assertIn("FBX add-on is disabled", fixtures.report_text(report))

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), "permissions not enforced")
    def test_unwritable_folder_gets_a_clear_message(self):
        os.chmod(self.tmp, 0o500)
        try:
            cube = fixtures.make_cube("Crate")
            done, _manifest, report = fixtures.run_export([cube], os.path.join(self.tmp, "sub"))
        finally:
            os.chmod(self.tmp, 0o700)
        self.assertEqual(done, [])
        self.assertIn("permission denied", fixtures.report_text(report))


if __name__ == "__main__":
    unittest.main()
