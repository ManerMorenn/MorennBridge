# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

import bpy

from tests import fixtures


class SceneSetupTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=False)
        self.prefs = fixtures.addon_module("prefs").get_prefs()
        self.Report = fixtures.addon_module("core.log").Report

    def test_setup_sets_units_fps_and_viewports(self):
        setup = fixtures.addon_module("core.scene_setup")
        fixtures.make_camera()
        report = self.Report()
        setup.apply(bpy.context, self.prefs, report)
        us = self.scene.unit_settings
        self.assertEqual(us.system, "METRIC")
        self.assertAlmostEqual(us.scale_length, 0.01)
        self.assertEqual(us.length_unit, "CENTIMETERS")
        self.assertEqual(self.scene.render.fps, self.prefs.fps)
        self.assertEqual(self.scene.frame_start, 0)
        spaces = list(setup.iter_view3d_spaces())
        self.assertTrue(spaces, "factory screens should contain a 3D view")
        for space in spaces:
            self.assertAlmostEqual(space.clip_start, self.prefs.clip_start)
            self.assertAlmostEqual(space.clip_end, self.prefs.clip_end)
            self.assertAlmostEqual(space.overlay.grid_scale, self.prefs.grid_scale)
        cam = bpy.data.cameras["Camera"]
        self.assertAlmostEqual(cam.clip_end, self.prefs.clip_end)
        self.assertTrue(report.ok)

    def test_operator_logs(self):
        bpy.ops.morenn.scene_setup()
        self.assertTrue(len(self.scene.morenn.log) >= 1)
        self.assertTrue(fixtures.addon_module("core.units").is_cm_scene(self.scene))

    def test_convert_units_scales_everything(self):
        units = fixtures.addon_module("core.units")
        cube = fixtures.make_cube(size=2.0, location=(1, 2, 3))
        rig = fixtures.make_armature(bones=2, bone_length=1.0, location=(0, 0, 1))
        cam = fixtures.make_camera()
        cam.data.clip_end = 100.0
        action = fixtures.key_location(cam, [(1, (0, 0, 0)), (10, (1, 0, 0))])
        report = self.Report()
        f = units.convert_scene_to_cm(self.scene, report)
        self.assertAlmostEqual(f, 100.0)
        self.assertTrue(units.is_cm_scene(self.scene))
        fixtures.assert_vec_close(self, cube.location, (100, 200, 300))
        xs = [v.co.x for v in cube.data.vertices]
        self.assertAlmostEqual(max(xs) - min(xs), 200.0, places=4)
        self.assertAlmostEqual(rig.data.bones["bone_01"].length, 100.0, places=4)
        fixtures.assert_vec_close(self, rig.location, (0, 0, 100))
        self.assertAlmostEqual(cam.data.clip_end, 10000.0)
        compat = fixtures.addon_module("compat")
        values = {}
        for _slot, fc in compat.iter_fcurves(action):
            if fc.data_path == "location" and fc.array_index == 0:
                values = {int(k.co.x): k.co.y for k in fc.keyframe_points}
        self.assertAlmostEqual(values[10], 100.0)
        self.assertTrue(report.ok, [str(e) for e in report.entries])

    def test_convert_units_is_identity_in_cm_scene(self):
        units = fixtures.addon_module("core.units")
        fixtures.fresh_scene(cm=True)
        cube = fixtures.make_cube(size=2.0, location=(1, 2, 3))
        f = units.convert_scene_to_cm(bpy.context.scene, self.Report())
        self.assertAlmostEqual(f, 1.0)
        fixtures.assert_vec_close(self, cube.location, (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
