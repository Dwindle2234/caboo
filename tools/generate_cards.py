#!/usr/bin/env python3
"""
Generate the Cabo card deck as SVG files.

Produces 52 standard faces, 2 jokers and the card back into
app/static/cards/ (or the directory passed as the first argument).

File names:  AS.svg, 10H.svg, KD.svg ...  JR.svg (red joker), JB.svg (black joker), back.svg

Run:  python tools/generate_cards.py
"""
import math
import os
import sys

W, H = 250, 350           # card size (poker ratio 2.5 x 3.5)
R = 16                    # corner radius

RED = "#d0193a"
BLACK = "#17142b"
GOLD = "#e3b04b"
PINK = "#ff2e88"
NIGHT = "#140a24"
PAPER = "#fdfaf3"

SUITS = {
    "S": ("spade", BLACK),
    "H": ("heart", RED),
    "D": ("diamond", RED),
    "C": ("club", BLACK),
}
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

# Suit shapes drawn in a 100 x 100 box centred on (50, 50)
SUIT_PATHS = {
    "heart": "M50 90 C 22 68 2 50 6 30 C 10 10 38 6 50 28 C 62 6 90 10 94 30 C 98 50 78 68 50 90 Z",
    "diamond": "M50 4 Q 66 30 86 50 Q 66 70 50 96 Q 34 70 14 50 Q 34 30 50 4 Z",
    "spade": ("M50 6 C 38 28 6 42 8 64 C 10 82 34 86 46 70 C 45 80 40 90 30 96 L 70 96 "
              "C 60 90 55 80 54 70 C 66 86 90 82 92 64 C 94 42 62 28 50 6 Z"),
    "club": ("M50 8 A 19 19 0 0 1 64 40 A 19 19 0 1 1 55 70 C 56 80 61 90 70 96 L 30 96 "
             "C 39 90 44 80 45 70 A 19 19 0 1 1 36 40 A 19 19 0 0 1 50 8 Z"),
}

FONT = "Georgia, 'DejaVu Serif', 'Times New Roman', serif"
SANS = "'Arial Black', 'Helvetica Neue', Arial, 'DejaVu Sans', sans-serif"


# --------------------------------------------------------------------------- helpers
def smooth_path(points, closed=True, tension=0.5):
    """Catmull-Rom spline through points -> cubic bezier SVG path."""
    pts = list(points)
    n = len(pts)
    d = f"M{pts[0][0]:.1f} {pts[0][1]:.1f} "
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 3, p1[1] + (p2[1] - p0[1]) * tension / 3)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 3, p2[1] - (p3[1] - p1[1]) * tension / 3)
        d += f"C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p2[0]:.1f} {p2[1]:.1f} "
    return d + ("Z" if closed else "")


def svg_doc(body, defs=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
        f"<defs>{defs}</defs>{body}</svg>\n"
    )


def suit_use(suit_name, cx, cy, size, color, rotate=False, extra=""):
    s = size / 100.0
    rot = f" rotate(180 50 50)" if rotate else ""
    return (f'<path d="{SUIT_PATHS[suit_name]}" fill="{color}" {extra} '
            f'transform="translate({cx - size / 2:.1f} {cy - size / 2:.1f}) scale({s:.3f}){rot}"/>')


