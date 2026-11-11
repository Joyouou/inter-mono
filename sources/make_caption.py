"""Closed-caption colour version of Inter Mono.

Usage: python3 make_caption.py <InterMono-Style.ttf> <out.ttf> [--white]

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


def box_glyph(font, cell):
    hhea = font["hhea"]
    slant = math.tan(math.radians(-font["post"].italicAngle))
    pivot = font["OS/2"].sxHeight / 2          # the letters lean about half x-height
    lean = lambda x, y: (round(x + slant * (y - pivot)), y)
    top, bottom = hhea.ascent, hhea.descent - OVERLAP
    pen = TTGlyphPen(None)
    pen.moveTo(lean(0, bottom))
    pen.lineTo(lean(0, top))
    pen.lineTo(lean(cell + OVERLAP, top))
    pen.lineTo(lean(cell + OVERLAP, bottom))
    pen.closePath()
    return pen.glyph()


def rename(font):
    name = font["name"]
    style = name.getDebugName(2)
    ps = FAMILY.replace(" ", "") + "-" + style.replace(" ", "")
    version = name.getDebugName(5).split(";")[0]
    about = name.getDebugName(10) or ""
    for nid, val in ((1, FAMILY), (3, f"{version.split()[-1]};{ps}"), (4, f"{FAMILY} {style}"), (6, ps),
                     (10, "Closed-caption colour version: letters in the text colour on opaque black boxes. " + about)):
        name.setName(val, nid, 3, 1, 0x409)


def main(src, dst, white=False):
    letter = WHITE if white else TEXT
    font = TTFont(src)
    order = font.getGlyphOrder()
    hmtx = font["hmtx"]
    cell = max(hmtx[g][0] for g in order)

    font.setGlyphOrder(order + [BOX])
    font["glyf"][BOX] = box_glyph(font, cell)
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
    font["CPAL"] = buildCPAL([PALETTE])
    # Programs that space lines by the Windows metrics get the same line
    # height as the box, so lines stack without gaps there too.
    font["OS/2"].usWinAscent = font["hhea"].ascent
    font["OS/2"].usWinDescent = -font["hhea"].descent + OVERLAP
    rename(font)
    font.save(dst)
    print("wrote", dst, "-", len(layers), "colour glyphs, cell", cell)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], white="--white" in sys.argv)
