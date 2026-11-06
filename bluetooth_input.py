"""Bluetooth Classic (RFCOMM/SPP) text channel so a phone acts as the bot's
keyboard+screen, replacing the touchscreen.

Stdlib only: Python's socket module speaks RFCOMM on Linux (AF_BLUETOOTH),
so there is no pybluez/pyserial dependency. Pair the phone once with
bluetoothctl, then any generic "Bluetooth serial terminal" app connects to
the advertised SPP channel and exchanges newline-terminated text.

Line protocol: phone sends one request per line (\n); we reply with the
bot's text, also newline-terminated. Same shape as the stdin/stdout loop,
so main.py's command handling is reused unchanged.
"""
import socket

# Well-known RFCOMM channel for a simple SPP server. 1-30 are valid; 1 is the
# conventional default a serial terminal app tries first.
_CHANNEL = 1


class BluetoothChannel:
    """One accepted RFCOMM connection, with line-oriented read/write that
    mirror input()/print() so the main loop does not change."""

    def __init__(self, channel=_CHANNEL):
        self._srv = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                                  socket.BTPROTO_RFCOMM)
        # Rebind the channel even if a just-killed kiosk's socket lingers in
        # TIME_WAIT; without this a restart hits "Address already in use" and
        # BT silently disables.
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("00:00:00:00:00:00", channel))  # BDADDR_ANY: any adapter
        self._srv.listen(1)
        self._conn = None
        self._buf = b""

    def wait_for_phone(self):
        """Block until a phone connects. Returns the peer address."""
        self._conn, addr = self._srv.accept()
        return addr

    def readline(self):
        """Return the next line (str, newline stripped), or '' if the phone
        disconnected -- same sentinel main.py treats as end-of-input. An abrupt
        drop (Android apps reset the socket rather than close it cleanly) raises
        OSError from recv; treat that as a disconnect too, not a crash."""
        while b"\n" not in self._buf:
            try:
                chunk = self._conn.recv(1024)
            except OSError:          # connection reset / aborted by the phone
                line, self._buf = self._buf, b""
                return line.decode("utf-8", "replace").strip()
            if not chunk:            # peer closed cleanly
                line, self._buf = self._buf, b""
                return line.decode("utf-8", "replace").strip()
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("utf-8", "replace").strip()

    def reset_conn(self):
        """Drop the current phone connection and clear its buffer so the next
        wait_for_phone() accepts a fresh one with no leftover bytes."""
        try:
            if self._conn is not None:
                self._conn.close()
        except OSError:
            pass
        self._conn = None
        self._buf = b""

    def write(self, text):
        """Send text to the phone. No-op if not connected."""
        if self._conn is None:
            return
        try:
            self._conn.sendall(text.encode("utf-8", "replace"))
        except OSError:              # phone vanished mid-reply -> drop it
            self.reset_conn()

    def close(self):
        # shutdown() before close() forces the kernel to tear the RFCOMM bind
        # down now; a bare close() can leave the listening socket orphaned on
        # channel 1 (bind state) if an accepted child is still half-open, which
        # then blocks the next kiosk's bind with EADDRINUSE until an adapter
        # reset. SHUT_RDWR on both the connection and the server avoids that.
        for s in (self._conn, self._srv):
            if s is None:
                continue
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass
        self._conn = None
        self._srv = None


if __name__ == "__main__":
    # self-check: the line parser must split a buffered multi-line payload the
    # same way readline() does, without a real radio.
    c = BluetoothChannel.__new__(BluetoothChannel)
    c._buf = b"hello\nworld\npartial"

    class _Fake:
        def recv(self, _n):
            return b""   # force flush of the trailing partial line
    c._conn = _Fake()
    assert c.readline() == "hello"
    assert c.readline() == "world"
    assert c.readline() == "partial"   # trailing, no newline, peer closed

    # an abrupt reset (recv raises OSError) is a disconnect, not a crash
    c2 = BluetoothChannel.__new__(BluetoothChannel)
    c2._buf = b""

    class _Reset:
        def recv(self, _n):
            raise ConnectionResetError("peer reset")
        def close(self):
            pass
    c2._conn = _Reset()
    assert c2.readline() == ""          # returns disconnect sentinel, no raise

    # reset_conn clears the connection and buffer for a clean re-accept
    c2._buf = b"stale"
    c2.reset_conn()
    assert c2._conn is None and c2._buf == b""
    print("bluetooth_input self-check OK")