# --------------------------------------------------------------------------- the pin-up silhouette
# A classic 1950s "hand-on-hip" pin-up silhouette, facing right, in a ~100 x 215 box.
FIGURE_OUTLINE = [
    # head (top, face profile)
    (49, 6), (57, 7), (62, 12), (64, 19), (67, 24), (64, 26), (64, 29), (61, 33), (57, 35),
    # neck + chest
    (57, 40), (61, 45), (68, 53), (70, 60), (66, 67), (59, 71),
    # waist + front hip
    (57, 80), (57, 89), (61, 99), (63, 110),
    # front leg (stepping forward)
    (63, 128), (61, 148), (61, 168), (60, 188), (62, 197), (69, 206), (66, 210), (58, 207), (55, 198),
    # inner leg / crotch
    (55, 180), (54, 158), (50, 136), (48, 132),
    # back leg
    (46, 150), (45, 172), (45, 193), (47, 205), (42, 210), (38, 204), (38, 190), (36, 166), (32, 140),
    # seat + back hip
    (28, 120), (29, 106), (33, 97),
    # arm on hip, elbow out to the left
    (31, 93), (22, 80), (18, 70), (25, 57), (33, 46),
    # shoulder, back of neck, hair
    (38, 41), (42, 38), (38, 36), (31, 31), (29, 22), (33, 12), (41, 6),
]
# negative space between bent arm and waist
FIGURE_ARM_GAP = [(36, 86), (28, 75), (29, 64), (37, 56), (42, 64), (43, 77)]


def figure_path():
    return smooth_path(FIGURE_OUTLINE) + " " + smooth_path(FIGURE_ARM_GAP)


# Lady's head-and-shoulders profile for the Queens (cameo), in a ~100x100 box facing right
CAMEO_OUTLINE = [
    (50, 8), (62, 10), (70, 20), (72, 32), (76, 40), (72, 43), (73, 49), (70, 51), (71, 56),
    (66, 61), (60, 62), (60, 70), (68, 78), (82, 86), (88, 96),
    (14, 96), (20, 84), (34, 76), (38, 66), (30, 62), (22, 54), (18, 42), (20, 28), (28, 16), (38, 9),
]


# --------------------------------------------------------------------------- faces
PIP_X = {"L": 78, "M": 125, "R": 172}
PIP_LAYOUT = {
    2: [("M", 72), ("M", 278)],
    3: [("M", 72), ("M", 175), ("M", 278)],
    4: [("L", 72), ("R", 72), ("L", 278), ("R", 278)],
    5: [("L", 72), ("R", 72), ("M", 175), ("L", 278), ("R", 278)],
    6: [("L", 72), ("R", 72), ("L", 175), ("R", 175), ("L", 278), ("R", 278)],
    7: [("L", 72), ("R", 72), ("M", 123), ("L", 175), ("R", 175), ("L", 278), ("R", 278)],
    8: [("L", 72), ("R", 72), ("M", 123), ("L", 175), ("R", 175), ("M", 227), ("L", 278), ("R", 278)],
    9: [("L", 72), ("R", 72), ("L", 141), ("R", 141), ("M", 175), ("L", 209), ("R", 209), ("L", 278), ("R", 278)],
    10: [("L", 72), ("R", 72), ("M", 106), ("L", 141), ("R", 141), ("L", 209), ("R", 209), ("M", 244),
         ("L", 278), ("R", 278)],
}


def card_base(fill=PAPER, stroke="#cfc6b0"):
    return (f'<rect x="1.5" y="1.5" width="{W - 3}" height="{H - 3}" rx="{R}" ry="{R}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="3"/>')


def corner_index(rank, suit_name, color):
    # top-left, then same group rotated 180 for bottom-right
    size = 40 if rank != "10" else 36
    spacing = "-3" if rank == "10" else "0"
    g = (f'<text x="25" y="46" font-family="{FONT}" font-weight="bold" font-size="{size}" '
         f'letter-spacing="{spacing}" text-anchor="middle" fill="{color}">{rank}</text>'
         + suit_use(suit_name, 25, 68, 26, color))
    return f"<g>{g}</g><g transform='rotate(180 {W / 2} {H / 2})'>{g}</g>"


def number_card(rank, suit):
    suit_name, color = SUITS[suit]
    body = card_base() + corner_index(rank, suit_name, color)
    if rank == "A":
        if suit == "S":   # the ornate Ace of Spades
            body += (f'<circle cx="125" cy="175" r="78" fill="none" stroke="{GOLD}" stroke-width="3"/>'
                     f'<circle cx="125" cy="175" r="70" fill="none" stroke="{GOLD}" stroke-width="1" '
                     f'stroke-dasharray="3 4"/>')
            body += suit_use(suit_name, 125, 172, 120, color)
            body += (f'<text x="125" y="290" font-family="{SANS}" font-size="13" letter-spacing="4" '
                     f'text-anchor="middle" fill="{GOLD}">CABO</text>')
        else:
            body += suit_use(suit_name, 125, 175, 100, color)
    else:
        for col, y in PIP_LAYOUT[int(rank)]:
            body += suit_use(suit_name, PIP_X[col], y, 46, color, rotate=y > 176)
    return svg_doc(body)


