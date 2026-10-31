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


def _is_location_path(path):
    return path in _LOCATION_PATH_SUFFIXES or path.endswith(".location")


def convert_scene_to_cm(scene, report):
    """Scale every length in ``scene`` so that switching the unit scale to 0.01 keeps physical sizes.

    Best effort: meshes, curves, armatures, lattices, empties, cameras, lights, object/bone locations,
    common length-type modifier settings and location F-Curves. Linked data is left untouched (warning).
    Returns the factor applied (1.0 when the scene already is in centimetres).
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
        for mod in obj.modifiers:
            for name in _LENGTH_MODIFIER_PROPS.get(mod.type, ()):
                if hasattr(mod, name):
                    _scale_prop(mod, name, f)
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
