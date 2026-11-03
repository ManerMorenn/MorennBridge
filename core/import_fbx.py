# SPDX-License-Identifier: GPL-3.0-or-later
"""Import FBX files coming from Unreal (or MorennBridge) into a clean Blender setup."""

import os
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix

from .. import compat
from ..fbxpatch import ascii as fbx_ascii
from ..fbxpatch import inspect as I
from ..fbxpatch import patches, tree
from . import bones, classify, naming, units
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
    anim_ranges: list = field(default_factory=list)  # (start, stop) seconds per stack, None when not stored
    has_skin: bool = False
    mesh_nodes: list = field(default_factory=list)
    null_nodes: list = field(default_factory=list)
    is_ascii: bool = False

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
    stacks = I.anim_stacks(root)
    info.anim_stacks = [I.object_name(s).decode("utf-8", "replace") for s in stacks]
    info.anim_ranges = [_stack_range(s) for s in stacks]
    info.has_skin = bool(I.find_objects(root, b"Deformer", subclass=b"Skin"))
    info.mesh_nodes = I.model_names(root, b"Mesh")
    info.null_nodes = I.model_names(root, b"Null")
    return info


FBX_TICKS_PER_SECOND = 46186158000


def _stack_range(stack):
    """``(start, stop)`` in seconds from an AnimationStack, or None when the file does not store a stop time."""
    stop = I.prop_values(stack, b"LocalStop") or I.prop_values(stack, b"ReferenceStop")
    if not stop:
        return None
    start = I.prop_values(stack, b"LocalStart") or I.prop_values(stack, b"ReferenceStart") or [0]
    return start[0] / FBX_TICKS_PER_SECOND, stop[0] / FBX_TICKS_PER_SECOND


def _temporary_copy_path(filepath):
    return os.path.join(bpy.app.tempdir or os.path.dirname(filepath), "morenn_" + os.path.basename(filepath))


def _prepare_ascii_file(filepath, report):
    with open(filepath, "rb") as fh:
        data = fh.read()
    if not fbx_ascii.looks_like_fbx(data):
        raise tree.FbxFormatError(f"'{os.path.basename(filepath)}' is not an FBX file")
    info = ImportInfo(
        creator=fbx_ascii.creator(data),
        unit_scale=fbx_ascii.unit_scale(data),
        root_bones=fbx_ascii.root_bone_names(data),
        anim_stacks=fbx_ascii.anim_stacks(data),
        is_ascii=True,
    )
    report.warning(f"'{os.path.basename(filepath)}' is ASCII FBX: LOD groups and bone axis markers are not read")
    if not info.root_bones:
        return filepath, info
    tmp = _temporary_copy_path(filepath)
    with open(tmp, "wb") as fh:
        fh.write(fbx_ascii.retype_root_bones(data))
    report.info(f"Root bone(s) {', '.join(info.root_bones)} restored as bones (pre-pass)")
    return tmp, info


def prepare_file(filepath, report):
    """Analyze and, when needed, write a patched temporary copy. Returns ``(path_to_import, info)``."""
    if not tree.is_binary(filepath):
        return _prepare_ascii_file(filepath, report)
    root, version = tree.load(filepath)
    info = analyze(root)
    if not info.root_bones:
        return filepath, info
    res = patches.retype_root_bones(root)
    tmp = _temporary_copy_path(filepath)
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
        global_scale=100.0,
        import_colors="SRGB",
        use_custom_normals=True,
        use_custom_props=True,
        validate_meshes=True,
        ignore_leaf_bones=False,
        use_anim=True,
        anim_offset=float(prefs.import_anim_offset),
    )
    kw = compat.filter_kwargs(bpy.ops.wm.fbx_import, kw, report)
    with context.temp_override(**compat.window_override()):
        result = bpy.ops.wm.fbx_import(**kw)
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender's FBX importer returned {result}")


def import_file(context, filepath, prefs, report, convert_axes=None, tidy=None, reset_pose=None):
    """Import one FBX and post-process it. Returns ImportResult."""
    convert_axes = prefs.import_convert_bone_axes if convert_axes is None else convert_axes
    tidy = prefs.import_tidy_hierarchy if tidy is None else tidy
    reset_pose = prefs.import_reset_pose if reset_pose is None else reset_pose

    path, info = prepare_file(filepath, report)
    before = _snapshot()
    try:
        run_importer(context, path, prefs, report)
    finally:
        if path != filepath:
            try:
                os.remove(path)
            except OSError:
                pass
    result = ImportResult(filepath, info)
    result.objects = _new_since(before, "objects")
    result.armatures = [o for o in result.objects if o.type == "ARMATURE"]
    result.meshes = [o for o in result.objects if o.type == "MESH"]
    result.actions = _new_since(before, "actions")
    context.view_layer.update()

    stem = os.path.splitext(os.path.basename(filepath))[0]
    for arm in result.armatures:
        _post_process_armature(context, arm, result, prefs, report, convert_axes, reset_pose, stem)
    _apply_take_ranges(context, result, prefs, report)
    _rename_actions(result, stem, report)
    if tidy:
        _tidy(context, result, stem, report)
    _rescale_to_scene_units(context, result, report)
    return result


