# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate the documentation screenshots with a real Blender UI session.

    Blender --factory-startup --window-geometry 0 0 1400 850 <blank.blend> \
        --python morennbridge/dev/make_screenshots.py -- <out_dir> [<ue_animation.fbx>]

Opening a .blend from the command line suppresses the splash screen. The add-on must be reachable as
``bl_ext.user_default.morennbridge`` (the test runner creates that symlink).
"""

import json
import math
import os
import sys
import traceback

import bmesh
import bpy
import numpy as np
from mathutils import Euler

ARGS = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
CROP_ONLY = "--crop" in ARGS
ARGS = [a for a in ARGS if a != "--crop"]
OUT = (
    os.path.abspath(ARGS[0])
    if ARGS
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "images")
)
UE_FBX = ARGS[1] if len(ARGS) > 1 and os.path.exists(ARGS[1]) else ""
CROPS_JSON = os.path.join(OUT, "_crops.json")
EXPORT_DIR = os.path.join(OUT, "_export_tmp")
ADDON = "bl_ext.user_default.morennbridge"
os.makedirs(OUT, exist_ok=True)
if not CROP_ONLY:
    os.makedirs(EXPORT_DIR, exist_ok=True)

STATE = {"panels": None, "steps": [], "crops": []}


# ---------------------------------------------------------------------- helpers


def addon():
    return sys.modules[ADDON]


def submodule(name):
    return sys.modules[f"{ADDON}.{name}"]


def enable_addon():
    bpy.ops.extensions.repo_refresh_all()
    bpy.ops.preferences.addon_enable(module=ADDON)
    panels = submodule("ui.panels")
    STATE["panels"] = {cls.bl_idname: cls for cls in panels.classes}
    STATE["panel_defaults"] = {cls.bl_idname: set(getattr(cls, "bl_options", set())) for cls in panels.classes}
    bpy.context.preferences.view.show_splash = False
    bpy.context.preferences.system.use_region_overlap = False  # opaque sidebar: crops can be trimmed
    scene = bpy.context.scene
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.compression = 90


def register_panels(idnames, expanded=True):
    """Keep only the given panels registered; ``expanded`` clears DEFAULT_CLOSED before registration."""
    for idname, cls in STATE["panels"].items():
        if getattr(cls, "is_registered", False):
            bpy.utils.unregister_class(cls)
    for idname in idnames:
        cls = STATE["panels"][idname]
        cls.bl_options = set() if expanded else set(STATE["panel_defaults"][idname])
        bpy.utils.register_class(cls)


def view3d():
    win = bpy.context.window
    area = next(a for a in win.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    ui = next(r for r in area.regions if r.type == "UI")
    return win, area, region, ui


def show_tab():
    """Show the sidebar and select our tab. Returns False when the tab could not be set yet (region not drawn)."""
    win, area, region, ui = view3d()
    space = area.spaces.active
    space.show_region_ui = True
    space.shading.type = "SOLID"
    try:
        if ui.active_panel_category != "MorennBridge":
            ui.active_panel_category = "MorennBridge"
            return False
        return True
    except Exception as exc:
        print("tab not ready:", exc)
        return False


def set_view(rot=(72.0, 0.0, 32.0)):
    win, area, region, ui = view3d()
    rv3d = area.spaces.active.region_3d
    rv3d.view_perspective = "PERSP"
    rv3d.view_rotation = Euler(tuple(math.radians(v) for v in rot), "XYZ").to_quaternion()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.view3d.view_all(center=False)


def ui_call(op, **kw):
    """Run an operator with the 3D view context (as a button click would); errors are reported, not raised."""
    win, area, region, ui = view3d()
    try:
        with bpy.context.temp_override(window=win, area=area, region=region):
            return op(**kw)
    except RuntimeError as exc:
        print("operator reported:", exc)
        return {"CANCELLED"}


def shoot(name, crop=None):
    """Take the screenshot; if the tab had to be switched first, retry this step on the next tick."""
    if not show_tab():
        STATE["steps"].insert(0, STATE["current"])
        print("retrying", name, "after redraw")
        return
    win, area, region, ui = view3d()
    path = os.path.join(OUT, name)
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.screenshot(filepath=path)
    if crop == "sidebar":
        STATE["crops"].append((path, ui.x, ui.y, ui.width, ui.height, win.width))
    elif crop == "area":
        STATE["crops"].append((path, area.x, area.y, area.width, area.height, win.width))
    print("SHOT", name)


def _crop(path, x, y, w, h, win_width):
    """Region coordinates are native (retina) pixels, so they map 1:1 onto the screenshot."""
    img = bpy.data.images.load(path)
    W, H = img.size
    s = 1.0
    x0, y0 = int(x * s), int(y * s)
    x1, y1 = int((x + w) * s), int((y + h) * s)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    buf = np.empty(W * H * 4, dtype=np.float32)
    img.pixels.foreach_get(buf)
    sub = np.ascontiguousarray(buf.reshape(H, W, 4)[y0:y1, x0:x1])
    sub = _trim_bottom(sub)
    out = bpy.data.images.new("crop", width=sub.shape[1], height=sub.shape[0], alpha=False)
    out.pixels.foreach_set(sub.ravel())
    out.filepath_raw = path
    out.file_format = "PNG"
    out.save()
    bpy.data.images.remove(img)
    bpy.data.images.remove(out)


def _trim_bottom(sub, margin=28):
    """Cut the empty (flat-coloured) part under the panels; rows are bottom-up in Blender images."""
    h, w = sub.shape[:2]
    if h < 64:
        return sub
    inner = sub[:, : max(1, int(w * 0.85)), :3]  # ignore the vertical tab strip on the right
    ref = np.median(inner[8], axis=0)  # rows 0-3 are the region border
    diff = np.abs(inner - ref).max(axis=2)  # (h, w)
    busy = (diff > 0.03).mean(axis=1) > 0.02  # rows with content
    rows = np.nonzero(busy)[0]
    rows = rows[rows > 8]
    if len(rows) == 0:
        return sub
    first = max(0, rows[0] - margin)
    return np.ascontiguousarray(sub[first:])


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.orphans_purge(do_recursive=True)
    scene = bpy.context.scene
    scene.morenn.log.clear()
    scene.morenn.issues.clear()


def link(obj):
    bpy.context.scene.collection.objects.link(obj)
    return obj


def box(name, size=(100, 100, 100), location=(0, 0, 0), material=None, wire=False):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    uv = bm.loops.layers.uv.new("UVMap")
    for f in bm.faces:
        for i, loop in enumerate(f.loops):
            loop[uv].uv = ((i in (1, 2)) * 1.0, (i in (2, 3)) * 1.0)
    bm.to_mesh(me)
    bm.free()
    if material is not None:
        me.materials.append(material)
    obj = bpy.data.objects.new(name, me)
    obj.location = location
    if wire:
        obj.display_type = "WIRE"
    return link(obj)


def select(objs, active=None):
    for o in bpy.context.view_layer.objects:
        o.select_set(o in objs)
    bpy.context.view_layer.objects.active = active or (objs[0] if objs else None)


def make_rig(name, bones, bad=False):
    arm = bpy.data.armatures.new(name)
    rig = bpy.data.objects.new(name, arm)
    link(rig)
    rig.show_in_front = True
    bpy.context.view_layer.objects.active = rig
    with bpy.context.temp_override(active_object=rig, object=rig, selected_objects=[rig]):
        bpy.ops.object.mode_set(mode="EDIT")
        parent = None
        for i, bname in enumerate(bones):
            eb = arm.edit_bones.new(bname)
            eb.head = (0, 0, i * 30)
            eb.tail = (0, 0, (i + 1) * 30)
            eb.parent = parent
            parent = eb
        if bad:
            eb = arm.edit_bones.new("extra_root")
            eb.head, eb.tail = (40, 0, 0), (40, 0, 20)
        bpy.ops.object.mode_set(mode="OBJECT")
    return rig


# ---------------------------------------------------------------------- steps


def step_overview():
    enable_addon()
    register_panels(list(STATE["panels"]), expanded=False)
    show_tab()
    clear_scene()
    os.makedirs(bpy.path.abspath("//export/"), exist_ok=True)  # default export folder shown as valid
    mat = bpy.data.materials.new("M_Crate")
    mat.diffuse_color = (0.55, 0.35, 0.18, 1.0)
    crate = box("Crate", material=mat)
    select([crate], crate)
    set_view()


def step_shot_overview():
    shoot("00_sidebar_overview.png", crop="sidebar")


def step_workspace():
    bpy.context.window.workspace = bpy.data.workspaces["Modeling"]


def step_scene_before():
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    register_panels(["MORENN_PT_scene"])
    show_tab()
    set_view()


def step_shot_scene_before():
    shoot("01_scene_before_setup.png", crop="sidebar")


def step_scene_setup():
    ui_call(bpy.ops.morenn.scene_setup)
    register_panels(["MORENN_PT_scene", "MORENN_PT_log"])
    show_tab()


def step_shot_scene_after():
    shoot("02_scene_after_setup.png", crop="sidebar")


def step_export_scene():
    clear_scene()
    mat = bpy.data.materials.new("M_Crate")
    mat.diffuse_color = (0.55, 0.35, 0.18, 1.0)
    crate = box("Crate", material=mat)
    col = box("UCX_Crate", size=(104, 104, 104), wire=True)
    col.parent = crate
    col.display_type = "WIRE"
    sock = bpy.data.objects.new("SOCKET_Lid", None)
    sock.empty_display_type = "ARROWS"
    sock.empty_display_size = 25
    sock.location = (0, 0, 50)
    link(sock)
    sock.parent = crate
    lod1 = box("Crate_LOD1", size=(100, 100, 100), location=(180, 0, 0), material=mat)
    lod1.morenn.role = "LOD"
    lod1.morenn.lod_index = 1
    lod1.morenn.lod_of = crate
    scene = bpy.context.scene
    scene.morenn.export_dir = EXPORT_DIR
    scene.morenn.ue_content_path = "/Game/Props"
    scene.morenn.generate_collision_mesh = True
    scene.morenn.collision_ratio = 0.5
    select([crate], crate)
    register_panels(["MORENN_PT_export", "MORENN_PT_object"])
    show_tab()
    set_view()


def step_shot_export_scene():
    shoot("03_export_static_setup.png")


def step_export_run():
    crate = bpy.data.objects["Crate"]
    select([crate], crate)  # selection does not survive across timer ticks in this automated session
    print("SEL:", [o.name for o in bpy.context.view_layer.objects if o.select_get()])
    ui_call(bpy.ops.morenn.export_ue, mode="STATIC")
    for e in bpy.context.scene.morenn.log:
        print("EXPORTLOG", e.level, e.message)
    register_panels(["MORENN_PT_export", "MORENN_PT_log"])
    show_tab()


def step_shot_export_done():
    shoot("04_export_done_log.png", crop="sidebar")


def step_preflight():
    clear_scene()
    rig = make_rig("Hero", ["root", "spine.001", "spine.002"], bad=True)
    body = box("Hero_Body", size=(30, 30, 90), location=(0, 0, 45))
    body.data.uv_layers.remove(body.data.uv_layers[0])
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = rig
    body.parent = rig
    vg = body.vertex_groups.new(name="spine.001")
    vg.add([v.index for v in body.data.vertices], 1.0, "REPLACE")
    select([rig], rig)
    ui_call(bpy.ops.morenn.validate)
    register_panels(["MORENN_PT_preflight"])
    show_tab()
    set_view()


def step_shot_preflight():
    shoot("05_preflight_issues.png", crop="sidebar")


def step_import():
    clear_scene()
    if not UE_FBX:
        print("no UE fbx given; skipping import shots")
        return
    ui_call(bpy.ops.morenn.import_ue, filepath=UE_FBX)
    ours = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    before = set(bpy.data.objects)
    bpy.ops.wm.fbx_import(filepath=UE_FBX, global_scale=100.0)
    classic = [o for o in bpy.data.objects if o not in before]
    for o in classic:
        if o.parent is None:
            o.delta_location.x += 320  # location is animated by the stock importer (root node curves)
        if o.type == "ARMATURE":
            o.name = "Classic_import"
            o.data.display_type = "OCTAHEDRAL"
    for o in ours:
        o.data.display_type = "OCTAHEDRAL"
        o.name = "MorennBridge_import"
    bpy.context.scene.frame_set(1)
    select(ours, ours[0] if ours else None)
    register_panels(["MORENN_PT_import"])
    show_tab()
    set_view((88.0, 0.0, 0.0))  # front view: the two rigs sit side by side


def step_shot_import():
    if UE_FBX:
        shoot("06_import_compare.png")


def step_armature_panel():
    if not UE_FBX:
        return
    arm = bpy.data.objects.get("MorennBridge_import")
    if arm is None:
        return
    select([arm], arm)
    bone = arm.data.bones.get("clavicle_r")
    if bone is not None:
        arm.data.bones.active = bone
    register_panels(["MORENN_PT_armature"])
    show_tab()


def step_shot_armature():
    if UE_FBX:
        shoot("07_armature_panel.png", crop="sidebar")


def step_unreal_link():
    register_panels(["MORENN_PT_unreal"])
    show_tab()


def step_shot_unreal_link():
    shoot("08_unreal_link.png", crop="sidebar")


# ---------------------------------------------------------------------- preferences window


def prefs_window():
    for w in bpy.context.window_manager.windows:
        if any(a.type == "PREFERENCES" for a in w.screen.areas):
            return w
    return None


def shoot_window(win, name):
    """Secondary windows only redraw on their own events: force a draw before reading the framebuffer."""
    area = next(a for a in win.screen.areas if a.type == "PREFERENCES")
    region = next(r for r in area.regions if r.type == "WINDOW")
    path = os.path.join(OUT, name)
    area.tag_redraw()  # a section change alone does not re-layout a secondary window
    for r in area.regions:
        r.tag_redraw()
    with bpy.context.temp_override(window=win, area=area, region=region):
        try:
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        except Exception as exc:
            print("redraw:", exc)
        bpy.ops.screen.screenshot(filepath=path)
    print("SHOT", name)


def step_prefs_open():
    win, area, region, ui = view3d()
    with bpy.context.temp_override(window=win, area=area, region=region):
        bpy.ops.screen.userpref_show("INVOKE_DEFAULT")
    pw = prefs_window()
    if pw is not None:
        with bpy.context.temp_override(window=pw):
            try:
                bpy.ops.wm.window_fullscreen_toggle()
            except Exception as exc:
                print("fullscreen:", exc)


def step_prefs_addons():
    prefs_window()
    prefs = bpy.context.preferences
    prefs.view.ui_scale = 0.8  # the whole preferences panel fits in one window at retina resolution
    prefs.active_section = "ADDONS"
    bpy.context.window_manager.addon_search = "MorennBridge"
    try:
        bpy.ops.preferences.addon_expand(module=ADDON)
    except Exception as exc:
        print("addon_expand:", exc)


def step_shot_prefs_addons():
    pw = prefs_window()
    if pw is not None:
        shoot_window(pw, "09_preferences_addon.png")


def step_prefs_addons_scale1():
    bpy.context.preferences.view.ui_scale = 1.0
    bpy.context.preferences.active_section = "ADDONS"


def step_shot_prefs_addons_scale1():
    pw = prefs_window()
    if pw is not None:
        shoot_window(pw, "09b_preferences_addon_scale1.png")


def step_prefs_extensions():
    bpy.context.preferences.view.ui_scale = 1.0
    bpy.context.preferences.active_section = "EXTENSIONS"
    bpy.context.window_manager.extension_search = "MorennBridge"


def step_shot_prefs_extensions():
    pw = prefs_window()
    if pw is not None:
        shoot_window(pw, "10_preferences_extensions.png")


def step_prefs_close():
    pw = prefs_window()
    if pw is not None:
        with bpy.context.temp_override(window=pw):
            try:
                bpy.ops.wm.window_close()
            except Exception as exc:
                print("window_close:", exc)


def step_settle():
    pass


def step_crop():
    with open(CROPS_JSON, "w", encoding="utf-8") as fh:
        json.dump(STATE["crops"], fh)
    print("crop boxes written; run again with --crop in background mode to apply them")


def crop_pass():
    with open(CROPS_JSON, encoding="utf-8") as fh:
        crops = json.load(fh)
    failed = 0
    for path, x, y, w, h, win_width in crops:
        try:
            _crop(path, x, y, w, h, win_width)
            print("CROPPED", os.path.basename(path))
        except Exception:
            failed += 1
            traceback.print_exc()
    if not failed:
        os.remove(CROPS_JSON)
    import shutil

    shutil.rmtree(EXPORT_DIR, ignore_errors=True)


def step_done():
    try:
        import shutil

        shutil.rmtree(EXPORT_DIR, ignore_errors=True)
        register_panels(list(STATE["panels"]), expanded=False)  # leave the add-on consistent for shutdown
    finally:
        bpy.ops.wm.quit_blender()


STATE["steps"] = [
    step_overview,
    step_shot_overview,
    step_workspace,
    step_scene_before,
    step_shot_scene_before,
    step_scene_setup,
    step_shot_scene_after,
    step_export_scene,
    step_shot_export_scene,
    step_export_run,
    step_shot_export_done,
    step_preflight,
    step_shot_preflight,
    step_import,
    step_shot_import,
    step_armature_panel,
    step_shot_armature,
    step_unreal_link,
    step_shot_unreal_link,
    step_prefs_open,
    step_settle,
    step_settle,
    step_prefs_addons,
    step_settle,
    step_shot_prefs_addons,
    step_prefs_extensions,
    step_settle,
    step_shot_prefs_extensions,
    step_prefs_close,
    step_crop,
    step_done,
]


def run_next():
    if not STATE["steps"]:
        return None
    step = STATE["steps"].pop(0)
    STATE["current"] = step
    try:
        step()
    except Exception:
        print("STEP FAILED", step.__name__)
        traceback.print_exc()
    return 1.5


if CROP_ONLY:
    crop_pass()
else:
    bpy.app.timers.register(run_next, first_interval=2.5)
