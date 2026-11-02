# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import bpy

from tests import fixtures


class ConvertSceneTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=False)
        self.units = fixtures.addon_module("core.units")
        self.Report = fixtures.addon_module("core.log").Report

    def convert(self):
        report = self.Report()
        self.units.convert_scene_to_cm(self.scene, report)
        return report

    def test_shared_mesh_is_scaled_once(self):
        cube = fixtures.make_cube("A", size=2.0)
        fixtures.link(bpy.data.objects.new("B", cube.data))
        self.convert()
        self.assertAlmostEqual(max(v.co.x for v in cube.data.vertices), 100.0, places=3)

    def test_object_constraints(self):
        cube = fixtures.make_cube("A")
        distance = cube.constraints.new("LIMIT_DISTANCE")
        distance.distance = 2.0
        location = cube.constraints.new("LIMIT_LOCATION")
        location.max_x, location.min_z = 1.0, -3.0
        floor = cube.constraints.new("FLOOR")
        floor.offset = 0.5
        shrinkwrap = cube.constraints.new("SHRINKWRAP")
        shrinkwrap.distance = 0.25
        stretch = cube.constraints.new("STRETCH_TO")
        stretch.rest_length = 1.5
        pivot = cube.constraints.new("PIVOT")
        pivot.offset = (1, 2, 3)
        self.convert()
        self.assertAlmostEqual(distance.distance, 200.0)
        self.assertAlmostEqual(location.max_x, 100.0)
        self.assertAlmostEqual(location.min_z, -300.0)
        self.assertAlmostEqual(floor.offset, 50.0)
        self.assertAlmostEqual(shrinkwrap.distance, 25.0)
        self.assertAlmostEqual(stretch.rest_length, 150.0)
        fixtures.assert_vec_close(self, pivot.offset, (100, 200, 300))

    def test_bone_constraints(self):
        rig = fixtures.make_armature("Rig", bones=2)
        constraint = rig.pose.bones["bone_01"].constraints.new("LIMIT_DISTANCE")
        constraint.distance = 1.0
        self.convert()
        self.assertAlmostEqual(constraint.distance, 100.0)

    def test_child_of_inverse_matrix(self):
        cube = fixtures.make_cube("A")
        constraint = cube.constraints.new("CHILD_OF")
        matrix = constraint.inverse_matrix.copy()
        matrix.translation = (1, 2, 3)
        constraint.inverse_matrix = matrix
        self.convert()
        fixtures.assert_vec_close(self, constraint.inverse_matrix.translation, (100, 200, 300))

    def test_transform_constraint_only_scales_location_ranges(self):
        cube = fixtures.make_cube("A")
        constraint = cube.constraints.new("TRANSFORM")
        constraint.map_from = "LOCATION"
        constraint.map_to = "ROTATION"
        constraint.from_max_x = 1.0
        constraint.to_max_x_rot = 1.0
        self.convert()
        self.assertAlmostEqual(constraint.from_max_x, 100.0)
        self.assertAlmostEqual(constraint.to_max_x_rot, 1.0)

    def test_action_constraint_on_a_location_channel(self):
        cube = fixtures.make_cube("A")
        constraint = cube.constraints.new("ACTION")
        constraint.transform_channel = "LOCATION_X"
        constraint.min, constraint.max = -1.0, 2.0
        rotation = cube.constraints.new("ACTION")
        rotation.transform_channel = "ROTATION_X"
        rotation.max = 1.0
        self.convert()
        self.assertAlmostEqual(constraint.max, 200.0)
        self.assertAlmostEqual(constraint.min, -100.0)
        self.assertAlmostEqual(rotation.max, 1.0)

    def test_metaballs(self):
        blob = fixtures.make_metaball("Blob")
        element = blob.data.elements[0]
        element.co = (1, 0, 0)
        element.radius = 2.0
        resolution = blob.data.resolution
        report = self.convert()
        self.assertAlmostEqual(element.radius, 200.0)
        fixtures.assert_vec_close(self, element.co, (100, 0, 0))
        self.assertAlmostEqual(blob.data.resolution, resolution * 100.0, places=2)
        self.assertEqual(report.warnings, [])

    def test_curve_bevel_and_text_size(self):
        curve = fixtures.make_curve("Pipe", bevel=0.5)
        text = fixtures.make_text("Sign")
        size = text.data.size
        self.convert()
        self.assertAlmostEqual(curve.data.bevel_depth, 50.0)
        self.assertAlmostEqual(text.data.size, size * 100.0)

    def test_geometry_nodes_distance_input_is_scaled(self):
        compat = fixtures.addon_module("compat")
        cube = fixtures.make_cube("A")
        group, _source, _output = fixtures.new_geometry_group()
        socket = group.interface.new_socket("Offset", in_out="INPUT", socket_type="NodeSocketFloat")
        socket.subtype = "DISTANCE"
        modifier = cube.modifiers.new("GN", "NODES")
        modifier.node_group = group
        compat.set_node_modifier_input(modifier, socket.identifier, 1.5)
        report = self.convert()
        self.assertAlmostEqual(compat.node_modifier_input(modifier, socket.identifier), 150.0)
        self.assertEqual(report.warnings, [])

    def test_geometry_nodes_vector_translation_input_is_scaled(self):
        compat = fixtures.addon_module("compat")
        cube = fixtures.make_cube("A")
        group, _source, _output = fixtures.new_geometry_group()
        socket = group.interface.new_socket("Move", in_out="INPUT", socket_type="NodeSocketVector")
        socket.subtype = "TRANSLATION"
        modifier = cube.modifiers.new("GN", "NODES")
        modifier.node_group = group
        compat.set_node_modifier_input(modifier, socket.identifier, (1.0, 2.0, 3.0))
        self.convert()
        fixtures.assert_vec_close(self, compat.node_modifier_input(modifier, socket.identifier), (100, 200, 300))

    def test_plain_geometry_nodes_number_only_warns(self):
        compat = fixtures.addon_module("compat")
        cube = fixtures.make_cube("A")
        group, _source, _output = fixtures.new_geometry_group()
        socket = group.interface.new_socket("Amount", in_out="INPUT", socket_type="NodeSocketFloat")
        modifier = cube.modifiers.new("GN", "NODES")
        modifier.node_group = group
        compat.set_node_modifier_input(modifier, socket.identifier, 1.5)
        report = self.convert()
        self.assertAlmostEqual(compat.node_modifier_input(modifier, socket.identifier), 1.5)
        self.assertIn("Amount", fixtures.report_text(report))

    def test_modifier_lengths(self):
        cube = fixtures.make_cube("A")
        solidify = cube.modifiers.new("Solidify", "SOLIDIFY")
        solidify.thickness = 0.1
        array = cube.modifiers.new("Array", "ARRAY")
        array.constant_offset_displace = (1, 0, 0)
        self.convert()
        self.assertAlmostEqual(solidify.thickness, 10.0, places=4)
        fixtures.assert_vec_close(self, array.constant_offset_displace, (100, 0, 0))

    def test_parent_inverse_translation(self):
        parent = fixtures.make_empty("Parent", location=(1, 0, 0))
        child = fixtures.make_cube("Child")
        child.parent = parent
        matrix = child.matrix_parent_inverse.copy()
        matrix.translation = (-1, 0, 0)
        child.matrix_parent_inverse = matrix
        self.convert()
        fixtures.assert_vec_close(self, child.matrix_parent_inverse.translation, (-100, 0, 0))

    def test_bone_location_keys(self):
        rig = fixtures.make_armature("Rig", bones=2)
        pose_bone = rig.pose.bones["bone_01"]
        pose_bone.location = (0, 1, 0)
        pose_bone.keyframe_insert("location", frame=1)
        self.convert()
        compat = fixtures.addon_module("compat")
        values = [
            fc.keyframe_points[0].co.y
            for _slot, fc in compat.iter_fcurves(rig.animation_data.action)
            if fc.array_index == 1
        ]
        self.assertAlmostEqual(values[0], 100.0)

    def test_camera_and_light(self):
        camera = fixtures.make_camera()
        camera.data.clip_start = 0.1
        light = fixtures.link(bpy.data.objects.new("Lamp", bpy.data.lights.new("Lamp", "AREA")))
        light.data.size = 2.0
        self.convert()
        self.assertAlmostEqual(camera.data.clip_start, 10.0, places=4)
        self.assertAlmostEqual(light.data.size, 200.0)

    def test_drivers_warn(self):
        cube = fixtures.make_cube("A")
        cube.driver_add("location", 0)
        self.assertIn("driver", fixtures.report_text(self.convert()))

    def test_particles_warn(self):
        cube = fixtures.make_cube("A")
        cube.modifiers.new("Particles", "PARTICLE_SYSTEM")
        self.assertIn("particle", fixtures.report_text(self.convert()))

    def test_unsupported_data_warns(self):
        fixtures.link(bpy.data.objects.new("Points", bpy.data.pointclouds.new("Points")))
        self.assertIn("not rescaled", fixtures.report_text(self.convert()))


if __name__ == "__main__":
    unittest.main()
