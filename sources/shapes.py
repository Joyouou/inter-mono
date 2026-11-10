"""Large glyph sheet with cell boxes, for reviewing shapes.

Usage: python3 shapes.py <out.png> <font.ttf> [<font2.ttf> ...]
Each font gets the same rows; grey boxes are the 1229-unit cells, the
lines inside are baseline and x-height.
"""
import sys

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROWS = [
    "abcdefghijklm",
    "nopqrstuvwxyz",
    "ABCDEFGHIJKLM",
    "NOPQRSTUVWXYZ",
    "0123456789$%&",
    "@#*+-=<>()[]{}",
    ".,:;!?'\"`^~_|/\\",
    "íïłŀľťŕþßæœðĳ",
    "ЖШЩЮЫжшщюыΩωψ",
]
SIZE = 64


def main(out, *fonts):
    label = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    cw = SIZE * 1229 / 2048
    lh = int(SIZE * 1.3)
    width = int(24 + cw * max(len(r) for r in ROWS) + 24)
    img = Image.new("L", (width, (len(ROWS) * lh + 40) * len(fonts) + 10), 255)
    d = ImageDraw.Draw(img)
    y = 10
    for path in fonts:
        f = ImageFont.truetype(path, SIZE)
        tt = TTFont(path)
        asc = tt["hhea"].ascent * SIZE / 2048
        d.text((24, y), tt["name"].getDebugName(4), font=label, fill=110)
        y += 26
        for row in ROWS:
            base = y + asc * 0.95
            for i, ch in enumerate(row):
                x = 24 + i * cw
                d.rectangle([x, y, x + cw, y + lh - 4], outline=215)
                d.line([(x, base), (x + cw, base)], fill=225)
                d.line([(x, base - 1104 * SIZE / 2048), (x + cw, base - 1104 * SIZE / 2048)], fill=235)
                d.text((x, base), ch, font=f, fill=0, anchor="ls")
            y += lh
        y += 14
    img.crop((0, 0, width, y)).save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(*sys.argv[1:])
