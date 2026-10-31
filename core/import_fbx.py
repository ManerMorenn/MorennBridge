# SPDX-License-Identifier: GPL-3.0-or-later
"""Import FBX files coming from Unreal (or MorennBridge) into a clean Blender setup."""

import os
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix

from .. import compat
from ..fbxpatch import inspect as I
from ..fbxpatch import patches, tree
from . import bones, naming, units
from .export_fbx import MARKER_AXES, MARKER_AXES_MAP


@dataclass
class ImportInfo:
    creator: str = ""
    unit_scale: float = 1.0
    marker_axes: str = ""
    marker_axes_map: str = ""
    root_bones: list = field(default_factory=list)
    lod_groups: list = field(default_factory=list)
    anim_stacks: list = field(default_factory=list)
    has_skin: bool = False
    mesh_nodes: list = field(default_factory=list)
    null_nodes: list = field(default_factory=list)

    @property
    def from_blender(self):
        return self.creator.startswith("Blender")

    @property
    def from_unreal(self):
        return "Unreal" in self.creator or self.creator == ""


@dataclass
class ImportResult:
    filepath: str
    info: ImportInfo
    objects: list = field(default_factory=list)
    armatures: list = field(default_factory=list)
    meshes: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    in_scene_units: bool = False


# ---------------------------------------------------------------------- pre-pass


def analyze(root):
    info = ImportInfo()
    header = root.find(b"FBXHeaderExtension")
    creator = None
    if header is not None:
        c = header.find(b"Creator")
        if c is not None and c.props:
            creator = c.props[0]
    if creator is None:
        c = root.find(b"Creator")
        if c is not None and c.props:
            creator = c.props[0]
    if isinstance(creator, bytes):
        info.creator = creator.decode("utf-8", "replace")
    values = I.global_setting_values(root, b"UnitScaleFactor")
    info.unit_scale = float(values[0]) if values else 1.0
    info.marker_axes = patches.get_globalsettings_string(root, MARKER_AXES) or ""
    info.marker_axes_map = patches.get_globalsettings_string(root, MARKER_AXES_MAP) or ""
    info.root_bones = patches.root_bone_names(root)
    info.lod_groups = patches.lod_groups(root)
    info.anim_stacks = [I.object_name(s).decode("utf-8", "replace") for s in I.anim_stacks(root)]
    info.has_skin = bool(I.find_objects(root, b"Deformer", subclass=b"Skin"))
    info.mesh_nodes = I.model_names(root, b"Mesh")
    info.null_nodes = I.model_names(root, b"Null")
    return info


def prepare_file(filepath, report):
    """Analyze and, when needed, write a patched temporary copy. Returns ``(path_to_import, info)``."""
    root, version = tree.load(filepath)
    info = analyze(root)
    if not info.root_bones:
        return filepath, info
    res = patches.retype_root_bones(root)
    tmp_dir = bpy.app.tempdir or os.path.dirname(filepath)
    tmp = os.path.join(tmp_dir, "morenn_" + os.path.basename(filepath))
    tree.save(tmp, root, version)
    report.info(f"Root bone(s) {', '.join(res.notes)} restored as bones (pre-pass)")
    return tmp, info


# ---------------------------------------------------------------------- import


def _snapshot():
    return {
        name: {b.name for b in getattr(bpy.data, name)}
        for name in ("objects", "meshes", "armatures", "actions", "materials", "images", "collections")
    }


def _new_since(snapshot, name):
    return [b for b in getattr(bpy.data, name) if b.name not in snapshot[name]]


def run_importer(context, filepath, prefs, report):
    kw = dict(
        filepath=filepath,
        use_custom_normals=True,
        use_custom_props=True,
        validate_meshes=True,
        ignore_leaf_bones=False,
        use_anim=True,
        anim_offset=float(prefs.import_anim_offset),
    )
    if compat.has_new_fbx_importer():
        kw.update(global_scale=100.0, import_colors="SRGB")
        kw = compat.filter_kwargs(bpy.ops.wm.fbx_import, kw, report)
        with context.temp_override(**compat.window_override()):
            result = bpy.ops.wm.fbx_import(**kw)
        in_scene_units = False
    else:  # legacy Python importer: already converts to scene units, raw joint axes
        kw.update(
            global_scale=1.0,
            colors_type="SRGB",
            automatic_bone_orientation=False,
            primary_bone_axis="Y",
            secondary_bone_axis="X",
            use_manual_orientation=False,
        )
        kw = compat.filter_kwargs(bpy.ops.import_scene.fbx, kw, report)
        with context.temp_override(**compat.window_override()):
            result = bpy.ops.import_scene.fbx(**kw)
        in_scene_units = True
    if "FINISHED" not in result:
        raise RuntimeError(f"FBX importer returned {result}")
    return in_scene_units


