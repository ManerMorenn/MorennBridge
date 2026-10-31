# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences (global defaults) with a headless fallback."""

from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty

ADDON_ID = __package__

BONE_AXIS_ITEMS = [
    ("X", "X", ""),
    ("Y", "Y", ""),
    ("Z", "Z", ""),
    ("-X", "-X", ""),
    ("-Y", "-Y", ""),
    ("-Z", "-Z", ""),
]

FILE_MODE_ITEMS = [
    ("PER_ASSET", "One file per asset", "Every asset (mesh + its collision/LOD/socket children) gets its own FBX"),
    (
        "COMBINED",
        "Combined file",
        "All static meshes of a batch in one FBX; UE creates one asset per node (Interchange)",
    ),
]
LOD_MODE_ITEMS = [
    ("AUTO", "Auto", "LOD naming for Interchange, LodGroup nodes for the legacy importer"),
    ("LOD_NAMING", "LOD<i>_Name nodes", "Interchange name pattern (no LodGroup node, zero risk)"),
    ("LODGROUP_NODES", "FBX LodGroup nodes", "Real LOD group nodes injected after export (both UE importers)"),
    ("NONE", "LOD0 only", "Ignore LOD chains"),
]
UE_IMPORTER_ITEMS = [
    ("INTERCHANGE", "Interchange (UE 5.8 default)", ""),
    ("LEGACY", "Legacy FBX importer", ""),
]
COLOR_SPACE_ITEMS = [("SRGB", "sRGB", ""), ("LINEAR", "Linear", ""), ("NONE", "Do not export", "")]
SMOOTHING_ITEMS = [
    ("SMOOTH_GROUP", "Smoothing groups", "Computed from sharp edges (exact for UE)"),
    ("FACE", "Face", ""),
    ("EDGE", "Edge", ""),
    ("OFF", "Normals only", ""),
]
TEXTURE_ITEMS = [("NONE", "Names only", "Strip texture paths"), ("COPY", "Copy next to FBX", "")]
COLLISION_FILE_ITEMS = [
    ("SAME", "Same file as the mesh", ""),
    ("SEPARATE", "Separate file", "Required for the legacy UE importer"),
]
FRAME_ALIGN_ITEMS = [("NONE", "None (error if misaligned)", ""), ("SNAP_TO_CLOSEST", "Snap to closest frame", "")]


def _p(fn, **kw):
    return fn, kw