def crown_icon(color):
    return (f'<path d="M-34 14 L-40 -22 L-18 -2 L0 -32 L18 -2 L40 -22 L34 14 Z" fill="{GOLD}" '
            f'stroke="{color}" stroke-width="3" stroke-linejoin="round"/>'
            f'<rect x="-36" y="14" width="72" height="10" rx="2" fill="{GOLD}" stroke="{color}" stroke-width="3"/>'
            f'<circle cx="0" cy="-34" r="5" fill="{color}"/><circle cx="-40" cy="-24" r="4" fill="{color}"/>'
            f'<circle cx="40" cy="-24" r="4" fill="{color}"/>')


def cameo_icon(color):
    d = smooth_path(CAMEO_OUTLINE)
    return (f'<ellipse cx="0" cy="0" rx="40" ry="46" fill="{GOLD}" stroke="{color}" stroke-width="3"/>'
            f'<ellipse cx="0" cy="0" rx="33" ry="39" fill="{PAPER}"/>'
            f'<clipPath id="cameoclip"><ellipse cx="0" cy="0" rx="33" ry="39"/></clipPath>'
            f'<g clip-path="url(#cameoclip)"><path d="{d}" fill="{color}" '
            f'transform="translate(-34 -30) scale(0.68)"/></g>')


def dagger_icon(color):
    return (f'<path d="M0 -40 L8 -14 L8 14 L-8 14 L-8 -14 Z" fill="{PAPER}" stroke="{color}" stroke-width="3" '
            f'stroke-linejoin="round"/>'
            f'<line x1="0" y1="-30" x2="0" y2="12" stroke="{color}" stroke-width="2"/>'
            f'<rect x="-26" y="14" width="52" height="8" rx="4" fill="{GOLD}" stroke="{color}" stroke-width="3"/>'
            f'<rect x="-5" y="22" width="10" height="16" fill="{color}"/>'
            f'<circle cx="0" cy="42" r="6" fill="{GOLD}" stroke="{color}" stroke-width="3"/>')


def court_card(rank, suit):
    suit_name, color = SUITS[suit]
    gid = f"g{rank}{suit}"
    defs = (f'<linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{color}" stop-opacity="0.18"/>'
            f'<stop offset="0.5" stop-color="{color}" stop-opacity="0.04"/>'
            f'<stop offset="1" stop-color="{color}" stop-opacity="0.18"/></linearGradient>')
    body = card_base() + corner_index(rank, suit_name, color)
    # panel
    px, py, pw, ph = 52, 30, 146, 290
    body += (f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="10" fill="url(#{gid})" '
             f'stroke="{color}" stroke-width="3"/>'
             f'<rect x="{px + 6}" y="{py + 6}" width="{pw - 12}" height="{ph - 12}" rx="6" fill="none" '
             f'stroke="{GOLD}" stroke-width="2"/>')
    # art-deco fans in panel corners
    for (cx, cy, a) in [(px + 6, py + 6, 0), (px + pw - 6, py + 6, 90),
                        (px + pw - 6, py + ph - 6, 180), (px + 6, py + ph - 6, 270)]:
        rays = ""
        for k in range(5):
            ang = math.radians(a + 10 + k * 17.5)
            rays += (f'<line x1="{cx}" y1="{cy}" x2="{cx + 26 * math.cos(ang):.1f}" '
                     f'y2="{cy + 26 * math.sin(ang):.1f}" stroke="{GOLD}" stroke-width="1.5"/>')
        body += rays
    icon = {"K": crown_icon, "Q": cameo_icon, "J": dagger_icon}[rank](color)
    half = (f'<g transform="translate(125 86) scale(0.8)">{icon}</g>'
            f'<text x="125" y="156" font-family="{FONT}" font-weight="bold" font-size="40" '
            f'text-anchor="middle" fill="{color}">{rank}</text>')
    body += half + f'<g transform="rotate(180 125 175)">{half}</g>'
    body += (f'<line x1="{px + 14}" y1="175" x2="{px + pw - 14}" y2="175" stroke="{GOLD}" stroke-width="2"/>'
             + suit_use(suit_name, 125, 175, 22, color, extra=f'stroke="{PAPER}" stroke-width="10"'))
    return svg_doc(body, defs)


