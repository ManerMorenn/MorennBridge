# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol test for the remote-execution client against a fake Unreal node (same JSON protocol)."""

import json
import os
import select
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
    """Answers ping with pong, connects back on open_connection, echoes commands.

    Like OpenCommandConnection in PythonScriptRemoteExecution.cpp, an open_connection is ignored while connected to
    the same node and endpoint. ``drop_opens`` loses that many open_connection messages; ``connect_delay`` delays
    the connection back; ``delay`` delays each answer; ``hang_up`` closes the connection instead of answering.

    The thread sleeps in ``select`` and only runs Python while it answers a message: Blender 4.5 and 5.0 can crash
    in an export while another Python thread holds the GIL. ``close()`` wakes it through a socket pair (``select``
    on Windows takes sockets only) and closes its sockets.
    """

    def __init__(self):
        super().__init__(daemon=True)
        self.node_id = "fake-ue-node"
        self.commands = []
        self.success = True
        self.output = [{"type": "Info", "output": "hello from fake UE"}]
        self.delay = 0.0
        self.hang_up = False
        self.drop_opens = 0
        self.connect_delay = 0.0
        self.opens = 0
        self.connections = 0
        self.endpoint = None
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
        self.udp.setblocking(False)
        self.tcp = None
        self._wake, self._waker = socket.socketpair()

    def close(self):
        self._waker.send(b"x")
        if self.is_alive():
            self.join(timeout=2.0)
        for sock in (self.udp, self.tcp, self._wake, self._waker):
            if sock is not None:
                sock.close()

    def _send(self, msg_type, dest, data=None):
        msg = {"version": 1, "magic": "ue_py", "type": msg_type, "source": self.node_id, "dest": dest}
        if data is not None:
            msg["data"] = data
        self.udp.sendto(json.dumps(msg, separators=(",", ":")).encode(), (GROUP, PORT))

    def run(self):
        while True:
            sockets = [self._wake, self.udp] + ([self.tcp] if self.tcp is not None else [])
            readable = select.select(sockets, [], [])[0]
            if self._wake in readable:
                return
            if self.udp in readable and not self._serve_udp():
                return
            if self.tcp is not None and self.tcp in readable:
                self._serve_tcp()

    def _serve_udp(self):
        try:
            raw, _ = self.udp.recvfrom(65536)
        except (BlockingIOError, InterruptedError):
            return True
        except OSError:
            return False
        try:
            msg = json.loads(raw.decode())
        except ValueError:
            return True
        if msg.get("magic") != "ue_py" or msg.get("source") == self.node_id:
            return True
        if msg.get("dest") and msg["dest"] != self.node_id:
            return True
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
            self._open(msg)
        elif msg["type"] == "close_connection":
            self._close()
        return True

    def _open(self, msg):
        self.opens += 1
        if self.drop_opens > 0:
            self.drop_opens -= 1
            return
        endpoint = (msg["source"], msg["data"]["command_ip"], msg["data"]["command_port"])
        if self.tcp is not None and self.endpoint == endpoint:
            return
        self._close()
        time.sleep(self.connect_delay)
        try:
            self.tcp = socket.create_connection(endpoint[1:], timeout=2.0)
        except OSError:
            return
        self.endpoint = endpoint
        self.connections += 1

    def _close(self):
        if self.tcp is not None:
            self.tcp.close()
        self.tcp = self.endpoint = None

    def _serve_tcp(self):
        try:
            raw = self.tcp.recv(65536)
        except OSError:
            raw = b""
        if not raw:  # the client closed the connection
            self._close()
            return
        msg = json.loads(raw.decode())
        data = msg["data"]
        self.commands.append(data)
        if self.hang_up:
            self._close()
            return
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
        try:
            # fragment the answer to exercise reassembly
            self.tcp.sendall(payload[:10])
            time.sleep(0.05)
            self.tcp.sendall(payload[10:])
        except OSError:
            self._close()


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
        self.fake.close()

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

    def discovered(self):
        session = self.remote.RemoteSession(GROUP, PORT, "0.0.0.0", "127.0.0.1").__enter__()
        self.addCleanup(session.close)
        return session, session.discover(timeout=2.0, ping_interval=0.3)[0]

    def test_lost_open_connection_is_sent_again(self):
        session, node = self.discovered()
        self.fake.drop_opens = 2
        session.connect(node, timeout=3.0, resend_interval=0.1)
        self.assertEqual(session.evaluate("1+41", timeout=5.0).result, "42")
        self.assertGreaterEqual(self.fake.opens, 3)
        self.assertEqual(self.fake.connections, 1)

    def test_late_connection_back_is_made_once(self):
        """The repeats sent while the editor was connecting are ignored, as Unreal does: the session keeps working."""
        session, node = self.discovered()
        self.fake.connect_delay = 0.5
        session.connect(node, timeout=3.0, resend_interval=0.1)
        self.assertEqual(session.evaluate("1+41", timeout=5.0).result, "42")
        self.assertGreater(self.fake.opens, 1)
        self.assertEqual(self.fake.connections, 1)

    def test_no_connection_back_within_the_timeout(self):
        session, node = self.discovered()
        self.fake.drop_opens = 1000
        started = time.monotonic()
        with self.assertRaisesRegex(self.remote.RemoteError, "did not connect back within 0.6s"):
            session.connect(node, timeout=0.6, resend_interval=0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.6)
        self.assertGreaterEqual(self.fake.opens, 3)

    def test_steps_never_block(self):
        """With wait=0 every step returns at once, while the editor takes its time to connect and to answer."""
        session, node = self.discovered()
        self.fake.connect_delay = 0.5
        self.fake.delay = 0.5
        steps = []

        def until(poll):
            """Poll until done; returns the result and how many steps returned while the editor was still busy."""
            busy = 0
            while True:
                started = time.monotonic()
                done = poll(0.0)
                steps.append(time.monotonic() - started)
                if done:
                    return done, busy
                busy += 1
                time.sleep(0.02)

        session.start_connect(node, timeout=3.0, resend_interval=0.1)
        connected, busy_connecting = until(session.poll_connect)
        self.assertTrue(connected)
        session.start_command("1+41", self.remote.EXEC_EVALUATE, timeout=5.0)
        result, busy_answering = until(session.poll_result)
        self.assertEqual(result.result, "42")
        self.assertGreater(busy_connecting, 0)
        self.assertGreater(busy_answering, 0)
        self.assertLess(max(steps), 0.25)
        self.assertEqual(self.fake.connections, 1)

    def test_connection_made_while_blender_was_busy(self):
        """The editor connected back during an export that outlasted the timeout: the next step takes it."""
        session, node = self.discovered()
        started = time.monotonic()
        session.start_connect(node, timeout=0.3)
        self.assertFalse(session.poll_connect(0.0))
        while self.fake.tcp is None and time.monotonic() < started + 3.0:
            time.sleep(0.02)
        time.sleep(max(0.0, started + 0.4 - time.monotonic()))
        self.assertTrue(session.poll_connect(0.0))
        self.assertEqual(session.evaluate("1+41", timeout=5.0).result, "42")

    def test_no_answer_within_the_timeout(self):
        session, node = self.discovered()
        session.connect(node, timeout=3.0)
        self.fake.delay = 1.0
        with self.assertRaisesRegex(self.remote.RemoteError, "No answer from Unreal within 0.3s"):
            session.evaluate("1+41", timeout=0.3)

    def test_editor_hanging_up(self):
        session, node = self.discovered()
        session.connect(node, timeout=3.0)
        self.fake.hang_up = True
        with self.assertRaisesRegex(self.remote.RemoteError, "Unreal closed the command connection"):
            session.evaluate("1+41", timeout=5.0)

    def test_commands_need_a_connection(self):
        session = self.remote.RemoteSession(GROUP, PORT, "0.0.0.0", "127.0.0.1")
        with self.assertRaises(self.remote.RemoteError):
            session.run("print(1)")
        with self.assertRaisesRegex(self.remote.RemoteError, "No connection was requested"):
            session.poll_connect()
        with self.assertRaisesRegex(self.remote.RemoteError, "No command was sent"):
            session.poll_result()


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
        for job in list(self.ue_ops._JOBS):
            job.close()
        self.prefs.multicast_port = self.original_port
        self.rt.ue_nodes.clear()
        fake = getattr(self, "fake", None)
        if fake is not None:
            fake.close()

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
        with mock.patch.object(remote.RemoteSession, "poll_connect", side_effect=remote.RemoteError("no answer")):
            self.assertEqual(fixtures.call(bpy.ops.morenn.ue_push), {"CANCELLED"})
        self.assertIn("no answer", fixtures.log_text())
        self.assertEqual(self.rt.ue_status, "Failed")
        self.assertFalse(self.ue_ops.busy())

    def test_push_when_the_sockets_cannot_open(self):
        self.start_fake_editor()
        self.export_cube()
        remote = fixtures.addon_module("ue.remote")
        with mock.patch.object(remote.RemoteSession, "open_udp", side_effect=OSError("address in use")):
            self.assertEqual(fixtures.call(bpy.ops.morenn.ue_push), {"CANCELLED"})
        self.assertIn("address in use", fixtures.log_text())
        self.assertFalse(self.ue_ops.busy())

    def test_any_error_during_a_push_or_pull_is_reported(self):
        self.start_fake_editor()
        self.export_cube()
        remote = fixtures.addon_module("ue.remote")
        for operator in (bpy.ops.morenn.ue_push, bpy.ops.morenn.ue_pull):
            with self.subTest(operator=operator.idname()):
                self.scene.morenn.log.clear()
                with mock.patch.object(remote.RemoteSession, "start_command", side_effect=KeyError("command")):
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
        state = types.SimpleNamespace(manifest="", report=mock.Mock(), _timer=None, _job=None)
        state.cancel = lambda context: operator.cancel(state, context)
        state.finish = lambda context, job: operator.finish(state, context, job)
        return state

    def run_modal(self, operator, state, timeout=5.0):
        """Send timer events until the operator ends; returns its result and the longest step in seconds."""
        timer = types.SimpleNamespace(type="TIMER")
        longest = 0.0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            started = time.monotonic()
            result = operator.modal(state, bpy.context, timer)
            longest = max(longest, time.monotonic() - started)
            if result != {"PASS_THROUGH"}:
                return result, longest
            time.sleep(0.02)
        self.fail(f"{operator.bl_idname} did not finish within {timeout:g}s")

    def test_push_runs_in_timer_steps_that_never_block(self):
        self.start_fake_editor()
        self.fake.connect_delay = 0.5
        self.fake.delay = 0.5
        self.export_cube()
        push = self.ue_ops.MORENN_OT_ue_push
        state = self.modal_state(push)
        state._job = push.start_job(state, bpy.context)
        self.assertTrue(self.ue_ops.busy())
        self.assertFalse(bpy.ops.morenn.ue_push.poll())
        self.assertEqual(self.rt.ue_status, "Importing in FakeProject…")
        self.assertEqual(push.modal(state, bpy.context, types.SimpleNamespace(type="MOUSEMOVE")), {"PASS_THROUGH"})
        result, longest = self.run_modal(push, state)
        self.assertEqual(result, {"FINISHED"})
        self.assertLess(longest, 0.25, "a step waited for the editor")
        self.assertFalse(self.ue_ops.busy())
        self.assertEqual(self.rt.ue_status, "Done")
        self.assertIn("mb_ue_import.py", self.fake.commands[-1]["command"])

    def test_push_and_pull_start_no_thread(self):
        """Blender 4.5 and 5.0 can crash in an export while another Python thread holds the GIL."""
        self.start_fake_editor()
        self.export_cube()
        started = []
        start = threading.Thread.start

        def record(thread):
            started.append(thread.name)
            start(thread)

        with mock.patch.object(threading.Thread, "start", record):
            for operator in (self.ue_ops.MORENN_OT_ue_push, self.ue_ops.MORENN_OT_ue_pull):
                state = self.modal_state(operator)
                state._job = operator.start_job(state, bpy.context)
                self.run_modal(operator, state)
            fixtures.call(bpy.ops.morenn.ue_push)
            fixtures.call(bpy.ops.morenn.ue_pull)
        self.assertEqual(started, [])
        self.assertEqual([c["command"].count("mb_ue_import.py") for c in self.fake.commands], [1, 0, 1, 0])

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

    def test_cancel_closes_the_session(self):
        self.start_fake_editor()
        self.fake.delay = 0.5
        self.export_cube()
        push = self.ue_ops.MORENN_OT_ue_push
        state = self.modal_state(push)
        state._job = push.start_job(state, bpy.context)
        deadline = time.monotonic() + 3.0
        while not self.fake.commands and time.monotonic() < deadline:
            push.modal(state, bpy.context, types.SimpleNamespace(type="TIMER"))
            time.sleep(0.02)
        self.assertEqual(len(self.fake.commands), 1)
        push.cancel(state, bpy.context)
        self.assertTrue(state._job.done)
        self.assertFalse(state._job.session.connected)
        self.assertFalse(self.ue_ops.busy())
        self.assertEqual(self.rt.ue_status, "Cancelled")
        deadline = time.monotonic() + 3.0
        while self.fake.tcp is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNone(self.fake.tcp, "the editor still holds the command connection")

    def test_disabling_the_addon_closes_running_jobs(self):
        """Blender frees a running operator without calling cancel() when its class is unregistered."""
        self.start_fake_editor()
        self.export_cube()
        state = self.modal_state(self.ue_ops.MORENN_OT_ue_push)
        job = self.ue_ops.MORENN_OT_ue_push.start_job(state, bpy.context)
        self.assertTrue(self.ue_ops.busy())
        self.ue_ops.close_jobs()
        self.assertTrue(job.done)
        self.assertFalse(self.ue_ops.busy())

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

    def test_slots_sharing_one_material_asset_keep_their_names(self):
        """Unreal writes one FBX material per slot: the asset's name in its first slot, ``<asset>_<slot index>`` in
        the others."""
        crate = fixtures.make_cube("SM_Crate")
        crate.data.materials.clear()
        first, other, second = (bpy.data.materials.new(name) for name in ("M_Test", "M_Other", "M_Test_2"))
        for material in (first, other, second):
            crate.data.materials.append(material)
        for i, polygon in enumerate(crate.data.polygons):
            polygon.material_index = i % 3
        slots = [
            {"slot": "Body", "material": "/Game/M_Test"},
            {"slot": "Lid", "material": "/Game/M_Other"},
            {"slot": "Trim", "material": "/Game/M_Test"},
        ]
        report = fixtures.addon_module("core.log").Report("test")
        fixtures.addon_module("ops.ue_ops").remember_unreal_materials([crate], slots, report)
        recorded = json.loads(crate.morenn.ue_materials)
        self.assertEqual(
            recorded,
            {
                first.name: ["Body", "/Game/M_Test"],
                second.name: ["Trim", "/Game/M_Test"],
                other.name: ["Lid", "/Game/M_Other"],
            },
        )
        done, manifest_path, report = fixtures.run_export([crate], self.tmp)
        self.assertTrue(report.ok, fixtures.report_text(report))
        self.assertEqual(fixtures.fbx_materials(fixtures.fbx_load(done[0].path)[0]), {"Body", "Lid", "Trim"})
        with open(manifest_path, encoding="utf-8") as fh:
            sent = json.load(fh)["items"][0]["assets"][0]["ue_materials"]
        self.assertEqual(sent, {"Body": "/Game/M_Test", "Trim": "/Game/M_Test", "Lid": "/Game/M_Other"})

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
