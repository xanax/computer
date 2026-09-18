"""Decode a 1x1 PNG (CDP screenshot clip) without any image library.

For a single pixel every PNG filter degenerates to "raw" (no left/up neighbours),
so IDAT decompresses straight to [filter, r, g, b(, a)].
"""

import struct
import zlib


def png1x1(b64_data: bytes) -> tuple[int, ...]:
    data = b64_data
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a png"
    i = 8
    ihdr = None
    idat = bytearray()
    while i < len(data):
        (ln,) = struct.unpack(">I", data[i : i + 4])
        typ = data[i + 4 : i + 8]
        body = data[i + 8 : i + 8 + ln]
        if typ == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        i += 12 + ln
    w, h, depth, ctype, comp, filt, inter = ihdr
    assert (w, h, depth, inter) == (1, 1, 8, 0), ihdr
    nch = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(bytes(idat))
    # Every filter type degenerates to raw for a lone pixel (left/up/up-left are 0),
    # so raw[0] is just the filter byte we can ignore.
    return tuple(raw[1 : 1 + nch])