def jester_hat(color, accent):
    return (
        # three floppy points
        f'<path d="M-50 20 C -60 -10 -70 -30 -78 -44 C -50 -36 -30 -20 -18 0 Z" fill="{color}"/>'
        f'<path d="M50 20 C 60 -10 70 -30 78 -44 C 50 -36 30 -20 18 0 Z" fill="{color}"/>'
        f'<path d="M-24 18 C -24 -20 -10 -50 0 -66 C 10 -50 24 -20 24 18 Z" fill="{accent}"/>'
        f'<path d="M-52 18 Q 0 4 52 18 L 52 32 Q 0 18 -52 32 Z" fill="{GOLD}" stroke="{BLACK}" stroke-width="2"/>'
        f'<circle cx="-78" cy="-44" r="8" fill="{GOLD}" stroke="{BLACK}" stroke-width="2"/>'
        f'<circle cx="78" cy="-44" r="8" fill="{GOLD}" stroke="{BLACK}" stroke-width="2"/>'
        f'<circle cx="0" cy="-66" r="8" fill="{GOLD}" stroke="{BLACK}" stroke-width="2"/>'
    )


def joker_card(colour_code):
    color = RED if colour_code == "R" else BLACK
    accent = BLACK if colour_code == "R" else RED
    letters = "JOKER"
    idx = "".join(
        f'<text x="24" y="{36 + i * 24}" font-family="{SANS}" font-size="22" text-anchor="middle" '
        f'fill="{color}">{ch}</text>' for i, ch in enumerate(letters))
    idx += f'<g transform="translate(24 {36 + 5 * 24 - 6})">' \
           f'<path d="M0 -10 L3 -3 L10 -3 L4 2 L6 10 L0 5 L-6 10 L-4 2 L-10 -3 L-3 -3 Z" fill="{GOLD}"/></g>'
    body = card_base() + f"<g>{idx}</g><g transform='rotate(180 125 175)'>{idx}</g>"
    # starburst
    rays = ""
    for k in range(24):
        a1 = math.radians(k * 15)
        a2 = math.radians(k * 15 + 7.5)
        rays += (f'M125 170 L{125 + 95 * math.cos(a1):.1f} {170 + 95 * math.sin(a1):.1f} '
                 f'L{125 + 95 * math.cos(a2):.1f} {170 + 95 * math.sin(a2):.1f} Z ')
    body += f'<path d="{rays}" fill="{GOLD}" opacity="0.25"/>'
    body += f'<circle cx="125" cy="170" r="62" fill="none" stroke="{color}" stroke-width="3"/>'
    body += f'<g transform="translate(125 175) scale(1.0)">{jester_hat(color, accent)}</g>'
    body += (f'<text x="125" y="296" font-family="{SANS}" font-size="30" letter-spacing="3" '
             f'text-anchor="middle" fill="{color}">JOKER</text>')
    return svg_doc(body)