# ---------------------------------------------------------------------- post-processing


def _decide_axes(arm, info, prefs, report):
    if info.marker_axes and len(info.marker_axes) >= 2:
        axes = info.marker_axes
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


def _apply_take_ranges(context, result, prefs, report):
    """Use the take's time span as the action's frame range when the keys run past it.

    Unreal writes one key after the end of every animation; without this, it would come back one frame longer.
    """
    ranges = {name: span for name, span in zip(result.info.anim_stacks, result.info.anim_ranges) if span}
    if not ranges or not result.actions:
        return
    fps = context.scene.render.fps / context.scene.render.fps_base
    offset = float(prefs.import_anim_offset)
    for action in result.actions:
        span = ranges.get(action.name) or (next(iter(ranges.values())) if len(ranges) == 1 else None)
        if span is None:
            continue
        first, last = round(span[0] * fps + offset, 3), round(span[1] * fps + offset, 3)
        if action.frame_range[1] <= last + 1e-3 or last <= first:
            continue
        action.use_frame_range = True
        action.frame_start, action.frame_end = first, last
        report.info(
            f"'{action.name}': frame range {first:g} to {last:g} from the file (its last key lies past the end)"
        )


def _rename_actions(result, stem, report):
    for action in result.actions:
        if action.name.startswith("Unreal Take") or action.name.startswith("Take 001"):
            new_name = stem if len(result.actions) == 1 else f"{stem}_{action.name}"
            action.name = new_name
            action.use_fake_user = True
            report.info(f"Action renamed to '{action.name}'")


def _lod_indices(info):
    """``{node name: LOD index}`` for the children of the file's LOD groups (name suffix first, else child order)."""
    out = {}
    for _group, children in info.lod_groups:
        for position, child in enumerate(children):
            parsed = naming.parse_lod_name(child)
            out[child] = parsed[1] if parsed else position
    return out


def _tidy(context, result, stem, report):
    info = result.info
    base_asset = naming.sanitize_asset_name(stem)
    # skeletal layout: armature named after the asset, meshes <asset>_Mesh
    if result.armatures:
        arm = result.armatures[0]
        skinned = [m for m in result.meshes if any(md.type == "ARMATURE" and md.object == arm for md in m.modifiers)]
        lod_index = _lod_indices(info)
        meshes = [m for m in skinned if lod_index.get(naming.strip_numeric_suffix(m.name), 0) == 0]
        lods = [(lod_index[naming.strip_numeric_suffix(m.name)], m) for m in skinned if m not in meshes]
        for i, mesh in enumerate(meshes):  # meshes first: the UE mesh node carries the asset name
            mesh.name = f"{base_asset}_Mesh" if i == 0 else f"{base_asset}_Mesh_{i}"
            mesh.data.name = mesh.name
            mesh.morenn.export_name = naming.strip_known_prefix(base_asset, ["SM_", "SKM_", "SK_"])[0]
        for index, mesh in sorted(lods, key=lambda pair: pair[0]):
            mesh.name = mesh.data.name = f"{base_asset}_LOD{index}"
            mesh.morenn.role, mesh.morenn.lod_index = "LOD", index
            mesh.morenn.lod_of = meshes[0] if meshes else None
        if lods:
            report.info(f"'{base_asset}': LOD{'/'.join(str(i) for i, _ in sorted(lods))} kept as LOD objects")
        if arm.name != base_asset:
            arm.name = base_asset
            arm.data.name = base_asset
    # UE helper nulls (attribute-less parents) without children or data
    for obj in list(result.objects):
        if obj.type == "EMPTY" and not obj.children and not naming.is_socket_name(obj.name):
            bpy.data.objects.remove(obj, do_unlink=True)
            result.objects.remove(obj)
    # static LOD groups exported by Unreal: empty <Name> with <Name>_LOD<i> children (skinned LODs are done above)
    by_name = {o.name: o for o in result.objects}
    for group_name, children in info.lod_groups:
        group = by_name.get(group_name)
        lod_objs = [(naming.parse_lod_name(c), by_name.get(c)) for c in children]
        lod_objs = [(p[1], o) for p, o in lod_objs if p and o is not None and not classify.armature_of(o)]
        if not lod_objs:
            continue
        lod_objs.sort(key=lambda t: t[0])
        lod0 = lod_objs[0][1]
        for _index, obj in lod_objs:
            world = obj.matrix_world.copy()
            obj.parent = None
            obj.matrix_world = world
        if group is not None and group.type == "EMPTY":  # frees the name for LOD0
            result.objects.remove(group)
            by_name.pop(group_name, None)
            bpy.data.objects.remove(group, do_unlink=True)
        for index, obj in lod_objs:
            if index == 0:
                obj.name = group_name
                obj.data.name = group_name
            else:
                obj.morenn.role = "LOD"
                obj.morenn.lod_index = index
                obj.morenn.lod_of = lod0
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
