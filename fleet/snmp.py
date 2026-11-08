"""Minimal read-only SNMPv2c client (GET and WALK) with no third-party dependency.

Only what the PDU adapter needs: BER encode a GetRequest/GetNextRequest, decode the
GetResponse varbinds. Never sends SET. Values come back as Python ints/strs/OID strings;
noSuchObject / noSuchInstance / endOfMibView come back as None.
"""

import itertools
import socket

_req_ids = itertools.count(1)


def _len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    out = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(out)]) + out


def _tlv(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _len(len(body)) + body


def _int(v: int) -> bytes:
    body = v.to_bytes(max(1, (v.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(0x02, body)


def _oid(oid: str) -> bytes:
    parts = [int(p) for p in oid.strip(".").split(".")]
    body = bytearray([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.append(0x80 | (p & 0x7F))
            p >>= 7
        body.extend(reversed(chunk))
    return _tlv(0x06, bytes(body))


def _request(community: str, pdu_tag: int, oids: list[str]) -> tuple[int, bytes]:
    req_id = next(_req_ids) & 0x7FFFFFFF
    varbinds = b"".join(_tlv(0x30, _oid(o) + b"\x05\x00") for o in oids)
    pdu = _tlv(pdu_tag, _int(req_id) + _int(0) + _int(0) + _tlv(0x30, varbinds))
    msg = _tlv(0x30, _int(1) + _tlv(0x04, community.encode()) + pdu)  # version 1 = v2c
    return req_id, msg


def _read(buf: bytes, i: int) -> tuple[int, bytes, int]:
    tag = buf[i]
    n = buf[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n = int.from_bytes(buf[i:i + k], "big")
        i += k
    return tag, buf[i:i + n], i + n


def _decode_oid(body: bytes) -> str:
    parts = [body[0] // 40, body[0] % 40]
    v = 0
    for b in body[1:]:
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(v)
            v = 0
    return ".".join(map(str, parts))


def _decode_value(tag: int, body: bytes):
    if tag == 0x02:
        return int.from_bytes(body, "big", signed=True) if body else 0
    if tag in (0x41, 0x42, 0x43, 0x46):  # Counter32, Gauge32, TimeTicks, Counter64
        return int.from_bytes(body, "big", signed=False) if body else 0
    if tag == 0x04:
        return body.decode("utf-8", "replace")
    if tag == 0x06:
        return _decode_oid(body)
    if tag == 0x40:
        return ".".join(str(b) for b in body)
    return None  # NULL, noSuchObject (0x80), noSuchInstance (0x81), endOfMibView (0x82)


def _send(host: str, community: str, pdu_tag: int, oids: list[str], timeout: float,
          port: int = 161, retries: int = 1) -> list[tuple[str, object]]:
    req_id, msg = _request(community, pdu_tag, oids)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        for attempt in range(retries + 1):
            s.sendto(msg, (host, port))
            try:
                data, _ = s.recvfrom(65535)
            except socket.timeout:
                if attempt == retries:
                    raise
                continue
            _, top, _ = _read(data, 0)
            i = 0
            _, _, i = _read(top, i)          # version
            _, _, i = _read(top, i)          # community
            tag, pdu, _ = _read(top, i)
            if tag != 0xA2:
                raise ValueError(f"unexpected SNMP PDU 0x{tag:02x}")
            j = 0
            _, rid, j = _read(pdu, j)
            if int.from_bytes(rid, "big") != req_id:
                continue
            _, err, j = _read(pdu, j)
            _, _, j = _read(pdu, j)          # error index
            if err and int.from_bytes(err, "big"):
                raise ValueError(f"SNMP error-status {int.from_bytes(err, 'big')}")
            _, vbl, _ = _read(pdu, j)
            out, k = [], 0
            while k < len(vbl):
                _, vb, k = _read(vbl, k)
                _, ob, m = _read(vb, 0)
                vtag, vbody, _ = _read(vb, m)
                out.append((_decode_oid(ob), _decode_value(vtag, vbody)))
            return out
    raise TimeoutError(f"no SNMP response from {host}")


def get(host: str, community: str, oids: list[str], timeout: float = 2.0) -> dict:
    """GET several OIDs at once -> {oid: value or None}."""
    return dict(_send(host, community, 0xA0, oids, timeout))


def walk(host: str, community: str, base: str, timeout: float = 2.0, limit: int = 500) -> dict:
    """GETNEXT walk under `base` -> {oid: value}."""
    out, cur = {}, base.strip(".")
    prefix = cur + "."
    for _ in range(limit):
        (oid, value), = _send(host, community, 0xA1, [cur], timeout)
        if not oid.startswith(prefix) or value is None:
            break
        out[oid] = value
        cur = oid
    return out
