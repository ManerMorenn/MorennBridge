# SPDX-License-Identifier: GPL-3.0-or-later
"""The 'Set up scene for Unreal' button."""

import bpy

from . import units


def iter_view3d_spaces():
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    yield space


def apply(context, prefs, report):
    """Configure units, frame rate and every 3D viewport for a centimetre-based UE workflow."""
    scene = context.scene
    was_cm = units.is_cm_scene(scene)
    units.set_cm_units(scene)
    report.info("Units: metric, 1 unit = 1 cm" + ("" if was_cm else " (changed)"))

    render = scene.render
    if render.fps != prefs.fps or abs(render.fps_base - 1.0) > 1e-9:
        render.fps = prefs.fps
        render.fps_base = 1.0
        report.info(f"Frame rate set to {prefs.fps} fps")
    if prefs.frame_start_zero and scene.frame_start == 1:
        scene.frame_start = 0
        report.info("Frame range now starts at 0")

    n_views = 0
    for space in iter_view3d_spaces():
        space.clip_start = prefs.clip_start
        space.clip_end = prefs.clip_end
        space.overlay.grid_scale = prefs.grid_scale
        n_views += 1
    report.info(
        f"{n_views} 3D viewport(s): clip {prefs.clip_start:g}–{prefs.clip_end:g} cm, grid scale {prefs.grid_scale:g}"
    )

    if prefs.camera_clip:
        n_cams = 0
        for cam in bpy.data.cameras:
            if cam.library is not None:
                continue
            cam.clip_start = prefs.clip_start
            cam.clip_end = prefs.clip_end
            n_cams += 1
        if n_cams:
            report.info(f"{n_cams} camera(s): clip range set")
    return report
