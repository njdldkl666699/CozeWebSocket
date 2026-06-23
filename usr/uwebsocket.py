import log
import ubinascii
import urandom
import ure
import usocket
import websocket
from ucollections import namedtuple

LOGGER = log.getLogger(__name__)

URL_RE = ure.compile(r"(wss|ws)://([A-Za-z0-9-\.]+)(?:\:([0-9]+))?(/.+)?")
URI = namedtuple("URI", ("protocol", "hostname", "port", "path"))

IOCTL_CLOSE = 4
IOCTL_SET_DATA_OPTS = 9
FRAME_CONTINUATION = 0
FRAME_TEXT = 1
FRAME_BINARY = 2
FRAME_CLOSE = 8
FRAME_PING = 9
FRAME_PONG = 10


def urlparse(uri):
    """Parse ws:// URLs"""
    match = URL_RE.match(uri)
    if match:
        protocol = match.group(1)
        host = match.group(2)
        port = match.group(3)
        path = match.group(4)

        if protocol == "wss":
            if port is None:
                port = 443
        elif protocol == "ws":
            if port is None:
                port = 80
        else:
            raise ValueError("Scheme {} is invalid".format(protocol))

        return URI(protocol, host, int(port), path)


class NoDataException(Exception):
    pass


class ConnectionClosed(Exception):
    pass


class WebSocket:
    """
    Basis of the Websocket protocol.

    This can probably be replaced with the C-based websocket module, but
    this one currently supports more options.
    """

    is_client = False

    def __init__(self, sock, debug=False):
        self.sock = sock
        self.ws = websocket.websocket(sock)
        self.open = True
        self.debug = debug

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def settimeout(self, timeout):
        self.sock.settimeout(timeout)

    def _read_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining > 0:
            data = self.sock.read(remaining)
            if not data:
                raise ConnectionClosed()
            chunks.append(data)
            remaining -= len(data)
        return b"".join(chunks)

    def _read_payload_length(self, length: int) -> int:
        if length == 126:
            data = self._read_exact(2)
        elif length == 127:
            data = self._read_exact(8)
        else:
            return length

        value = 0
        for item in data:
            value = (value << 8) | item
        return value

    def _read_raw_frame(self):
        header = self._read_exact(2)
        first = header[0]
        second = header[1]
        fin = (first & 0x80) != 0
        opcode = first & 0x0F
        masked = (second & 0x80) != 0
        length = self._read_payload_length(second & 0x7F)

        mask = None
        if masked:
            mask = self._read_exact(4)

        payload = self._read_exact(length) if length else b""
        if mask and payload:
            unmasked = bytearray(payload)
            for index in range(length):
                unmasked[index] ^= mask[index & 3]
            payload = bytes(unmasked)
        return fin, opcode, payload

    def _write_control_frame(self, opcode: int, payload=b"") -> None:
        if payload is None:
            payload = b""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        if len(payload) > 125:
            payload = payload[:125]

        mask = bytes(urandom.getrandbits(8) for _ in range(4))
        header = bytearray(2)
        header[0] = 0x80 | opcode
        header[1] = 0x80 | len(payload)
        masked = bytearray(payload)
        for index in range(len(masked)):
            masked[index] ^= mask[index & 3]
        self.sock.write(bytes(header) + mask + bytes(masked))

    def read_full_frame(self):
        """Read one complete websocket message."""
        assert self.open

        message_type = None
        chunks = []

        while True:
            fin, opcode, payload = self._read_raw_frame()

            if opcode in (FRAME_TEXT, FRAME_BINARY):
                if message_type is not None:
                    raise ValueError("unexpected websocket data frame")
                message_type = opcode
                chunks.append(payload)
                if fin:
                    break
            elif opcode == FRAME_CONTINUATION:
                if message_type is None:
                    raise ValueError("unexpected websocket continuation frame")
                chunks.append(payload)
                if fin:
                    break
            elif opcode == FRAME_CLOSE:
                self.open = False
                try:
                    self._write_control_frame(FRAME_CLOSE, payload)
                except Exception as e:
                    if self.debug:
                        LOGGER.info("websocket close reply:%s" % (str(e)))
                self._close()
                return b""
            elif opcode == FRAME_PING:
                self._write_control_frame(FRAME_PONG, payload)
            elif opcode == FRAME_PONG:
                pass
            else:
                raise ValueError("unsupported websocket opcode {}".format(opcode))

        data = b"".join(chunks)
        if message_type == FRAME_TEXT and isinstance(data, bytes):
            return data.decode("utf-8")
        return data

    def read_frame(self):
        return self.read_full_frame()

    def write_frame(self, data=b""):
        return self.ws.write(data)

    def recv(self):
        """
        Receive data from the websocket.

        This is slightly different from 'websockets' in that it doesn't
        fire off a routine to process frames and put the data in a queue.
        If you don't call recv() sufficiently often you won't process control
        frames.
        """
        return self.read_full_frame()

    def send(self, buf):
        """Send data to the websocket."""

        assert self.open

        if isinstance(buf, str):
            buf = buf.encode("utf-8")
            self.ws.ioctl(IOCTL_SET_DATA_OPTS, FRAME_TEXT)
        elif isinstance(buf, bytes):
            self.ws.ioctl(IOCTL_SET_DATA_OPTS, FRAME_BINARY)
        else:
            raise TypeError()
        return self.write_frame(buf)

    def close(self):
        """Close the websocket."""
        if not self.open:
            return

        try:
            self.ws.ioctl(IOCTL_CLOSE)
        except Exception as e:
            if self.debug:
                LOGGER.info("websocekt close:%s" % (str(e)))
        self._close()

    def _close(self):
        if self.debug:
            LOGGER.info("Connection closed")
        self.open = False
        self.sock.close()


