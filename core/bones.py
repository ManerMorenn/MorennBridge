# SPDX-License-Identifier: GPL-3.0-or-later
"""Bone-axis conventions, exact per-bone conversion of rest/pose/animation data, bone lengths, collections.

Every bone may use its own Unreal-space axis pair ``(primary, secondary)``: ``primary`` is the Unreal axis that runs
along the bone (becomes Blender's bone Y), ``secondary`` becomes Blender's bone X. Conversions are exact because
Blender bone-local spaces are independent: for a rest change ``M' = M @ K`` the pose/keys change by
``P' = K⁻¹ P K`` for that bone only.
"""

import math
import re

import bpy
import numpy as np
from mathutils import Matrix, Vector

from .. import compat

AXES = ("X", "Y", "Z", "-X", "-Y", "-Z")
AXIS_VECTORS = {
    "X": Vector((1, 0, 0)),
    "-X": Vector((-1, 0, 0)),
    "Y": Vector((0, 1, 0)),
    "-Y": Vector((0, -1, 0)),
    "Z": Vector((0, 0, 1)),
    "-Z": Vector((0, 0, -1)),
}
HELPER_PREFIXES = ("ik_", "IK_", "SOCKET_", "VB ", "VB_")
_BONE_PATH = re.compile(
    r'^pose\.bones\["(?P<bone>.+?)"\]\.(?P<prop>location|rotation_quaternion|rotation_euler|scale)$'
)


# ---------------------------------------------------------------------- matrices


def import_matrix(primary, secondary):
    """``K``: a raw FBX joint matrix ``J`` becomes the Blender bone matrix ``J @ K``.

    Blender's bone Y (along the bone) takes the Unreal axis ``primary``, Blender's bone X takes ``secondary``,
    Blender's bone Z completes a right-handed frame. Columns of K are therefore (s, p, s × p).
    For ('X', 'Y') this is the symmetric X/Y swap Blender's own exporter uses.
    """
    p_vec = AXIS_VECTORS[primary]
    s_vec = AXIS_VECTORS[secondary]
    if abs(p_vec.dot(s_vec)) > 1e-6:
        s_vec = AXIS_VECTORS[default_secondary(primary)]
    t_vec = s_vec.cross(p_vec)
    return Matrix(((s_vec.x, p_vec.x, t_vec.x), (s_vec.y, p_vec.y, t_vec.y), (s_vec.z, p_vec.z, t_vec.z))).to_4x4()


def correction_matrix(primary, secondary):
    """``C = K⁻¹``: FBX bone matrix = Blender bone matrix @ C (what the export applies per bone)."""
    return import_matrix(primary, secondary).inverted()


def default_secondary(primary):
    """A secondary axis that is never colinear with ``primary``."""
    return "X" if primary in ("Y", "-Y") else "Y"


def signed_permutation(m3):
    """For a signed permutation matrix: ``(M v)[i] == sign[i] * v[perm[i]]``."""
    perm, sign = [0, 0, 0], [1.0, 1.0, 1.0]
    for i in range(3):
        row = [m3[i][0], m3[i][1], m3[i][2]]
        j = max(range(3), key=lambda k: abs(row[k]))
        if abs(abs(row[j]) - 1.0) > 1e-6 or abs(sum(abs(v) for v in row) - 1.0) > 1e-6:
            raise ValueError("matrix is not a signed axis permutation")
        perm[i] = j
        sign[i] = 1.0 if row[j] > 0 else -1.0
    return perm, sign


def _as_map(K, names):
    """Accept a single matrix or a ``{bone: matrix}`` map."""
    if isinstance(K, dict):
        return K
    return {n: K for n in names}


# ---------------------------------------------------------------------- per-bone axis maps