PREF_SPECS = {
    # --- naming
    "prefix_static": _p(StringProperty, name="Static mesh prefix", default="SM_"),
    "prefix_skeletal": _p(StringProperty, name="Skeletal mesh prefix", default="SKM_"),
    "prefix_skeleton": _p(StringProperty, name="Skeleton prefix (UE rename)", default="SKEL_"),
    "prefix_physics": _p(StringProperty, name="Physics asset prefix (UE rename)", default="PHYS_"),
    "prefix_anim": _p(StringProperty, name="Animation prefix", default="A_"),
    "known_prefixes": _p(
        StringProperty, name="Known prefixes (stripped before re-prefixing)", default="SM_,SKM_,SK_,SKEL_,A_,AS_,ANIM_"
    ),
    "anim_name_template": _p(StringProperty, name="Animation name template", default="{prefix}{rig}_{action}"),
    "collision_mesh_template": _p(StringProperty, name="Generated collision mesh name", default="{asset}_COLL"),
    # --- export
    "bone_primary": _p(EnumProperty, name="Bone primary axis (along bone)", items=BONE_AXIS_ITEMS, default="X"),
    "bone_secondary": _p(EnumProperty, name="Bone secondary axis", items=BONE_AXIS_ITEMS, default="Y"),
    "influence_limit": _p(IntProperty, name="Max bone influences per vertex", default=8, min=1, max=12),
    "vertex_color_space": _p(EnumProperty, name="Vertex colors", items=COLOR_SPACE_ITEMS, default="SRGB"),
    "smoothing": _p(EnumProperty, name="Smoothing", items=SMOOTHING_ITEMS, default="SMOOTH_GROUP"),
    "triangulate": _p(BoolProperty, name="Triangulate", default=True),
    "export_tangents": _p(BoolProperty, name="Export tangents", default=True),
    "file_mode": _p(EnumProperty, name="Static mesh files", items=FILE_MODE_ITEMS, default="PER_ASSET"),
    "lod_mode": _p(EnumProperty, name="LOD mode", items=LOD_MODE_ITEMS, default="AUTO"),
    "strip_armature_null": _p(
        BoolProperty,
        name="Strip armature null node",
        default=True,
        description="Remove the identity 'Armature' node so the root bone is the FBX root",
    ),
    "anim_include_mesh": _p(BoolProperty, name="Include mesh in animation files", default=False),
    "textures": _p(EnumProperty, name="Textures", items=TEXTURE_ITEMS, default="NONE"),
    "collision_mesh_file": _p(
        EnumProperty, name="Generated collision mesh file", items=COLLISION_FILE_ITEMS, default="SAME"
    ),
    "collision_mesh_min_faces": _p(IntProperty, name="Collision mesh minimum faces", default=12, min=4),
    # --- UE
    "ue_importer": _p(EnumProperty, name="UE importer", items=UE_IMPORTER_ITEMS, default="INTERCHANGE"),
    "ue_content_root": _p(StringProperty, name="Default UE content path", default="/Game/Imports"),
    "ue_build_nanite": _p(BoolProperty, name="Build Nanite", default=False),
    "ue_generate_lightmap_uvs": _p(BoolProperty, name="Generate lightmap UVs", default=False),
    "ue_create_physics_asset": _p(BoolProperty, name="Create physics asset", default=True),
    "ue_import_materials": _p(BoolProperty, name="Import materials", default=True),
    "ue_import_textures": _p(BoolProperty, name="Import textures", default=False),
    "ue_rename_skeleton": _p(BoolProperty, name="Rename skeleton / physics asset with prefixes", default=True),
    "ue_save_assets": _p(BoolProperty, name="Save imported assets", default=True),
    "ue_frame_alignment": _p(EnumProperty, name="Animation frame alignment", items=FRAME_ALIGN_ITEMS, default="NONE"),
    "ue_link_complex_collision": _p(
        BoolProperty, name="Link generated collision mesh as Complex Collision Mesh", default=True
    ),
    "ue_recompute_tangents": _p(BoolProperty, name="Let UE recompute tangents (MikkTSpace)", default=True),
    # --- import
    "import_convert_bone_axes": _p(BoolProperty, name="Convert bone axes on import", default=True),
    "import_bone_angle_tolerance": _p(
        FloatProperty, name="Bone alignment tolerance (deg)", default=15.0, min=0.0, max=90.0
    ),
    "import_axis_tolerance": _p(
        FloatProperty,
        name="Per-bone axis detection tolerance (deg)",
        default=45.0,
        min=5.0,
        max=90.0,
        description="A bone adopts the Unreal axis that points to one of its children within this angle",
    ),
    "import_root_bone_length": _p(FloatProperty, name="Root bone length (cm)", default=10.0, min=0.01),
    "import_helper_bone_length": _p(FloatProperty, name="Helper bone length (cm)", default=5.0, min=0.01),
    "import_min_bone_length": _p(FloatProperty, name="Minimum bone length (cm)", default=0.5, min=0.001),
    "import_reset_pose": _p(BoolProperty, name="Reset pose to rest after mesh import", default=True),
    "import_connect_bones": _p(BoolProperty, name="Connect bones when heads touch tails", default=False),
    "import_tidy_hierarchy": _p(BoolProperty, name="Tidy UE hierarchy (names, collisions, LODs)", default=True),
    "import_anim_offset": _p(FloatProperty, name="Animation frame offset", default=0.0),
    # --- scene setup
    "clip_start": _p(FloatProperty, name="Viewport clip start (cm)", default=0.1, min=1e-6),
    "clip_end": _p(FloatProperty, name="Viewport clip end (cm)", default=1_000_000.0, min=1.0),
    "grid_scale": _p(FloatProperty, name="Grid scale", default=1.0, min=0.0001),
    "fps": _p(IntProperty, name="Frame rate", default=30, min=1, max=240),
    "frame_start_zero": _p(BoolProperty, name="Start frame at 0 (when at the default 1)", default=True),
    "camera_clip": _p(BoolProperty, name="Also set camera clip ranges", default=True),
    # --- remote execution
    "multicast_group": _p(StringProperty, name="Multicast group", default="239.0.0.1"),
    "multicast_port": _p(IntProperty, name="Multicast port", default=6766, min=1, max=65535),
    "bind_address": _p(StringProperty, name="Bind address", default="0.0.0.0"),
    "command_ip": _p(StringProperty, name="Command IP (this machine)", default="127.0.0.1"),
    "discovery_timeout": _p(FloatProperty, name="Discovery timeout (s)", default=3.0, min=0.5),
    "command_timeout": _p(FloatProperty, name="Command timeout (s)", default=600.0, min=1.0),
}


