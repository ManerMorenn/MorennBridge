# SPDX-License-Identifier: GPL-3.0-or-later
"""Client for Unreal's Python remote execution protocol (PythonScriptPlugin), standard library only.

Protocol (from ``PythonScriptRemoteExecution.cpp``): UDP multicast discovery with JSON messages
``{"version": 1, "magic": "ue_py", "type": ..., "source": <guid>, "dest": <guid>?, "data": {...}?}``;
``ping`` -> ``pong``; ``open_connection`` makes the editor connect to a TCP listener we own; commands and
results travel over that TCP socket (no framing, one JSON object per message).
"""

import json
import select
import socket
import struct
import time
import uuid
from dataclasses import dataclass, field

PROTOCOL_VERSION = 1
PROTOCOL_MAGIC = "ue_py"

TYPE_PING = "ping"
TYPE_PONG = "pong"
TYPE_OPEN = "open_connection"
TYPE_CLOSE = "close_connection"
TYPE_COMMAND = "command"
TYPE_RESULT = "command_result"

EXEC_FILE = "ExecuteFile"
EXEC_STATEMENT = "ExecuteStatement"
EXEC_EVALUATE = "EvaluateStatement"


class RemoteError(RuntimeError):
    pass


@dataclass
class UENode:
    node_id: str
    user: str = ""
    machine: str = ""
    engine_version: str = ""
    engine_root: str = ""
    project_root: str = ""
    project_name: str = ""

    @property
    def label(self):
        proj = self.project_name or "(no project)"
        return f"{proj}, UE {self.engine_version} on {self.machine}"


@dataclass
class CommandResult:
    success: bool
    command: str = ""
    result: str = ""
    output: list = field(default_factory=list)  # [(type, text)]

    @property
    def errors(self):
        return [t for k, t in self.output if k.lower() == "error"]

    @property
    def warnings(self):
        return [t for k, t in self.output if k.lower() == "warning"]


@dataclass
class _Connecting:
    node: UENode
    request: bytes
    timeout: float
    resend_interval: float
    deadline: float
    next_send: float = 0.0


@dataclass
class _Command:
    outgoing: bytearray
    incoming: bytearray
    timeout: float
    deadline: float


def file_command(path, *args):
    """The ``ExecuteFile`` command text: path and arguments quoted (what ``sys.argv`` sees in Unreal)."""
    return " ".join(f'"{a}"' for a in (path, *args))


def _message(msg_type, source, dest=None, data=None):
    msg = {"version": PROTOCOL_VERSION, "magic": PROTOCOL_MAGIC, "type": msg_type, "source": source}
    if dest:
        msg["dest"] = dest
    if data is not None:
        msg["data"] = data
    return json.dumps(msg, separators=(",", ":")).encode("utf-8")


def _parse(raw, our_id):
    try:
        msg = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(msg, dict) or msg.get("version") != PROTOCOL_VERSION or msg.get("magic") != PROTOCOL_MAGIC:
        return None
    if msg.get("source") == our_id:
        return None
    dest = msg.get("dest")
    if dest and dest != our_id:
        return None
    return msg


def _command_result(msg):
    if msg.get("type") != TYPE_RESULT:
        raise RemoteError(f"Unexpected message type {msg.get('type')!r}")
    data = msg.get("data") or {}
    output = [(str(o.get("type", "Info")), str(o.get("output", ""))) for o in data.get("output", []) or []]
    return CommandResult(bool(data.get("success")), str(data.get("command", "")), str(data.get("result", "")), output)


