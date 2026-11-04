# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol test for the remote-execution client against a fake Unreal node (same JSON protocol)."""

import json
import os
import socket
import struct
import threading
import time
import types
import unittest
from unittest import mock

import bpy

from tests import fixtures
from tests.test_export_anim import _animated_character
from tests.test_export_skeletal import _rigged_character

GROUP, PORT = "239.0.0.1", 6799  # a test port so a real editor is never disturbed


class FakeUnrealNode(threading.Thread):
    """Answers ping with pong, connects back on open_connection, echoes commands."""

    def __init__(self):
        super().__init__(daemon=True)
        self.node_id = "fake-ue-node"
        self.stop = threading.Event()
        self.commands = []
        self.success = True
        self.output = [{"type": "Info", "output": "hello from fake UE"}]
        self.delay = 0.0
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.udp.bind(("0.0.0.0", PORT))
        mreq = socket.inet_aton(GROUP) + socket.inet_aton("127.0.0.1")
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 0))
        self.udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        self.udp.settimeout(0.1)
        self.tcp = None

    def _send(self, msg_type, dest, data=None):
        msg = {"version": 1, "magic": "ue_py", "type": msg_type, "source": self.node_id, "dest": dest}
        if data is not None:
            msg["data"] = data
        self.udp.sendto(json.dumps(msg, separators=(",", ":")).encode(), (GROUP, PORT))

    def run(self):
        while not self.stop.is_set():
            try:
                raw, _ = self.udp.recvfrom(65536)
            except TimeoutError:
                self._serve_tcp()
                continue
            except OSError:
                break
            try:
                msg = json.loads(raw.decode())
            except ValueError:
                continue
            if msg.get("magic") != "ue_py" or msg.get("source") == self.node_id:
                continue
            if msg.get("dest") and msg["dest"] != self.node_id:
                continue
            if msg["type"] == "ping":
                self._send(
                    "pong",
                    msg["source"],
                    {
                        "user": "tester",
                        "machine": "localhost",
                        "engine_version": "5.8.4",
                        "engine_root": "/UE",
                        "project_root": "/P",
                        "project_name": "FakeProject",
                    },
                )
            elif msg["type"] == "open_connection":
                d = msg["data"]
                self.tcp = socket.create_connection((d["command_ip"], d["command_port"]), timeout=2.0)
                self.tcp.settimeout(0.1)
            elif msg["type"] == "close_connection":
                if self.tcp is not None:
                    self.tcp.close()
                    self.tcp = None

    def _serve_tcp(self):
        if self.tcp is None:
            return
        try:
            raw = self.tcp.recv(65536)
        except TimeoutError:
            return
        except OSError:
            self.tcp = None
            return
        if not raw:
            return
        msg = json.loads(raw.decode())
        data = msg["data"]
        self.commands.append(data)
        result = {
            "success": self.success,
            "command": data["command"],
            "result": "42" if data["exec_mode"] == "EvaluateStatement" else "",
            "output": self.output,
        }
        reply = {
            "version": 1,
            "magic": "ue_py",
            "type": "command_result",
            "source": self.node_id,
            "dest": msg["source"],
            "data": result,
        }
        payload = json.dumps(reply, separators=(",", ":")).encode()
        time.sleep(self.delay)
        # fragment the answer to exercise reassembly
        self.tcp.sendall(payload[:10])
        time.sleep(0.05)
        self.tcp.sendall(payload[10:])


def loopback_multicast_available(timeout=0.5):
    """Some CI runners deliver no multicast on loopback; the protocol test cannot run there."""
    try:
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        rx.bind(("0.0.0.0", PORT))
        rx.setsockopt(
            socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(GROUP) + socket.inet_aton("127.0.0.1")
        )
        rx.settimeout(timeout)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", 0))
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        tx.sendto(b"probe", (GROUP, PORT))
        try:
            return rx.recvfrom(16)[0] == b"probe"
        except TimeoutError:
            return False
        finally:
            tx.close()
            rx.close()
    except OSError:
        return False


