# SPDX-License-Identifier: GPL-3.0-or-later
"""Checks that need a real window: panels drawn by Blender itself, undo after every operator that changes data.

    blender --factory-startup --python tests/gui_session.py

The window closes by itself; the exit code is non-zero on failure.
"""

import importlib
import os
import pathlib
import runpy
import sys
import tempfile
import traceback

import bpy

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures = []
drawn = set()
draw_errors = []


def check(condition, message):
    print(f"[gui] {'ok' if condition else 'FAILED'}: {message}")
    if not condition:
        failures.append(message)


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    return window, area, region


def override():
    window, area, region = view3d()
    return {"window": window, "area": area, "region": region}


def redraw():
    with bpy.context.temp_override(**override()):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def recording(name, call):
    drawn.add(name)
    try:
        call()
    except Exception:
        draw_errors.append(traceback.format_exc())
        raise


def watch_panel(panel):
    original = panel.draw

    def draw(self, context):
        recording(panel.__name__, lambda: original(self, context))

    panel.draw = draw


def watch_list(list_class):
    original = list_class.draw_item

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        recording(
            list_class.__name__,
            lambda: original(self, context, layout, data, item, icon, active_data, active_propname, index),
        )

    list_class.draw_item = draw_item


def open_all_panels(panels):
    for panel in panels:
        bpy.utils.unregister_class(panel)
        panel.bl_options = set()
        bpy.utils.register_class(panel)


def show_tab():
    _window, area, _region = view3d()
    area.spaces.active.show_region_ui = True
    sidebar = next(r for r in area.regions if r.type == "UI")
    try:
        sidebar.active_panel_category = "MorennBridge"
    except (TypeError, AttributeError):
        return False  # read-only until the sidebar has been drawn once
    area.tag_redraw()
    return True


def snapshot():
    scene = bpy.context.scene
    state = {"unit": round(scene.unit_settings.scale_length, 6), "fps": scene.render.fps}
    for obj in bpy.data.objects:
        entry = [tuple(round(v, 3) for v in obj.location), obj.morenn.role, obj.morenn.lod_index]
        entry.append([m.name for m in obj.modifiers])
        if obj.type == "MESH":
            entry.append((len(obj.data.vertices), tuple(round(v, 3) for v in obj.data.vertices[0].co)))
        elif obj.type == "ARMATURE":
            entry.append([tuple(round(v, 3) for v in b.matrix_local[0]) for b in obj.data.bones])
        state[obj.name] = entry
    return state


def fresh(metres=False):
    fixtures.fresh_scene(cm=not metres)
    return fixtures.make_cube("Crate", size=10.0)


def undo_case(name, prepare, run, changes=True):
    """Run ``run`` as an undoable operator, then check that undo restores the scene."""
    prepare()
    bpy.context.view_layer.update()
    with bpy.context.temp_override(**override()):
        bpy.ops.ed.undo_push(message="before")
    before = snapshot()
    with bpy.context.temp_override(**override()):
        run()
    yield
    after = snapshot()
    check((after != before) == changes, f"{name} {'changes' if changes else 'keeps'} the scene")
    with bpy.context.temp_override(**override()):
        bpy.ops.ed.undo()
    yield
    check(snapshot() == before, f"{name} is undone")


def select_crate():
    crate = bpy.data.objects["Crate"]
    fixtures.select_only(crate)


def prepare_rig():
    fixtures.fresh_scene(cm=True)
    rig = fixtures.make_armature("Rig", bones=3, bone_length=10.0)
    fixtures.select_only(rig)


def prepare_mirrored():
    cube = fresh()
    cube.scale = (-1, 1, 1)
    bpy.context.view_layer.update()
    fixtures.select_only(cube)
    fixtures.call(bpy.ops.morenn.validate)


def prepare_lods():
    cube = fresh()
    lod = fixtures.make_cube("Other", size=5.0)
    fixtures.select_only(lod, cube)


def prepare_export():
    fresh()
    select_crate()
    bpy.context.scene.morenn.export_dir = tempfile.mkdtemp()


