# SPDX-License-Identifier: GPL-3.0-or-later
"""Scene units: Unreal works in centimetres, MorennBridge keeps 1 Blender unit = 1 cm."""

from mathutils import Matrix

from .. import compat

CM_SCALE_LENGTH = 0.01


def unit_factor(scene):
    """Centimetres per Blender unit (what Blender's FBX exporter calls the unit scale, ``U``)."""
    us = scene.unit_settings
    if us.system == "NONE":
        return 100.0
    return 100.0 * us.scale_length


def is_cm_scene(scene, tol=1e-7):
    us = scene.unit_settings
    return us.system != "NONE" and abs(us.scale_length - CM_SCALE_LENGTH) <= tol


def set_cm_units(scene):
    us = scene.unit_settings
    us.system = "METRIC"  # must come first: changing the system resets the unit enums
    us.scale_length = CM_SCALE_LENGTH
    us.length_unit = "CENTIMETERS"
    us.mass_unit = "KILOGRAMS"


# Modifier properties that are lengths and must scale with the scene.
_LENGTH_MODIFIER_PROPS = {
    "SOLIDIFY": ("thickness",),
    "BEVEL": ("width",),
    "DISPLACE": ("strength",),
    "ARRAY": ("constant_offset_displace", "merge_threshold"),
    "MIRROR": ("merge_threshold", "bisect_threshold"),
    "WELD": ("merge_threshold",),
    "SCREW": ("screw_offset", "merge_threshold"),
    "WIREFRAME": ("thickness",),
    "SHRINKWRAP": ("offset",),
    "REMESH": ("voxel_size",),
    "BOOLEAN": ("double_threshold",),
    "OCEAN": ("size", "depth", "wave_scale"),
}
_LOCATION_PATH_SUFFIXES = ("location", "delta_location")
# Constraint properties that are lengths whatever the constraint's other settings are.
_LENGTH_CONSTRAINT_PROPS = {
    "LIMIT_DISTANCE": ("distance",),
    "LIMIT_LOCATION": ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z"),
    "FLOOR": ("offset",),
    "SHRINKWRAP": ("distance",),
    "STRETCH_TO": ("rest_length",),
    "PIVOT": ("offset",),
}
_TRANSFORM_CONSTRAINT_RANGES = ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z")
_SCALABLE_SOCKET_SUBTYPES = ("DISTANCE", "TRANSLATION")


def _scale_prop(owner, name, f):
    value = getattr(owner, name)
    try:
        setattr(owner, name, [v * f for v in value])
    except TypeError:
        setattr(owner, name, value * f)


def _scale_fcurve_values(fcurve, f):
    n = len(fcurve.keyframe_points)
    if n == 0:
        return
    import numpy as np

    for attr in ("co", "handle_left", "handle_right"):
        buf = np.empty(n * 2, dtype=np.float64)
        fcurve.keyframe_points.foreach_get(attr, buf)
        buf[1::2] *= f
        fcurve.keyframe_points.foreach_set(attr, buf)
    fcurve.update()


def _scale_constraint(constraint, f):
    for name in _LENGTH_CONSTRAINT_PROPS.get(constraint.type, ()):
        _scale_prop(constraint, name, f)
    if constraint.type == "CHILD_OF":
        m = constraint.inverse_matrix.copy()
        m.translation = m.translation * f
        constraint.inverse_matrix = m
    elif constraint.type == "TRANSFORM":
        for prefix, mapped in (("from_", constraint.map_from), ("to_", constraint.map_to)):
            if mapped == "LOCATION":
                for name in _TRANSFORM_CONSTRAINT_RANGES:
                    _scale_prop(constraint, prefix + name, f)
    elif constraint.type == "ACTION" and constraint.transform_channel.startswith("LOCATION"):
        constraint.min *= f
        constraint.max *= f


def _scale_node_group_inputs(obj, modifier, f, report):
    """Scale inputs declared as distances; plain numbers may or may not be lengths, so only warn about those."""
    group = modifier.node_group
    if group is None:
        return
    unknown = []
    for item in group.interface.items_tree:
        if item.item_type != "SOCKET" or item.in_out != "INPUT":
            continue
        value = compat.node_modifier_input(modifier, item.identifier)
        if value is None:
            continue
        subtype = getattr(item, "subtype", "")
        if subtype in _SCALABLE_SOCKET_SUBTYPES:
            scaled = [v * f for v in value] if hasattr(value, "__len__") else value * f
            compat.set_node_modifier_input(modifier, item.identifier, scaled)
        elif subtype == "NONE" and item.socket_type in ("NodeSocketFloat", "NodeSocketVector"):
            unknown.append(item.name)
    if unknown:
        report.warning(f"'{obj.name}': check Geometry Nodes inputs of '{modifier.name}': {', '.join(unknown)}")


