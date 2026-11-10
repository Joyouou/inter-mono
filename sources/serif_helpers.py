"""Add bracketed serifs to Inter (basic Latin) and save as "Inter Serif".

Usage: python make_serif.py <input.ttf> <output.ttf>

Serifs are built in a "local frame" where the stroke being capped points
up from y=0 (its terminal). Each serif is a slab of thickness T with curved
brackets joining it to the stroke, then unioned with the outline using
skia-pathops. Italic glyphs are de-slanted first and re-slanted afterwards,
so the same rules work for both styles.
"""
import math
import sys

import pathops
from fontTools.misc.transform import Transform
from fontTools.pens.basePen import BasePen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

# ---- design parameters (font units, UPM 2048; Inter Regular stem ~178) ----
# serifs ~0.75 px thick at 12 pt on a 96 dpi screen (1 px = 128 units there),
# so they still show without Retina; thinner ones blurred to grey
T = 92            # slab thickness
BRACKET = 84      # bracket reach (both along slab and up the stem)
EXT_UC = 128      # how far cap serifs stick out past the stem
EXT_LC = 112      # same for lowercase feet
EXT_HEAD = 148    # lowercase head serif (top-left of n, i, l, ...)
T_HEAD = 96
BEAK_LEN = 150    # vertical serifs on arm ends (E, F, L, T, Z)
BEAK_T = 96
STEM = 178
MIN_SB = 30       # minimum sidebearing measured from a serif tip
GIVE_BACK = 0.38  # share of the serif overhang added to the advance width
HEAD_SLOPE = 46   # lowercase head serifs dip this much at the tip (wedge)
SCAN = 6          # how far inside the terminal we probe for stroke edges

BASE, XH, CAP, DESC = 0, 1104, 1490, -418


# ---------------------------------------------------------------- geometry
class FlattenPen(BasePen):
    def __init__(self, steps=16):
        super().__init__(None)
        self.contours, self.cur, self.steps = [], None, steps

    def _moveTo(self, p):
        self.cur = [p]
        self.contours.append(self.cur)

    def _lineTo(self, p):
        self.cur.append(p)

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
            self.cur.append((a * p0[0] + b * p1[0] + c * p2[0],
                             a * p0[1] + b * p1[1] + c * p2[1]))

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        for i in range(1, self.steps + 1):
            t = i / self.steps
            mt = 1 - t
            a, b, c, d = mt ** 3, 3 * mt * mt * t, 3 * mt * t * t, t ** 3
            self.cur.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                             a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))


def flatten(path, tf):
    pen = FlattenPen()
    path.draw(TransformPen(pen, tf))
    return pen.contours


def intervals(contours, y):
    """Inside spans of the outline along the horizontal line at height y."""
    xs = []
    for c in contours:
        for (x0, y0), (x1, y1) in zip(c, c[1:] + c[:1]):
            if (y0 <= y < y1) or (y1 <= y < y0):
                xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
    xs.sort()
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]


def span_near(contours, y, a, c):
    """The span at height y that overlaps [a, c] the most."""
    best, score = (a, c), -1
    for lo, hi in intervals(contours, y):
        ov = min(hi, c) - max(lo, a)
        if ov > score:
            best, score = (lo, hi), ov
    return best


def serif_contour(contours, idx, ext_l, ext_r, t, b, slope=0):
    """Slab + brackets in local coordinates for the idx-th stroke at y=0."""
    if isinstance(idx, tuple):  # pick the span overlapping a range instead
        a, c = span_near(contours, SCAN, *idx)
    else:
        a, c = intervals(contours, SCAN)[idx]
    at, ct = span_near(contours, t, a, c)
    bl, br = min(b, ext_l * 0.7), min(b, ext_r * 0.7)
    pts = []  # list of ("L", pt) / ("Q", ctrl, pt)
    pts.append(("M", (a - ext_l, slope)))
    if slope:
        pts.append(("L", (a, 0)))
    if ext_r:
        pts += [("L", (c + ext_r, 0)), ("L", (c + ext_r, t)), ("L", (ct + br, t))]
        ctb = span_near(contours, t + br, a, c)[1]
        pts.append(("Q", (ct, t), (ctb, t + br)))
    else:
        xr = min(c, a + STEM * 0.55)
        pts += [("L", (xr, 0)), ("L", (xr, t + max(bl, 1)))]
    if ext_l:
        atb = span_near(contours, t + bl, a, c)[0]
        pts += [("L", (atb, t + bl)), ("Q", (at, t), (at - bl, t)),
                ("L", (a - ext_l, slope + t * (0.85 if slope else 1)))]
    else:
        xl = max(a, c - STEM * 0.55)
        pts += [("L", (xl, t + max(br, 1))), ("L", (xl, 0))]
    return pts