def detect_bone_axes(arm_obj, default, tolerance_deg=45.0):
    """``{bone: (primary, secondary)}`` from the raw (un-converted) rest pose.

    Rule per bone: keep ``default`` when a child lies within ``tolerance_deg`` of the default axis; otherwise take the
    axis best aligned with a child (within tolerance); otherwise inherit the parent's choice (leaf bones), else default.
    """
    cos_tol = math.cos(math.radians(tolerance_deg))
    default_vec = AXIS_VECTORS[default[0]]
    result = {}

    def depth(b):
        d = 0
        while b.parent is not None:
            b = b.parent
            d += 1
        return d

    for bone in sorted(arm_obj.data.bones, key=depth):
        inv = bone.matrix_local.to_3x3().inverted()
        dirs = []
        for child in bone.children:
            d = child.head_local - bone.head_local
            if d.length > 1e-4:
                dirs.append((inv @ d).normalized())
        chosen = None
        if dirs and max(d.dot(default_vec) for d in dirs) >= cos_tol:
            chosen = tuple(default)
        elif dirs:
            best_axis, best_dot = None, -2.0
            for d in dirs:
                for axis, vec in AXIS_VECTORS.items():
                    dot = d.dot(vec)
                    if dot > best_dot:
                        best_axis, best_dot = axis, dot
            if best_dot >= cos_tol:
                chosen = (best_axis, default_secondary(best_axis))
        if chosen is None:
            chosen = result.get(bone.parent.name, tuple(default)) if bone.parent is not None else tuple(default)
        result[bone.name] = chosen
    return result


def axes_map_from_stored(arm_obj, default):
    """Per-bone axes as stored on the bones (``DEFAULT`` resolves to the armature convention)."""
    out = {}
    for bone in arm_obj.data.bones:
        m = bone.morenn
        p = default[0] if m.axis_primary == "DEFAULT" else m.axis_primary
        s = default[1] if m.axis_secondary == "DEFAULT" else m.axis_secondary
        if p == s or p == ("-" + s) or s == ("-" + p):
            s = default_secondary(p)
        out[bone.name] = (p, s)
    return out


def store_bone_axes(arm_obj, axes_map, default):
    for bone in arm_obj.data.bones:
        axes = axes_map.get(bone.name, tuple(default))
        bone.morenn.axis_primary = "DEFAULT" if axes == tuple(default) else axes[0]
        bone.morenn.axis_secondary = "DEFAULT" if axes == tuple(default) else axes[1]


def clear_bone_axes(arm_obj):
    for bone in arm_obj.data.bones:
        bone.morenn.axis_primary = "DEFAULT"
        bone.morenn.axis_secondary = "DEFAULT"


def matrices_for(axes_map, inverse=False):
    """``{bone: K}`` (import) or ``{bone: C}`` (export, ``inverse=True``)."""
    fn = correction_matrix if inverse else import_matrix
    return {name: fn(p, s) for name, (p, s) in axes_map.items()}


def serialize_axes_map(axes_map, default):
    """Compact text for the FBX marker: only bones that differ from the default, ``name:P,S;...``."""
    parts = [f"{name}:{p},{s}" for name, (p, s) in sorted(axes_map.items()) if (p, s) != tuple(default)]
    return ";".join(parts)


def parse_axes_map(text):
    out = {}
    if not text:
        return out
    for part in text.split(";"):
        if ":" not in part:
            continue
        name, axes = part.rsplit(":", 1)
        if "," not in axes:
            continue
        p, s = axes.split(",", 1)
        if p in AXES and s in AXES:
            out[name] = (p, s)
    return out


# ---------------------------------------------------------------------- rest / pose conversion


def _edit_override(context, arm_obj):
    kw = {
        "active_object": arm_obj,
        "object": arm_obj,
        "selected_objects": [arm_obj],
        "selected_editable_objects": [arm_obj],
    }
    kw.update(compat.window_override())
    return kw


def convert_rest_axes(context, arm_obj, K, extra_override=None):
    """Right-multiply edit-bone matrices by ``K`` (matrix or per-bone map); lengths preserved, bones disconnected."""
    override = _edit_override(context, arm_obj)
    if extra_override:
        override.update(extra_override)
    else:
        context.view_layer.objects.active = arm_obj
    with context.temp_override(**override):
        bpy.ops.object.mode_set(mode="EDIT")
        ebs = arm_obj.data.edit_bones
        kmap = _as_map(K, [eb.name for eb in ebs])
        for eb in ebs:
            eb.use_connect = False
        for eb in ebs:
            k = kmap.get(eb.name)
            if k is None:
                continue
            length = eb.length
            eb.matrix = eb.matrix @ k
            eb.length = length
        bpy.ops.object.mode_set(mode="OBJECT")


