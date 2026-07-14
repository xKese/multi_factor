"""Persistenter WeasyPrint-Renderer als Subprozess-Worker.

Wird einmalig vom Dash-Prozess gestartet (siehe ``factsheet_pdf._ensure_worker``)
und bleibt am Leben, damit der teure WeasyPrint-Import (≈ 8 s Cold-Start)
nur einmal pro Server-Lauf anfällt — Folge-Renderings dauern dann <1 s.

Kommunikation über stdin/stdout mit längen-präfigiertem Binärprotokoll:

    Request (Parent → Worker):
        <htmllen: uint32 BE> <html_utf8: bytes>
        <urllen:  uint32 BE> <url_utf8:  bytes>

    Response (Worker → Parent):
        <status: uint8>  0 = ok, 1 = error
        <paylen: uint32 BE>
        <payload: bytes>   PDF-Bytes (status=0) oder UTF-8-Fehlertext (status=1)
"""

from __future__ import annotations

import struct
import sys


def _read_exact(stream, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            raise EOFError
        out.extend(chunk)
    return bytes(out)


def _read_request(stream) -> tuple[str, str] | None:
    try:
        html_len = struct.unpack(">I", _read_exact(stream, 4))[0]
    except EOFError:
        return None
    html = _read_exact(stream, html_len).decode("utf-8")
    url_len = struct.unpack(">I", _read_exact(stream, 4))[0]
    base_url = _read_exact(stream, url_len).decode("utf-8")
    return html, base_url


def _write_response(stream, status: int, payload: bytes) -> None:
    stream.write(bytes([status]))
    stream.write(struct.pack(">I", len(payload)))
    stream.write(payload)
    stream.flush()


def main() -> None:
    # Heavy import passiert einmalig pro Worker-Lebenszeit.
    import weasyprint

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer

    while True:
        req = _read_request(stdin)
        if req is None:
            return  # stdin geschlossen → sauber beenden
        html, base_url = req
        try:
            pdf = weasyprint.HTML(string=html, base_url=base_url).write_pdf()
            _write_response(stdout, 0, pdf)
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            _write_response(stdout, 1, msg.encode("utf-8"))


if __name__ == "__main__":
    main()
