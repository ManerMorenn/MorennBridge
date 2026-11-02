# SPDX-License-Identifier: GPL-3.0-or-later
"""Panels drawn against a recording layout: every operator, property and list they name has to exist."""

import types
import unittest

import bpy

from tests import fixtures
from tests.test_export_skeletal import _rigged_character


class Layout:
    def __init__(self, case):
        self.case = case
        self.enabled = True
        self.active = True
        self.scale_y = 1.0
        self.use_property_split = False
        self.use_property_decorate = True
        self.operators = []
        self.labels = []
        self.children = []

    def _child(self, *_args, **_kwargs):
        child = Layout(self.case)
        self.children.append(child)
        return child

    column = row = box = split = grid_flow = column_flow = _child

    def label(self, text="", icon="NONE"):
        self.labels.append(text)

    def separator(self, **_kwargs):
        pass

    def prop(self, data, name, **_kwargs):
        self.case.assertIn(name, data.bl_rna.properties, f"{type(data).__name__}.{name}")

    def operator(self, idname, **_kwargs):
        module, name = idname.split(".")
        operator = getattr(getattr(bpy.ops, module), name)
        known = {p.identifier for p in operator.get_rna_type().properties}
        self.operators.append(idname)
        return _OperatorProperties(self.case, known)

    def template_list(self, list_type, _list_id, data, prop, active_data, active_prop, **_kwargs):
        self.case.assertTrue(hasattr(bpy.types, list_type), list_type)
        self.case.assertIn(prop, data.bl_rna.properties)
        self.case.assertIn(active_prop, active_data.bl_rna.properties)

    def all_operators(self):
        found = list(self.operators)
        for child in self.children:
            found.extend(child.all_operators())
        return found

    def all_labels(self):
        found = list(self.labels)
        for child in self.children:
            found.extend(child.all_labels())
        return found


class _OperatorProperties:
    def __init__(self, case, known):
        object.__setattr__(self, "case", case)
        object.__setattr__(self, "known", known)

    def __setattr__(self, name, value):
        self.case.assertIn(name, self.known)


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=True)
        self.panels = fixtures.addon_module("ui.panels")

    def context(self, active_object=None, active_bone=None):
        return types.SimpleNamespace(
            scene=self.scene,
            active_object=active_object,
            active_bone=active_bone,
            window_manager=bpy.context.window_manager,
            preferences=bpy.context.preferences,
        )

    def draw(self, panel, context):
        layout = Layout(self)
        panel.draw(types.SimpleNamespace(layout=layout), context)
        return layout

    def test_every_panel_draws_with_an_empty_scene(self):
        context = self.context()
        for panel in self.panels.classes:
            if getattr(panel, "poll", None) is None or panel.poll(context):
                self.draw(panel, context)

    def test_scene_panel_reports_units(self):
        self.assertIn(
            "Units: 1 unit = 1 cm (Unreal)", self.draw(self.panels.MORENN_PT_scene, self.context()).all_labels()
        )
        self.scene = fixtures.fresh_scene(cm=False)
        self.assertIn("Units: 100 cm per unit", self.draw(self.panels.MORENN_PT_scene, self.context()).all_labels())

    def test_import_panel_warns_about_units(self):
        self.scene = fixtures.fresh_scene(cm=False)
        self.assertIn(
            "Set up the scene for Unreal first", self.draw(self.panels.MORENN_PT_import, self.context()).all_labels()
        )

    def test_object_panel_polls_for_exportable_objects(self):
        panel = self.panels.MORENN_PT_object
        self.assertFalse(panel.poll(self.context()))
        self.assertFalse(panel.poll(self.context(fixtures.make_camera())))
        for obj in (fixtures.make_cube("Crate"), fixtures.make_curve("Pipe"), fixtures.make_empty("Null")):
            self.assertTrue(panel.poll(self.context(obj)), obj.name)

    def test_object_panel_shows_role_settings(self):
        cube = fixtures.make_cube("Crate")
        for role in ("AUTO", "LOD", "COLLISION"):
            cube.morenn.role = role
            layout = self.draw(self.panels.MORENN_PT_object, self.context(cube))
            self.assertIn("morenn.add_socket", layout.all_operators())

    def test_armature_panel(self):
        rig, _body = _rigged_character("Hero")
        rig.data.morenn.source_file = "/tmp/SKM_Hero.fbx"
        bone = rig.data.bones["root"]
        panel = self.panels.MORENN_PT_armature
        self.assertTrue(panel.poll(self.context(rig)))
        layout = self.draw(panel, self.context(rig, bone))
        self.assertIn("morenn.convert_bone_axes", layout.all_operators())
        self.assertIn("Bone 'root'", layout.all_labels())
        self.assertIn("From: SKM_Hero.fbx", layout.all_labels())

    def test_preflight_without_issues(self):
        layout = self.draw(self.panels.MORENN_PT_preflight, self.context())
        self.assertIn("No issues recorded", layout.all_labels())

    def test_preflight_offers_export_anyway_only_for_errors(self):
        issue = self.scene.morenn.issues.add()
        issue.code, issue.severity, issue.message = "W005", "WARNING", "no uv"
        layout = self.draw(self.panels.MORENN_PT_preflight, self.context())
        self.assertNotIn("morenn.export_ue", layout.all_operators())
        issue = self.scene.morenn.issues.add()
        issue.code, issue.severity, issue.message, issue.fix_id = "E003", "ERROR", "mirrored", "apply_scale"
        self.scene.morenn.issues_index = 1
        layout = self.draw(self.panels.MORENN_PT_preflight, self.context())
        self.assertIn("morenn.export_ue", layout.all_operators())
        self.assertIn("morenn.fix_issue", layout.all_operators())

    def test_unreal_panel_with_an_editor(self):
        runtime = bpy.context.window_manager.morenn_rt
        runtime.ue_nodes.clear()
        runtime.ue_nodes.add()
        runtime.ue_status = "1 editor(s) found"
        layout = self.draw(self.panels.MORENN_PT_unreal, self.context())
        self.assertIn("1 editor(s) found", layout.all_labels())
        runtime.ue_nodes.clear()
        runtime.ue_status = ""

    def test_log_panel(self):
        self.draw(self.panels.MORENN_PT_log, self.context())

    def test_lists(self):
        lists = fixtures.addon_module("ui.lists")
        log = self.scene.morenn.log.add()
        issue = self.scene.morenn.issues.add()
        issue.object_name = "Crate"
        node = bpy.context.window_manager.morenn_rt.ue_nodes.add()
        for draw_list, item in (
            (lists.MORENN_UL_log, log),
            (lists.MORENN_UL_issues, issue),
            (lists.MORENN_UL_uenodes, node),
        ):
            draw_list.draw_item(None, bpy.context, Layout(self), None, item, 0, None, "", 0)
        bpy.context.window_manager.morenn_rt.ue_nodes.clear()

    def test_preferences_draw(self):
        prefs_module = fixtures.addon_module("prefs")
        prefs = prefs_module.get_prefs()
        if not hasattr(prefs, "bl_rna"):
            self.skipTest("the add-on was loaded from the source tree, without preferences")
        stand_in = types.SimpleNamespace(layout=Layout(self), bl_rna=prefs.bl_rna)
        prefs_module.MORENN_AP_prefs.draw(stand_in, bpy.context)


if __name__ == "__main__":
    unittest.main()