def draw_pts(pts, pen):
    for p in pts:
        if p[0] == "M":
            pen.moveTo(p[1])
        elif p[0] == "L":
            pen.lineTo(p[1])
        else:
            pen.qCurveTo(p[1], p[2])
    pen.closePath()


# ------------------------------------------------------------------ frames
def foot(y0):            # stroke rises from y0
    return Transform(1, 0, 0, 1, 0, -y0)


def head(y0):            # stroke hangs down from y0
    return Transform(1, 0, 0, -1, 0, y0)


def arm_end(x0, right):  # horizontal arm ending at x0; local x = global y
    return Transform(0, -1, 1, 0, 0, x0) if right else Transform(0, 1, 1, 0, 0, -x0)


SIDES = {"B": (1, 1), "L": (1, 0), "R": (0, 1)}


def S(frame, idx, sides="B", ext=None, t=T, slope=0):
    return ("S", frame, idx, sides, ext, t, slope)


def beak(right, band, side):
    """Vertical serif on the end of a horizontal arm in the y-band given.
    side "L" points down (top arms), "R" points up (bottom arms)."""
    return ("K", right, band, side)


TOP_BAND, BOT_BAND = (CAP - 140, CAP), (0, 140)
TOP_BAND_X, BOT_BAND_X = (XH - 140, XH), (0, 140)


def hs(y0, idx=0):  # lowercase head serif, left side only
    return S(head(y0), idx, "L", EXT_HEAD, T_HEAD, HEAD_SLOPE)


def ft(idx=0, sides="B", y0=BASE, ext=EXT_LC):
    return S(foot(y0), idx, sides, ext)


def uft(idx=0, sides="B"):
    return S(foot(BASE), idx, sides, EXT_UC)


def utop(idx=0, sides="B"):
    return S(head(CAP), idx, sides, EXT_UC)


SPEC = {
    # uppercase
    "A": [uft(0), uft(-1)],
    "B": [uft(0, "L"), utop(0, "L")],
    "D": [uft(0, "L"), utop(0, "L")],
    "E": [uft(0, "L"), utop(0, "L"), beak(True, TOP_BAND, "L"), beak(True, BOT_BAND, "R")],
    "F": [uft(0), utop(0, "L"), beak(True, TOP_BAND, "L")],
    "H": [uft(0), uft(1), utop(0), utop(1)],
    "I": [uft(0), utop(0)],
    "J": [utop(0)],
    "K": [uft(0), uft(-1), utop(0), utop(-1)],
    "L": [uft(0, "L"), utop(0), beak(True, BOT_BAND, "R")],
    "M": [uft(0), uft(-1), utop(0, "L"), utop(-1, "R")],
    "N": [uft(0), utop(0, "L"), utop(-1)],
    "P": [uft(0), utop(0, "L")],
    "R": [uft(0), utop(0, "L"), uft(-1, "R")],
    "T": [uft(0), beak(False, TOP_BAND, "L"), beak(True, TOP_BAND, "L")],
    "U": [utop(0), utop(-1)],
    "V": [utop(0), utop(-1)],
    "W": [utop(0), utop(-1)],
    "X": [utop(0), utop(-1), uft(0), uft(-1)],
    "Y": [utop(0), utop(-1), uft(0)],
    "Z": [beak(False, TOP_BAND, "L"), beak(True, BOT_BAND, "R")],
    # lowercase
    "a": [ft(-1, "R")],
    "b": [hs(CAP)],
    "d": [hs(CAP, -1), ft(-1, "R")],
    "f": [ft(0)],
    "h": [hs(CAP), ft(0), ft(1)],
    "dotlessi": [hs(XH), ft(0)],
    "uni0237": [hs(XH)],
    "k": [hs(CAP), ft(0), ft(-1), S(head(XH), -1, "B", EXT_LC)],
    "l": [hs(CAP), ft(0)],
    "m": [hs(XH), ft(0), ft(1), ft(2)],
    "n": [hs(XH), ft(0), ft(1)],
    "p": [hs(XH), ft(0, "B", DESC)],
    "q": [ft(0, "B", DESC)],
    "r": [hs(XH), ft(0)],
    "u": [hs(XH, 0), hs(XH, -1), ft(-1, "R")],
    "v": [S(head(XH), 0, "B", EXT_LC), S(head(XH), -1, "B", EXT_LC)],
    "w": [S(head(XH), 0, "B", EXT_LC), S(head(XH), -1, "B", EXT_LC)],
    "x": [S(head(XH), 0, "B", EXT_LC), S(head(XH), -1, "B", EXT_LC), ft(0), ft(-1)],
    "y": [S(head(XH), 0, "B", EXT_LC), S(head(XH), -1, "B", EXT_LC)],
    "z": [beak(False, TOP_BAND_X, "L"), beak(True, BOT_BAND_X, "R")],
    "one": [ft(0, "B", ext=EXT_UC)],
    # Latin letters Inter draws on their own (not base + accent)
    "Ħ": [uft(0), uft(1), utop(0), utop(1)],
    "Þ": [uft(0), utop(0)],
    "þ": [hs(CAP), ft(0, "B", DESC)],
    "ĸ": [hs(XH), ft(0), ft(-1), S(head(XH), -1, "B", EXT_LC)],
    "ŋ": [hs(XH), ft(0)],
    # Greek and Cyrillic capitals that share Latin construction
    "Γ": [uft(0), utop(0, "L"), beak(True, TOP_BAND, "L")],
    "Π": [uft(0), uft(-1)],
    "Г": [uft(0), utop(0, "L"), beak(True, TOP_BAND, "L")],
    "Б": [uft(0, "L"), utop(0, "L"), beak(True, TOP_BAND, "L")],
    "И": [uft(0), uft(-1), utop(0), utop(-1)],
    "Ш": [uft(0, "L"), uft(-1, "R"), utop(0), utop(1), utop(2)],
    "К": [uft(0), uft(-1), utop(0), utop(-1)],
}

