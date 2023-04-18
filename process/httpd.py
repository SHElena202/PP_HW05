import enum
import logging
import socket
import threading
import time
import typing
import urllib.parse
from pathlib import Path


ALLOWED_CONTENT_TYPES = {
    ".html": "text/html",
    ".js": "application/javascript",
    ".css": "text/css",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".swf": "application/x-shockwave-flash",
    ".txt": "text/plain",
}
BACKLOG = 10
REQUEST_SOCKET_TIMEOUT = 10
REQUEST_CHUNK_SIZE = 1024
REQUEST_MAX_SIZE = 8 * 1024

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname).1s %(msg)s",
    datefmt="%Y.%m.%d %H:%M:%S",
)

class HTTPMethod(enum.Enum):
    GET = "GET"
    HEAD = "HEAD"

    def __str__(self):
        return self.value


class HTTPStatus(enum.Enum):
    OK = 200
    BAD_REQUEST = 400
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    REQUEST_TIMEOUT = 408
    ENTITY_TOO_LARGE = 413
    UNSUPPORTED_MEDIA_TYPE = 415
    INTERNAL_SERVER_ERROR = 500
    NOT_IMPLEMENTED = 501
    HTTP_VERSION_NOT_SUPPORTED = 505
    ERRORS = {
        OK: "OK",
        BAD_REQUEST: "Bad Request",
        FORBIDDEN: "Forbidden",
        NOT_FOUND: "Not Found",
        METHOD_NOT_ALLOWED: "Method Not Allowed",
        REQUEST_TIMEOUT: "Request Timeout",
        ENTITY_TOO_LARGE: "Entity Too Large",
        UNSUPPORTED_MEDIA_TYPE: "Unsupported Media Type",
        INTERNAL_SERVER_ERROR: "Internal Server Error",
        NOT_IMPLEMENTED: "Not Implemented",
        HTTP_VERSION_NOT_SUPPORTED: "HTTP Version Not Supported"
    }

    def __str__(self):
        code, msg = self.value
        return f'{code} {msg}'


class HTTPRequest(typing.NamedTuple):
    method: HTTPMethod
    target: str

    def clean_target(self):
        return self.target.partition("/")[-1].partition("?")[0]


class HTTPResponse(typing.NamedTuple):
    status: HTTPStatus
    body: bytes
    content_type: str
    content_length: int

    @classmethod
    def error(cls, status: HTTPStatus):
        body = str(status).encode('utf-8')
        return cls(status=status, body=body, content_type="text/plain", content_length=len(body))

class HTTPException(Exception):
    pass

def receive(conn: socket.socket) -> bytearray:
    conn.settimeout(REQUEST_SOCKET_TIMEOUT)

    try:
        received = bytearray()

        while True:
            if len(received) > REQUEST_MAX_SIZE:
                break
            if b"\r\n\r\n" in received:
                break
            chunk = conn.recv(REQUEST_CHUNK_SIZE)
            if not chunk:
                break

            received += chunk

    except socket.timeout:
        raise HTTPException(HTTPStatus.REQUEST_TIMEOUT)

    return received

def parse_request(received: bytearray) -> HTTPRequest:
    raw_request_line, *_ = received.partition(b"\r\n")
    request_line = str(raw_request_line)

    try:
        raw_method, raw_target, version = request_line.split()
    except ValueError:
        raise HTTPException(HTTPStatus.BAD_REQUEST)

    try:
        method = HTTPMethod[raw_method]
    except KeyError:
        raise HTTPException(HTTPStatus.METHOD_NOT_ALLOWED)

    return HTTPRequest(method=method, target=urllib.parse.unquote(raw_target))

def handle_request(request: HTTPRequest, document_root: Path) -> HTTPResponse:
    method = request.method
    target = request.clean_target()

    path = Path(document_root, target).resolve()

    if path.is_dir():
        path /= "index.html"

    if document_root not in path.parents:
        return HTTPResponse.error(HTTPStatus.FORBIDDEN)

    if not path.is_file():
        return HTTPResponse.error(HTTPStatus.NOT_FOUND)

    if path.suffix not in ALLOWED_CONTENT_TYPES:
        return HTTPResponse.error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)

    stat = path.stat()
    content_length = stat.st_size
    body = b"" if method is HTTPMethod.HEAD else path.read_bytes()

    return HTTPResponse(
        status=HTTPStatus.OK,
        body=body,
        content_type=ALLOWED_CONTENT_TYPES[path.suffix],
        content_length=content_length,
    )

def send_response(conn: socket.socket, response: HTTPResponse, dt=None) -> None:
    now = dt.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")

    headers = (
        f'HTTP/1.1 {response.status}',
        f'Date: {now}',
        f'Content-Type: {response.content_type}',
        f'Content-Length: {response.content_length}',
        f'Server: Python-HTTP-Server',
        f'Connection: close',
        f'',
    )

    try:
        raw_response: bytes = "\r\n".join(headers).encode('utf-8')
        raw_response += b"\r\n" + response.body
        conn.sendall(raw_response)
    except socket.timeout:
        pass

def send_error(conn:socket.socket, status: HTTPStatus) -> None:
    response = HTTPResponse.error(status)
    send_response(conn, response)

def handle_client_connection(conn: socket.socket, addr: typing.Tuple, document_root: Path) -> None:
    logging.debug(f'Connected by: {addr}')

    with conn:
        try:
            raw_bytes = receive(conn)
            request = parse_request(raw_bytes)
            response = handle_request(request, document_root)
            logging.info(f'{addr}: {request.method} {request.target}')
        except HTTPException as exc:
            status = exc.args[0]
            response = HTTPResponse.error(status)
            logging.info(f'{addr}: HTTP exception "{response.status}"')
        except Exception:
            logging.exception(f'{addr}: Error')
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            response = HTTPResponse.error(status)

        try:
            send_response(conn, response)
        except Exception:
            logging.exception(f"{addr}: Can't send a response")

    logging.debug(f'{addr}: connection closed')

def wait_connection(listening_socket:socket.socket, thread_id: int, document_root: Path) -> None:
    logging.debug(f'Worker_{thread_id} has been started')

    while True:
        conn, addr = listening_socket.accept()
        handle_client_connection(conn, addr, document_root)

    logging.debug(f'Worker_{thread_id} has been stopped')
    return None

def serve_forever(address: str, port: int, document_root: Path, n_workers: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(address, port)
        except PermissionError:
            logging.error(f'Permission denied {address}:{port}')
            return None
        except OSError:
            logging.error(f'Invalied address/ port: {address}:{port}')
            return None

        sock.listen(BACKLOG)

        for i in range(1, n_workers + 1):
            thread = threading.Thread(target=wait_connection, args=(sock, i, document_root))
            thread.daemon = True
            thread.start()

        logging.info(f'Run on http')

        try:
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            logging.info('Server is stopping')
            return None




