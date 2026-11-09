#!/usr/bin/env python3
"""
Make the Cabo app icon (Unraid dashboard, browser tab, phone home screen).

Writes app/static/icon.svg. PNGs are rendered from it (needs rsvg-convert):
  rsvg-convert -w 512 app/static/icon.svg -o app/static/icon.png
  rsvg-convert -w 180 app/static/icon.svg -o app/static/apple-touch-icon.png
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_cards import BLACK, GOLD, NIGHT, PINK, SUIT_PATHS, figure_path  # noqa: E402

S = 512


def card(x, y, angle, inner):
    """A 200x280 card centred on (x, y), rotated by `angle`."""
    return (f'<g transform="translate({x} {y}) rotate({angle}) translate(-100 -140)">'
            f'<rect x="0" y="0" width="200" height="280" rx="18" fill="#000" opacity=".35" transform="translate(6 10)"/>'
            f'{inner}</g>')


def back():
    return (f'<rect width="200" height="280" rx="18" fill="{NIGHT}" stroke="{PINK}" stroke-width="7"/>'
            f'<path d="M30 250 L30 92 A70 70 0 0 1 170 92 L170 250 Z" fill="url(#spot)"/>'
            f'<g transform="translate(44 48) scale(1.12)"><path d="{figure_path()}" fill="{NIGHT}" fill-rule="evenodd"/></g>'
            f'<path d="M30 250 L30 92 A70 70 0 0 1 170 92 L170 250 Z" fill="none" stroke="{GOLD}" stroke-width="5"/>')


def ace():
    spade = SUIT_PATHS["spade"]
    return (f'<rect width="200" height="280" rx="18" fill="#fdfaf3" stroke="{GOLD}" stroke-width="7"/>'
            f'<text x="34" y="62" font-family="Georgia, \'DejaVu Serif\', serif" font-weight="bold" font-size="54" '
            f'text-anchor="middle" fill="{BLACK}">A</text>'
            f'<path d="{spade}" fill="{BLACK}" transform="translate(40 80) scale(1.2)"/>')


svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}" width="{S}" height="{S}">'
       f'<defs><radialGradient id="spot" cx="0.5" cy="0.4" r="0.65">'
       f'<stop offset="0" stop-color="#ff7ab8"/><stop offset="0.55" stop-color="{PINK}"/>'
       f'<stop offset="1" stop-color="#5b1259"/></radialGradient>'
       f'<radialGradient id="bg" cx="0.5" cy="0.3" r="0.8"><stop offset="0" stop-color="#2c1450"/>'
       f'<stop offset="1" stop-color="{NIGHT}"/></radialGradient></defs>'
       f'<rect width="{S}" height="{S}" rx="104" fill="url(#bg)"/>'
       f'<rect x="14" y="14" width="{S - 28}" height="{S - 28}" rx="92" fill="none" stroke="{PINK}" stroke-width="10" opacity=".9"/>'
       + card(206, 262, -14, back())
       + card(312, 262, 12, ace())
       + '</svg>\n')

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "static", "icon.svg")
with open(out, "w") as f:
    f.write(svg)
print("wrote", os.path.normpath(out))
