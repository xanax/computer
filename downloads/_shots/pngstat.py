"""Minimal dependency-free PNG reader + palette/grey analysis for screenshots."""

from __future__ import annotations

import collections
import struct
import sys
import zlib
from pathlib import Path


def read_png(path: str):
    data = Path(path).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a png"
    pos = 8
    idat = b""
    w = h = bitdepth = ctype = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        ctag = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if ctag == b"IHDR":
            w, h, bitdepth, ctype, comp, filt, interlace = struct.unpack(">IIBBBBB", chunk)
            assert bitdepth == 8 and interlace == 0, (bitdepth, interlace)
        elif ctag == b"IDAT":
            idat += chunk
        elif ctag == b"IEND":
            break
    raw = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * channels
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p : p + stride])
        p += stride
        if ft == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride : (y + 1) * stride] = line
        prev = line
    return w, h, channels, bytes(out)


def analyse(path: str, top: int = 20):
    w, h, ch, px = read_png(path)
    counts: collections.Counter = collections.Counter()
    greys = 0
    for i in range(0, len(px), ch):
        if ch >= 3:
            c = (px[i], px[i + 1], px[i + 2])
        else:
            c = (px[i],) * 3
        counts[c] += 1
        if c[0] == c[1] == c[2] and c[0] not in (0, 255):
            greys += 1
    total = w * h
    pure = counts[(0, 0, 0)] + counts[(255, 255, 255)]
    return {
        "size": f"{w}x{h}",
        "distinct": len(counts),
        "grey_px": greys,
        "grey_pct": round(100 * greys / total, 4),
        "pure_pct": round(100 * pure / total, 3),
        "top": [(c, n) for c, n in counts.most_common(top)],
    }


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = analyse(p)
        print(f"\n== {p}")
        print(f"   size={r['size']} distinct={r['distinct']} grey_px={r['grey_px']} ({r['grey_pct']}%) pure_bw={r['pure_pct']}%")
        for c, n in r["top"]:
            tag = "pure" if c in ((0, 0, 0), (255, 255, 255)) else "GREY"
            print(f"     rgb{c} x{n:>7}  {tag}")
