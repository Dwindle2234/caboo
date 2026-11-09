#!/usr/bin/env python3
"""
Import the traditional court-card figures (J, Q, K of each suit) for the Cabo deck.

Source: Byron Knoll's vector playing cards, released into the PUBLIC DOMAIN
        (https://github.com/notpeter/vector-playing-cards - cards-svg/JS.svg ... KC.svg).
        The figures follow the classic 19th-century English court designs.

What this does, once, by hand:
  * pulls just the figure artwork out of each court card (no card border, corners or pips -
    the Cabo generator draws those itself),
  * sorts every original colour into a named group (red, yellow, blue, ...), so the
    generator can recolour the figures in the Cabo neon-and-gold palette,
  * converts the paths to absolute coordinates, rounds them, and writes them back as compact
    relative paths. Rounding the absolute points means no error builds up along a path,
    and the result is a fraction of the original ~8 MB.

Usage:  python tools/import_courts.py <path to cards-svg folder>
Writes: tools/court_art.json   (committed; read by generate_cards.py at build time)
"""
import colorsys
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

SVG = "{http://www.w3.org/2000/svg}"
TOKEN = re.compile(r"[MmLlCcZzHhVvSsQqTtAa]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
# the figure panel inside each source card (same for all 12), in the source card's coordinates
PANEL = (22.3, 22.5, 122.5, 197.9)          # x, y, width, height


def colour_group(hex_colour):
    """Sort an original colour into a named group by hue / brightness."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h *= 360
    if v < 0.15:
        return "ink"
    if s < 0.15:
        return "paper"
    if h < 20 or h >= 330:
        return "red" if v > 0.75 else "darkred"
    if h < 75:
        return "yellow" if v > 0.75 else "olive"
    if h < 165:
        return "green" if v > 0.45 else "darkgreen"
    if h < 260:
        return "blue" if v < 0.70 or s > 0.75 else "lightblue"
    return "red"


Q = int(os.environ.get("COURT_GRID", "2"))   # grid size in source units (1 unit ~ 0.2 px on a full-size card)


def compact_path(d, places=0):
    """Absolute-ise, round and re-emit a path as relative commands (no drift, much smaller)."""
    toks = TOKEN.findall(d)
    out, i = [], 0
    cx = cy = sx = sy = 0.0        # exact current point / subpath start
    rx = ry = 0.0                  # last point as emitted (rounded) - relative output is from here
    cmd = None

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def rnd(v):
        return float(round(v / Q))

    def fmt(v):
        s = f"{v:.{places}f}" if places else str(int(v))
        return s

    def emit_points(letter, pts):
        nonlocal rx, ry
        parts = []
        for (ax, ay) in pts:
            px, py = rnd(ax), rnd(ay)
            parts.append(f"{fmt(px - rx if letter.islower() else px)} {fmt(py - ry if letter.islower() else py)}")
        # relative control points are all relative to the segment start, so advance only at the end
        rx, ry = rnd(pts[-1][0]), rnd(pts[-1][1])
        return parts

    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                out.append("z")
                cx, cy = sx, sy
                rx, ry = rnd(sx), rnd(sy)
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            x, y = num(), num()
            if rel:
                x, y = cx + x, cy + y
            px, py = rnd(x), rnd(y)
            out.append(f"M{fmt(px)} {fmt(py)}")
            cx, cy, sx, sy = x, y, x, y
            rx, ry = px, py
            cmd = "l" if rel else "L"          # implicit lineto after moveto
        elif c == "L":
            x, y = num(), num()
            if rel:
                x, y = cx + x, cy + y
            start = (rx, ry)
            px, py = rnd(x), rnd(y)
            out.append(f"l{fmt(px - start[0])} {fmt(py - start[1])}")
            cx, cy, rx, ry = x, y, px, py
        elif c == "H":
            x = num() + (cx if rel else 0)
            px = rnd(x)
            out.append(f"h{fmt(px - rx)}")
            cx, rx = x, px
        elif c == "V":
            y = num() + (cy if rel else 0)
            py = rnd(y)
            out.append(f"v{fmt(py - ry)}")
            cy, ry = y, py
        elif c == "C":
            vals = [num() for _ in range(6)]
            pts = []
            for k in range(0, 6, 2):
                x, y = vals[k], vals[k + 1]
                if rel:
                    x, y = cx + x, cy + y
                pts.append((x, y))
            start = (rx, ry)
            seg = []
            for (ax, ay) in pts:
                seg.append(f"{fmt(rnd(ax) - start[0])} {fmt(rnd(ay) - start[1])}")
            out.append("c" + " ".join(seg))
            cx, cy = pts[-1]
            rx, ry = rnd(cx), rnd(cy)
        else:
            raise ValueError(f"unsupported path command {cmd!r}")
    out = [o for o in out if o not in ("l0 0", "c0 0 0 0 0 0", "h0", "v0")]
    s = "".join(out)
    s = re.sub(r" -", "-", s)                      # "1 -2" -> "1-2" (valid SVG, smaller)
    return s


def extract(path):
    root = ET.parse(path).getroot()
    fig = max(root.iter(SVG + "g"), key=lambda g: sum(len(p.get("d", "")) for p in g.findall(SVG + "path")))
    shapes = []
    for p in fig.findall(SVG + "path"):
        m = re.search(r"fill:(#[0-9a-fA-F]{6})", p.get("style", "")) or re.search(r"#[0-9a-fA-F]{6}", p.get("fill", ""))
        colour = (m.group(1) if m.groups() else m.group(0)).lower() if m else "#000000"
        shapes.append([colour_group(colour), compact_path(p.get("d"))])
    return {"transform": fig.get("transform"), "grid": Q, "shapes": shapes}


def main(src):
    art = {"source": "Byron Knoll, vector-playing-cards (public domain)", "panel": PANEL, "cards": {}}
    for r in "JQK":
        for s in "SHDC":
            f = os.path.join(src, f"{r}{s}.svg")
            art["cards"][r + s] = extract(f)
            print(f"{r}{s}: {len(art['cards'][r + s]['shapes'])} shapes, "
                  f"groups {sorted({g for g, _ in art['cards'][r + s]['shapes']})}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "court_art.json")
    with open(out, "w") as fh:
        json.dump(art, fh, separators=(",", ":"))
    print(f"wrote {out} ({os.path.getsize(out) / 1024:.0f} KB)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
