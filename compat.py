# SPDX-License-Identifier: GPL-3.0-or-later
"""Wrappers around Blender APIs that change between versions or are awkward to call."""

import bpy
from bpy_extras import anim_utils

# --------------------------------------------------------------------------- operators


def operator_available(op):
    """Whether ``bpy.ops.x.y`` is a real operator; the attribute lookup itself succeeds for any name."""
    try:
        op.get_rna_type()
    except KeyError:
        return False
    return True


def fbx_exporter_available():
    return operator_available(bpy.ops.export_scene.fbx)


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
    return bpy.app.online_access


def window_override():
    """Extra ``temp_override`` keywords needed when there is no active window (background mode)."""
    ctx = bpy.context
    if getattr(ctx, "window", None) is not None:
        return {}
    wm = ctx.window_manager
    if wm is not None and len(wm.windows):
        return {"window": wm.windows[0]}
    return {}


def has_instances(evaluated_object):
    """Whether the object generates instances (Geometry Nodes), which ``new_from_object`` would drop."""
    try:
        geometry = evaluated_object.evaluated_geometry()
    except TypeError:  # raised for objects the depsgraph skipped, such as hidden ones
        return False
    return len(geometry.instance_references()) > 0


def node_modifier_input(modifier, identifier):
    """The value of a Geometry Nodes modifier input, or None when the modifier does not store one."""
    if hasattr(modifier, "properties"):  # Blender 5.2 and newer
        return getattr(getattr(modifier.properties.inputs, identifier, None), "value", None)
    return modifier[identifier] if identifier in modifier else None


def set_node_modifier_input(modifier, identifier, value):
    if hasattr(modifier, "properties"):
        getattr(modifier.properties.inputs, identifier).value = value
    else:
        modifier[identifier] = value


# --------------------------------------------------------------------------- actions / slots


def action_channelbag(action, slot, ensure=False):
    """Return the channelbag holding ``slot``'s F-Curves (None when absent and not ensured)."""
    bag = anim_utils.action_get_channelbag_for_slot(action, slot)
    if bag is not None or not ensure:
        return bag
    layer = action.layers[0] if len(action.layers) else action.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type="KEYFRAME")
    return strip.channelbag(slot, ensure=True)


def iter_fcurves(action, slot=None):
    """Yield ``(slot, fcurve)`` for one slot of ``action``, or for all of them."""
    if action is None:
        return
    for current in [slot] if slot is not None else action.slots:
        bag = action_channelbag(action, current)
        if bag is None:
            continue
        for fcurve in bag.fcurves:
            yield current, fcurve


def assigned_actions(id_data, include_muted=True):
    """``(action, slot)`` pairs for the active action and the NLA strips of ``id_data``."""
    ad = id_data.animation_data
    if ad is None:
        return []
    out = [(ad.action, ad.action_slot)] if ad.action is not None else []
    for track in ad.nla_tracks:
        if track.mute and not include_muted:
            continue
        for strip in track.strips:
            if strip.action is not None and (include_muted or not strip.mute):
                out.append((strip.action, strip.action_slot))
    return out


def new_action_with_slot(name, id_type="OBJECT", slot_name=None):
    """Create an action plus one slot and return ``(action, slot, channelbag)``."""
    action = bpy.data.actions.new(name)
    slot = action.slots.new(id_type=id_type, name=slot_name or name)
    return action, slot, action_channelbag(action, slot, ensure=True)


def assign_action(id_data, action, slot):
    ad = id_data.animation_data or id_data.animation_data_create()
    ad.action = action
    ad.action_slot = slot
    return ad


def slot_for_id(action, id_data):
    """Find the slot of ``action`` that is (or could be) assigned to ``id_data``."""
    ad = id_data.animation_data
    if ad is not None and ad.action == action and ad.action_slot is not None:
        return ad.action_slot
    want = getattr(id_data, "id_type", None)
    for slot in action.slots:
        if want is None or slot.target_id_type == want:
            return slot
    return None


def new_fcurve(container, data_path, index=0, group=None):
    """Create an F-Curve in a channelbag, assigning a group when supported."""
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
