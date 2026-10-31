# SPDX-License-Identifier: GPL-3.0-or-later
import bpy

from .. import compat
from ..core import units
from ..prefs import get_prefs


class MorennPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "MorennBridge"


class MORENN_PT_scene(MorennPanel, bpy.types.Panel):
    bl_label = "Scene"
    bl_idname = "MORENN_PT_scene"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        col = layout.column(align=True)
        if units.is_cm_scene(scene):
            col.label(text="Units: 1 unit = 1 cm (Unreal)", icon="CHECKMARK")
        else:
            col.label(text=f"Units: {units.unit_factor(scene):g} cm per unit", icon="ERROR")
        col.operator("morenn.scene_setup", icon="SCENE_DATA")
        col.operator("morenn.scene_convert_units", icon="FULLSCREEN_ENTER")


class MORENN_PT_export(MorennPanel, bpy.types.Panel):
    bl_label = "Export"
    bl_idname = "MORENN_PT_export"

    def draw(self, context):
        layout = self.layout
        s = context.scene.morenn
        prefs = get_prefs(context)
        col = layout.column(align=True)
        col.prop(s, "export_dir", text="Folder")
        col.prop(s, "ue_content_path", text="UE path")
        if hasattr(prefs, "bl_idname"):
            col.prop(prefs, "file_mode", text="Files")
        box = layout.box()
        row = box.row(align=True)
        row.prop(s, "generate_collision_mesh")
        sub = box.row(align=True)
        sub.enabled = s.generate_collision_mesh
        sub.prop(s, "collision_ratio", slider=True)
        col = layout.column(align=True)
        col.scale_y = 1.3
        col.operator("morenn.export_ue", text="Export Selected", icon="EXPORT").mode = "AUTO"
        row = layout.row(align=True)
        row.operator("morenn.export_ue", text="Static").mode = "STATIC"
        row.operator("morenn.export_ue", text="Skeletal").mode = "SKELETAL"
        box = layout.box()
        box.prop(s, "anim_source", text="Source")
        box.operator("morenn.export_ue", text="Export Animations", icon="ACTION").mode = "ANIMATION"
        row = layout.row(align=True)
        row.operator(
            "morenn.export_ue", text="Export Active Collection", icon="OUTLINER_COLLECTION"
        ).scope = "COLLECTION"


