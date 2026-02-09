import log
import usocket as socket
import ubinascii as binascii
import urandom as random
import log
import ure as re
import ustruct as struct
import urandom as random
import usocket as socket
import websocket
from ucollections import namedtuple
import dataCall

LOGGER = log.getLogger(__name__)

URL_RE = re.compile(r'(wss|ws)://([A-Za-z0-9-\.]+)(?:\:([0-9]+))?(/.+)?')
URI = namedtuple('URI', ('protocol', 'hostname', 'port', 'path'))


def urlparse(uri):
    """Parse ws:// URLs"""
    match = URL_RE.match(uri)
    if match:
        protocol = match.group(1)
        host = match.group(2)
        port = match.group(3)
        path = match.group(4)

        if protocol == 'wss':
            if port is None:
                port = 443
        elif protocol == 'ws':
            if port is None:
                port = 80
        else:
            raise ValueError('Scheme {} is invalid'.format(protocol))

        return URI(protocol, host, int(port), path)


class NoDataException(Exception):
    pass


class ConnectionClosed(Exception):
    pass


class Websocket(object):
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

    def read_frame(self, sz):
        return self.ws.read(sz)

    def write_frame(self, data=b''):
        return self.ws.write(data)

    def recv(self, max_size=2048):
        """
        Receive data from the websocket.

        This is slightly different from 'websockets' in that it doesn't
        fire off a routine to process frames and put the data in a queue.
        If you don't call recv() sufficiently often you won't process control
        frames.
        """
        assert self.open

        return self.read_frame(max_size)

    def send(self, buf):
        """Send data to the websocket."""

        assert self.open

        if isinstance(buf, str):
            buf = buf.encode('utf-8')
            self.ws.ioctl(9, 1)
        elif isinstance(buf, bytes):
            self.ws.ioctl(9, 2)
        else:
            raise TypeError()
        return self.write_frame(buf)

    def close(self):
        """Close the websocket."""
        if not self.open:
            return

        try:
            self.ws.ioctl(4)
        except Exception as e:
            if self.debug: LOGGER.info("websocekt close:%s"%(str(e)))
        self._close()

    def _close(self):
        if self.debug: LOGGER.info("Connection closed")
        self.open = False
        self.sock.close()


class WebsocketClient(Websocket):
    is_client = True


class Client(object):

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

        if debug: LOGGER.info("open connection %s:%s",
                              uri.hostname, uri.port)

        sock = socket.socket()
        addr = socket.getaddrinfo(uri.hostname, uri.port, socket.AF_INET)
        sock.connect(addr[0][4])

        if uri.protocol == 'wss':
            import ussl
            sock = ussl.wrap_socket(sock)

        def send_header(header, *args):
            if debug: LOGGER.info(str(header), *args)
            sock.write(header % args + '\r\n')

        # Sec-WebSocket-Key is 16 bytes of random base64 encoded
        key = binascii.b2a_base64(bytes(random.getrandbits(8) for _ in range(16)))[:-1]
        send_header(b'GET %s HTTP/1.1', uri.path or '/')
        send_header(b'Host: %s:%s', 'ws.coze.cn', uri.port)
        send_header(b'Connection: Upgrade')
        send_header(b'Upgrade: websocket')
        send_header(b'Sec-WebSocket-Key: %s', key)
        send_header(b'Sec-WebSocket-Version: 13')
        send_header(b'Origin: http://{hostname}:{port}'.format(
            hostname=uri.hostname,
            port=uri.port)
        )
        for k, v in headers.items():
            send_header('{}:{}'.format(k, v).encode())
        send_header(b'')

        header = sock.readline()[:-2]
        assert header.startswith(b'HTTP/1.1 101 '), header

        # We don't (currently) need these headers
        # FIXME: should we check the return key?
        while header:
            if debug: LOGGER.info(str(header))
            header = sock.readline()[:-2]

        return WebsocketClient(sock, debug)
