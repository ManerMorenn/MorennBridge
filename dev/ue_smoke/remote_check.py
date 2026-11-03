# SPDX-License-Identifier: GPL-3.0-or-later
"""Unreal Link against a running editor with remote execution on: discovery, a statement, push and pull.

    blender -b --factory-startup --python dev/ue_smoke/remote_check.py -- <out_dir> <content_root> <project_name>

Uses the add-on's operators. Folder, file and asset names carry spaces and accents on purpose, the scripts folder
the editor runs included. Writes <out_dir>/remote_results.json in the format of the smoke test results.
"""

import importlib
import json
import os
import pathlib
import runpy
import shutil
import sys
import traceback
from unittest import mock

import bpy

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MANNY = "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple"
JOG = "/Game/Characters/Mannequins/Anims/Unarmed/Jog/MF_Unarmed_Jog_Fwd"


class Results:
    def __init__(self):
        self.items = []
        self.engine = ""

    def check(self, ok, what, detail=""):
        self.items.append({"ok": bool(ok), "check": what, "detail": str(detail)})
        print(f"[remote] {'ok  ' if ok else 'FAIL'} {what}: {detail}")
        return bool(ok)

    def write(self, out_dir):
        data = {"engine": self.engine, "cases": [{"name": "remote", "item": 10, "results": self.items}]}
        with open(os.path.join(out_dir, "remote_results.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)


def statement(ue_ops, prefs, node, text):
    with ue_ops._session(prefs) as session:
        session.connect(node, timeout=5.0)
        return session.run(text)


def log_tail(lines=6):
    return " | ".join(f"{e.level}: {e.message}" for e in list(bpy.context.scene.morenn.log)[-lines:])


def run(results, out_dir, content_root, project):
    ue_ops = fixtures.addon_module("ops.ue_ops")
    prefs = fixtures.addon_module("prefs").get_prefs()
    work = os.path.join(out_dir, "remote é ü")
    shutil.rmtree(work, ignore_errors=True)
    scripts = os.path.join(work, "scripts ß")
    shutil.copytree(ROOT / "ue" / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__"))
    folder = f"{content_root}/remote"

    with ue_ops._session(prefs) as session:
        nodes = session.discover(timeout=float(prefs.discovery_timeout))
    node = next((n for n in nodes if n.project_name == project), None)
    if not results.check(node, "discovery finds the editor", [n.label for n in nodes]):
        return
    results.engine = node.engine_version
    runtime = bpy.context.window_manager.morenn_rt
    runtime.ue_nodes.clear()
    item = runtime.ue_nodes.add()
    item.node_id, item.user, item.machine = node.node_id, node.user, node.machine
    item.project, item.engine = node.project_name, node.engine_version
    runtime.ue_selected = 0

    with ue_ops._session(prefs) as session:
        session.connect(node, timeout=5.0)
        version = session.evaluate("unreal.SystemLibrary.get_engine_version()")
    results.check(version.success and node.engine_version in version.result, "statement evaluated", version.result)
    cleared = statement(ue_ops, prefs, node, f"unreal.EditorAssetLibrary.delete_directory('{folder}')")
    results.check(cleared.success, "test folder cleared in the editor", cleared.output)

    with mock.patch.object(ue_ops, "SCRIPTS_DIR", scripts):
        scene = fixtures.fresh_scene(cm=True)
        scene.morenn.export_dir = os.path.join(work, "export")
        scene.morenn.ue_content_path = folder
        crate = fixtures.make_cube("Crâte", size=40.0)
        fixtures.select_only(crate)
        fixtures.call(bpy.ops.morenn.export_ue)
        manifest = scene.morenn.last_manifest
        results.check(os.path.exists(manifest), "export with accents in the path", manifest)
        pushed = fixtures.call(bpy.ops.morenn.ue_push)
        results.check(pushed == {"FINISHED"}, "push imports the export", log_tail())
        result_file = manifest + ".result.json"
        if results.check(os.path.exists(result_file), "the editor wrote the result next to the manifest"):
            with open(result_file, encoding="utf-8") as fh:
                imported = [p for i in json.load(fh)["items"] for p in i["imported"]]
            results.check(f"{folder}/SM_Crâte" in imported, "asset name keeps its accent", imported)

        selection = [f"{folder}/SM_Crâte", MANNY, JOG]
        synced = statement(ue_ops, prefs, node, f"unreal.EditorAssetLibrary.sync_browser_to_objects({selection!r})")
        results.check(synced.success, "assets selected in the Content Browser", synced.output)
        scene = fixtures.fresh_scene(cm=True)
        scene.morenn.export_dir = os.path.join(work, "pull")
        pulled = fixtures.call(bpy.ops.morenn.ue_pull)
        results.check(pulled == {"FINISHED"}, "pull imports the selection", log_tail())
        objects = {o.name: o.type for o in bpy.data.objects}
        results.check(objects.get("SM_Crâte") == "MESH", "pulled static mesh", sorted(objects))
        results.check(objects.get("SKM_Manny_Simple") == "ARMATURE", "pulled skeletal mesh", sorted(objects))
        lods = [objects.get(f"SKM_Manny_Simple_LOD{i}") for i in (1, 2)]
        results.check(lods == ["MESH", "MESH"], "skeletal LODs kept apart from LOD0", sorted(objects))
        actions = [a.name for a in bpy.data.actions]
        results.check("MF_Unarmed_Jog_Fwd" in actions, "pulled animation", actions)


def main():
    out_dir, content_root, project = sys.argv[sys.argv.index("--") + 1 :][:3]
    results = Results()
    compat = fixtures.addon_module("compat")
    try:
        with mock.patch.object(compat, "online_access_allowed", return_value=True):
            run(results, out_dir, content_root, project)
    except Exception:
        results.check(False, "remote check raised", traceback.format_exc())
    results.write(out_dir)
    sys.stdout.flush()
    os._exit(0 if all(r["ok"] for r in results.items) else 1)


namespace = runpy.run_path(str(ROOT / "tests" / "run_tests.py"), run_name="ue_smoke")
os.environ["MORENN_ADDON_MODULE"] = namespace["load_addon"]().__name__
fixtures = importlib.import_module("tests.fixtures")
main()
