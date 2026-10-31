# SPDX-License-Identifier: GPL-3.0-or-later
"""Talk to a running Unreal Editor through Python remote execution."""

import json
import os
import time

import bpy
from bpy.props import StringProperty

from .. import compat
from ..core import import_fbx
from ..core.log import Report
from ..prefs import get_prefs
from ..ue import remote

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ue", "scripts")


def _session(prefs):
    return remote.RemoteSession(prefs.multicast_group, prefs.multicast_port, prefs.bind_address, prefs.command_ip)


def _selected_node(context):
    rt = context.window_manager.morenn_rt
    if not (0 <= rt.ue_selected < len(rt.ue_nodes)):
        return None
    item = rt.ue_nodes[rt.ue_selected]
    return remote.UENode(item.node_id, item.user, item.machine, item.engine, "", "", item.project)


def _online_ok(operator):
    if not compat.online_access_allowed():
        operator.report({"ERROR"}, "Blender's 'Allow Online Access' preference is off; enable it to talk to Unreal")
        return False
    return True


class MORENN_OT_ue_discover(bpy.types.Operator):
    """Look for running Unreal Editors with remote execution enabled"""

    bl_idname = "morenn.ue_discover"
    bl_label = "Find Unreal"

    _timer = None
    _session = None
    _found = None
    _deadline = 0.0
    _next_ping = 0.0

    def execute(self, context):
        if not _online_ok(self):
            return {"CANCELLED"}
        prefs = get_prefs(context)
        try:
            self._session = _session(prefs)
            self._session.open_udp()
        except OSError as exc:
            self.report({"ERROR"}, f"Cannot open the discovery socket: {exc}")
            return {"CANCELLED"}
        self._found = {}
        self._deadline = time.monotonic() + float(prefs.discovery_timeout)
        self._next_ping = 0.0
        context.window_manager.morenn_rt.ue_status = "Searching…"
        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        now = time.monotonic()
        if now >= self._next_ping:
            try:
                self._session.ping()
            except OSError as exc:
                self._finish(context, f"Ping failed: {exc}")
                return {"CANCELLED"}
            self._next_ping = now + 1.0
        for node in self._session.poll_pongs():
            self._found[node.node_id] = node
        if now >= self._deadline:
            self._finish(context)
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _finish(self, context, error=None):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
        if self._session is not None:
            self._session.close()
        rt = wm.morenn_rt
        rt.ue_nodes.clear()
        for node in self._found.values():
            item = rt.ue_nodes.add()
            item.node_id, item.user, item.machine = node.node_id, node.user, node.machine
            item.project, item.engine = node.project_name, node.engine_version
        rt.ue_selected = 0
        if error:
            rt.ue_status = error
            self.report({"ERROR"}, error)
        elif self._found:
            rt.ue_status = f"{len(self._found)} editor(s) found"
            self.report({"INFO"}, rt.ue_status)
        else:
            rt.ue_status = "No Unreal Editor found (see Setup help)"
            self.report({"WARNING"}, rt.ue_status)


class MORENN_OT_ue_push(bpy.types.Operator):
    """Import the last export (its manifest) into the selected Unreal Editor"""

    bl_idname = "morenn.ue_push"
    bl_label = "Push Last Export to Unreal"

    manifest: StringProperty(subtype="FILE_PATH", default="")

    @classmethod
    def poll(cls, context):
        return bool(context.scene.morenn.last_manifest) and len(context.window_manager.morenn_rt.ue_nodes) > 0

    def execute(self, context):
        if not _online_ok(self):
            return {"CANCELLED"}
        prefs = get_prefs(context)
        report = Report("Push to Unreal")
        node = _selected_node(context)
        manifest_path = bpy.path.abspath(self.manifest or context.scene.morenn.last_manifest)
        if node is None or not os.path.exists(manifest_path):
            report.error("No Unreal Editor selected or no manifest to push")
            report.flush(context.scene, self)
            return {"CANCELLED"}
        script = os.path.join(SCRIPTS_DIR, "mb_ue_import.py")
        try:
            with _session(prefs) as session:
                session.connect(node, timeout=5.0)
                context.window_manager.morenn_rt.ue_status = f"Importing in {node.project_name}…"
                result = session.run_file(script, manifest_path, timeout=float(prefs.command_timeout))
        except (remote.RemoteError, OSError) as exc:
            report.error(str(exc))
            report.flush(context.scene, self)
            return {"CANCELLED"}
        for kind, text in result.output:
            if kind.lower() == "error":
                report.error(f"UE: {text}")
            elif kind.lower() == "warning":
                report.warning(f"UE: {text}")
        if not result.success:
            report.error("Unreal reported a Python error while importing (see the UE Output Log)")
        result_path = manifest_path + ".result.json"
        if os.path.exists(result_path):
            with open(result_path, encoding="utf-8") as fh:
                ue_report = json.load(fh)
            _apply_ue_report(context, ue_report, report)
        else:
            report.warning("Unreal did not write a result file")
        context.window_manager.morenn_rt.ue_status = "Done" if report.ok else "Failed"
        report.flush(context.scene, self)
        return {"FINISHED"} if report.ok else {"CANCELLED"}


