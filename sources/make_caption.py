"""Closed-caption colour version of Inter Mono.

Usage: python3 make_caption.py <InterMono-Style.ttf> <out.ttf> [--white] [--alpha N]

Every spaced glyph becomes a colour glyph (COLR version 0) with two layers:
an opaque black box filling the whole cell, and the letter on top.  The letter
takes the text colour set in the program (white, yellow, cyan … as on real
captions); the box is always black.  With --white the letter is fixed white
instead, for programs where the text colour cannot be set.
All cells are the same size, so the boxes join into a continuous bar, as on
television captions.  The space gets a box too.  Zero-width combining marks
get no box, only the white layer.

The box runs from the descender to the ascender, which is also the default
line height, so lines stack without gaps.  It reaches OVERLAP units into the
next cell and the next line: the following box is painted over that part, and
it stops the hairline seams that anti-aliasing would leave between boxes.
In Italic the box leans with the letters.

With --alpha N (1-99) the box is N percent opaque, so the picture shows
through it, and the family is named "Inter Mono Caption N".  These boxes do not
overlap, because see-through boxes would look darker where they do; they meet
exactly at the cell edges instead, the line height is exactly 1.2 em, and
the box stays upright in Italic (a leaning edge can never fall on whole pixels).
Use a size that is a multiple of 5 px (20, 25, 30 ...): the boxes then fall on
whole pixels and join cleanly.  At other sizes thin lines show between them.

Programs without colour-font support show the plain letters.
"""
import math
import sys

from fontTools.colorLib.builder import buildCOLR, buildCPAL
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

BOX = "caption.box"
OVERLAP = 250                      # units the box reaches right and down
BLACK, WHITE = 0, 1                # palette entries
TEXT = 0xFFFF                      # COLR's "use the text colour" entry
PALETTE = [(0, 0, 0, 1.0), (1, 1, 1, 1.0)]
FAMILY = "Inter Mono Caption"


def box_glyph(font, cell, overlap, upright=False):
    hhea = font["hhea"]
    slant = 0 if upright else math.tan(math.radians(-font["post"].italicAngle))
    pivot = font["OS/2"].sxHeight / 2          # the letters lean about half x-height
    lean = lambda x, y: (round(x + slant * (y - pivot)), y)
    top, bottom = hhea.ascent, hhea.descent - overlap
    pen = TTGlyphPen(None)
    pen.moveTo(lean(0, bottom))
    pen.lineTo(lean(0, top))
    pen.lineTo(lean(cell + overlap, top))
    pen.lineTo(lean(cell + overlap, bottom))
    pen.closePath()
    return pen.glyph()


def rename(font, family, what):
    name = font["name"]
    style = name.getDebugName(2)
    ps = family.replace(" ", "") + "-" + style.replace(" ", "")
    version = name.getDebugName(5).split(";")[0]
    about = name.getDebugName(10) or ""
    for nid, val in ((1, family), (3, f"{version.split()[-1]};{ps}"), (4, f"{family} {style}"), (6, ps),
                     (10, f"Closed-caption colour version: letters in the text colour on {what} black boxes. " + about)):
        name.setName(val, nid, 3, 1, 0x409)


def main(src, dst, white=False, alpha=100):
    letter = WHITE if white else TEXT
    overlap = OVERLAP if alpha == 100 else 0
    family = FAMILY if alpha == 100 else f"{FAMILY} {alpha}"
    palette = [(0, 0, 0, alpha / 100), PALETTE[1]]
    font = TTFont(src)
    order = font.getGlyphOrder()
    hmtx = font["hmtx"]
    cell = max(hmtx[g][0] for g in order)

    if alpha < 100:
        # See-through boxes must not overlap, so they have to meet on whole
        # pixels.  The line becomes exactly 1.2 em (the cell is 0.6 em): at
        # sizes that are a multiple of 5 px both are whole pixels.
        upm, hhea, os2 = font["head"].unitsPerEm, font["hhea"], font["OS/2"]
        # 1.0 em above the baseline and 0.2 em below, so the top and bottom
        # edges land on whole pixels too.
        hhea.ascent, hhea.descent = upm, -round(0.2 * upm)
        hhea.lineGap = os2.sTypoLineGap = 0
        os2.sTypoAscender, os2.sTypoDescender = hhea.ascent, hhea.descent

    font.setGlyphOrder(order + [BOX])
    font["glyf"][BOX] = box_glyph(font, cell, overlap, upright=alpha < 100)
    hmtx[BOX] = (cell, 0)

    glyf = font["glyf"]
    layers = {}
    for g in order:
        ink = glyf[g].numberOfContours != 0
        if hmtx[g][0] == cell:
            layers[g] = [(BOX, BLACK)] + ([(g, letter)] if ink else [])
        elif ink:
            layers[g] = [(g, letter)]            # combining marks
    font["COLR"] = buildCOLR(layers, version=0, glyphMap=font.getReverseGlyphMap())
    font["CPAL"] = buildCPAL([palette])
    # Windows metrics only set the clipping area here: USE_TYPO_METRICS is on,
    # so DirectWrite spaces lines by the typo metrics, and GDI (which spaces by
    # these) draws no colour layers anyway. Cover the box and every glyph.
    os2 = font["OS/2"]
    os2.usWinAscent = max(os2.usWinAscent, font["hhea"].ascent)
    os2.usWinDescent = max(os2.usWinDescent, -font["hhea"].descent + overlap)
    rename(font, family, "opaque" if alpha == 100 else f"{alpha}% opaque")
    font.save(dst)
    print("wrote", dst, "-", len(layers), "colour glyphs, cell", cell)


if __name__ == "__main__":
    args = sys.argv[1:]
    alpha = int(args[args.index("--alpha") + 1]) if "--alpha" in args else 100
    main(args[0], args[1], white="--white" in args, alpha=alpha)
