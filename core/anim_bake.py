# SPDX-License-Identifier: GPL-3.0-or-later
"""Bake the *visual* pose of a rig (constraints, drivers, NLA included) into a plain action."""

import bpy
import numpy as np

from .. import compat

PROPS = (("location", 3), ("rotation_quaternion", 4), ("scale", 3))


class ActionAssignment:
    """Temporarily play ``action`` (or solo an NLA track) on ``obj``; restores everything on exit."""

    def __init__(self, obj, action=None, solo_track=None):
        self.obj = obj
        self.action = action
        self.solo_track = solo_track
        self._saved = None

    def __enter__(self):
        ad = self.obj.animation_data
        if self.action is None and self.solo_track is None:
            return self
        if ad is None:
            ad = self.obj.animation_data_create()
            self._saved = ("created",)
        else:
            self._saved = (
                "restore",
                ad.action,
                getattr(ad, "action_slot", None),
                [(t, t.is_solo, t.mute) for t in ad.nla_tracks],
                ad.use_nla,
            )
        if self.action is not None:
            ad.action = self.action
            slot = compat.slot_for_id(self.action, self.obj)
            if slot is not None and hasattr(ad, "action_slot"):
                ad.action_slot = slot
        if self.solo_track is not None:
            ad.action = None
            for t in ad.nla_tracks:
                t.is_solo = t == self.solo_track
        return self

    def __exit__(self, *exc):
        ad = self.obj.animation_data
        if self._saved is None or ad is None:
            return False
        if self._saved[0] == "created":
            self.obj.animation_data_clear()
            return False
        _, action, slot, tracks, use_nla = self._saved
        for t, solo, mute in tracks:
            t.is_solo = solo
            t.mute = mute
        ad.action = action
        if slot is not None and hasattr(ad, "action_slot"):
            try:
                ad.action_slot = slot
            except (RuntimeError, TypeError):
                pass
        ad.use_nla = use_nla
        return False


def bake_visual_pose(
    context,
    src_arm,
    dst_arm,
    bone_names,
    frame_start,
    frame_end,
    scale,
    name,
    action=None,
    solo_track=None,
    report=None,
    conjugate=None,
):
    """Sample ``src_arm``'s pose every frame and key ``dst_arm`` (LINEAR keys, quaternions).

    ``scale`` multiplies bone-local translations (unit factor × uniform armature scale).
    ``conjugate``: optional ``{bone: D}`` when the destination rest pose is ``M @ D`` (basis becomes ``D⁻¹ P D``).
    Returns the created action.
    """
    scene = context.scene
    frames = list(range(int(frame_start), int(frame_end) + 1))
    bone_names = [b for b in bone_names if b in src_arm.pose.bones and b in dst_arm.pose.bones]
    samples = {(b, prop): np.empty((len(frames), n), dtype=np.float64) for b in bone_names for prop, n in PROPS}
    prev = {b: None for b in bone_names}
    conj = {b: (D, D.inverted()) for b, D in (conjugate or {}).items()}
    saved_frame = scene.frame_current
    try:
        with ActionAssignment(src_arm, action=action, solo_track=solo_track):
            for fi, f in enumerate(frames):
                scene.frame_set(f)
                for b in bone_names:
                    pb = src_arm.pose.bones[b]
                    basis = src_arm.convert_space(pose_bone=pb, matrix=pb.matrix, from_space="POSE", to_space="LOCAL")
                    if b in conj:
                        D, Dinv = conj[b]
                        basis = Dinv @ basis @ D
                    loc, rot, sca = basis.decompose()
                    if prev[b] is not None and rot.dot(prev[b]) < 0.0:
                        rot = -rot
                    prev[b] = rot
                    samples[(b, "location")][fi] = (loc.x * scale, loc.y * scale, loc.z * scale)
                    samples[(b, "rotation_quaternion")][fi] = (rot.w, rot.x, rot.y, rot.z)
                    samples[(b, "scale")][fi] = (sca.x, sca.y, sca.z)
    finally:
        scene.frame_set(saved_frame)

    act, slot, bag = compat.new_action_with_slot(name, "OBJECT", dst_arm.name)
    act.use_fake_user = False
    fr = np.array(frames, dtype=np.float64)
    for (b, prop), data in samples.items():
        for i in range(data.shape[1]):
            fc = compat.new_fcurve(bag, f'pose.bones["{b}"].{prop}', index=i, group=b)
            fc.keyframe_points.add(len(frames))
            co = np.column_stack((fr, data[:, i])).ravel()
            fc.keyframe_points.foreach_set("co", co)
            for k in fc.keyframe_points:
                k.interpolation = "LINEAR"
            fc.update()
    compat.assign_action(dst_arm, act, slot)
    if report is not None:
        report.info(f"Baked {len(frames)} frames for {len(bone_names)} bones into '{name}'")
    return act


def action_frame_range(action):
    if getattr(action, "use_frame_range", False):
        return int(round(action.frame_start)), int(round(action.frame_end))
    fr = action.frame_range
    return int(round(fr[0])), int(round(fr[1]))


def actions_for_armature(arm):
    """Actions that animate pose bones (any slot): candidates for 'all actions' export."""
    out = []
    for action in bpy.data.actions:
        if action.library is not None:
            continue
        for _slot, fc in compat.iter_fcurves(action):
            if fc.data_path.startswith("pose.bones"):
                out.append(action)
                break
    return out