class RemoteProtocolTests(unittest.TestCase):
    def setUp(self):
        self.remote = fixtures.addon_module("ue.remote")
        if not loopback_multicast_available():
            self.skipTest("loopback multicast is not available on this machine")
        try:
            self.fake = FakeUnrealNode()
        except OSError as exc:
            self.skipTest(f"multicast socket could not be created: {exc}")
        self.fake.start()
        time.sleep(0.2)

    def tearDown(self):
        self.fake.stop.set()
        self.fake.join(timeout=2.0)
        self.fake.udp.close()

    def test_discover_connect_run_close(self):
        with self.remote.RemoteSession(GROUP, PORT, "0.0.0.0", "127.0.0.1") as session:
            nodes = session.discover(timeout=2.0, ping_interval=0.3)
            self.assertEqual([n.project_name for n in nodes], ["FakeProject"])
            node = nodes[0]
            self.assertEqual(node.engine_version, "5.8.4")
            session.connect(node, timeout=3.0)
            res = session.run_file("/tmp/x.py", "arg one", timeout=5.0)
            self.assertTrue(res.success)
            self.assertEqual(res.output, [("Info", "hello from fake UE")])
            self.assertEqual(self.fake.commands[-1]["exec_mode"], "ExecuteFile")
            self.assertEqual(self.fake.commands[-1]["command"], '"/tmp/x.py" "arg one"')
            self.assertTrue(self.fake.commands[-1]["unattended"])
            res = session.evaluate("1+41", timeout=5.0)
            self.assertEqual(res.result, "42")
        time.sleep(0.3)
        self.assertIsNone(self.fake.tcp)

    def test_commands_need_a_connection(self):
        session = self.remote.RemoteSession(GROUP, PORT, "0.0.0.0", "127.0.0.1")
        with self.assertRaises(self.remote.RemoteError):
            session.run("print(1)")