class WebSocketClient(WebSocket):
    is_client = True


class Client:
    @staticmethod
    def connect(uri, headers=None, debug=False):
        """
        Connect a websocket.
        :param uri: example ws://172.16.185.123/
        :param headers: k, v of header
        :param debug: allow output log
        :return:
        """
        if not headers:
            headers = dict()
        if not isinstance(headers, dict):
            raise Exception("headers must be dict type but {} you given.".format(type(headers)))

        uri = urlparse(uri)
        assert uri

        if debug:
            LOGGER.info("open connection %s:%s", uri.hostname, uri.port)

        sock = usocket.socket()
        addr = usocket.getaddrinfo(uri.hostname, uri.port, usocket.AF_INET)
        sock.connect(addr[0][4])

        if uri.protocol == "wss":
            import ussl

            sock = ussl.wrap_socket(sock)

        def send_header(header, *args):
            if debug:
                LOGGER.info(str(header), *args)
            sock.write(header % args + "\r\n")

        # Sec-WebSocket-Key is 16 bytes of random base64 encoded
        key = ubinascii.b2a_base64(bytes(urandom.getrandbits(8) for _ in range(16)))[:-1]
        send_header(b"GET %s HTTP/1.1", uri.path or "/")
        send_header(b"Host: %s:%s", "ws.coze.cn", uri.port)
        send_header(b"Connection: Upgrade")
        send_header(b"Upgrade: websocket")
        send_header(b"Sec-WebSocket-Key: %s", key)
        send_header(b"Sec-WebSocket-Version: 13")
        send_header(
            b"Origin: http://{hostname}:{port}".format(hostname=uri.hostname, port=uri.port)
        )
        for k, v in headers.items():
            send_header("{}:{}".format(k, v).encode())
        send_header(b"")

        header = sock.readline()[:-2]
        assert header.startswith(b"HTTP/1.1 101 "), header

        # We don't (currently) need these headers
        # FIXME: should we check the return key?
        while header:
            if debug:
                LOGGER.info(str(header))
            header = sock.readline()[:-2]

        return WebSocketClient(sock, debug)