def import_file(context, filepath, prefs, report, convert_axes=None, tidy=None, reset_pose=None):
    """Import one FBX. Returns ImportResult. Post-processing follows §9 of the plan."""
    convert_axes = prefs.import_convert_bone_axes if convert_axes is None else convert_axes
    tidy = prefs.import_tidy_hierarchy if tidy is None else tidy
    reset_pose = prefs.import_reset_pose if reset_pose is None else reset_pose

    path, info = prepare_file(filepath, report)
    before = _snapshot()
    try:
        in_scene_units = run_importer(context, path, prefs, report)
    finally:
        if path != filepath:
            try:
                os.remove(path)
            except OSError:
                pass
    result = ImportResult(filepath, info, in_scene_units=in_scene_units)
    result.objects = _new_since(before, "objects")
    result.armatures = [o for o in result.objects if o.type == "ARMATURE"]
    result.meshes = [o for o in result.objects if o.type == "MESH"]
    result.actions = _new_since(before, "actions")
    context.view_layer.update()

    stem = os.path.splitext(os.path.basename(filepath))[0]
    for arm in result.armatures:
        _post_process_armature(context, arm, result, prefs, report, convert_axes, reset_pose, stem)
    _rename_actions(result, stem, report)
    if tidy:
        _tidy(context, result, stem, report)
    if not in_scene_units:
        _rescale_to_scene_units(context, result, report)
    return result


# ---------------------------------------------------------------------- post-processing


def _decide_axes(arm, info, prefs, report):
    if info.marker_axes and len(info.marker_axes) >= 2:
        axes = info.marker_axes
        axes[:-1] if axes.startswith("-") and len(axes) == 3 and False else None
        # marker is "<P><S>" with optional '-' signs, e.g. "XY", "-XY", "X-Z"
        tokens = []
        i = 0
        while i < len(axes):
            if axes[i] == "-":
                tokens.append(axes[i : i + 2])
                i += 2
            else:
                tokens.append(axes[i])
                i += 1
        if len(tokens) == 2 and all(t in bones.AXES for t in tokens):
            return tokens[0], tokens[1], "marker"
    if info.from_blender:
        return "Y", "X", "blender"
    vote = bones.vote_bones_point_to_children(arm)
    if vote is not None and vote > 0.6:
        report.info(f"'{arm.name}': bones already point to their children; no axis conversion")
        return "Y", "X", "vote"
    return prefs.bone_primary, prefs.bone_secondary, "prefs"


def _post_process_armature(context, arm, result, prefs, report, convert_axes, reset_pose, stem):
    info = result.info
    primary, secondary, source = _decide_axes(arm, info, prefs, report)
    default = (primary, secondary)
    converted = False
    axes_map = {b.name: default for b in arm.data.bones}
    if source == "marker":
        axes_map.update({k: v for k, v in bones.parse_axes_map(info.marker_axes_map).items() if k in axes_map})
    elif source in ("prefs", "vote") and default != ("Y", "X"):
        axes_map = bones.detect_bone_axes(arm, default, tolerance_deg=prefs.import_axis_tolerance)
    if convert_axes and any(axes != ("Y", "X") for axes in axes_map.values()):
        K_map = bones.matrices_for(axes_map)
        bones.convert_rest_axes(context, arm, K_map)
        bones.conjugate_pose(arm, K_map)
        for action in result.actions:
            bones.conjugate_bone_fcurves(action, K_map, report)
        converted = True
        n_special = sum(1 for axes in axes_map.values() if axes != default)
        report.info(
            f"'{arm.name}': bone axes converted from ({primary}, {secondary}) ({source})"
            + (f", {n_special} bone(s) with their own axis" if n_special else "")
        )
    bones.store_bone_axes(arm, axes_map, default)
    deform = bones.deform_bone_names(arm)
    bones.recompute_bone_lengths(
        context, arm, prefs, deform_names=deform if deform else None, connect=prefs.import_connect_bones
    )
    bones.assign_bone_collections(arm, deform)
    if reset_pose and not info.anim_stacks and arm.pose is not None:
        for pb in arm.pose.bones:
            if _near_identity(pb.matrix_basis):
                pb.matrix_basis = Matrix.Identity(4)
    m = arm.data.morenn
    m.bone_primary, m.bone_secondary = primary, secondary
    m.axes_converted = converted
    m.source_file = result.filepath
    m.source_creator = info.creator
    arm.show_in_front = True


def _near_identity(m, tol=1e-4):
    ident = Matrix.Identity(4)
    return all(abs(m[i][j] - ident[i][j]) <= tol for i in range(4) for j in range(4))


