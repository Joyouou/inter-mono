"""Final fixes after hinting, from fontbakery's universal checks.

Usage: python finish_font.py <font.ttf> [...]   (edits in place)

- usWinAscent/usWinDescent cover every glyph, so Windows never clips
  (Inter's own values fall 2-3 units short of Aringacute and U+0347)
- head flag bit 3 ("force integer ppem"), expected in hinted fonts
"""
import sys

from fontTools.ttLib import TTFont

for path in sys.argv[1:]:
    font = TTFont(path)
    glyf = font["glyf"]
    y_max, y_min = 0, 0
    for name in font.getGlyphOrder():
        g = glyf[name]
        g.recalcBounds(glyf)
        if hasattr(g, "yMax"):
            y_max, y_min = max(y_max, g.yMax), min(y_min, g.yMin)
    os2 = font["OS/2"]
    os2.usWinAscent = max(os2.usWinAscent, y_max)
    os2.usWinDescent = max(os2.usWinDescent, -y_min)
    font["head"].flags |= 1 << 3
    font.save(path)
    print(path, "win", os2.usWinAscent, os2.usWinDescent)
