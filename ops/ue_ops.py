# SPDX-License-Identifier: GPL-3.0-or-later
"""Talk to a running Unreal Editor through Python remote execution."""

import json
import os
import threading
import time

import bpy
from bpy.props import StringProperty

from .. import compat
from ..core import import_fbx
from ..core.log import Report, describe_error
from ..prefs import get_prefs
from ..ue import remote

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ue", "scripts")
_JOBS = []


def _session(prefs):
    return remote.RemoteSession(prefs.multicast_group, prefs.multicast_port, prefs.bind_address, prefs.command_ip)


class RemoteJob:
    """One command sent to Unreal from a worker thread. Only plain values cross the thread, never Blender data."""

    def __init__(self, prefs, node, script, *args):
        self.settings = (prefs.multicast_group, prefs.multicast_port, prefs.bind_address, prefs.command_ip)
        self.node = node
        self.command = (script, *args)
        self.timeout = float(prefs.command_timeout)
        self.result = None
        self.error = None
        self.thread = threading.Thread(target=self._run, name="MorennBridge Unreal Link", daemon=True)

    def _run(self):
        try:
            with remote.RemoteSession(*self.settings) as session:
                session.connect(self.node, timeout=5.0)
                self.result = session.run_file(*self.command, timeout=self.timeout)
        except (remote.RemoteError, OSError) as exc:
            self.error = exc

    def start(self):
        _JOBS.append(self)
        self.thread.start()
        return self

    @property
    def running(self):
        return self.thread.is_alive()

    def wait(self):
        self.thread.join()
        return self


def busy():
    """True while a push or pull waits for Unreal."""
    _JOBS[:] = [job for job in _JOBS if job.running]
    return bool(_JOBS)


def _set_status(context, text):
    wm = context.window_manager
    wm.morenn_rt.ue_status = text
    for window in wm.windows:
        for area in window.screen.areas if window.screen else ():
            if area.type == "VIEW_3D":
                area.tag_redraw()


class RemoteJobOperator:
    """Push and pull: the editor works in a thread while Blender stays usable; results land on the main thread.

    Subclasses implement ``start_job(context)`` (returns a started RemoteJob or None) and ``finish(context, job)``.
    Without a window (background mode, scripts calling ``execute``) the operator waits for the job.
    """

    _job = None
    _timer = None

    def invoke(self, context, event):
        if context.window is None:
            return self.execute(context)
        self._job = self.start_job(context)
        if self._job is None:
            return {"CANCELLED"}
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        job = self.start_job(context)
        if job is None:
            return {"CANCELLED"}
        return self.finish(context, job.wait())

    def modal(self, context, event):
        if event.type != "TIMER" or self._job.running:
            return {"PASS_THROUGH"}
        self.cancel(context)
        return self.finish(context, self._job)

    def cancel(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


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


def _editor_output(result, report):
    for kind, text in result.output:
        if kind.lower() == "error":
            report.error(f"UE: {text}")
        elif kind.lower() == "warning":
            report.warning(f"UE: {text}")


def _connection_failed(operator, context, job):
    if job.error is None:
        return False
    operator._report.error(str(job.error))
    _set_status(context, "Failed")
    operator._report.flush(context.scene, operator)
    return True


class MORENN_OT_ue_push(RemoteJobOperator, bpy.types.Operator):
    """Import the last export (its manifest) into the selected Unreal Editor"""

    bl_idname = "morenn.ue_push"
    bl_label = "Push Last Export to Unreal"

    manifest: StringProperty(subtype="FILE_PATH", default="")

    @classmethod
    def poll(cls, context):
        rt = context.window_manager.morenn_rt
        return bool(context.scene.morenn.last_manifest) and len(rt.ue_nodes) > 0 and not busy()

    def start_job(self, context):
        if not _online_ok(self):
            return None
        self._report = Report("Push to Unreal")
        node = _selected_node(context)
        self._manifest = bpy.path.abspath(self.manifest or context.scene.morenn.last_manifest)
        if node is None or not os.path.exists(self._manifest):
            self._report.error("No Unreal Editor selected or no manifest to push")
            self._report.flush(context.scene, self)
            return None
        _set_status(context, f"Importing in {node.project_name}…")
        script = os.path.join(SCRIPTS_DIR, "mb_ue_import.py")
        return RemoteJob(get_prefs(context), node, script, self._manifest).start()

    def finish(self, context, job):
        if _connection_failed(self, context, job):
            return {"CANCELLED"}
        report = self._report
        _editor_output(job.result, report)
        if not job.result.success:
            report.error("Unreal reported a Python error while importing (see the UE Output Log)")
        result_path = self._manifest + ".result.json"
        if os.path.exists(result_path):
            with open(result_path, encoding="utf-8") as fh:
                ue_report = json.load(fh)
            _apply_ue_report(context, ue_report, report)
        else:
            report.warning("Unreal did not write a result file")
        _set_status(context, "Done" if report.ok else "Failed")
        report.flush(context.scene, self)
        return {"FINISHED"} if report.ok else {"CANCELLED"}


def _apply_ue_report(context, ue_report, report):
    for warning in ue_report.get("warnings", []):
        report.warning(f"UE: {warning}")
    for item in ue_report.get("items", []):
        if item.get("success"):
            report.info(f"UE imported {item['file']}: {', '.join(item.get('imported', []))}")
        else:
            report.error(f"UE failed on {item['file']}: {'; '.join(item.get('errors', []))[:300]}")
        for warning in item.get("warnings", []):
            report.warning(f"UE, {item['file']}: {warning}")
        skeleton = item.get("skeleton")
        arm_name = item.get("source_armature")
        if skeleton and arm_name:
            arm = bpy.data.objects.get(arm_name)
            if arm is not None and arm.type == "ARMATURE":
                arm.data.morenn.ue_skeleton_path = skeleton
                report.info(f"'{arm_name}' now targets skeleton {skeleton}")


class MORENN_OT_ue_pull(RemoteJobOperator, bpy.types.Operator):
    """Export the assets selected in Unreal's Content Browser and import them here"""

    bl_idname = "morenn.ue_pull"
    bl_label = "Pull Selected Assets from Unreal"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return len(context.window_manager.morenn_rt.ue_nodes) > 0 and not busy()

    def start_job(self, context):
        if not _online_ok(self):
            return None
        self._report = Report("Pull from Unreal")
        node = _selected_node(context)
        if node is None:
            self._report.error("No Unreal Editor selected")
            self._report.flush(context.scene, self)
            return None
        from .export_ops import resolve_export_dir

        export_dir = resolve_export_dir(context.scene.morenn.export_dir, self._report)
        self._out_dir = os.path.join(export_dir, "from_unreal")
        _set_status(context, f"Exporting from {node.project_name}…")
        script = os.path.join(SCRIPTS_DIR, "mb_ue_export.py")
        return RemoteJob(get_prefs(context), node, script, self._out_dir).start()

    def finish(self, context, job):
        if _connection_failed(self, context, job):
            return {"CANCELLED"}
        prefs = get_prefs(context)
        report = self._report
        _editor_output(job.result, report)
        result_path = os.path.join(self._out_dir, "pull.result.json")
        if not os.path.exists(result_path):
            report.error("Unreal did not write a result file")
            _set_status(context, "Failed")
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
                report.error(f"Import of {item['file']} failed: {describe_error(exc)}")
        report.title = f"Pull: {imported} asset(s)"
        _set_status(context, "Done" if imported else "Failed")
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