def _rename_actions(result, stem, report):
    for action in result.actions:
        if action.name.startswith("Unreal Take") or action.name.startswith("Take 001"):
            new_name = stem if len(result.actions) == 1 else f"{stem}_{action.name}"
            action.name = new_name
            action.use_fake_user = True
            report.info(f"Action renamed to '{action.name}'")


def _tidy(context, result, stem, report):
    info = result.info
    base_asset = naming.sanitize_asset_name(stem)
    # skeletal layout: armature named after the asset, meshes <asset>_Mesh
    if result.armatures:
        arm = result.armatures[0]
        skinned = [m for m in result.meshes if any(md.type == "ARMATURE" and md.object == arm for md in m.modifiers)]
        for i, mesh in enumerate(skinned):  # meshes first: the UE mesh node carries the asset name
            mesh.name = f"{base_asset}_Mesh" if i == 0 else f"{base_asset}_Mesh_{i}"
            mesh.data.name = mesh.name
            mesh.morenn.export_name = naming.strip_known_prefix(base_asset, ["SM_", "SKM_", "SK_"])[0]
        if arm.name != base_asset:
            arm.name = base_asset
            arm.data.name = base_asset
    # UE helper nulls (attribute-less parents) without children or data
    for obj in list(result.objects):
        if obj.type == "EMPTY" and not obj.children and not naming.is_socket_name(obj.name):
            bpy.data.objects.remove(obj, do_unlink=True)
            result.objects.remove(obj)
    # LOD groups exported by Unreal: empty <Name> with <Name>_LOD<i> children
    by_name = {o.name: o for o in result.objects}
    for group_name, children in info.lod_groups:
        group = by_name.get(group_name)
        lod_objs = [(naming.parse_lod_name(c), by_name.get(c)) for c in children]
        lod_objs = [(p[1], o) for p, o in lod_objs if p and o is not None]
        if not lod_objs:
            continue
        lod_objs.sort(key=lambda t: t[0])
        lod0 = lod_objs[0][1]
        for index, obj in lod_objs:
            world = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world
            if index == 0:
                obj.name = group_name
                obj.data.name = group_name
            else:
                obj.morenn.role = "LOD"
                obj.morenn.lod_index = index
                obj.morenn.lod_of = lod0
        if group is not None and group.type == "EMPTY":
            bpy.data.objects.remove(group, do_unlink=True)
            result.objects.remove(group)
            by_name.pop(group_name, None)
    # collision meshes exported by Unreal: UCX_<Name>[_LOD0]
    by_name = {o.name: o for o in result.objects}
    for obj in list(result.meshes):
        if obj.name not in by_name:
            continue
        parsed = naming.parse_collision_name(obj.name)
        if not parsed:
            continue
        target = None
        for cand in naming.collision_body_candidates(obj.name):
            target = by_name.get(cand) or by_name.get(
                naming.parse_lod_name(cand)[0] if naming.parse_lod_name(cand) else cand
            )
            if target is not None and target != obj:
                break
        obj.morenn.role = "COLLISION"
        obj.morenn.collision_type = "UCX" if parsed[0] == "MCDCX" else parsed[0]
        obj.display_type = "WIRE"
        obj.hide_render = True
        if target is not None and target != obj:
            obj.morenn.collision_of = target
            world = obj.matrix_world.copy()
            obj.parent = target
            obj.matrix_parent_inverse = Matrix.Identity(4)
            obj.matrix_world = world


def _rescale_to_scene_units(context, result, report):
    """Imported data is in centimetres (1 unit = 1 cm); rescale when the scene uses other units."""
    scene = context.scene
    f = 1.0 / units.unit_factor(scene)  # cm -> scene units
    if abs(f - 1.0) < 1e-6:
        return
    scale = Matrix.Scale(f, 4)
    for obj in result.objects:
        obj.location = obj.location * f
        if obj.parent is not None:
            m = obj.matrix_parent_inverse.copy()
            m.translation = m.translation * f
            obj.matrix_parent_inverse = m
        if obj.type == "MESH":
            obj.data.transform(scale, shape_keys=True)
        elif obj.type == "ARMATURE":
            obj.data.transform(scale)
            if obj.pose is not None:
                for pb in obj.pose.bones:
                    pb.location = pb.location * f
        elif obj.type == "EMPTY":
            obj.empty_display_size *= f
    for action in result.actions:
        for _slot, fc in compat.iter_fcurves(action):
            if fc.data_path in ("location", "delta_location") or fc.data_path.endswith(".location"):
                units._scale_fcurve_values(fc, f)
    report.warning(f"Scene is not in centimetres: imported data scaled by {f:g} (consider 'Set Up Scene for Unreal')")