# Cyrillic lowercase: upright forms only (the italic ones are shaped like n, u, ...)
UPRIGHT_ONLY = {
    "п": [ft(0), ft(-1)],
    "и": [S(head(XH), 0, "B", EXT_LC), S(head(XH), -1, "B", EXT_LC), ft(0), ft(-1)],
    "ш": [S(head(XH), 0, "B", EXT_LC), S(head(XH), 1, "B", EXT_LC),
          S(head(XH), -1, "B", EXT_LC), ft(0, "L"), ft(-1, "R")],
    "г": [ft(0), S(head(XH), 0, "L", EXT_LC), beak(True, TOP_BAND_X, "L")],
    "к": [S(head(XH), 0, "B", EXT_LC), ft(0), ft(-1), S(head(XH), -1, "B", EXT_LC)],
}


# ------------------------------------------------------------------- build
def glyph_path(gs, name, tf):
    p = pathops.Path()
    gs[name].draw(TransformPen(p.getPen(), tf))
    return p


def serif_path(contours_global, item):
    """Return a pathops.Path (in de-slanted glyph coords) for one serif."""
    out = pathops.Path()
    if item[0] == "S":
        _, frame, idx, sides, ext, t, slope = item
        ext = EXT_UC if ext is None else ext
        el, er = SIDES[sides]
        local = [[frame.transformPoint(p) for p in c] for c in contours_global]
        pts = serif_contour(local, idx, ext * el, ext * er, t, BRACKET, slope)
    else:
        _, right, (ylo, yhi), side = item
        xs = [x for c in contours_global for x, y in c if ylo <= y <= yhi]
        x0 = max(xs) if right else min(xs)
        frame = arm_end(x0, right)
        local = [[frame.transformPoint(p) for p in c] for c in contours_global]
        el, er = SIDES[side]
        pts = serif_contour(local, (ylo, yhi), BEAK_LEN * el, BEAK_LEN * er, BEAK_T,
                            BRACKET * 0.8)
    draw_pts(pts, TransformPen(out.getPen(), frame.inverse()))
    return out


