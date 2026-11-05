"""Client for the `kstone run --serve` debug channel: 4-byte big-endian length + JSON frames."""

import base64
import json
import socket
import struct
import threading
from collections import deque

MAX_FRAME = 64 << 20
PROTOCOL_VERSION = 1


class Client:
    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(None)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._backlog: deque[dict] = deque()

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()

    def receive(self) -> dict:
        """Blocks for the next frame; raises ConnectionError when the server goes away.

        States queue in the socket if read slower than the subscribed rate, so they get old:
        subscribe at the rate you read, or read on a thread and keep the latest."""
        if self._backlog:
            return self._backlog.popleft()
        return self._read_frame()

    def request(self, op: str, module: str | None = None, **fields) -> dict:
        """Sends and waits for the reply; other messages stay queued for receive() (only the latest state)."""
        request_id = self.send(op, module, **fields)
        while True:
            message = self._read_frame()
            # Unparsable requests are answered with id null.
            if message.get("type") == "reply" and message.get("id") in (request_id, None):
                return message
            if message.get("type") == "state":
                self._backlog = deque(m for m in self._backlog if m.get("type") != "state")
            self._backlog.append(message)

    def fetch(self, name: str) -> bytes:
        """Downloads one model asset; raises ConnectionError with the server's code on failure."""
        data = bytearray()
        while True:
            reply = self.request("asset", name=name, offset=len(data))
            if not reply.get("ok"):
                raise ConnectionError(f"{name}: {reply.get('code')}: {reply.get('error')}")
            data += base64.b64decode(reply.get("data", ""))
            if len(data) >= reply.get("total", 0) or not reply.get("data"):
                return bytes(data)

    def _read_frame(self) -> dict:
        (length,) = struct.unpack(">I", self._read_exact(4))
        if length > MAX_FRAME:
            raise ConnectionError(f"frame of {length} bytes exceeds the limit")
        return json.loads(self._read_exact(length))

    def handshake(self, token: str | None = None) -> dict:
        """Reads hello, authenticates if required and returns the model message."""
        hello = self.receive()
        if hello.get("type") == "reply" and not hello.get("ok"):
            raise ConnectionError(f"server refused the connection: {hello.get('error')}")
        if hello.get("type") != "hello" or hello.get("version") != PROTOCOL_VERSION:
            raise ConnectionError(f"unsupported greeting {hello}")
        if hello.get("auth_required"):
            if not token:
                raise ConnectionError("server requires a token")
            request_id = self.send("auth", token=token)
            reply = self.receive()
            if reply.get("type") != "reply" or reply.get("id") != request_id or not reply.get("ok"):
                raise ConnectionError(f"authentication failed: {reply.get('error', reply)}")
        while True:
            message = self.receive()
            if message.get("type") == "model":
                return message

    def send(self, op: str, module: str | None = None, **fields) -> int:
        """Thread-safe; returns the request id echoed in the matching reply."""
        with self._send_lock:
            request_id = self._next_id
            self._next_id += 1
            request = {"op": op, "id": request_id, **fields}
            if module is not None:
                request["module"] = module
            payload = json.dumps(request).encode()
            self._sock.sendall(struct.pack(">I", len(payload)) + payload)
        return request_id

    def _read_exact(self, n: int) -> bytes:
        chunks = []
        while n > 0:
            chunk = self._sock.recv(min(n, 1 << 16))
            if not chunk:
                raise ConnectionError("server closed the connection")
            chunks.append(chunk)
            n -= len(chunk)
        return b"".join(chunks)