def push_without_blocking():
    """A push returns at once and starts no thread; timer steps finish it when the editor answers."""
    import threading
    from unittest import mock

    from tests.test_remote import PORT, FakeUnrealNode, loopback_multicast_available

    if not loopback_multicast_available():
        print("[gui] skipped push: loopback multicast is not available")
        return
    ue_ops = fixtures.addon_module("ops.ue_ops")
    prefs = fixtures.addon_module("prefs").get_prefs()
    runtime = bpy.context.window_manager.morenn_rt
    fake = FakeUnrealNode()
    fake.delay = 1.0
    fake.start()
    threads = set(threading.enumerate())
    port, prefs.multicast_port = prefs.multicast_port, PORT
    try:
        with mock.patch.object(ue_ops.compat, "online_access_allowed", return_value=True):
            prepare_export()
            bpy.ops.morenn.export_ue()
            runtime.ue_nodes.clear()
            node = runtime.ue_nodes.add()
            node.node_id, node.project = fake.node_id, "FakeProject"
            with bpy.context.temp_override(**override()):
                started = bpy.ops.morenn.ue_push("INVOKE_DEFAULT")
            check(started == {"RUNNING_MODAL"}, "push returns while Unreal imports")
            check(ue_ops.busy(), "push is running")
            extra = set()
            for _ in range(100):
                extra |= set(threading.enumerate()) - threads
                if not ue_ops.busy() and runtime.ue_status != "Importing in FakeProject…":
                    break
                yield
            check(not extra, f"push starts no thread ({sorted(t.name for t in extra)})")
            check(runtime.ue_status == "Done", f"push finishes on timer steps (status: {runtime.ue_status})")
            check(len(fake.commands) == 1, "Unreal received one import command")
    finally:
        fake.close()
        prefs.multicast_port = port
        runtime.ue_nodes.clear()


def undo_cases(fbx_path):
    return (
        ("scene setup", lambda: fresh(metres=True), lambda: bpy.ops.morenn.scene_setup("EXEC_DEFAULT", True)),
        ("convert units", lambda: fresh(metres=True), lambda: bpy.ops.morenn.scene_convert_units("EXEC_DEFAULT", True)),
        (
            "add socket",
            lambda: (fresh(), select_crate()),
            lambda: bpy.ops.morenn.add_socket("EXEC_DEFAULT", True, name="Lid"),
        ),
        (
            "add collision",
            lambda: (fresh(), select_crate()),
            lambda: bpy.ops.morenn.add_collision("EXEC_DEFAULT", True, kind="UBX"),
        ),
        ("make LOD chain", prepare_lods, lambda: bpy.ops.morenn.make_lod_chain("EXEC_DEFAULT", True)),
        (
            "set role",
            lambda: (fresh(), select_crate()),
            lambda: bpy.ops.morenn.set_role("EXEC_DEFAULT", True, role="COLLISION"),
        ),
        (
            "convert bone axes",
            prepare_rig,
            lambda: bpy.ops.morenn.convert_bone_axes("EXEC_DEFAULT", True, direction="TO_UNREAL"),
        ),
        ("fix issue", prepare_mirrored, lambda: bpy.ops.morenn.fix_issue("EXEC_DEFAULT", True, index=0)),
        ("fix all", prepare_mirrored, lambda: bpy.ops.morenn.fix_all("EXEC_DEFAULT", True)),
        ("import", fresh, lambda: bpy.ops.morenn.import_ue("EXEC_DEFAULT", True, filepath=fbx_path)),
    )


def steps(addon):
    panels = fixtures.addon_module("ui.panels").classes
    lists = fixtures.addon_module("ui.lists").classes
    open_all_panels(panels)
    for panel in panels:
        watch_panel(panel)
    for list_class in lists:
        watch_list(list_class)

    while not show_tab():
        yield
    cube = fresh()
    fixtures.select_only(cube)
    for _ in range(3):
        redraw()
        yield

    prepare_rig()
    redraw()
    yield

    prepare_mirrored()
    bpy.ops.morenn.scene_setup()
    runtime = bpy.context.window_manager.morenn_rt
    node = runtime.ue_nodes.add()
    node.project = "Project"
    redraw()
    yield
    runtime.ue_nodes.clear()

    expected = {cls.__name__ for cls in panels} | {cls.__name__ for cls in lists}
    check(drawn == expected, f"panels and lists drawn: missing {sorted(expected - drawn)}")
    check(not draw_errors, "no drawing errors" if not draw_errors else draw_errors[0])

    yield from push_without_blocking()

    prepare_export()
    out = bpy.context.scene.morenn.export_dir
    bpy.ops.morenn.export_ue()
    fbx_path = os.path.join(out, "SM_Crate.fbx")

    yield from undo_case("export", prepare_export, lambda: bpy.ops.morenn.export_ue("EXEC_DEFAULT", True), False)
    check(len(bpy.data.scenes) == 1, "export leaves one scene")
    for name, prepare, run in undo_cases(fbx_path):
        yield from undo_case(name, prepare, run)


def run(generator):
    try:
        next(generator)
    except StopIteration:
        print(f"[gui] {len(failures)} failure(s)")
        sys.stdout.flush()
        os._exit(1 if failures else 0)
    except Exception:
        traceback.print_exc()
        os._exit(1)
    return 0.2


bpy.context.preferences.view.show_splash = False
namespace = runpy.run_path(str(ROOT / "tests" / "run_tests.py"), run_name="gui_session")
loaded = namespace["load_addon"]()
os.environ["MORENN_ADDON_MODULE"] = loaded.__name__
fixtures = importlib.import_module("tests.fixtures")

generator = steps(loaded)
bpy.app.timers.register(lambda: run(generator), first_interval=1.0, persistent=True)