def _default_of(fn, kw):
    if "default" in kw:
        return kw["default"]
    if fn is EnumProperty:
        return kw["items"][0][0]
    return {BoolProperty: False, IntProperty: 0, FloatProperty: 0.0, StringProperty: ""}[fn]


DEFAULTS = SimpleNamespace(**{name: _default_of(fn, kw) for name, (fn, kw) in PREF_SPECS.items()})

_SECTIONS = (
    (
        "Naming",
        (
            "prefix_static",
            "prefix_skeletal",
            "prefix_skeleton",
            "prefix_physics",
            "prefix_anim",
            "known_prefixes",
            "anim_name_template",
            "collision_mesh_template",
        ),
    ),
    (
        "Export",
        (
            "bone_primary",
            "bone_secondary",
            "influence_limit",
            "vertex_color_space",
            "smoothing",
            "triangulate",
            "export_tangents",
            "file_mode",
            "lod_mode",
            "strip_armature_null",
            "anim_include_mesh",
            "textures",
            "collision_mesh_file",
            "collision_mesh_min_faces",
        ),
    ),
    (
        "Unreal import",
        (
            "ue_importer",
            "ue_content_root",
            "ue_build_nanite",
            "ue_generate_lightmap_uvs",
            "ue_create_physics_asset",
            "ue_import_materials",
            "ue_import_textures",
            "ue_rename_skeleton",
            "ue_save_assets",
            "ue_frame_alignment",
            "ue_link_complex_collision",
            "ue_recompute_tangents",
        ),
    ),
    (
        "Import from Unreal",
        (
            "import_convert_bone_axes",
            "import_axis_tolerance",
            "import_bone_angle_tolerance",
            "import_root_bone_length",
            "import_helper_bone_length",
            "import_min_bone_length",
            "import_reset_pose",
            "import_connect_bones",
            "import_tidy_hierarchy",
            "import_anim_offset",
        ),
    ),
    ("Scene setup", ("clip_start", "clip_end", "grid_scale", "fps", "frame_start_zero", "camera_clip")),
    (
        "Unreal remote execution",
        ("multicast_group", "multicast_port", "bind_address", "command_ip", "discovery_timeout", "command_timeout"),
    ),
)


class MORENN_AP_prefs(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    def draw(self, context):
        layout = self.layout
        flow = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False, align=False)
        for title, names in _SECTIONS:
            box = flow.box()
            box.label(text=title)
            col = box.column(align=True)
            for name in names:
                col.prop(self, name)


MORENN_AP_prefs.__annotations__ = {name: fn(**kw) for name, (fn, kw) in PREF_SPECS.items()}


def get_prefs(context=None):
    """Live preferences when the add-on is enabled, otherwise the documented defaults."""
    context = context or bpy.context
    try:
        addon = context.preferences.addons.get(ADDON_ID)
    except AttributeError:
        addon = None
    if addon is not None and addon.preferences is not None:
        return addon.preferences
    return DEFAULTS


classes = (MORENN_AP_prefs,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
