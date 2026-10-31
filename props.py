# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-datablock settings and runtime state."""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .prefs import BONE_AXIS_ITEMS

LEVEL_ITEMS = [("INFO", "Info", ""), ("WARNING", "Warning", ""), ("ERROR", "Error", "")]
ASSET_TYPE_ITEMS = [
    ("AUTO", "Auto", ""),
    ("STATIC", "Static mesh", ""),
    ("SKELETAL", "Skeletal mesh", ""),
    ("EXCLUDE", "Never export", ""),
]
ROLE_ITEMS = [
    ("AUTO", "Auto (from name)", ""),
    ("RENDER", "Render mesh", ""),
    ("COLLISION", "Collision", ""),
    ("SOCKET", "Socket", ""),
    ("LOD", "LOD", ""),
]
COLLISION_TYPE_ITEMS = [
    ("UCX", "Convex (UCX)", ""),
    ("UBX", "Box (UBX)", ""),
    ("USP", "Sphere (USP)", ""),
    ("UCP", "Capsule (UCP)", ""),
]
BONE_EXPORT_ITEMS = [
    ("AUTO", "Auto (deform + ik_/SOCKET_)", ""),
    ("INCLUDE", "Always export", ""),
    ("EXCLUDE", "Never export", ""),
]
ANIM_SOURCE_ITEMS = [
    ("ACTIVE_ACTION", "Active action", ""),
    ("ALL_ACTIONS", "All actions of the rig", ""),
    ("NLA_STRIPS", "NLA strips", ""),
    ("SCENE_RANGE", "Scene frame range", ""),
]
AXIS_OR_DEFAULT_ITEMS = [("DEFAULT", "Use preference", "")] + BONE_AXIS_ITEMS


class MORENN_PG_logentry(bpy.types.PropertyGroup):
    time: StringProperty()
    level: EnumProperty(items=LEVEL_ITEMS, default="INFO")
    message: StringProperty()
    path: StringProperty(subtype="FILE_PATH")


class MORENN_PG_issue(bpy.types.PropertyGroup):
    code: StringProperty()
    severity: EnumProperty(items=LEVEL_ITEMS, default="WARNING")
    message: StringProperty()
    object_name: StringProperty()
    bone_name: StringProperty()
    fix_id: StringProperty()
    fix_label: StringProperty()


class MORENN_PG_object(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Export", default=True)
    asset_type: EnumProperty(name="Asset type", items=ASSET_TYPE_ITEMS, default="AUTO")
    role: EnumProperty(name="Role", items=ROLE_ITEMS, default="AUTO")
    export_name: StringProperty(name="Export name", description="Base asset name without prefix (empty = object name)")
    destination_path: StringProperty(name="UE destination path", description="Overrides the scene UE content path")
    lod_index: IntProperty(name="LOD index", default=0, min=0, max=7)
    lod_of: PointerProperty(name="LOD of", type=bpy.types.Object)
    collision_of: PointerProperty(name="Collision of", type=bpy.types.Object)
    collision_type: EnumProperty(name="Collision type", items=COLLISION_TYPE_ITEMS, default="UCX")


class MORENN_PG_armature(bpy.types.PropertyGroup):
    bone_primary: EnumProperty(name="Bone primary axis", items=AXIS_OR_DEFAULT_ITEMS, default="DEFAULT")
    bone_secondary: EnumProperty(name="Bone secondary axis", items=AXIS_OR_DEFAULT_ITEMS, default="DEFAULT")
    ue_skeleton_path: StringProperty(name="UE skeleton asset", description="/Game/... path of the Skeleton to reuse")
    source_file: StringProperty(name="Imported from")
    source_creator: StringProperty(name="Source creator")
    axes_converted: BoolProperty(name="Bone axes converted on import", default=False)


class MORENN_PG_bone(bpy.types.PropertyGroup):
    export: EnumProperty(name="Export", items=BONE_EXPORT_ITEMS, default="AUTO")
    axis_primary: EnumProperty(
        name="UE axis along bone",
        items=AXIS_OR_DEFAULT_ITEMS,
        default="DEFAULT",
        description="Unreal-space axis that runs along this bone (DEFAULT = armature convention)",
    )
    axis_secondary: EnumProperty(name="UE secondary axis", items=AXIS_OR_DEFAULT_ITEMS, default="DEFAULT")


class MORENN_PG_scene(bpy.types.PropertyGroup):
    export_dir: StringProperty(name="Export folder", subtype="DIR_PATH", default="//export/")
    ue_content_path: StringProperty(name="UE content path", default="/Game/Imports")
    anim_source: EnumProperty(name="Animations", items=ANIM_SOURCE_ITEMS, default="ACTIVE_ACTION")
    generate_collision_mesh: BoolProperty(
        name="Generate collision mesh",
        default=False,
        description="Export a decimated copy of each static mesh as <asset>_COLL",
    )
    collision_ratio: FloatProperty(
        name="Triangle ratio",
        default=0.1,
        min=0.01,
        max=1.0,
        description="Fraction of triangles kept in the generated collision mesh",
    )
    push_after_export: BoolProperty(name="Push to Unreal after export", default=False)
    issues: CollectionProperty(type=MORENN_PG_issue)
    issues_index: IntProperty(default=0)
    log: CollectionProperty(type=MORENN_PG_logentry)
    log_index: IntProperty(default=0)
    last_manifest: StringProperty(subtype="FILE_PATH")


class MORENN_PG_uenode(bpy.types.PropertyGroup):
    node_id: StringProperty()
    user: StringProperty()
    machine: StringProperty()
    project: StringProperty()
    engine: StringProperty()


class MORENN_PG_runtime(bpy.types.PropertyGroup):
    ue_nodes: CollectionProperty(type=MORENN_PG_uenode)
    ue_selected: IntProperty(default=0)
    ue_status: StringProperty(default="")


classes = (
    MORENN_PG_logentry,
    MORENN_PG_issue,
    MORENN_PG_object,
    MORENN_PG_armature,
    MORENN_PG_bone,
    MORENN_PG_scene,
    MORENN_PG_uenode,
    MORENN_PG_runtime,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.morenn = PointerProperty(type=MORENN_PG_object)
    bpy.types.Armature.morenn = PointerProperty(type=MORENN_PG_armature)
    bpy.types.Bone.morenn = PointerProperty(type=MORENN_PG_bone)
    bpy.types.Scene.morenn = PointerProperty(type=MORENN_PG_scene)
    bpy.types.WindowManager.morenn_rt = PointerProperty(type=MORENN_PG_runtime)


def unregister():
    del bpy.types.WindowManager.morenn_rt
    del bpy.types.Scene.morenn
    del bpy.types.Bone.morenn
    del bpy.types.Armature.morenn
    del bpy.types.Object.morenn
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