class MORENN_PT_import(MorennPanel, bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "MORENN_PT_import"

    def draw(self, context):
        layout = self.layout
        if not units.is_cm_scene(context.scene):
            layout.label(text="Set up the scene for Unreal first", icon="ERROR")
        layout.operator("morenn.import_ue", icon="IMPORT")


class MORENN_PT_object(MorennPanel, bpy.types.Panel):
    bl_label = "Object"
    bl_idname = "MORENN_PT_object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type in {"MESH", "EMPTY"}

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        m = obj.morenn
        col = layout.column(align=True)
        col.prop(m, "enabled")
        col.prop(m, "asset_type")
        col.prop(m, "role")
        col.prop(m, "export_name", text="Name")
        col.prop(m, "destination_path", text="UE path")
        if m.role == "LOD":
            col.prop(m, "lod_index")
            col.prop(m, "lod_of")
        elif m.role == "COLLISION":
            col.prop(m, "collision_type")
            col.prop(m, "collision_of")
        if obj.type == "MESH":
            box = layout.box()
            box.label(text="Add convention objects")
            row = box.row(align=True)
            row.operator("morenn.add_socket", icon="EMPTY_ARROWS", text="Socket")
            row.operator("morenn.make_lod_chain", icon="MOD_DECIM", text="LOD chain")
            row = box.row(align=True)
            for kind, label in (("UBX", "Box"), ("UCX", "Convex"), ("USP", "Sphere"), ("UCP", "Capsule")):
                row.operator("morenn.add_collision", text=label).kind = kind


class MORENN_PT_armature(MorennPanel, bpy.types.Panel):
    bl_label = "Armature"
    bl_idname = "MORENN_PT_armature"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == "ARMATURE"

    def draw(self, context):
        layout = self.layout
        arm = context.active_object
        m = arm.data.morenn
        col = layout.column(align=True)
        col.prop(m, "bone_primary", text="Primary")
        col.prop(m, "bone_secondary", text="Second")
        col.prop(m, "ue_skeleton_path", text="Skeleton")
        roots = [b.name for b in arm.data.bones if b.parent is None]
        col.label(
            text=f"Root bone(s): {', '.join(roots) if roots else 'none'}",
            icon="CHECKMARK" if len(roots) == 1 else "ERROR",
        )
        if m.source_file:
            col.label(text=f"From: {m.source_file.rsplit('/', 1)[-1]}", icon="IMPORT")
        layout.operator("morenn.convert_bone_axes", icon="ORIENTATION_GIMBAL")
        bone = context.active_bone
        if bone is not None and hasattr(bone, "morenn"):
            box = layout.box()
            box.label(text=f"Bone '{bone.name}'")
            box.prop(bone.morenn, "export")
            row = box.row(align=True)
            row.prop(bone.morenn, "axis_primary", text="Axis")
            row.prop(bone.morenn, "axis_secondary", text="")


class MORENN_PT_preflight(MorennPanel, bpy.types.Panel):
    bl_label = "Pre-flight"
    bl_idname = "MORENN_PT_preflight"

    def draw(self, context):
        layout = self.layout
        s = context.scene.morenn
        row = layout.row(align=True)
        row.operator("morenn.validate", icon="CHECKMARK")
        if not len(s.issues):
            layout.label(text="No issues recorded", icon="INFO")
            return
        layout.template_list("MORENN_UL_issues", "", s, "issues", s, "issues_index", rows=4)
        if 0 <= s.issues_index < len(s.issues):
            item = s.issues[s.issues_index]
            box = layout.box()
            box.label(text=item.message)
            row = box.row(align=True)
            if item.fix_id:
                row.operator("morenn.fix_issue", text=item.fix_label or "Fix", icon="MODIFIER").index = s.issues_index
        row = layout.row(align=True)
        row.operator("morenn.fix_all", icon="MODIFIER")
        row.operator("morenn.select_offenders", icon="RESTRICT_SELECT_OFF")


class MORENN_PT_unreal(MorennPanel, bpy.types.Panel):
    bl_label = "Unreal Link"
    bl_idname = "MORENN_PT_unreal"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        rt = context.window_manager.morenn_rt
        if not compat.online_access_allowed():
            layout.label(text="Enable 'Allow Online Access' in Preferences > System", icon="ERROR")
            layout.enabled = False
        row = layout.row(align=True)
        row.operator("morenn.ue_discover", icon="VIEWZOOM")
        row.operator("morenn.ue_setup_help", text="", icon="QUESTION")
        if rt.ue_status:
            layout.label(text=rt.ue_status)
        if len(rt.ue_nodes):
            layout.template_list("MORENN_UL_uenodes", "", rt, "ue_nodes", rt, "ue_selected", rows=2)
        col = layout.column(align=True)
        col.prop(context.scene.morenn, "push_after_export")
        col.operator("morenn.ue_push", icon="EXPORT")
        col.operator("morenn.ue_pull", icon="IMPORT")


class MORENN_PT_log(MorennPanel, bpy.types.Panel):
    bl_label = "Log"
    bl_idname = "MORENN_PT_log"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        s = context.scene.morenn
        layout.template_list("MORENN_UL_log", "", s, "log", s, "log_index", rows=4)
        row = layout.row(align=True)
        row.operator("morenn.log_clear", icon="TRASH", text="Clear")
        row.operator("morenn.log_copy", icon="COPYDOWN", text="Copy")
        row.operator("morenn.log_open", icon="FILE_FOLDER", text="Open")


classes = (
    MORENN_PT_scene,
    MORENN_PT_export,
    MORENN_PT_import,
    MORENN_PT_object,
    MORENN_PT_armature,
    MORENN_PT_preflight,
    MORENN_PT_unreal,
    MORENN_PT_log,
)