def main(src, dst):
    font = TTFont(src)
    glyf, hmtx = font["glyf"], font["hmtx"]
    gs = font.getGlyphSet()
    cmap = font.getBestCmap()
    slant = math.tan(math.radians(-font["post"].italicAngle))
    # pivot at half x-height, where italic sidebearings are conventionally measured
    deslant = Transform(1, 0, -slant, 1, slant * XH / 2, 0)
    reslant = deslant.inverse()

    names = {}
    for key in SPEC:
        names[key] = cmap[ord(key)] if len(key) == 1 else key
    for key in ("one",):
        names[key] = cmap[ord("1")]

    shifts = {}  # glyph -> (dx, extra advance)
    spec = dict(SPEC)
    if slant:  # the italic f descends, so a baseline foot would sit mid-stroke
        spec["f"] = []
    else:
        spec.update(UPRIGHT_ONLY)
    for key in spec:
        if key not in names:
            names[key] = cmap[ord(key)]
    for key, items in spec.items():
        if not items:
            continue
        gname = names[key]
        base = glyph_path(gs, gname, deslant)
        flat = flatten(base, Transform())
        result = base
        try:
            for item in items:
                result = pathops.op(result, serif_path(flat, item), pathops.PathOp.UNION,
                                    clockwise=base.clockwise)
        except (IndexError, ValueError) as e:
            print("skipped", key, gname, e)
            continue

        # spacing: give back part of the overhang, keep MIN_SB from serif tips.
        # Measured de-slanted, so italic serifs leaning over the next letter
        # don't count as overhang; never ask for more room than Inter had.
        adv, _ = hmtx[gname]
        old_l, _, old_r, _ = base.bounds
        new_l, _, new_r, _ = result.bounds
        over_l = max(0, old_l - new_l)
        over_r = max(0, new_r - old_r)
        lsb_new = old_l - over_l * (1 - GIVE_BACK)
        dx = round(over_l * GIVE_BACK + max(0, min(MIN_SB, old_l) - lsb_new))
        rsb_old = adv - old_r
        rsb_new = rsb_old - over_r * (1 - GIVE_BACK)
        extra = dx + round(over_r * GIVE_BACK + max(0, min(MIN_SB, rsb_old) - rsb_new))

        new_pen = TTGlyphPen(None)
        result.draw(Cu2QuPen(TransformPen(
            new_pen, Transform().translate(dx, 0).transform(reslant)), max_err=0.5))
        new = new_pen.glyph()
        glyf[gname] = new
        new.recalcBounds(glyf)
        hmtx[gname] = (adv + extra, new.xMin)
        shifts[gname] = (dx, extra)

    # composites built on a changed glyph (accents, i, j, ...) follow it
    changed = True
    while changed:
        changed = False
        for gname in font.getGlyphOrder():
            g = glyf[gname]
            if gname in shifts or not g.isComposite():
                continue
            basec = g.components[0].glyphName
            if basec in shifts:
                dx, extra = shifts[basec]
                carry = dx  # offset for marks that follow the current letter
                for comp in g.components[1:]:
                    if comp.glyphName in shifts and comp.x > 0:  # a second letter
                        cdx, cextra = shifts[comp.glyphName]
                        comp.x += extra + cdx
                        carry = extra + cdx
                        extra += cextra
                    else:
                        comp.x += carry
                adv, _ = hmtx[gname]
                g.recalcBounds(glyf)
                hmtx[gname] = (adv + extra, g.xMin)
                shifts[gname] = (dx, extra)
                changed = True

    shift_mark_anchors(font, shifts)

    for gname in shifts:
        g = glyf[gname]
        g.recalcBounds(glyf)
        hmtx[gname] = (hmtx[gname][0], getattr(g, "xMin", 0))

    rename(font)
    font["OS/2"].recalcAvgCharWidth(font)
    font.save(dst)
    print("wrote", dst, "-", len(shifts), "glyphs changed")


def shift_mark_anchors(font, shifts):
    """Keep mark-to-base anchors aligned with shifted base glyphs."""
    if "GPOS" not in font:
        return
    for lookup in font["GPOS"].table.LookupList.Lookup:
        for st in lookup.SubTable:
            if lookup.LookupType == 9:
                st = st.ExtSubTable
            if getattr(st, "LookupType", None) != 4 and lookup.LookupType not in (4, 9):
                continue
            if not hasattr(st, "BaseCoverage"):
                continue
            for gname, rec in zip(st.BaseCoverage.glyphs, st.BaseArray.BaseRecord):
                if gname in shifts:
                    for anchor in rec.BaseAnchor:
                        if anchor is not None:
                            anchor.XCoordinate += shifts[gname][0]


def rename(font):
    name = font["name"]
    style = name.getDebugName(17) or name.getDebugName(2)
    family = "Inter Serif"
    ps = "InterSerif-" + style.replace(" ", "")
    for rec in list(name.names):
        if rec.nameID in (16, 17, 21, 22, 25):
            name.removeNames(nameID=rec.nameID)
    for nid, val in ((1, family), (2, style), (3, f"{ps};derived from Inter"),
                     (4, f"{family} {style}"), (6, ps)):
        name.setName(val, nid, 3, 1, 0x409)
        name.setName(val, nid, 1, 0, 0)
    name.removeNames(platformID=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