def conjugate_pose(arm_obj, K):
    if arm_obj.pose is None:
        return
    kmap = _as_map(K, [pb.name for pb in arm_obj.pose.bones])
    for pb in arm_obj.pose.bones:
        k = kmap.get(pb.name)
        if k is not None:
            pb.matrix_basis = k.inverted() @ pb.matrix_basis @ k


def _group_bone_fcurves(action):
    groups = {}
    bags = {}
    for slot, fcurve in compat.iter_fcurves(action):
        m = _BONE_PATH.match(fcurve.data_path)
        if not m:
            continue
        key = slot.identifier if slot is not None else ""
        bags[key] = (slot, compat.action_channelbag(action, slot) if slot is not None else action)
        groups.setdefault((key, m.group("bone"), m.group("prop")), {})[fcurve.array_index] = fcurve
    return groups, bags


def _curve_arrays(fc):
    n = len(fc.keyframe_points)
    out = {}
    for attr in ("co", "handle_left", "handle_right"):
        buf = np.empty(n * 2, dtype=np.float64)
        fc.keyframe_points.foreach_get(attr, buf)
        out[attr] = buf.reshape(n, 2)
    return out


def _same_frames(arrays):
    frames = [a["co"][:, 0] for a in arrays]
    return all(len(f) == len(frames[0]) and np.allclose(f, frames[0]) for f in frames)


def _resample(fcurves):
    frames = sorted({float(k.co.x) for fc in fcurves.values() for k in fc.keyframe_points})
    out = {}
    for i, fc in fcurves.items():
        values = [fc.evaluate(f) for f in frames]
        fc.keyframe_points.clear()
        fc.keyframe_points.add(len(frames))
        co = np.array([(f, v) for f, v in zip(frames, values)], dtype=np.float64)
        fc.keyframe_points.foreach_set("co", co.ravel())
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
        fc.update()
        out[i] = _curve_arrays(fc)
    return out


def _write_arrays(fc, arrays):
    for attr, arr in arrays.items():
        fc.keyframe_points.foreach_set(attr, np.ascontiguousarray(arr).ravel())
    fc.update()


def _permute_vector_channels(channels, perm, sign, scale_values=True):
    """channel i takes channel perm[i] (values and handle Y multiplied by sign[i])."""
    if set(channels) != {0, 1, 2}:
        return False
    arrays = {i: _curve_arrays(channels[i]) for i in range(3)}
    if not _same_frames(list(arrays.values())):
        arrays = _resample(channels)
    new = {}
    for i in range(3):
        src = {k: v.copy() for k, v in arrays[perm[i]].items()}
        if scale_values:
            for k in src:
                src[k][:, 1] *= sign[i]
        new[i] = src
    for i in range(3):
        _write_arrays(channels[i], new[i])
    return True


def conjugate_bone_fcurves(action, K, report=None, bone_names=None):
    """Transform bone-space F-Curves of ``action`` for a rest change ``M' = M @ K`` (``K``: matrix or per-bone map)."""
    groups, bags = _group_bone_fcurves(action)
    names = bone_names if bone_names is not None else {bone for (_k, bone, _p) in groups}
    kmap = _as_map(K, names)
    converted = 0
    for (slot_key, bone, prop), channels in groups.items():
        k = kmap.get(bone)
        if k is None or (bone_names is not None and bone not in bone_names):
            continue
        perm, sign = signed_permutation(k.inverted().to_3x3())
        if prop == "location":
            _permute_vector_channels(channels, perm, sign, scale_values=True)
        elif prop == "scale":
            _permute_vector_channels(channels, perm, sign, scale_values=False)
        elif prop == "rotation_quaternion":
            vec = {i - 1: fc for i, fc in channels.items() if i >= 1}
            _permute_vector_channels(vec, perm, sign, scale_values=True)
        elif prop == "rotation_euler":
            _euler_to_conjugated_quaternion(bags[slot_key], bone, channels, k, report)
        converted += 1
    return converted