def _apply_ue_report(context, ue_report, report):
    for item in ue_report.get("items", []):
        if item.get("success"):
            report.info(f"UE imported {item['file']}: {', '.join(item.get('imported', []))}")
        else:
            report.error(f"UE failed on {item['file']}: {'; '.join(item.get('errors', []))[:300]}")
        skeleton = item.get("skeleton")
        arm_name = item.get("source_armature")
        if skeleton and arm_name:
            arm = bpy.data.objects.get(arm_name)
            if arm is not None and arm.type == "ARMATURE":
                arm.data.morenn.ue_skeleton_path = skeleton
                report.info(f"'{arm_name}' now targets skeleton {skeleton}")


class MORENN_OT_ue_pull(bpy.types.Operator):
    """Export the assets selected in Unreal's Content Browser and import them here"""

    bl_idname = "morenn.ue_pull"
    bl_label = "Pull Selected Assets from Unreal"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(context.window_manager.morenn_rt.ue_nodes) > 0

    def execute(self, context):
        if not _online_ok(self):
            return {"CANCELLED"}
        prefs = get_prefs(context)
        report = Report("Pull from Unreal")
        node = _selected_node(context)
        if node is None:
            report.error("No Unreal Editor selected")
            report.flush(context.scene, self)
            return {"CANCELLED"}
        from .export_ops import resolve_export_dir

        out_dir = os.path.join(resolve_export_dir(context.scene.morenn.export_dir, report), "from_unreal")
        script = os.path.join(SCRIPTS_DIR, "mb_ue_export.py")
        try:
            with _session(prefs) as session:
                session.connect(node, timeout=5.0)
                result = session.run_file(script, out_dir, timeout=float(prefs.command_timeout))
        except (remote.RemoteError, OSError) as exc:
            report.error(str(exc))
            report.flush(context.scene, self)
            return {"CANCELLED"}
        for kind, text in result.output:
            if kind.lower() in ("error", "warning"):
                getattr(report, kind.lower())(f"UE: {text}")
        result_path = os.path.join(out_dir, "pull.result.json")
        if not os.path.exists(result_path):
            report.error("Unreal did not write a result file")
            report.flush(context.scene, self)
            return {"CANCELLED"}
        with open(result_path, encoding="utf-8") as fh:
            ue_report = json.load(fh)
        imported = 0
        for item in ue_report.get("items", []):
            if not item.get("success"):
                report.error(f"UE could not export {item.get('asset_path')}: {'; '.join(item.get('errors', []))}")
                continue
            try:
                res = import_fbx.import_file(context, item["file"], prefs, report)
                imported += 1
                for arm in res.armatures:
                    if item.get("skeleton"):
                        arm.data.morenn.ue_skeleton_path = item["skeleton"]
                report.info(f"Imported {os.path.basename(item['file'])}", item["file"])
            except Exception as exc:
                report.error(f"Import of {item['file']} failed: {exc!r}")
        report.title = f"Pull: {imported} asset(s)"
        report.flush(context.scene, self)
        return {"FINISHED"} if imported else {"CANCELLED"}


class MORENN_OT_ue_setup_help(bpy.types.Operator):
    """Open the Unreal project setup guide in the Text Editor"""

    bl_idname = "morenn.ue_setup_help"
    bl_label = "Unreal Setup Help"

    def execute(self, context):
        path = os.path.join(os.path.dirname(SCRIPTS_DIR.rstrip(os.sep)), "..", "docs", "UE_SETUP.md")
        path = os.path.normpath(path)
        text = bpy.data.texts.get("MorennBridge UE_SETUP.md")
        if text is None:
            text = bpy.data.texts.load(path)
            text.name = "MorennBridge UE_SETUP.md"
        self.report({"INFO"}, "Guide opened as a text datablock: see the Text Editor")
        return {"FINISHED"}


classes = (MORENN_OT_ue_discover, MORENN_OT_ue_push, MORENN_OT_ue_pull, MORENN_OT_ue_setup_help)
