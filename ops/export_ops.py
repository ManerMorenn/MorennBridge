# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile

import bpy
from bpy.props import BoolProperty, EnumProperty

from ..core import classify, export_fbx, naming, validate
from ..core.log import Report
from ..prefs import get_prefs

MODE_ITEMS = [
    ("AUTO", "Auto", "Skinned meshes and armatures become skeletal meshes, everything else static meshes"),
    ("STATIC", "Static meshes", "Force static mesh export"),
    ("SKELETAL", "Skeletal meshes", "Force skeletal mesh export"),
    ("ANIMATION", "Animations", "Export animations of the selected rigs (source chosen in the Export panel)"),
]
SCOPE_ITEMS = [("SELECTED", "Selected objects", ""), ("COLLECTION", "Active collection", "")]


def resolve_export_dir(raw, report=None):
    path = bpy.path.abspath(raw) if raw else ""
    if not raw or (raw.startswith("//") and not bpy.data.filepath):
        path = os.path.join(tempfile.gettempdir(), "morennbridge_export")
        if report is not None:
            report.warning(f"Blend file not saved; exporting to {path}")
    return os.path.normpath(path)


def selected_objects(context):
    """Selected objects even when no screen context is available (scripts, timers, background mode)."""
    try:
        objs = list(context.selected_objects)
    except AttributeError:
        objs = []
    if not objs:
        objs = [o for o in context.view_layer.objects if o.select_get()]
    return objs


def batch_name():
    stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0] if bpy.data.filepath else "export"
    return naming.sanitize_asset_name(stem)


class MORENN_OT_export_ue(bpy.types.Operator):
    """Export the selection as Unreal-ready FBX files (SM_/SKM_ naming, cm units, UE axes, no leaf bones)"""

    bl_idname = "morenn.export_ue"
    bl_label = "Export to Unreal"
    bl_options = {"REGISTER", "UNDO"}

    mode: EnumProperty(name="Mode", items=MODE_ITEMS, default="AUTO")
    scope: EnumProperty(name="Scope", items=SCOPE_ITEMS, default="SELECTED")
    force: BoolProperty(name="Export despite errors", default=False)

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT"

    def execute(self, context):
        prefs = get_prefs(context)
        scene = context.scene
        report = Report("Export")
        if self.scope == "COLLECTION":
            objects = list(context.view_layer.active_layer_collection.collection.all_objects)
        else:
            objects = selected_objects(context)
        settings = export_fbx.ExportSettings.from_prefs(prefs, scene)
        settings.export_dir = resolve_export_dir(scene.morenn.export_dir, report)
        settings.batch_name = batch_name()
        if self.mode == "ANIMATION":
            groups = classify.build_anim_groups(
                objects,
                prefs,
                scene,
                source=scene.morenn.anim_source,
                destination=settings.destination_path,
                include_mesh=prefs.anim_include_mesh,
            )
            if not groups:
                report.error(
                    "No animation to export: select a rig with an active action (or change the animation source)"
                )
                report.flush(scene, self)
                return {"CANCELLED"}
        else:
            groups = classify.build_groups(objects, prefs, scene, mode=self.mode, destination=settings.destination_path)
        for g in groups:
            for w in g.warnings:
                report.warning(w)
        issues = validate.run(context, groups, prefs)
        validate.store(scene, issues)
        for issue in issues:
            if issue.severity == "ERROR":
                report.error(f"{issue.code}: {issue.message}")
            elif issue.severity == "WARNING":
                report.warning(f"{issue.code}: {issue.message}")
        if validate.has_errors(issues) and not self.force:
            report.error(
                "Export aborted: fix the errors listed in the Pre-flight panel (or use 'Export despite errors')"
            )
            report.flush(scene, self)
            return {"CANCELLED"}
        done, manifest_path = export_fbx.export_groups(context, groups, settings, report)
        if manifest_path:
            scene.morenn.last_manifest = manifest_path
        report.title = f"Export: {len(done)} file(s)"
        report.flush(scene, self)
        if done and scene.morenn.push_after_export and len(context.window_manager.morenn_rt.ue_nodes):
            bpy.ops.morenn.ue_push()
        return {"FINISHED"} if done else {"CANCELLED"}


classes = (MORENN_OT_export_ue,)