def _euler_to_conjugated_quaternion(slot_bag, bone, channels, K, report):
    from mathutils import Euler

    slot, bag = slot_bag
    frames = sorted({float(k.co.x) for fc in channels.values() for k in fc.keyframe_points})
    if not frames:
        return
    Kq = K.to_quaternion()
    Kqi = Kq.conjugated()
    quats = []
    for f in frames:
        e = [channels[i].evaluate(f) if i in channels else 0.0 for i in range(3)]
        quats.append(Kqi @ Euler(e, "XYZ").to_quaternion() @ Kq)
    for fc in list(channels.values()):
        bag.fcurves.remove(fc)
    for comp in range(4):
        fc = compat.new_fcurve(bag, f'pose.bones["{bone}"].rotation_quaternion', index=comp, group=bone)
        fc.keyframe_points.add(len(frames))
        co = np.array([(f, q[comp]) for f, q in zip(frames, quats)], dtype=np.float64)
        fc.keyframe_points.foreach_set("co", co.ravel())
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"
        fc.update()
    if report is not None:
        report.info(f"Bone '{bone}': Euler rotation keys converted to quaternions")


# ---------------------------------------------------------------------- lengths / helpers / collections


def bone_direction(bone):
    """Armature-space unit vector along the bone (``Bone.y_axis`` is parent-relative)."""
    return (bone.matrix_local.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()


def deform_bone_names(arm_obj):
    names = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not any(m.type == "ARMATURE" and m.object == arm_obj for m in obj.modifiers):
            continue
        index_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
        for v in obj.data.vertices:
            for g in v.groups:
                if g.weight > 0.0:
                    names.add(index_to_name.get(g.group))
    names.discard(None)
    return {n for n in names if n in arm_obj.data.bones}


def recompute_bone_lengths(context, arm_obj, prefs, deform_names=None, connect=False):
    """Give bones sensible lengths: towards an aligned child, else mean child distance, else fractions."""
    tol = math.radians(prefs.import_bone_angle_tolerance)
    context.view_layer.objects.active = arm_obj
    with context.temp_override(**_edit_override(context, arm_obj)):
        bpy.ops.object.mode_set(mode="EDIT")
        ebs = arm_obj.data.edit_bones

        def depth(eb):
            d = 0
            while eb.parent is not None:
                eb = eb.parent
                d += 1
            return d

        for eb in sorted(ebs, key=depth):
            y = eb.y_axis.normalized()
            dists, aligned = [], []
            for child in eb.children:
                d = child.head - eb.head
                if d.length < 1e-4:
                    continue
                dists.append(d.length)
                if d.normalized().angle(y) <= tol:
                    aligned.append(d.length)
            named_helper = eb.name.startswith(HELPER_PREFIXES)
            unweighted = deform_names is not None and eb.name not in deform_names
            if aligned:
                length = max(aligned)  # farthest aligned child: twist bones sit at the parent's head
            elif named_helper:
                length = prefs.import_helper_bone_length
            elif dists:
                length = sum(dists) / len(dists)
            elif unweighted:
                length = prefs.import_helper_bone_length
            elif eb.parent is not None:
                length = 0.5 * eb.parent.length
            else:
                length = prefs.import_root_bone_length
            eb.length = max(length, prefs.import_min_bone_length)
        if connect:
            for eb in ebs:
                if eb.parent is not None and (eb.head - eb.parent.tail).length < 1e-3:
                    eb.use_connect = True
        bpy.ops.object.mode_set(mode="OBJECT")


def assign_bone_collections(arm_obj, deform_names):
    arm = arm_obj.data
    if not hasattr(arm, "collections"):
        return
    groups = {"Deform": [], "IK": [], "Helpers": []}
    for bone in arm.bones:
        if bone.name.lower().startswith("ik_"):
            groups["IK"].append(bone)
        elif bone.name in deform_names:
            groups["Deform"].append(bone)
        else:
            groups["Helpers"].append(bone)
    for name, bones in groups.items():
        if not bones:
            continue
        coll = arm.collections.get(name) or arm.collections.new(name)
        for bone in bones:
            coll.assign(bone)


def vote_bones_point_to_children(arm_obj):
    """Fraction of single-child bones whose +Y already points to the child (None without data)."""
    hits = total = 0
    for bone in arm_obj.data.bones:
        if len(bone.children) != 1:
            continue
        d = bone.children[0].head_local - bone.head_local
        if d.length < 1e-6:
            continue
        total += 1
        if d.normalized().dot(bone_direction(bone)) > 0.98:
            hits += 1
    return (hits / total) if total else None
