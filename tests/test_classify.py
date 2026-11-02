# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import bpy

from tests import fixtures
from tests.test_export_skeletal import _rigged_character


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=True)
        self.classify = fixtures.addon_module("core.classify")
        self.prefs = fixtures.addon_module("prefs").get_prefs()

    def groups(self, objects, mode="AUTO"):
        return self.classify.build_groups(objects, self.prefs, self.scene, mode=mode)

    def split(self, objects):
        return self.classify.split_selection(objects, bpy.context.view_layer, bpy.context.evaluated_depsgraph_get())

    def test_plain_mesh_is_static(self):
        cube = fixtures.make_cube("Crate")
        group = self.groups([cube])[0]
        self.assertEqual(group.kind, self.classify.KIND_STATIC)
        self.assertEqual(group.asset_name, "SM_Crate")

    def test_prefixed_name_is_not_prefixed_twice(self):
        self.assertEqual(self.groups([fixtures.make_cube("SM_Crate.002")])[0].asset_name, "SM_Crate")

    def test_export_name_overrides_the_object_name(self):
        cube = fixtures.make_cube("Crate")
        cube.morenn.export_name = "Box"
        self.assertEqual(self.groups([cube])[0].asset_name, "SM_Box")

    def test_skinned_mesh_groups_on_its_armature(self):
        rig, body = _rigged_character("Hero")
        for selection in ([rig], [body]):
            group = self.groups(selection)[0]
            self.assertEqual(group.kind, self.classify.KIND_SKELETAL)
            self.assertEqual(group.asset_name, "SKM_Hero")
            self.assertEqual(group.armature, rig)
            self.assertEqual(group.render, [body])

    def test_rig_and_mesh_selected_together_give_one_group(self):
        rig, body = _rigged_character("Hero")
        self.assertEqual(len(self.groups([rig, body])), 1)

    def test_forced_static_mode_on_a_skinned_mesh(self):
        _rig, body = _rigged_character("Hero")
        self.assertEqual(self.groups([body], mode="STATIC")[0].kind, self.classify.KIND_STATIC)

    def test_collision_found_by_name_parent_and_pointer(self):
        cube = fixtures.make_cube("Crate")
        fixtures.make_cube("UCX_Crate_01")
        by_parent = fixtures.make_cube("UBX_Other")
        by_parent.parent = cube
        by_pointer = fixtures.make_cube("Shell")
        by_pointer.morenn.role = "COLLISION"
        by_pointer.morenn.collision_of = cube
        by_pointer.morenn.collision_type = "USP"
        group = self.groups([cube])[0]
        self.assertEqual(
            {(obj.name, kind) for obj, kind in group.collisions},
            {("UCX_Crate_01", "UCX"), ("UBX_Other", "UBX"), ("Shell", "USP")},
        )

    def test_selected_satellites_do_not_become_assets(self):
        cube = fixtures.make_cube("Crate")
        collision = fixtures.make_cube("UCX_Crate_01")
        lod = fixtures.make_cube("Crate_LOD1")
        self.assertEqual([g.asset_name for g in self.groups([cube, collision, lod])], ["SM_Crate"])

    def test_lods_by_name_and_by_pointer(self):
        cube = fixtures.make_cube("Rock")
        named = fixtures.make_cube("Rock_LOD1")
        pointed = fixtures.make_cube("Simpler")
        pointed.morenn.role = "LOD"
        pointed.morenn.lod_of = cube
        pointed.morenn.lod_index = 2
        group = self.groups([cube])[0]
        self.assertEqual(group.lods, {1: [named], 2: [pointed]})
        self.assertEqual(group.lod_count, 3)

    def test_sockets_must_be_children(self):
        cube = fixtures.make_cube("Crate")
        child = fixtures.make_empty("SOCKET_Lid", parent=cube)
        fixtures.make_empty("SOCKET_Elsewhere")
        self.assertEqual(self.groups([cube])[0].sockets, [child])

    def test_never_export_and_disabled_objects_are_left_out(self):
        excluded = fixtures.make_cube("Excluded")
        excluded.morenn.asset_type = "EXCLUDE"
        disabled = fixtures.make_cube("Disabled")
        disabled.morenn.enabled = False
        self.assertEqual(self.groups([excluded, disabled]), [])
        self.assertEqual(self.split([excluded, disabled]).objects, [])

    def test_disabled_satellite_is_left_out(self):
        cube = fixtures.make_cube("Crate")
        fixtures.make_cube("UCX_Crate_01").morenn.enabled = False
        self.assertEqual(self.groups([cube])[0].collisions, [])

    def test_curves_text_and_metaballs_are_static_assets(self):
        objects = [fixtures.make_curve("Pipe"), fixtures.make_text("Sign"), fixtures.make_metaball("Blob")]
        split = self.split(objects)
        self.assertEqual(split.objects, objects)
        groups = self.groups(split.objects)
        self.assertEqual({g.asset_name for g in groups}, {"SM_Pipe", "SM_Sign", "SM_Blob"})
        self.assertTrue(all(g.kind == self.classify.KIND_STATIC for g in groups))

    def test_curve_without_surface_is_reported(self):
        path = fixtures.make_curve("Path", bevel=0.0)
        split = self.split([path])
        self.assertEqual(split.objects, [])
        self.assertEqual(split.no_surface, [path])

    def test_collection_instance_is_a_static_asset(self):
        instance = fixtures.make_collection_instance("Kit")
        self.assertTrue(self.classify.is_collection_instance(instance))
        self.assertEqual(self.groups(self.split([instance]).objects)[0].asset_name, "SM_Kit")

    def test_plain_empty_lights_and_cameras_are_ignored_without_noise(self):
        split = self.split([fixtures.make_empty("Null"), fixtures.make_camera()])
        self.assertEqual((split.objects, split.hidden, split.no_surface, split.unsupported), ([], [], [], []))

    def test_unsupported_geometry_is_reported(self):
        pointcloud = bpy.data.objects.new("Points", bpy.data.pointclouds.new("Points"))
        fixtures.link(pointcloud)
        self.assertEqual(self.split([pointcloud]).unsupported, [pointcloud])

    def test_hidden_assets_are_reported(self):
        by_eye = fixtures.make_cube("ByEye")
        by_eye.hide_set(True)
        by_monitor = fixtures.make_cube("ByMonitor")
        by_monitor.hide_viewport = True
        split = self.split([by_eye, by_monitor])
        self.assertEqual(split.objects, [])
        self.assertEqual(split.hidden, [by_eye, by_monitor])

    def test_assets_in_excluded_collections_are_hidden(self):
        collection = bpy.data.collections.new("Off")
        self.scene.collection.children.link(collection)
        cube = fixtures.make_cube("Inside")
        self.scene.collection.objects.unlink(cube)
        collection.objects.link(cube)
        bpy.context.view_layer.layer_collection.children["Off"].exclude = True
        self.assertEqual(self.split([cube]).hidden, [cube])

    def test_hidden_satellites_stay(self):
        cube = fixtures.make_cube("Crate")
        collision = fixtures.make_cube("UCX_Crate_01")
        collision.hide_set(True)
        self.assertIn(collision, self.split([cube, collision]).objects)
        self.assertEqual(len(self.groups([cube])[0].collisions), 1)

    def test_hidden_skinned_meshes_are_left_out_of_the_rig(self):
        rig, body = _rigged_character("Hero")
        body.hide_set(True)
        self.assertEqual(self.groups([rig])[0].render, [])


if __name__ == "__main__":
    unittest.main()
