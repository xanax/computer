"""Locate a specific flat colour in a screenshot and report the DOM element under it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pngstat import read_png

path = sys.argv[1]
target = tuple(int(v) for v in sys.argv[2].split(","))
w, h, ch, px = read_png(path)

xs, ys = [], []
for y in range(h):
    for x in range(w):
        i = (y * w + x) * ch
        if (px[i], px[i + 1], px[i + 2]) == target:
            xs.append(x)
            ys.append(y)
if not xs:
    print(f"{path}: no pixels of {target}")
    sys.exit()
print(f"{path}: {target} found n={len(xs)}")
print(f"   bbox x=[{min(xs)},{max(xs)}] y=[{min(ys)},{max(ys)}]")
cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
print(f"   centre probe point: ({cx},{cy})")
print("   probe_points " + ",".join(f"[{max(0,cx-200)},{cy}]" for _ in [0]) + f" centre=({cx},{cy})")
print("   rowspan/colspan histograms (top rows with count):")
from collections import Counter

rc = Counter(ys)
print("     rows:", rc.most_common(6))
