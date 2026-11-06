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
        disconnected -- same sentinel main.py treats as end-of-input."""
        while b"\n" not in self._buf:
            chunk = self._conn.recv(1024)
            if not chunk:            # peer closed
                line, self._buf = self._buf, b""
                return line.decode("utf-8", "replace").strip()
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line.decode("utf-8", "replace").strip()

    def write(self, text):
        """Send text to the phone. No-op if not connected."""
        if self._conn is not None:
            self._conn.sendall(text.encode("utf-8", "replace"))

    def close(self):
        for s in (self._conn, self._srv):
            try:
                if s is not None:
                    s.close()
            except OSError:
                pass


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
    print("bluetooth_input self-check OK")
