# SPDX-License-Identifier: GPL-3.0-or-later
"""Protocol test for the remote-execution client against a fake Unreal node (same JSON protocol)."""

import json
import socket
import struct
import threading
import time
import unittest

from tests import fixtures

GROUP, PORT = "239.0.0.1", 6799  # a test port so a real editor is never disturbed


class FakeUnrealNode(threading.Thread):
    """Answers ping with pong, connects back on open_connection, echoes commands."""

    def __init__(self):
        super().__init__(daemon=True)
        self.node_id = "fake-ue-node"
        self.stop = threading.Event()
        self.commands = []
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
            "success": True,
            "command": data["command"],
            "result": "42" if data["exec_mode"] == "EvaluateStatement" else "",
            "output": [{"type": "Info", "output": "hello from fake UE"}],
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


if __name__ == "__main__":
    unittest.main()