# --------------------------------------------------------------------------- back
def back_card():
    defs = (
        f'<radialGradient id="spot" cx="0.5" cy="0.42" r="0.6">'
        f'<stop offset="0" stop-color="#ff7ab8"/><stop offset="0.55" stop-color="{PINK}"/>'
        f'<stop offset="1" stop-color="#5b1259"/></radialGradient>'
        f'<pattern id="lattice" width="20" height="20" patternUnits="userSpaceOnUse">'
        f'<path d="M10 0 L20 10 L10 20 L0 10 Z" fill="none" stroke="{GOLD}" stroke-opacity="0.22" '
        f'stroke-width="1"/></pattern>'
        f'<filter id="glow" x="-20%" y="-20%" width="140%" height="140%">'
        f'<feGaussianBlur stdDeviation="3" result="b"/>'
        f'<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        f'<clipPath id="arch"><path d="M56 300 L56 112 A69 69 0 0 1 194 112 L194 300 Z"/></clipPath>'
    )
    body = card_base(fill=NIGHT, stroke="#3a2450")
    body += f'<rect x="14" y="14" width="{W - 28}" height="{H - 28}" rx="10" fill="url(#lattice)"/>'
    # neon frame
    body += (f'<rect x="14" y="14" width="{W - 28}" height="{H - 28}" rx="10" fill="none" stroke="{PINK}" '
             f'stroke-width="3" filter="url(#glow)"/>'
             f'<rect x="22" y="22" width="{W - 44}" height="{H - 44}" rx="6" fill="none" stroke="{GOLD}" '
             f'stroke-width="1.5"/>')
    # arch window with spotlight
    body += (f'<path d="M56 300 L56 112 A69 69 0 0 1 194 112 L194 300 Z" fill="url(#spot)"/>')
    # spotlight rays + floor inside arch
    rays = ""
    for k in range(-4, 5):
        rays += (f'<path d="M125 20 L{125 + k * 22 - 6} 310 L{125 + k * 22 + 6} 310 Z" '
                 f'fill="#ffffff" opacity="0.07"/>')
    body += f'<g clip-path="url(#arch)">{rays}' \
            f'<ellipse cx="125" cy="292" rx="56" ry="9" fill="{NIGHT}" opacity="0.45"/></g>'
    # the lady
    body += (f'<g transform="translate(70 60) scale(1.12)">'
             f'<path d="{figure_path()}" fill="{NIGHT}" fill-rule="evenodd"/></g>')
    body += (f'<path d="M56 300 L56 112 A69 69 0 0 1 194 112 L194 300 Z" fill="none" stroke="{GOLD}" '
             f'stroke-width="3"/>')
    # stars
    for (sx, sy, s) in [(40, 46, 1.0), (210, 46, 1.0), (36, 200, 0.7), (214, 200, 0.7), (125, 34, 0.8)]:
        body += (f'<g transform="translate({sx} {sy}) scale({s})">'
                 f'<path d="M0 -9 L2.5 -2.5 L9 0 L2.5 2.5 L0 9 L-2.5 2.5 L-9 0 L-2.5 -2.5 Z" fill="{GOLD}"/></g>')
    # title plaque
    body += (f'<rect x="58" y="300" width="134" height="30" rx="4" fill="{NIGHT}" stroke="{GOLD}" '
             f'stroke-width="2"/>'
             f'<text x="125" y="323" font-family="{SANS}" font-size="22" letter-spacing="6" '
             f'text-anchor="middle" fill="{PINK}" filter="url(#glow)">CABO</text>')
    return svg_doc(body, defs)


# --------------------------------------------------------------------------- main
def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    count = 0
    for suit in SUITS:
        for rank in RANKS:
            svg = court_card(rank, suit) if rank in "JQK" else number_card(rank, suit)
            with open(os.path.join(out_dir, f"{rank}{suit}.svg"), "w") as f:
                f.write(svg)
            count += 1
    for c in ("R", "B"):
        with open(os.path.join(out_dir, f"J{c}.svg"), "w") as f:
            f.write(joker_card(c))
        count += 1
    with open(os.path.join(out_dir, "back.svg"), "w") as f:
        f.write(back_card())
    print(f"Wrote {count} faces + back to {out_dir}")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    default = os.path.join(here, "..", "app", "static", "cards")
    main(sys.argv[1] if len(sys.argv) > 1 else default)
