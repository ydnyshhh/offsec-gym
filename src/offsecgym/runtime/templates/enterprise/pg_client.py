"""Small standard-library PostgreSQL text-protocol client for the offline range image.

The range only needs parameterized scalar SQL, transactions and typed row reads. This
module keeps the target image build offline; the controller never imports it.
"""

import os
import socket
import struct
from datetime import datetime
from urllib.parse import urlsplit
from uuid import UUID


class Error(Exception):
    pass


class OperationalError(Error):
    pass


def _read_exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        part = sock.recv(count - len(chunks))
        if not part:
            raise OperationalError("database connection closed")
        chunks.extend(part)
    return bytes(chunks)


def _message(sock):
    kind = _read_exact(sock, 1)
    length = struct.unpack("!I", _read_exact(sock, 4))[0]
    if length < 4 or length > 16_000_000:
        raise OperationalError("invalid database message length")
    return kind, _read_exact(sock, length - 4)


def _literal(value):
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if type(value) is int:
        return str(value)
    if isinstance(value, (UUID, datetime)):
        value = str(value)
    if isinstance(value, str) and "\x00" not in value:
        return "'" + value.replace("'", "''") + "'"
    raise ValueError("unsupported SQL parameter")


def _substitute(query, parameters):
    pieces = query.split("%s")
    if len(pieces) != len(parameters) + 1:
        raise ValueError("SQL parameter count mismatch")
    return "".join(pieces[i] + _literal(parameters[i]) for i in range(len(parameters))) + pieces[-1]


def _decode(oid, value):
    if value is None:
        return None
    text = value.decode("utf-8")
    if oid == 2950:
        return UUID(text)
    if oid in (20, 21, 23):
        return int(text)
    if oid == 16:
        return text == "t"
    if oid in (1114, 1184):
        return datetime.fromisoformat(text.replace(" ", "T"))
    return text


class Cursor:
    def __init__(self, connection):
        self.connection = connection
        self.results = []
        self.index = 0

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None

    def execute(self, query, parameters=()):
        sql = _substitute(query, tuple(parameters))
        self.results = self.connection.query(sql)
        self.index = 0
        return self

    def fetchone(self):
        if self.index >= len(self.results):
            return None
        value = self.results[self.index]
        self.index += 1
        return value

    def fetchall(self):
        values = self.results[self.index :]
        self.index = len(self.results)
        return values


class Connection:
    def __init__(self, url, timeout):
        parsed = urlsplit(url)
        if parsed.scheme != "postgresql" or not parsed.hostname or not parsed.username:
            raise ValueError("invalid synthetic database URL")
        self.sock = socket.create_connection((parsed.hostname, parsed.port or 5432), timeout)
        self.sock.settimeout(15)
        self.in_transaction = False
        startup = (
            b"user\0"
            + parsed.username.encode()
            + b"\0database\0"
            + parsed.path.lstrip("/").encode()
            + b"\0client_encoding\0UTF8\0"
            + b"options\0-c TimeZone=UTC\0\0"
        )
        packet = struct.pack("!II", len(startup) + 8, 196608) + startup
        self.sock.sendall(packet)
        while True:
            kind, body = _message(self.sock)
            if kind == b"R" and body != b"\x00\x00\x00\x00":
                raise OperationalError("unsupported database authentication mode")
            if kind == b"E":
                raise OperationalError("database startup rejected")
            if kind == b"Z":
                break

    def __enter__(self):
        self.query("BEGIN")
        self.in_transaction = True
        return self

    def __exit__(self, exc_type, *_exc):
        try:
            if self.in_transaction:
                self.query("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.sock.close()

    def cursor(self):
        return Cursor(self)

    def commit(self):
        if self.in_transaction:
            self.query("COMMIT")
            self.in_transaction = False

    def query(self, sql):
        raw = sql.encode("utf-8") + b"\0"
        self.sock.sendall(b"Q" + struct.pack("!I", len(raw) + 4) + raw)
        columns = []
        results = []
        error = None
        while True:
            kind, body = _message(self.sock)
            if kind == b"T":
                count = struct.unpack_from("!H", body, 0)[0]
                offset = 2
                columns = []
                for _ in range(count):
                    end = body.index(b"\0", offset)
                    name = body[offset:end].decode()
                    offset = end + 1
                    oid = struct.unpack_from("!I", body, offset + 6)[0]
                    offset += 18
                    columns.append((name, oid))
            elif kind == b"D":
                count = struct.unpack_from("!H", body, 0)[0]
                offset = 2
                values = []
                for _ in range(count):
                    length = struct.unpack_from("!i", body, offset)[0]
                    offset += 4
                    values.append(None if length == -1 else body[offset : offset + length])
                    if length >= 0:
                        offset += length
                results.append(
                    {
                        name: _decode(oid, value)
                        for (name, oid), value in zip(columns, values, strict=True)
                    }
                )
            elif kind == b"E":
                fields = body.split(b"\0")
                error = next(
                    (part[1:].decode(errors="replace") for part in fields if part.startswith(b"M")),
                    "database query failed",
                )
            elif kind == b"Z":
                if error:
                    raise Error(error)
                return results


def connect(url=None, *, connect_timeout=3):
    return Connection(url or os.environ["DATABASE_URL"], connect_timeout)