class UnrealOperatorTests(unittest.TestCase):
    def setUp(self):
        self.scene = fixtures.fresh_scene(cm=True)
        self.tmp = fixtures.temp_dir()
        self.scene.morenn.export_dir = self.tmp
        self.scene.morenn.push_after_export = False
        self.ue_ops = fixtures.addon_module("ops.ue_ops")
        self.rt = bpy.context.window_manager.morenn_rt
        self.rt.ue_nodes.clear()
        self.prefs = fixtures.addon_module("prefs").get_prefs()
        self.original_port = self.prefs.multicast_port
        patcher = mock.patch.object(self.ue_ops.compat, "online_access_allowed", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.prefs.multicast_port = self.original_port
        self.rt.ue_nodes.clear()
        fake = getattr(self, "fake", None)
        if fake is not None:
            fake.stop.set()
            fake.join(timeout=2.0)
            fake.udp.close()

    def start_fake_editor(self):
        if not loopback_multicast_available():
            self.skipTest("loopback multicast is not available on this machine")
        self.fake = FakeUnrealNode()
        self.fake.start()
        time.sleep(0.2)
        self.prefs.multicast_port = PORT
        node = self.rt.ue_nodes.add()
        node.node_id, node.project, node.engine, node.machine = "fake-ue-node", "FakeProject", "5.8.4", "localhost"
        self.rt.ue_selected = 0

    def export_cube(self):
        fixtures.select_only(fixtures.make_cube("Crate"))
        bpy.ops.morenn.export_ue()
        return self.scene.morenn.last_manifest

    def test_push_applies_the_editor_report(self):
        self.start_fake_editor()
        rig, _body = _rigged_character("Hero")
        fixtures.select_only(rig)
        bpy.ops.morenn.export_ue()
        manifest = self.scene.morenn.last_manifest
        result = {
            "items": [
                {
                    "file": "SKM_Hero.fbx",
                    "success": True,
                    "imported": ["/Game/SKM_Hero"],
                    "skeleton": "/Game/SKEL_Hero",
                    "source_armature": rig.name,
                }
            ]
        }
        with open(manifest + ".result.json", "w", encoding="utf-8") as fh:
            json.dump(result, fh)
        self.assertEqual(bpy.ops.morenn.ue_push(), {"FINISHED"})
        self.assertEqual(rig.data.morenn.ue_skeleton_path, "/Game/SKEL_Hero")
        self.assertEqual(self.fake.commands[-1]["exec_mode"], "ExecuteFile")
        self.assertIn("mb_ue_import.py", self.fake.commands[-1]["command"])
        self.assertIn(manifest, self.fake.commands[-1]["command"])
        self.assertEqual(self.rt.ue_status, "Done")

    def test_push_after_export_runs_the_push(self):
        self.start_fake_editor()
        cube = fixtures.make_cube("Crate")
        fixtures.select_only(cube)
        self.scene.morenn.push_after_export = True
        fixtures.call(bpy.ops.morenn.export_ue)
        self.assertEqual(len(self.fake.commands), 1)

    def test_push_reports_editor_errors(self):
        self.start_fake_editor()
        manifest = self.export_cube()
        self.fake.success = False
        self.fake.output = [{"type": "Error", "output": "import exploded"}, {"type": "Warning", "output": "careful"}]
        self.assertEqual(fixtures.call(bpy.ops.morenn.ue_push), {"CANCELLED"})
        text = fixtures.log_text()
        self.assertIn("UE: import exploded", text)
        self.assertIn("UE: careful", text)
        self.assertIn("Unreal did not write a result file", text)
        self.assertFalse(os.path.exists(manifest + ".result.json"))

    def test_push_reports_failed_items(self):
        self.start_fake_editor()
        manifest = self.export_cube()
        with open(manifest + ".result.json", "w", encoding="utf-8") as fh:
            json.dump({"items": [{"file": "SM_Crate.fbx", "success": False, "errors": ["no permission"]}]}, fh)
        self.assertEqual(fixtures.call(bpy.ops.morenn.ue_push), {"CANCELLED"})
        self.assertIn("UE failed on SM_Crate.fbx: no permission", fixtures.log_text())
        self.assertEqual(self.rt.ue_status, "Failed")

    def test_push_shows_item_warnings(self):
        self.start_fake_editor()
        manifest = self.export_cube()
        item = {"file": "SM_Crate.fbx", "success": True, "imported": ["/Game/SM_Crate"], "warnings": ["/Game/X gone"]}
        with open(manifest + ".result.json", "w", encoding="utf-8") as fh:
            json.dump({"items": [item], "warnings": ["Imported with Force Front X Axis off"]}, fh)
        self.assertEqual(fixtures.call(bpy.ops.morenn.ue_push), {"FINISHED"})
        self.assertIn("WARNING: UE, SM_Crate.fbx: /Game/X gone", fixtures.log_text())
        self.assertIn("WARNING: UE: Imported with Force Front X Axis off", fixtures.log_text())

    def test_push_without_a_connection_back(self):
        self.start_fake_editor()
        self.export_cube()
        remote = fixtures.addon_module("ue.remote")
        with mock.patch.object(remote.RemoteSession, "connect", side_effect=remote.RemoteError("no answer")):
            self.assertEqual(fixtures.call(bpy.ops.morenn.ue_push), {"CANCELLED"})
        self.assertIn("no answer", fixtures.log_text())
        self.assertEqual(self.rt.ue_status, "Failed")

    def test_any_error_in_the_worker_thread_is_reported(self):
        self.start_fake_editor()
        self.export_cube()
        remote = fixtures.addon_module("ue.remote")
        for operator in (bpy.ops.morenn.ue_push, bpy.ops.morenn.ue_pull):
            with self.subTest(operator=operator.idname()):
                self.scene.morenn.log.clear()
                with mock.patch.object(remote.RemoteSession, "run_file", side_effect=KeyError("command")):
                    self.assertEqual(fixtures.call(operator), {"CANCELLED"})
                self.assertIn("Unreal Link failed: unexpected KeyError", fixtures.log_text())
                self.assertEqual(self.rt.ue_status, "Failed")
        push = self.ue_ops.MORENN_OT_ue_push
        state = self.modal_state(push)
        state._report = fixtures.addon_module("core.log").Report("Push to Unreal")
        silent = types.SimpleNamespace(error=None, result=None)
        self.assertEqual(push.finish(state, bpy.context, silent), {"CANCELLED"})
        self.assertIn("No answer from Unreal", fixtures.log_text())

    def modal_state(self, operator):
        """Stand-in for a running modal operator instance (bpy operators cannot be created directly)."""
        state = types.SimpleNamespace(manifest="", report=mock.Mock(), _timer=None)
        state.cancel = lambda context: operator.cancel(state, context)
        state.finish = lambda context, job: operator.finish(state, context, job)
        return state

    def test_push_waits_in_a_thread_and_finishes_on_the_main_thread(self):
        self.start_fake_editor()
        self.fake.delay = 0.5
        self.export_cube()
        push = self.ue_ops.MORENN_OT_ue_push
        state = self.modal_state(push)
        state._job = push.start_job(state, bpy.context)
        self.assertTrue(self.ue_ops.busy())
        self.assertFalse(bpy.ops.morenn.ue_push.poll())
        self.assertEqual(self.rt.ue_status, "Importing in FakeProject…")
        timer = types.SimpleNamespace(type="TIMER")
        self.assertEqual(push.modal(state, bpy.context, timer), {"PASS_THROUGH"})
        state._job.wait()
        self.assertEqual(push.modal(state, bpy.context, types.SimpleNamespace(type="MOUSEMOVE")), {"PASS_THROUGH"})
        self.assertEqual(push.modal(state, bpy.context, timer), {"FINISHED"})
        self.assertFalse(self.ue_ops.busy())
        self.assertEqual(self.rt.ue_status, "Done")
        self.assertIn("mb_ue_import.py", self.fake.commands[-1]["command"])

    def test_push_without_a_window_waits_for_the_editor(self):
        self.start_fake_editor()
        self.export_cube()
        self.assertEqual(bpy.ops.morenn.ue_push("INVOKE_DEFAULT"), {"FINISHED"})
        self.assertEqual(len(self.fake.commands), 1)

    def test_cancel_removes_the_timer(self):
        wm = bpy.context.window_manager
        state = self.modal_state(self.ue_ops.MORENN_OT_ue_push)
        state._timer = wm.event_timer_add(10.0, window=wm.windows[0])
        self.ue_ops.MORENN_OT_ue_push.cancel(state, bpy.context)
        self.assertIsNone(state._timer)

    def test_export_does_not_start_a_second_push(self):
        self.start_fake_editor()
        self.fake.delay = 0.5
        self.export_cube()
        state = self.modal_state(self.ue_ops.MORENN_OT_ue_push)
        job = self.ue_ops.MORENN_OT_ue_push.start_job(state, bpy.context)
        self.scene.morenn.push_after_export = True
        fixtures.call(bpy.ops.morenn.export_ue)
        job.wait()
        self.assertIn("Not pushed to Unreal: the previous push is still running", fixtures.log_text())
        self.assertEqual(len(self.fake.commands), 1)

    def test_push_needs_a_manifest_and_an_editor(self):
        self.assertFalse(bpy.ops.morenn.ue_push.poll())

    def test_pull_imports_what_the_editor_exported(self):
        self.start_fake_editor()
        done, _manifest, report = fixtures.run_export([fixtures.make_cube("Crate")], self.tmp)
        self.assertTrue(report.ok)
        fixtures.fresh_scene(cm=True)
        self.scene.morenn.export_dir = self.tmp
        out = os.path.join(self.tmp, "from_unreal")
        os.makedirs(out)
        items = [
            {"file": done[0].path, "success": True},
            {"asset_path": "/Game/Broken", "success": False, "errors": ["cannot export"]},
        ]
        with open(os.path.join(out, "pull.result.json"), "w", encoding="utf-8") as fh:
            json.dump({"items": items}, fh)
        fixtures.call(bpy.ops.morenn.ue_pull)
        self.assertIn("SM_Crate", bpy.data.objects)
        self.assertIn("UE could not export /Game/Broken: cannot export", fixtures.log_text())
        self.assertIn("mb_ue_export.py", self.fake.commands[-1]["command"])

    def test_pull_keeps_the_unreal_animation_settings(self):
        self.start_fake_editor()
        rig, _body = _animated_character()
        settings = fixtures.export_settings(self.tmp)
        groups = fixtures.addon_module("core.classify").build_anim_groups([rig], self.prefs, self.scene)
        report = fixtures.addon_module("core.log").Report("test")
        done, _manifest = fixtures.addon_module("core.export_fbx").export_groups(bpy.context, groups, settings, report)
        fixtures.fresh_scene(cm=True)
        self.scene.morenn.export_dir = self.tmp
        out = os.path.join(self.tmp, "from_unreal")
        os.makedirs(out)
        jog = {"enable_root_motion": True, "retarget_source_asset": "/Game/Characters/SKM_Hero"}
        item = {"file": done[0].path, "success": True, "skeleton": "/Game/SKEL_Hero", "settings": jog}
        with open(os.path.join(out, "pull.result.json"), "w", encoding="utf-8") as fh:
            json.dump({"items": [item]}, fh)
        fixtures.call(bpy.ops.morenn.ue_pull)
        self.assertEqual(json.loads(bpy.data.actions["A_Hero_Run"].morenn.ue_settings), jog)
        self.assertIn("Unreal animation settings kept on A_Hero_Run for the next push", fixtures.log_text())

    def test_pull_keeps_the_unreal_material_slots(self):
        self.start_fake_editor()
        done, _manifest, report = fixtures.run_export([fixtures.make_cube("Crate")], self.tmp)
        fixtures.fresh_scene(cm=True)
        self.scene.morenn.export_dir = self.tmp
        out = os.path.join(self.tmp, "from_unreal")
        os.makedirs(out)
        slots = [{"slot": "Wood", "material": "/Game/Props/M_Test"}, {"slot": "Unused", "material": None}]
        with open(os.path.join(out, "pull.result.json"), "w", encoding="utf-8") as fh:
            json.dump({"items": [{"file": done[0].path, "success": True, "materials": slots}]}, fh)
        fixtures.call(bpy.ops.morenn.ue_pull)
        crate = bpy.data.objects["SM_Crate"]
        recorded = json.loads(crate.morenn.ue_materials)
        self.assertEqual(list(recorded.values()), [["Wood", "/Game/Props/M_Test"]])
        self.assertEqual(list(recorded), [m.name for m in crate.data.materials])
        self.assertIn("Unreal material slots kept on SM_Crate", fixtures.log_text())

    def test_push_lists_the_animation_settings_set_again(self):
        self.start_fake_editor()
        manifest = self.export_cube()
        item = {"file": "A_Hero_Jog.fbx", "success": True, "imported": ["/Game/A_Hero_Jog"]}
        item["settings"] = ["enable_root_motion", "force_root_lock"]
        with open(manifest + ".result.json", "w", encoding="utf-8") as fh:
            json.dump({"items": [item]}, fh)
        self.assertEqual(fixtures.call(bpy.ops.morenn.ue_push), {"FINISHED"})
        self.assertIn(
            "UE, A_Hero_Jog.fbx: animation settings restored (enable_root_motion, force_root_lock)",
            fixtures.log_text(),
        )

    def test_pull_without_a_result_file(self):
        self.start_fake_editor()
        self.assertEqual(fixtures.call(bpy.ops.morenn.ue_pull), {"CANCELLED"})
        self.assertIn("Unreal did not write a result file", fixtures.log_text())

    def test_offline_blender_refuses(self):
        with mock.patch.object(self.ue_ops.compat, "online_access_allowed", return_value=False):
            self.assertEqual(fixtures.call(bpy.ops.morenn.ue_discover), {"CANCELLED"})

    def test_discovery_modal_fills_the_node_list(self):
        remote = fixtures.addon_module("ue.remote")
        session = mock.Mock()
        session.poll_pongs.return_value = [remote.UENode("id", "me", "box", "5.8.4", "", "", "Game")]
        state = types.SimpleNamespace(
            _timer=None, _session=session, _found={}, _deadline=0.0, _next_ping=0.0, report=mock.Mock()
        )
        state._finish = lambda context, error=None: self.ue_ops.MORENN_OT_ue_discover._finish(state, context, error)
        event = types.SimpleNamespace(type="TIMER")
        result = self.ue_ops.MORENN_OT_ue_discover.modal(state, bpy.context, event)
        self.assertEqual(result, {"FINISHED"})
        self.assertEqual([n.project for n in self.rt.ue_nodes], ["Game"])
        self.assertEqual(self.rt.ue_status, "1 editor(s) found")
        session.close.assert_called_once()

    def test_discovery_without_an_editor(self):
        state = types.SimpleNamespace(_timer=None, _session=None, _found={}, report=mock.Mock())
        self.ue_ops.MORENN_OT_ue_discover._finish(state, bpy.context)
        self.assertIn("No Unreal Editor found", self.rt.ue_status)
        state.report.assert_called_once()

    def test_discovery_error(self):
        state = types.SimpleNamespace(_timer=None, _session=None, _found={}, report=mock.Mock())
        self.ue_ops.MORENN_OT_ue_discover._finish(state, bpy.context, "Ping failed")
        self.assertEqual(self.rt.ue_status, "Ping failed")

    def test_discovery_ignores_other_events(self):
        event = types.SimpleNamespace(type="MOUSEMOVE")
        self.assertEqual(self.ue_ops.MORENN_OT_ue_discover.modal(None, bpy.context, event), {"PASS_THROUGH"})

    def test_setup_help_opens_the_guide(self):
        self.assertEqual(bpy.ops.morenn.ue_setup_help(), {"FINISHED"})
        self.assertIn("MorennBridge UE_SETUP.md", bpy.data.texts)


if __name__ == "__main__":
    unittest.main()
