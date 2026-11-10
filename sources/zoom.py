"""Big side-by-side of chosen characters: Inter (grey) | Inter Mono (black).

Usage: python3 zoom.py <inter-src.ttf> <mono.ttf> <out.png> <chars>
"""
import math
import sys

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

import compare as C
import make_mono as mm


def main(src, mono_path, out, chars, per=6):
    C.SC = 0.09
    m = mm.Mono(src)
    mm.CW_SIGN = math.copysign(1, mm.area(mm.rect(0, 0, 10, 10)))
    mono = TTFont(mono_path)
    mgs, mcmap, ocmap = mono.getGlyphSet(), mono.getBestCmap(), m.orig.getBestCmap()
    th = int((C.TOP - C.BOT) * C.SC)
    tw = int((2300 + mm.CELL) * C.SC) + 40
    chars = [c for c in chars if ord(c) in mcmap and ord(c) in ocmap]
    rows = (len(chars) + per - 1) // per
    img = Image.new("RGB", (per * tw + 20, rows * (th + 26) + 20), "white")
    d = ImageDraw.Draw(img)
    lab = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
    for i, ch in enumerate(chars):
        x, y = 10 + (i % per) * tw, 10 + (i // per) * (th + 26)
        d.text((x, y), f"U+{ord(ch):04X} {mcmap[ord(ch)]}", font=lab, fill=(90, 90, 90))
        y += 18
        adv = m.ohmtx[ocmap[ord(ch)]][0]
        d.rectangle([x, y, x + adv * C.SC, y + th], outline=(220, 220, 220))
        C.fill(d, C.path_of(m.ogs, ocmap[ord(ch)], m.deslant), x, y, (150, 150, 150))
        mx = x + int(max(adv, 1300) * C.SC) + 10
        d.rectangle([mx, y, mx + mm.CELL * C.SC, y + th], outline=(190, 190, 255))
        C.fill(d, C.path_of(mgs, mcmap[ord(ch)], m.deslant), mx, y, (0, 0, 0))
    img.save(out)


if __name__ == "__main__":
    main(*sys.argv[1:])