def _scale_metaball(data, f):
    data.resolution *= f
    data.render_resolution *= f
    for element in data.elements:
        element.co = element.co * f
        element.radius *= f
        element.size_x *= f
        element.size_y *= f
        element.size_z *= f


def _is_location_path(path):
    return path in _LOCATION_PATH_SUFFIXES or path.endswith(".location")


def convert_scene_to_cm(scene, report):
    """Scale every length in ``scene`` so that switching the unit scale to 0.01 keeps physical sizes.

    Covers meshes, curves, metaballs, armatures, lattices, empties, cameras, lights, object and bone locations,
    length-type modifier and constraint settings, and location F-Curves. Whatever cannot be converted is reported
    as a warning, linked data included. Returns the factor applied (1.0 when the scene already is in centimetres).
    """
    f = unit_factor(scene)
    if abs(f - 1.0) < 1e-6:
        set_cm_units(scene)
        report.info("Scene already uses centimetres; units normalised")
        return 1.0

    scale = Matrix.Scale(f, 4)
    done = set()

    def once(id_block):
        key = id_block.as_pointer()
        if key in done:
            return False
        done.add(key)
        if id_block.library is not None:
            report.warning(f"Linked data '{id_block.name}' cannot be scaled")
            return False
        return True

    actions = set()
    for obj in scene.objects:
        if obj.library is not None:
            report.warning(f"Linked object '{obj.name}' skipped")
            continue
        obj.location = obj.location * f
        obj.delta_location = obj.delta_location * f
        if obj.parent is not None:
            m = obj.matrix_parent_inverse.copy()
            m.translation = m.translation * f
            obj.matrix_parent_inverse = m
        if obj.type == "EMPTY":
            obj.empty_display_size *= f
        if obj.pose is not None:
            for pb in obj.pose.bones:
                pb.location = pb.location * f
                for constraint in pb.constraints:
                    _scale_constraint(constraint, f)
        for constraint in obj.constraints:
            _scale_constraint(constraint, f)
        for mod in obj.modifiers:
            if mod.type == "NODES":
                _scale_node_group_inputs(obj, mod, f, report)
            for name in _LENGTH_MODIFIER_PROPS.get(mod.type, ()):
                if hasattr(mod, name):
                    _scale_prop(mod, name, f)
        if len(obj.particle_systems):
            report.warning(f"'{obj.name}' has particle systems; their sizes were not rescaled")
        ad = obj.animation_data
        if ad is not None:
            if ad.action is not None:
                actions.add(ad.action)
            for track in ad.nla_tracks:
                for strip in track.strips:
                    if strip.action is not None:
                        actions.add(strip.action)
            if ad.drivers:
                report.warning(f"'{obj.name}' has drivers; driver expressions were not rescaled")

        data = obj.data
        if data is None or not once(data):
            continue
        if obj.type == "MESH":
            data.transform(scale, shape_keys=True)
        elif obj.type in {"CURVE", "SURFACE", "FONT"}:
            data.transform(scale)
            for name in ("extrude", "bevel_depth", "offset"):
                if hasattr(data, name):
                    _scale_prop(data, name, f)
            if obj.type == "FONT":
                data.size *= f
        elif obj.type == "ARMATURE":
            data.transform(scale)
        elif obj.type == "LATTICE":
            data.transform(scale)
        elif obj.type == "META":
            _scale_metaball(data, f)
        elif obj.type == "CAMERA":
            data.clip_start *= f
            data.clip_end *= f
            data.ortho_scale *= f
            data.dof.focus_distance *= f
        elif obj.type == "LIGHT":
            data.shadow_soft_size *= f
            if data.type == "AREA":
                data.size *= f
                data.size_y *= f
        else:
            report.warning(f"'{obj.name}' ({obj.type}) data was not rescaled")

    for action in actions:
        if action.library is not None:
            continue
        for _slot, fcurve in compat.iter_fcurves(action):
            if _is_location_path(fcurve.data_path):
                _scale_fcurve_values(fcurve, f)

    set_cm_units(scene)
    report.info(f"Scene converted to centimetres (all lengths × {f:g})")
    return f
