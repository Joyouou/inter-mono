"""Every-glyph review of Inter Mono against Inter, by glyph name.

Usage: python3 review.py <inter-src.ttf> <mono.ttf> <outdir>

Glyphs whose outline is Inter's, only moved sideways, are checked by number
(listed in same.tsv with their sidebearings). Every other glyph is drawn on
paged sheets: Inter in grey at its own width, Inter Mono in black in its cell.
"""
import math
import os
import sys

from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

import compare as C
import make_mono as mm

SC = 0.04
TOP, BOT = 2050, -560
PAGE_W, ROWS = 1500, 11


def record(p):
    r = RecordingPen()
    p.draw(r)
    return [pt for _, pts in r.value for pt in pts]


def same_shape(O, M):
    a, b = record(O), record(M)
    if len(a) != len(b) or not a:
        return None
    dx = b[0][0] - a[0][0]
    if all(abs(q[0] - p[0] - dx) < 1.6 and abs(q[1] - p[1]) < 1.6 for p, q in zip(a, b)):
        return dx
    return None


def main(src, mono_path, outdir):
    os.makedirs(outdir, exist_ok=True)
    C.SC, C.TOP, C.BOT = SC, TOP, BOT
    m = mm.Mono(src)
    mm.CW_SIGN = math.copysign(1, mm.area(mm.rect(0, 0, 10, 10)))
    mono = TTFont(mono_path)
    mgs = mono.getGlyphSet()
    rev = {}
    for u, n in sorted(m.orig.getBestCmap().items()):
        rev.setdefault(n, u)
    names = [n for n in m.orig.getGlyphOrder() if n in mgs and m.ohmtx[n][0] > 0]
    names.sort(key=lambda n: (rev.get(n, 0x10FFFF + 1), n))
    tf = m.deslant
    changed, same = [], []
    for n in names:
        O, M = C.path_of(m.ogs, n, tf), C.path_of(mgs, n, tf)
        if O.bounds == (0, 0, 0, 0):
            continue
        dx = same_shape(O, M)
        if dx is None:
            changed.append((n, O, M))
        else:
            adv = m.ohmtx[n][0]
            same.append((n, rev.get(n), adv, O.bounds[0], adv - O.bounds[2],
                         M.bounds[0], mm.CELL - M.bounds[2]))
    with open(os.path.join(outdir, "same.tsv"), "w") as f:
        f.write("name\tu\tadv\tolsb\torsb\tlsb\trsb\n")
        for r in same:
            f.write("\t".join("%04X" % x if i == 1 and x else str(round(x, 1) if isinstance(x, float) else x)
                              for i, x in enumerate(r)) + "\n")
    print(len(names), "glyphs:", len(same), "same shape,", len(changed), "drawn")
    lab = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
    th = int((TOP - BOT) * SC)
    rh = th + 20
    page, img, d, x, row = 0, None, None, 10, 0
    index = open(os.path.join(outdir, "index.tsv"), "w")

    def flush():
        nonlocal page, img
        if img:
            img.crop((0, 0, PAGE_W, 10 + (row + 1) * rh)).save(os.path.join(outdir, "p%02d.png" % page))
            page += 1
            img = None

    for n, O, M in changed:
        ow, cw = int(m.ohmtx[n][0] * SC), int(mm.CELL * SC)
        w = max(ow + cw + 22, 96)
        if img is None:
            img = Image.new("RGB", (PAGE_W, 10 + ROWS * rh), "white")
            d = ImageDraw.Draw(img)
            x, row = 10, 0
        if x + w > PAGE_W - 6:
            x, row = 10, row + 1
            if row >= ROWS:
                row -= 1
                flush()
                img = Image.new("RGB", (PAGE_W, 10 + ROWS * rh), "white")
                d = ImageDraw.Draw(img)
                x, row = 10, 0
        y = 10 + row * rh
        u = rev.get(n)
        d.text((x, y), (("%04X " % u) if u else "") + n[:int(w / 6) - (5 if u else 0)], font=lab, fill=(90, 90, 90))
        y += 13
        d.rectangle([x, y, x + ow, y + th], outline=(228, 228, 228))
        C.fill(d, O, x, y, (150, 150, 150))
        mx = x + ow + 8
        d.rectangle([mx, y, mx + cw, y + th], outline=(190, 190, 255))
        C.fill(d, M, mx, y, (0, 0, 0))
        index.write(f"p{page:02d}\t{n}\t{'%04X' % u if u else ''}\n")
        x += w + 8
    flush()
    print(page, "pages")


if __name__ == "__main__":
    main(*sys.argv[1:])