class RemoteSession:
    """Discovery + one command connection. Use as a context manager."""

    def __init__(
        self, multicast_group="239.0.0.1", multicast_port=6766, bind_address="0.0.0.0", command_ip="127.0.0.1", ttl=0
    ):
        self.node_id = str(uuid.uuid4())
        self.group = multicast_group
        self.port = int(multicast_port)
        self.bind_address = bind_address
        self.command_ip = command_ip
        self.ttl = int(ttl)
        self._udp = None
        self._listener = None
        self._conn = None
        self._connecting = None
        self._command = None
        self.connected_node = None

    # ------------------------------------------------------------------ lifecycle
    def __enter__(self):
        self.open_udp()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def open_udp(self):
        if self._udp is not None:
            return
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind((self.bind_address, self.port))
        interface = socket.inet_aton(self.command_ip if self.command_ip else "127.0.0.1")
        mreq = socket.inet_aton(self.group) + interface
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, interface)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack("b", self.ttl))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        s.setblocking(False)
        self._udp = s

    def close(self):
        if self._conn is not None and self.connected_node is not None:
            try:
                self._send_udp(_message(TYPE_CLOSE, self.node_id, dest=self.connected_node.node_id))
            except OSError:
                pass
        for sock in (self._conn, self._listener, self._udp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._conn = self._listener = self._udp = None
        self._connecting = self._command = None
        self.connected_node = None

    # ------------------------------------------------------------------ discovery
    def _send_udp(self, payload):
        self._udp.sendto(payload, (self.group, self.port))

    def ping(self):
        self._send_udp(_message(TYPE_PING, self.node_id))

    def poll_pongs(self):
        """Non-blocking: return UENodes from any pending pong messages."""
        nodes = []
        while True:
            try:
                raw, _addr = self._udp.recvfrom(65536)
            except (BlockingIOError, InterruptedError):
                break
            except OSError:
                break
            msg = _parse(raw, self.node_id)
            if msg is None or msg.get("type") != TYPE_PONG:
                continue
            data = msg.get("data") or {}
            nodes.append(
                UENode(
                    msg.get("source", ""),
                    data.get("user", ""),
                    data.get("machine", ""),
                    data.get("engine_version", ""),
                    data.get("engine_root", ""),
                    data.get("project_root", ""),
                    data.get("project_name", ""),
                )
            )
        return nodes

    def discover(self, timeout=3.0, ping_interval=1.0):
        """Blocking discovery: returns the list of UENodes seen within ``timeout`` seconds."""
        found = {}
        deadline = time.monotonic() + timeout
        next_ping = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_ping:
                self.ping()
                next_ping = now + ping_interval
            for node in self.poll_pongs():
                found[node.node_id] = node
            time.sleep(0.05)
        return list(found.values())

    # ------------------------------------------------------------------ command connection
    # start_connect/poll_connect and start_command/poll_result never block with wait=0, so a modal operator can call
    # them from its timer on the main thread; connect and run repeat them until done.
    @property
    def connected(self):
        return self._conn is not None

    def start_connect(self, node, timeout=5.0, resend_interval=1.0):
        """Open our TCP listener for ``node``; ``poll_connect`` asks the editor to connect to it."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.command_ip, 0))
            listener.listen(1)
            listener.setblocking(False)
        except OSError:
            listener.close()
            raise
        self._listener = listener
        port = listener.getsockname()[1]
        request = _message(
            TYPE_OPEN, self.node_id, dest=node.node_id, data={"command_ip": self.command_ip, "command_port": port}
        )
        self._connecting = _Connecting(node, request, timeout, resend_interval, time.monotonic() + timeout)

    def poll_connect(self, wait=0.0):
        """Send ``open_connection`` when due, then take the editor's connection if it came, waiting at most ``wait``
        seconds for it. True once connected.

        ``open_connection`` travels by UDP and can be lost, so it is sent again every ``resend_interval`` seconds
        until the editor connects; the editor ignores a repeat once it is connected to this listener.
        """
        pending = self._connecting
        if pending is None:
            raise RemoteError("No connection was requested")
        now = time.monotonic()
        if pending.next_send <= now < pending.deadline:
            self._send_udp(pending.request)
            pending.next_send = now + pending.resend_interval
        wait = max(0.0, min(wait, pending.next_send - now, pending.deadline - now))
        if select.select([self._listener], [], [], wait)[0]:
            try:
                conn, _addr = self._listener.accept()
            except (BlockingIOError, InterruptedError):
                pass
            else:
                conn.setblocking(False)
                self._conn = conn
                self.connected_node = pending.node
                self._connecting = None
                return True
        if time.monotonic() >= pending.deadline:
            self._connecting = None
            self._listener.close()
            self._listener = None
            raise RemoteError(
                f"Unreal ({pending.node.label}) did not connect back within {pending.timeout:g}s; "
                "check 'Enable Remote Execution' and the bind address"
            )
        return False

    def connect(self, node, timeout=5.0, resend_interval=1.0):
        """Ask ``node`` to connect to our TCP listener and wait until it has."""
        self.start_connect(node, timeout, resend_interval)
        while not self.poll_connect(wait=resend_interval):
            pass
        return self

    def start_command(self, command, exec_mode=EXEC_STATEMENT, timeout=600.0, unattended=True):
        """Queue one command for the editor; ``poll_result`` sends it and reads the answer."""
        if self._conn is None:
            raise RemoteError("Not connected to an Unreal editor")
        payload = _message(
            TYPE_COMMAND,
            self.node_id,
            dest=self.connected_node.node_id,
            data={"command": command, "unattended": bool(unattended), "exec_mode": exec_mode},
        )
        self._command = _Command(bytearray(payload), bytearray(), timeout, time.monotonic() + timeout)

    def poll_result(self, wait=0.0):
        """Send what is left of the command and read what has arrived, waiting at most ``wait`` seconds for it.
        Returns the CommandResult once the answer parses (the editor sends it without framing), else None."""
        pending = self._command
        if pending is None:
            raise RemoteError("No command was sent")
        if pending.outgoing:
            try:
                del pending.outgoing[: self._conn.send(pending.outgoing)]
            except (BlockingIOError, InterruptedError):
                pass
        wait = max(0.0, min(wait, pending.deadline - time.monotonic()))
        writers = [self._conn] if pending.outgoing else []
        if select.select([self._conn], writers, [], wait)[0]:
            msg = self._read_answer(pending.incoming)
            if msg is not None:
                self._command = None
                return _command_result(msg)
        if time.monotonic() >= pending.deadline:
            self._command = None
            raise RemoteError(f"No answer from Unreal within {pending.timeout:g}s")
        return None

    def _read_answer(self, incoming):
        closed = False
        while True:
            try:
                chunk = self._conn.recv(65536)
            except (BlockingIOError, InterruptedError):
                break
            if not chunk:
                closed = True
                break
            incoming.extend(chunk)
        msg = _parse(bytes(incoming), self.node_id)
        if msg is None and closed:
            raise RemoteError("Unreal closed the command connection")
        return msg

    def run(self, command, exec_mode=EXEC_STATEMENT, timeout=600.0, unattended=True):
        """Send one command and wait for its result."""
        self.start_command(command, exec_mode, timeout, unattended)
        while True:
            result = self.poll_result(wait=1.0)
            if result is not None:
                return result

    def run_file(self, path, *args, timeout=600.0):
        """``ExecuteFile`` with a quoted path and arguments (what ``sys.argv`` sees in Unreal)."""
        return self.run(file_command(path, *args), exec_mode=EXEC_FILE, timeout=timeout)

    def evaluate(self, expression, timeout=60.0):
        return self.run(expression, exec_mode=EXEC_EVALUATE, timeout=timeout)
