"""Coarse ASCII map of a screenshot: '#'=black '.'=white '?'=grey, plus sample pixels."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pngstat import read_png

path = sys.argv[1]
cols = int(sys.argv[2]) if len(sys.argv) > 2 else 70
w, h, ch, px = read_png(path)
rows = max(1, int(cols * h / w / 2))  # /2 for char aspect
cw, chh = w / cols, h / rows
print(f"{path}  {w}x{h}  grid {cols}x{rows}")
for r in range(rows):
    line = []
    for c in range(cols):
        x0, y0 = int(c * cw), int(r * chh)
        x1, y1 = min(w, int((c + 1) * cw)), min(h, int((r + 1) * chh))
        n = grey = white = black = 0
        for y in range(y0, y1, max(1, (y1 - y0) // 4)):
            for x in range(x0, x1, max(1, (x1 - x0) // 4)):
                i = (y * w + x) * ch
                rr, gg, bb = px[i], px[i + 1], px[i + 2]
                n += 1
                if rr == gg == bb:
                    if rr == 0:
                        black += 1
                    elif rr == 255:
                        white += 1
                    else:
                        grey += 1
                else:
                    grey += 1
        if n == 0:
            line.append(" ")
            continue
        if grey / n > 0.5:
            line.append("?")
        elif black / n > white / n:
            line.append("#")
        else:
            line.append(".")
    print("".join(line))
print("samples:")
for name, (x, y) in {
    "topleft-5,5": (5, 5),
    "center": (w // 2, h // 2),
    "bottomright": (w - 6, h - 6),
    "top-center": (w // 2, 6),
}.items():
    i = (y * w + x) * ch
    print(f"   {name:16} rgb({px[i]},{px[i+1]},{px[i+2]})")
