# SPDX-License-Identifier: GPL-3.0-or-later
"""Version shims so the add-on runs on Blender 4.2 … 5.x without branching everywhere."""

import bpy

try:
    from bpy_extras import anim_utils
except ImportError:
    anim_utils = None


def version():
    return tuple(bpy.app.version)


# --------------------------------------------------------------------------- operators


def has_new_fbx_importer():
    """True when the C++ (ufbx) importer ``wm.fbx_import`` exists (Blender 4.5+)."""
    return hasattr(bpy.ops.wm, "fbx_import")


def op_property_names(op):
    return {p.identifier for p in op.get_rna_type().properties}


def filter_kwargs(op, kwargs, report=None):
    """Drop keyword arguments the operator does not know in this Blender version."""
    known = op_property_names(op)
    out = {}
    for key, value in kwargs.items():
        if key in known:
            out[key] = value
        elif report is not None:
            report.info(f"Operator option '{key}' not available in Blender {bpy.app.version_string}; skipped")
    return out


def online_access_allowed():
    return bool(getattr(bpy.app, "online_access", True))


def window_override():
    """Extra ``temp_override`` keywords needed when there is no active window (background mode)."""
    ctx = bpy.context
    if getattr(ctx, "window", None) is not None:
        return {}
    wm = ctx.window_manager
    if wm is not None and len(wm.windows):
        return {"window": wm.windows[0]}
    return {}


# --------------------------------------------------------------------------- actions / slots


def has_slotted_actions():
    return "slots" in bpy.types.Action.bl_rna.properties


def action_slots(action):
    return list(getattr(action, "slots", ()))


def action_channelbag(action, slot, ensure=False):
    """Return the channelbag holding ``slot``'s F-Curves (None when absent and not ensured)."""
    if anim_utils is not None and hasattr(anim_utils, "action_get_channelbag_for_slot"):
        bag = anim_utils.action_get_channelbag_for_slot(action, slot)
        if bag is not None or not ensure:
            return bag
    if not ensure:
        return None
    layer = action.layers[0] if len(action.layers) else action.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type="KEYFRAME")
    return strip.channelbag(slot, ensure=True)


def iter_fcurves(action):
    """Yield ``(slot, fcurve)`` for every F-Curve of ``action`` (slot is None for legacy actions)."""
    if action is None:
        return
    slots = action_slots(action)
    if slots:
        for slot in slots:
            bag = action_channelbag(action, slot)
            if bag is None:
                continue
            for fcurve in bag.fcurves:
                yield slot, fcurve
        return
    for fcurve in getattr(action, "fcurves", ()):
        yield None, fcurve


def new_action_with_slot(name, id_type="OBJECT", slot_name=None):
    """Create an action plus one slot and return ``(action, slot, channelbag)``."""
    action = bpy.data.actions.new(name)
    if has_slotted_actions():
        slot = action.slots.new(id_type=id_type, name=slot_name or name)
        bag = action_channelbag(action, slot, ensure=True)
        return action, slot, bag
    return action, None, action


def assign_action(id_data, action, slot=None):
    ad = id_data.animation_data or id_data.animation_data_create()
    ad.action = action
    if slot is not None and hasattr(ad, "action_slot"):
        ad.action_slot = slot
    return ad


def slot_for_id(action, id_data):
    """Find the slot of ``action`` that is (or could be) assigned to ``id_data``."""
    ad = id_data.animation_data
    if ad is not None and ad.action == action and getattr(ad, "action_slot", None) is not None:
        return ad.action_slot
    want = getattr(id_data, "id_type", None)
    for slot in action_slots(action):
        if want is None or slot.target_id_type == want:
            return slot
    return None


def new_fcurve(container, data_path, index=0, group=None):
    """Create an F-Curve in a channelbag or a legacy action, assigning a group when supported."""
    try:
        fc = (
            container.fcurves.new(data_path, index=index, action_group=group)
            if group
            else container.fcurves.new(data_path, index=index)
        )
    except TypeError:
        fc = container.fcurves.new(data_path, index=index)
        if group and hasattr(container, "groups"):
            grp = container.groups.get(group) if hasattr(container.groups, "get") else None
            if grp is None:
                try:
                    grp = container.groups.new(group)
                except (RuntimeError, TypeError):
                    grp = None
            if grp is not None:
                try:
                    fc.group = grp
                except (RuntimeError, AttributeError):
                    pass
    return fc
