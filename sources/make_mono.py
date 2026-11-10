"""Inter Mono: a monospaced Inter, made from the Inter 18pt static fonts.

Usage: python3 make_mono.py <Inter_18pt-Style.ttf> <InterMono-Style.ttf>

Every glyph gets a 1229-unit cell (0.6 em); zero-width marks stay zero.

- Glyphs that already fit are only re-centred.
- Wider glyphs are narrowed without thinning their stems: the outline is
  scaled horizontally, then each point moves out along its outline normal
  by the lost weight times the normal's horizontal part, so vertical
  strokes get their weight back and the tops of bowls stay round. Glyphs
  with three or more stems (m w M W ш ...) keep LIGHT of the stem weight,
  so their counters stay open.
- Narrow letters get coding forms: i and j a head stroke, i and r a foot
  slab, l a head stroke and a longer tail, f and t longer crossbars.
- Composites (accented letters) keep their components; each mark is moved
  with the part of the base glyph it sat over. Mark anchors follow too.
- Box drawing and block elements are added (U+2500-259F, no double lines).

Italic glyphs are de-slanted before any of this and re-slanted after.
"""
import io
import math
from collections import Counter
import sys
import unicodedata

import pathops
from fontTools import subset
from fontTools.misc.transform import Transform
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.filterPen import FilterPen
from fontTools.pens.pointPen import PointToSegmentPen, SegmentToPointPen
from fontTools.pens.reverseContourPen import ReverseContourPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.removeOverlaps import removeOverlaps

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from serif_helpers import flatten, intervals  # noqa: E402

# ---- design parameters (font units, UPM 2048) ----
CELL = 1229          # every advance width (0.6 em)
XH, CAP, DESC = 1104, 1490, -418
ASC_LINE, DESC_LINE = 1984, -494   # hhea line: box drawing spans this
BAR = 150            # head/foot slabs on narrow letters (Inter's horizontals ~152)
HEAD = 190           # head stroke of i j l, measured from the stem's left edge
FOOT_I, FOOT_R = 330, 260   # half-width of the i and r foot slabs
SB_MIN = 40          # narrowed glyphs never get sidebearings below this
SB_POWER = 1.5       # narrowed glyphs keep (cell/advance)**SB_POWER of their sidebearings
LIGHT = 0.88         # stem weight kept by glyphs with 3+ stems
LIGHT_WIDE = 0.80    # stem weight kept by the widest Cyrillic letters (WIDE)
WIDE = "ЖжШшЩщЫыЮюҖҗӜӝӸӹѬѭWMwẀẂẄŴẁẃẅŵМм%"
DEEP = 0.60          # ink squeezed below this share of its width gets lighter still…
MARK = 0.62          # size of the marks before Greek capitals that carry two (Ὢ ᾪ)
DEEP_MIN = 0.62      # …down to this stem weight (DZ, Rs, Ⅷ), with thinner bars to match
GAP_MIN = 80         # separate parts (Ⅷ, Ώ, ‱) never get closer than this
SHAPE_STROKE = 0.72  # stroke weight kept by symbols scaled in proportion (SHAPES)
SHAPES = [(0x2300, 0x23FF), (0x2A00, 0x2AFF), (0x2460, 0x24FF), (0x25A0, 0x25FF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
          (0x2776, 0x2793), (0x2295, 0x22A1)]   # circled/enclosed, geometric, dingbats,
                                                  # circled operators: keep their aspect
ARROWS = [(0x2190, 0x21FF), (0x27F0, 0x27FF), (0x2900, 0x297F), (0x2B00, 0x2B11)]
BENT = "↰↱↲↳↴↵⏎"     # cornered arrows shrink whole, so the family stays alike
HEAD_ZONE = 0.42     # share of the cell each end of a squeezed arrow keeps at full size
PAIR = 0.80          # stem weight of each letter in a two-letter glyph (ĳ Ĳ)
PAIR_GAP = 70        # ink gap between the two letters of a pair
PAIR_SB = 90         # sidebearing each side of a pair
BOX_LIGHT, BOX_HEAVY = 150, 300
BOX_DOUBLE = 150     # centre-to-centre half-distance of double box lines
UNIFORM = "@‰‱™℠"   # too intricate to squeeze sideways only
FULL_RULES = "_‾‗"   # rules that run edge to edge so repeats join
VERSION = "1.400"

# baked into the default glyphs: tabular figures, slashed zero, tailed l, serifed I
BAKE = ["tnum", "zero", "cv05", "cv08"]
KEEP_FEATURES = ["ccmp", "locl", "mark", "mkmk", "case", "frac", "numr", "dnom",
                 "sups", "subs", "sinf", "ordn", "salt", "aalt"] + \
    [f"ss0{i}" for i in range(1, 9)] + [f"cv{i:02d}" for i in range(1, 15)]

# narrow letters: name -> (builder, args)
L_LIKE = ["l.ss02"]
I_LIKE = ["dotlessi"]
J_LIKE = ["uni0237"]
R_LIKE = ["r"]
FT_LIKE = {"f": (1.45, 1.3), "f.1": (1.45, 1.3), "t": (1.5, 1.3), "t.1": (1.5, 1.3),
           "longs": (1.0, 1.3)}


# ---------------------------------------------------------------- helpers
class MapPen(FilterPen):
    """Passes every point through fn(x, y) -> (x, y)."""

    def __init__(self, out, fn):
        super().__init__(out)
        self.fn = fn

    def moveTo(self, p):
        self._outPen.moveTo(self.fn(*p))

    def lineTo(self, p):
        self._outPen.lineTo(self.fn(*p))

    def qCurveTo(self, *ps):
        self._outPen.qCurveTo(*[None if p is None else self.fn(*p) for p in ps])

    def curveTo(self, *ps):
        self._outPen.curveTo(*[self.fn(*p) for p in ps])


def area(path):
    pen = AreaPen()
    path.draw(pen)
    return pen.value


CW_SIGN = None  # AreaPen sign of a clockwise contour (set in main)
SMOOTH_DEG = 12   # on-curve points turning less than this are smooth, not corners


class ContourPointPen:
    """Point pen that keeps each contour as [(x, y, segmentType)]."""

    def __init__(self):
        self.contours = []

    def beginPath(self, identifier=None, **kw):
        self.contours.append([])

    def endPath(self):
        pass

    def addPoint(self, pt, segmentType=None, smooth=False, name=None, identifier=None, **kw):
        self.contours[-1].append((pt[0], pt[1], segmentType))

    def addComponent(self, *a, **kw):
        raise ValueError("components should be decomposed")


def _unit_normal(dx, dy, ink_right):
    """x and y of the outward normal of travel direction (dx, dy)."""
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return 0.0, 0.0
    # ink on the right of travel -> outside is on the left: (-dy, dx)
    return ((-dy / L, dx / L) if ink_right else (dy / L, -dx / L))


def compensate(path, ex, ey=0.0):
    """Thicken strokes by ex horizontally (ey vertically) without moving
    curve extremes sideways: every point moves along its outline normal,
    scaled by that normal's x (y) part. Vertical sides gain ex/2 each,
    the tops of curves gain nothing, so bowls stay round."""
    if abs(ex) < 0.5 and abs(ey) < 0.5:
        return path
    rec = ContourPointPen()
    path.draw(SegmentToPointPen(rec))
    ink_right = (area(path) * CW_SIGN) > 0   # clockwise outers: ink to the right
    out = pathops.Path()
    ppen = PointToSegmentPen(out.getPen())
    for c in rec.contours:
        n = len(c)
        ppen.beginPath()
        for i, (x, y, t) in enumerate(c):
            px, py, _ = c[i - 1]
            nx_, ny_, _ = c[(i + 1) % n]
            a_in = _unit_normal(x - px, y - py, ink_right)
            a_out = _unit_normal(nx_ - x, ny_ - y, ink_right)
            turn = math.degrees(math.acos(max(-1, min(1, a_in[0] * a_out[0] + a_in[1] * a_out[1]))))
            if t is None or turn < SMOOTH_DEG:
                nx, ny = _unit_normal(nx_ - px, ny_ - py, ink_right)
            else:   # corner: the side facing most sideways (up) decides
                nx = max(a_in[0], a_out[0], key=abs) if a_in[0] * a_out[0] >= 0 else (a_in[0] + a_out[0]) / 2
                ny = max(a_in[1], a_out[1], key=abs) if a_in[1] * a_out[1] >= 0 or ey < 0 \
                    else (a_in[1] + a_out[1]) / 2   # thinning: flat tops stay flat
            ppen.addPoint((x + ex / 2 * nx, y + ey / 2 * ny), t)
        ppen.endPath()
    out.simplify()
    if ey < 0:      # thinning can pinch off specks at sharp tips (the caron of ǅ)
        keep = pathops.Path()
        pen = keep.getPen()
        for c in out.contours:
            ap = AreaPen()
            c.draw(ap)
            if abs(ap.value) >= 600:
                c.draw(pen)
        out = keep
    return out


def rect(x0, y0, x1, y1):
    p = pathops.Path()
    pen = p.getPen()
    pen.moveTo((x0, y0))
    pen.lineTo((x0, y1))
    pen.lineTo((x1, y1))
    pen.lineTo((x1, y0))
    pen.closePath()
    return p


def union(*paths):
    out = pathops.Path()
    pen = out.getPen()
    for p in paths:
        if area(p) * CW_SIGN < 0:
            p.draw(ReverseContourPen(pen))
        else:
            p.draw(pen)
    out.simplify()
    return out


def moved(path, fn):
    out = pathops.Path()
    path.draw(MapPen(out.getPen(), fn))
    out.simplify()
    return out


def stem_spans(path, y):
    return intervals(flatten(path, Transform()), y)


# ---------------------------------------------------------------- the font
class Mono:
    def __init__(self, src):
        font = TTFont(src)
        self.slant = math.tan(math.radians(-font["post"].italicAngle))
        self.deslant = Transform(1, 0, -self.slant, 1, self.slant * XH / 2, 0)
        self.reslant = self.deslant.inverse()
        bake_defaults(font)
        opts = subset.Options()
        opts.layout_features = KEEP_FEATURES
        opts.glyph_names = True
        opts.notdef_outline = True
        opts.name_IDs = ["*"]
        opts.name_languages = ["*"]
        opts.hinting = False
        opts.drop_tables += ["STAT"]
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=font.getBestCmap().keys())
        sub.subset(font)
        buf = io.BytesIO()
        font.save(buf)
        self.font = TTFont(io.BytesIO(buf.getvalue()))
        self.orig = TTFont(io.BytesIO(buf.getvalue()))
        self.ogs = self.orig.getGlyphSet()
        self.oglyf = self.orig["glyf"]
        self.ohmtx = self.orig["hmtx"]
        gdef = self.orig["GDEF"].table.GlyphClassDef.classDefs
        # zero-width glyphs stay as they are; spacing marks (dotbelow...) get a cell
        self.marks = {n for n in self.orig.getGlyphOrder() if self.ohmtx[n][0] == 0}
        cmap = self.orig.getBestCmap()
        self.uniform = {cmap[ord(c)] for c in UNIFORM if ord(c) in cmap}
        self.wide = {cmap[ord(c)] for c in WIDE if ord(c) in cmap}
        self.shapes = {g for u, g in cmap.items() if any(a <= u <= b for a, b in SHAPES)} | \
            {g for g in self.orig.getGlyphOrder() if ".circled" in g or ".squared" in g} | \
            {cmap[ord(c)] for c in "‱©℗∞\U0001F12F" if ord(c) in cmap} | {"diamondwhite_x"}
        self.arrows = {g for u, g in cmap.items() if any(a <= u <= b for a, b in ARROWS)}
        self.cmap = cmap
        self.code = {g: u for u, g in cmap.items()}
        self.bent = {cmap[ord(c)] for c in BENT if ord(c) in cmap}
        # unencoded variants (arrowleft.case, leftLongArrow2) follow their base glyph
        order = self.orig.getGlyphOrder()
        for group in (self.arrows, self.bent, self.shapes, self.uniform, self.wide):
            group |= {g for g in order if g.split(".")[0] in group}
        self.arrows |= {g for g in order if "Arrow" in g}
        self.numerals = {g for u, g in cmap.items() if 0x2160 <= u <= 0x217F}
        self.maps = {}   # glyph -> (s, d): de-slanted x -> s*x + d
        self.box = {}    # glyph -> de-slanted bounds of the original

    # -- paths
    def dpath(self, name):
        p = pathops.Path()
        self.ogs[name].draw(TransformPen(p.getPen(glyphSet=self.ogs), self.deslant))
        p.simplify()
        return p

    def write(self, name, path):
        glyf = self.font["glyf"]
        pen = TTGlyphPen(None)
        tpen = TransformPen(pen, self.reslant)
        if area(path) * CW_SIGN < 0:
            tpen = ReverseContourPen(tpen)
        path.draw(Cu2QuPen(tpen, max_err=0.5, reverse_direction=False))
        g = pen.glyph()
        glyf[name] = g
        g.recalcBounds(glyf)
        self.font["hmtx"][name] = (CELL, getattr(g, "xMin", 0))

    def place(self, name, path, fallback_map=None):
        """Centre path's ink in the cell; returns the shift applied."""
        if path.bounds == (0, 0, 0, 0):
            self.write(name, path)
            return 0
        x0, _, x1, _ = path.bounds
        dx = (CELL - (x1 - x0)) / 2 - x0
        path = moved(path, lambda x, y: (x + dx, y))
        self.write(name, path)
        return dx

    # -- glyph kinds
    def auto(self, name):
        P = self.dpath(name)
        adv = self.ohmtx[name][0]
        if P.bounds == (0, 0, 0, 0):        # spaces
            self.write(name, P)
            self.maps[name] = (1, (CELL - adv) / 2)
            return
        x0, y0, x1, y1 = P.bounds
        ink, lsb, rsb = x1 - x0, x0, adv - x1
        r = CELL / adv

        def target(sb):
            if r >= 1:
                return sb
            if sb < SB_MIN:   # overhangs (the tail of Ц Щ) must stay inside the cell
                return max(sb, SB_MIN / 2)
            return max(SB_MIN, sb * r ** SB_POWER)

        sbl, sbr = target(lsb), target(rsb)
        avail = min(CELL - sbl - sbr, CELL - SB_MIN)   # never wider than the cell
        s, e = 1.0, 0.0
        if ink > avail:
            w, n = self.stems(P)
            light = LIGHT_WIDE if name in self.wide else LIGHT if n >= 3 else None
            light = self.deep(avail / ink, light)
            k = 1.0
            for _ in range(6):
                s = (avail - k * w) / (ink - k * w)
                if light:
                    k = min(1.0, max(0.0, (light - s) / (1 - s)))
            e = k * w * (1 - s)
            new_l = min(max(sbl, SB_MIN / 2), CELL - SB_MIN / 2 - (s * ink + e))
            if name in self.shapes:
                return self.proportional(name, P, avail, w)
            if name in self.arrows and name not in self.bent and ink > (y1 - y0) * 1.1 \
                    and self.squeeze(name, P, avail):
                return
            if name in self.arrows and s < 0.8:     # bent arrows (↵ ↳): keep the head's shape
                return self.proportional(name, P, avail, w)
            parts = self.clusters(P)
            marks = self.greek_marks(name, P) if s < 0.9 else None   # Ὃ: shrink the marks
            if marks:
                return self.spread(name, P, None, avail, w, light or 1.0, marks)
            if len(parts) > 1 and min(b[0] - a[1] for a, b in zip(parts, parts[1:])) * s - e < GAP_MIN:
                return self.spread(name, P, parts, avail, w, light or 1.0)
        else:
            if lsb >= 0 and rsb >= 0 and lsb + rsb > 80:   # tighter than that says nothing
                prop = lsb / (lsb + rsb)
            else:
                prop = 0.5
            new_l = (CELL - ink) * (0.5 * prop + 0.25)
        sy, ey = 1.0, 0.0
        if s < 1 and name in self.uniform:   # @ ‰ ™: shrink upright too, keep counters
            sy = s ** 0.5
            ey = e / (1 - s) * (1 - sy) * 0.85 if s < 1 else 0
        elif s < 1:
            sy, ey = self.thin_bars(s + e / w if e else 1.0, y1 - y0)
        ym = (y0 + y1) / 2
        Q = moved(P, lambda x, y: (s * (x - x0), ym + (y - ym) * sy))
        Q = compensate(Q, e, ey)
        dx = new_l - Q.bounds[0]
        Q = moved(Q, lambda x, y: (x + dx, y))
        self.write(name, Q)
        self.maps[name] = (s, -s * x0 + e / 2 + dx)

    def deep(self, r, light):
        """Stem weight for ink squeezed to r of its width: below DEEP the
        letters (digraphs, Ⅷ, Æ) get lighter so their counters stay open."""
        if r >= DEEP + LIGHT - LIGHT_WIDE:
            return light
        return min(light or 1.0, max(DEEP_MIN, LIGHT_WIDE - (DEEP - r)))

    def thin_bars(self, weight, height):
        """Lightened letters get thinner horizontals too (never heavier than
        their stems). Returns (y stretch, vertical compensation): the stretch
        puts the ink back on its original top and bottom."""
        if weight >= 0.78:
            return 1.0, 0.0
        ey = -(1 - min(1.0, weight + 0.18)) * BAR
        return (height - ey) / height, ey

    def proportional(self, name, P, avail, w):
        """Symbols whose shape is the point (● ■ ① ♥): scale both ways alike,
        about the ink centre, and keep strokes at SHAPE_STROKE weight."""
        x0, y0, x1, y1 = P.bounds
        ink = x1 - x0
        hollow = sum(1 for _ in P.contours) > 1
        if name in self.arrows or 0x2300 <= self.code.get(name, 0) <= 0x23FF:
            # line drawings (↵ ⌥): mostly empty boxes, unlike ■ ▲ ★
            hollow = hollow or abs(area(P)) < 0.36 * ink * (y1 - y0)
        s, e = avail / ink, 0.0
        cap = 1.3 if name in self.uniform else 1.5
        for _ in range(6):     # tiny results (‱) get lighter strokes, or they clog
            e = max(0.0, min(SHAPE_STROKE, cap * s) - s) * w if hollow else 0.0
            s = (avail - e) / ink
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        Q = moved(P, lambda x, y: (CELL / 2 + s * (x - xm), ym + s * (y - ym)))
        Q = compensate(Q, e, e)
        self.write(name, Q)
        self.maps[name] = (s, CELL / 2 - s * xm)

    def squeeze(self, name, P, avail):
        """Wide horizontal arrows (↔ ⇔ ↩): keep both ends at full size and
        shorten only the shaft between them."""
        x0, _, x1, _ = P.bounds
        ink = x1 - x0
        # the shaft: columns where the ink is no taller than the thinnest column
        flip = flatten(P, Transform(0, 1, 1, 0, 0, 0))
        xs = [x0 + i for i in range(5, int(ink) - 4, 5)]
        tall = [sum(b - a for a, b in intervals(flip, x)) for x in xs]
        # the shaft's height is the commonest column height (tips are thinner still)
        thin = Counter(round(t / 5) * 5 for t in tall if t > 0).most_common(1)[0][0]
        runs, run = [], []
        for x, t in zip(xs, tall):          # longest unbroken stretch at that height
            if thin * 0.75 <= t <= thin * 1.25:
                run.append(x)
            else:
                runs.append(run)
                run = []
        shaft = max(runs + [run], key=len)
        if not shaft or (shaft[-1] - shaft[0]) - (ink - avail) < 60:
            return False             # no room in the shaft: narrow as usual
        hl, hr = shaft[0] + 10, shaft[-1] - 10     # head (or hook) ends here
        k = (hr - hl - (ink - avail)) / (hr - hl)  # shaft scale
        new_l = (CELL - avail) / 2

        def fx(x):
            if x <= hl:
                return new_l + (x - x0)
            if x >= hr:
                return new_l + avail - (x1 - x)
            return new_l + (hl - x0) + (x - hl) * k
        Q = moved(P, lambda x, y: (fx(x), y))
        self.write(name, Q)
        self.maps[name] = (avail / ink, new_l - avail / ink * x0)
        return True

    def clusters(self, P):
        """x-ranges of the separate parts of a glyph (contours that overlap
        sideways belong to one part)."""
        spans = sorted(c.bounds[::2] for c in P.contours)
        out = []
        for a, b in spans:
            if out and a < out[-1][1] + 1:
                out[-1] = (out[-1][0], max(out[-1][1], b))
            else:
                out.append((a, b))
        return out

    def greek_marks(self, name, P):
        """Greek capitals with breathings (Ἂ Ὃ ᾫ): the marks left of the letter,
        as (groups of contours, x-ranges, which are marks); None without marks."""
        u = self.code.get(name, 0)
        if not (0x1F00 <= u <= 0x1FFF or 0x386 <= u <= 0x38F):
            return None
        cs = list(P.contours)
        top = P.bounds[3]
        big = max(cs, key=lambda c: (c.bounds[2] - c.bounds[0]) * (c.bounds[3] - c.bounds[1])).bounds
        left = (big[0] + big[2]) / 2 - 0.15 * (big[2] - big[0])
        is_mark = [c.bounds[1] > 0.6 * top and (c.bounds[0] + c.bounds[2]) / 2 < left for c in cs]
        marks = sorted((c for c, m in zip(cs, is_mark) if m), key=lambda c: c.bounds[0])
        rest = [c for c, m in zip(cs, is_mark) if not m]
        if not marks or not rest:
            return None
        groups, parts = [], []
        for c in marks:                       # marks that overlap sideways go together
            a, b = c.bounds[0], c.bounds[2]
            if parts and a < parts[-1][1] + 1:
                groups[-1].append(c)
                parts[-1] = (parts[-1][0], max(parts[-1][1], b))
            else:
                groups.append([c])
                parts.append((a, b))
        groups.append(rest)
        parts.append((min(c.bounds[0] for c in rest), max(c.bounds[2] for c in rest)))
        return groups, parts, [True] * (len(parts) - 1) + [False]

    def spread(self, name, P, parts, avail, w, light, marks=None):
        """Very wide glyphs made of separate parts (Ⅷ Ώ ‱ Ѹ): narrow each part
        on its own and set them GAP_MIN apart, so they never run together."""
        if marks:
            groups, parts, mark = marks
        else:
            mark = [False] * len(parts)
            groups = [[] for _ in parts]
            for c in P.contours:
                a = c.bounds[0]
                i = max(j for j, (lo, _) in enumerate(parts) if lo <= a + 1)
                groups[i].append(c)
        paths = []
        for g in groups:              # keep each contour's direction: counters stay open
            p = pathops.Path()
            pen = p.getPen()
            for c in g:
                c.draw(pen)
            p.simplify()
            paths.append(p)
        widths = [b - a for a, b in parts]
        gaps = [b[0] - a[1] for a, b in zip(parts, parts[1:])]
        fixed = sum(MARK * wd for wd, m in zip(widths, mark) if m)
        widths = [wd for wd, m in zip(widths, mark) if not m]
        nl = len(widths)
        light = self.deep((avail - fixed - sum(self.gap(g, 0, m) for g, m in zip(gaps, mark)))
                          / sum(widths),
                          min(light, LIGHT_WIDE))
        room = avail
        for _ in range(5):      # a mark that sits over the letter (Ά) makes the sum run wide
            s = e = 0.0
            for _ in range(8):
                G = sum(self.gap(g, s, m) for g, m in zip(gaps, mark))
                e = max(0.0, light - s) * w
                s = (room - G - fixed - nl * e) / sum(widths)
            sy, ey = 1.0, 0.0
            if name in self.uniform:      # ‰ ‱: smaller all round, as in auto()
                sy = s ** 0.5
                ey = max(0.0, light - sy) * w * 0.85
            else:
                sy, ey = self.thin_bars(max(light, s), P.bounds[3] - P.bounds[1])
            ym = (P.bounds[1] + P.bounds[3]) / 2
            x, out = None, []
            for p, (a, _), g, m in zip(paths, parts, gaps + [0], mark):
                if m:      # a mark: smaller all round, hung from its own top
                    t = p.bounds[3]
                    Q = moved(p, lambda X, Y, a=a, t=t: (MARK * (X - a), t + MARK * (Y - t)))
                else:
                    Q = compensate(moved(p, lambda X, Y, a=a: (s * (X - a), ym + sy * (Y - ym))), e, ey)
                x = SB_MIN if x is None else x
                Q = moved(Q, lambda X, Y, d=x - Q.bounds[0]: (X + d, Y))
                out.append(Q)
                x = Q.bounds[2] + self.gap(g, s, m)
            Q = union(*out)
            if Q.bounds[2] - Q.bounds[0] <= avail + 1:
                break
            room -= Q.bounds[2] - Q.bounds[0] - avail
        sh = (CELL - Q.bounds[2] - Q.bounds[0]) / 2
        Q = moved(Q, lambda X, Y: (X + sh, Y))
        self.write(name, Q)
        x0 = P.bounds[0]
        self.maps[name] = (s, Q.bounds[0] - s * x0)

    @staticmethod
    def gap(g, s, mark):
        """Space after a part: marks before a Greek capital sit closer."""
        if mark:        # a mark that reaches over the letter (Ἂ) keeps doing so
            return max(45, g * MARK) if g >= 0 else g * s
        return max(GAP_MIN, g * s)

    def stems(self, P):
        """Horizontal width of the thinnest stroke and the most strokes on
        one line, sampled across the glyph's height."""
        _, y0, _, y1 = P.bounds
        flat = flatten(P, Transform())
        best = []
        for f in (0.22, 0.3, 0.4, 0.5, 0.6, 0.7, 0.78):
            spans = [b - a for a, b in intervals(flat, y0 + f * (y1 - y0)) if b - a > 40]
            if len(spans) > len(best):
                best = spans
        w = min(best) if best else 180
        return min(260, max(120, w)), len(best)

    def special(self, name):
        P = self.dpath(name)
        mid = XH * 0.45
        a, b = stem_spans(P, mid)[0]
        if name in FT_LIKE:
            fl, fr = FT_LIKE[name]
            a, b = stem_spans(P, XH * 0.7)[0]
            P = moved(P, lambda x, y: (a - (a - x) * fl if x < a else
                                       b + (x - b) * fr if x > b else x, y))
        elif name in L_LIKE:
            top = P.bounds[3]
            P = moved(P, lambda x, y: (b + (x - b) * 1.6 if x > b else x, y))
            P = union(P, rect(a - HEAD, top - BAR, a + 1, top))
        elif name in I_LIKE:
            c = (a + b) / 2
            P = union(P, rect(a - HEAD, XH - BAR, a + 1, XH),
                      rect(c - FOOT_I, 0, c + FOOT_I, BAR))
        elif name in J_LIKE:
            P = moved(P, lambda x, y: (a - (a - x) * 1.6 if x < a else x, y))
            P = union(P, rect(a - HEAD, XH - BAR, a + 1, XH))
        elif name in R_LIKE:
            c = (a + b) / 2
            P = moved(P, lambda x, y: (b + (x - b) * 1.3 if x > b else x, y))
            P = union(P, rect(c - FOOT_R, 0, c + FOOT_R, BAR))
        dx = self.place(name, P)
        self.maps[name] = (1, dx)   # marks follow the stem, which only moved

    def composite(self, name):
        """Keep components; move each with the base glyph's map."""
        g = self.oglyf[name]
        comps = g.components
        base = comps[0]
        ok = not any(hasattr(c, "transform") for c in comps) and (base.x, base.y) == (0, 0) \
            and base.glyphName not in self.marks
        special = self.is_special(base.glyphName)
        for c in comps[1:]:
            if c.glyphName in self.marks:
                continue
            bx = self.ink(c.glyphName)
            if bx is None or bx[2] - bx[0] > 300 or bx[3] - bx[1] > 700 or not special:
                ok = False      # a second letter (ij, …) or a part (tail of Ц): one drawing
        if not ok:
            self.auto(name)
            return
        self.ensure(base.glyphName)
        for c in comps[1:]:
            self.ensure(c.glyphName)
        s, d = self.maps[base.glyphName]
        new = self.font["glyf"][name]
        for c, nc in zip(comps, new.components):
            if c is base:
                continue
            bx = self.ink(c.glyphName)
            if bx is None:
                continue
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2   # italic coords
            X = cx + c.x - self.slant * (cy + c.y - XH / 2)
            Xc = cx - self.slant * (cy - XH / 2)
            cs, cd = self.maps.get(c.glyphName, (1, 0))
            nc.x = round(c.x + (s * X + d - X) - (cs * Xc + cd - Xc))
        lo, hi = self.extent(name)
        if lo < -2 or hi > CELL + 2:          # an accent pokes out of the cell
            if not special:
                self.auto(name)               # narrow letter and accent together
                return
            for c, nc in zip(comps[1:], new.components[1:]):
                nc.x += round(max(0, -lo) - max(0, hi - CELL))
        new.recalcBounds(self.font["glyf"])
        self.font["hmtx"][name] = (CELL, getattr(new, "xMin", 0))
        self.maps[name] = (s, d)

    def extent(self, name):
        """De-slanted left and right ink edges of a glyph in the new font."""
        gs = self.font.getGlyphSet()
        p = pathops.Path()
        gs[name].draw(TransformPen(p.getPen(glyphSet=gs), self.deslant))
        x0, _, x1, _ = p.bounds
        return x0, x1

    def is_special(self, name):
        return name in L_LIKE + I_LIKE + J_LIKE + R_LIKE or name in FT_LIKE

    def ink(self, name):
        g = self.oglyf[name]
        g.recalcBounds(self.oglyf)
        if not hasattr(g, "xMin"):
            return None
        return (g.xMin, g.yMin, g.xMax, g.yMax)

    def alias(self, name):
        """Roman numerals that are one plain letter (ⅰ ⅽ ⅾ): exactly that letter."""
        g = self.oglyf[name]
        if not (name in self.numerals and g.isComposite() and len(g.components) == 1):
            return False
        c = g.components[0]
        if hasattr(c, "transform") or c.y or c.glyphName in self.marks:
            return False
        self.ensure(c.glyphName)
        new = self.font["glyf"][name]
        new.components[0].x = 0
        new.recalcBounds(self.font["glyf"])
        self.font["hmtx"][name] = (CELL, getattr(new, "xMin", 0))
        self.maps[name] = self.maps[c.glyphName]
        return True

    def slong_dot(self, name):
        """ẛ: the new ſ with the dot back over its stem."""
        self.ensure("longs")
        N, O = self.npath("longs"), self.dpath(name)
        dot = max(O.contours, key=lambda c: c.bounds[1])
        d = pathops.Path()
        dot.draw(d.getPen())
        y = XH * 0.45
        dx = stem_spans(N, y)[0][0] - stem_spans(O, y)[0][0]
        self.write(name, union(N, moved(d, lambda X, Y: (X + dx, Y))))
        self.maps[name] = (1, dx)

    def ensure(self, name):
        if name in self.maps or name in self.marks:
            return
        if name.startswith("slongdotaccent") and "longs" in self.oglyf.keys():
            return self.slong_dot(name)
        if self.alias(name):
            return
        if self.is_special(name):
            self.special(name)
        elif self.oglyf[name].isComposite():
            self.composite(name)
        else:
            self.auto(name)

    # -- run
    def build(self):
        for name in self.orig.getGlyphOrder():
            try:
                self.ensure(name)
            except Exception as ex:  # keep going; report at the end
                print("FAILED", name, repr(ex))
                raise
        self.rules()
        self.pairs()
        self.anchors()
        add_box_drawing(self.font, self)
        finish(self.font)
        return self.font

    def npath(self, name):
        """De-slanted outline of a glyph as already rebuilt in the new font."""
        gs = self.font.getGlyphSet()
        p = pathops.Path()
        gs[name].draw(TransformPen(p.getPen(glyphSet=gs), self.deslant))
        p.simplify()
        return p

    def rules(self):
        """_ ‾ ‗ span the whole cell (upright even in italic) so runs join."""
        for c in FULL_RULES:
            name = self.cmap.get(ord(c))
            if not name or name not in self.maps:
                continue
            _, y0, _, y1 = self.npath(name).bounds
            p = pathops.Path()
            pen = TransformPen(p.getPen(), self.deslant)
            if c == "‗":                       # double low line: two rules
                h = (y1 - y0) * 0.38
                for a, b in ((y0, y0 + h), (y1 - h, y1)):
                    pen.moveTo((0, a)); pen.lineTo((0, b)); pen.lineTo((CELL, b))
                    pen.lineTo((CELL, a)); pen.closePath()
            else:
                pen.moveTo((0, y0)); pen.lineTo((0, y1)); pen.lineTo((CELL, y1))
                pen.lineTo((CELL, y0)); pen.closePath()
            self.write(name, p)
            self.font["hmtx"][name] = (CELL, 0)

    def pairs(self):
        """ĳ and Ĳ from the new slab i, j and serifed I, each kept at PAIR weight."""
        for lig, (a, b) in (("ĳ", ("i", "j")), ("Ĳ", ("I", "J"))):
            name = self.cmap.get(ord(lig))
            if not name or ord(a) not in self.cmap or ord(b) not in self.cmap:
                continue
            A, B = (self.npath(self.cmap[ord(ch)]) for ch in (a, b))
            w = 178
            la, lb = A.bounds[2] - A.bounds[0], B.bounds[2] - B.bounds[0]
            avail = CELL - 2 * PAIR_SB - PAIR_GAP
            e = 0.0
            for _ in range(6):
                s = (avail - 2 * e) / (la + lb)
                e = max(0.0, (PAIR - s) * w)
            parts, x = [], PAIR_SB
            for P in (A, B):
                x0 = P.bounds[0]
                Q = compensate(moved(P, lambda X, Y, x0=x0: (s * (X - x0), Y)), e)
                dx = x - Q.bounds[0]
                Q = moved(Q, lambda X, Y, dx=dx: (X + dx, Y))
                parts.append(Q)
                x = Q.bounds[2] + PAIR_GAP
            P = union(*parts)
            P = moved(P, lambda X, Y, sh=(CELL - P.bounds[2] - P.bounds[0]) / 2: (X + sh, Y))
            self.write(name, P)

    def anchors(self):
        gpos = self.font["GPOS"].table
        for lookup in gpos.LookupList.Lookup:
            for st in lookup.SubTable:
                if lookup.LookupType == 9:
                    st = st.ExtSubTable
                if st.LookupType in (4, 5, 6):
                    for g, rec in zip(st.MarkCoverage.glyphs if st.LookupType != 6 else
                                      st.Mark1Coverage.glyphs,
                                      (st.MarkArray if st.LookupType != 6 else
                                       st.Mark1Array).MarkRecord):
                        self.move_anchor(g, rec.MarkAnchor)
                if st.LookupType == 6:
                    for g, rec in zip(st.Mark2Coverage.glyphs, st.Mark2Array.Mark2Record):
                        for a in rec.Mark2Anchor:
                            self.move_anchor(g, a)
                if st.LookupType == 4:
                    for g, rec in zip(st.BaseCoverage.glyphs, st.BaseArray.BaseRecord):
                        for a in rec.BaseAnchor:
                            self.move_anchor(g, a)
                elif st.LookupType == 5:
                    for g, lig in zip(st.LigatureCoverage.glyphs,
                                      st.LigatureArray.LigatureAttach):
                        for comp in lig.ComponentRecord:
                            for a in comp.LigatureAnchor:
                                self.move_anchor(g, a)

    def move_anchor(self, g, a):
        if a is None or g not in self.maps:
            return
        s, d = self.maps[g]
        X = a.XCoordinate - self.slant * (a.YCoordinate - XH / 2)
        a.XCoordinate = round(a.XCoordinate + s * X + d - X)


def bake_defaults(font):
    gsub = font["GSUB"].table
    for tag in BAKE:
        mapping = {}
        for fr in gsub.FeatureList.FeatureRecord:
            if fr.FeatureTag != tag:
                continue
            for i in fr.Feature.LookupListIndex:
                lk = gsub.LookupList.Lookup[i]
                for st in lk.SubTable:
                    st = getattr(st, "ExtSubTable", st)
                    mapping.update(getattr(st, "mapping", {}))
        for t in font["cmap"].tables:
            if t.isUnicode():
                t.cmap = {u: mapping.get(g, g) for u, g in t.cmap.items()}


# ------------------------------------------------------------ box drawing
def box_arms(words):
    """Parse a BOX DRAWINGS name into {direction: weight}."""
    dirs = {"UP": ["U"], "DOWN": ["D"], "LEFT": ["L"], "RIGHT": ["R"],
            "VERTICAL": ["U", "D"], "HORIZONTAL": ["L", "R"]}
    arms, pending, prefix = {}, [], None
    weights = ("LIGHT", "HEAVY", "SINGLE", "DOUBLE")
    prefix_mode = words[0] in weights
    for w in words:
        if w in weights:
            if prefix_mode:
                prefix = w
            else:
                for d in pending:
                    arms[d] = w
                pending = []
        elif w in dirs:
            if prefix_mode:
                for d in dirs[w]:
                    arms[d] = prefix
            else:
                pending += dirs[w]
    return arms


def add_box_drawing(font, mono):
    cx, cy = CELL / 2, (ASC_LINE + DESC_LINE) / 2
    T, B = ASC_LINE, DESC_LINE
    th = {"LIGHT": BOX_LIGHT, "HEAVY": BOX_HEAVY}
    new = {}
    for u in range(0x2500, 0x25A0):
        name = unicodedata.name(chr(u), "")
        words = name.split()
        parts = []
        if name.startswith("BOX DRAWINGS"):
            w = words[2:]
            if ("DOUBLE" in w or "SINGLE" in w) and "DASH" not in w:
                parts = double_box(box_arms(w), cx, cy, T, B)
            elif "DASH" in w:
                n = {"DOUBLE": 2, "TRIPLE": 3, "QUADRUPLE": 4}[w[1]]
                t = th[w[0]] / 2
                if w[-1] == "HORIZONTAL":
                    step = CELL / n
                    parts = [rect(i * step + step * 0.2, cy - t, (i + 1) * step - step * 0.2, cy + t)
                             for i in range(n)]
                else:
                    step = (T - B) / n
                    parts = [rect(cx - t, B + i * step + step * 0.2, cx + t, B + (i + 1) * step - step * 0.2)
                             for i in range(n)]
            elif "ARC" in w:
                parts = [arc(w[2], w[4], cx, cy, T, B)]     # LIGHT ARC DOWN AND RIGHT
            elif "DIAGONAL" in w:
                t = BOX_LIGHT / 2
                if w[2:4] == ["UPPER", "RIGHT"] or "CROSS" in w:
                    parts.append(line_poly((0, B), (CELL, T), t))
                if w[2:4] == ["UPPER", "LEFT"] or "CROSS" in w:
                    parts.append(line_poly((0, T), (CELL, B), t))
            else:
                arms = box_arms(w)
                hv = max([th[arms[d]] for d in "LR" if d in arms] or [0])
                vv = max([th[arms[d]] for d in "UD" if d in arms] or [0])
                for d, wt in arms.items():
                    t = th[wt] / 2
                    if d == "U":
                        parts.append(rect(cx - t, cy - hv / 2, cx + t, T))
                    if d == "D":
                        parts.append(rect(cx - t, B, cx + t, cy + hv / 2))
                    if d == "L":
                        parts.append(rect(0, cy - t, cx + vv / 2, cy + t))
                    if d == "R":
                        parts.append(rect(cx - vv / 2, cy - t, CELL, cy + t))
        elif "BLOCK" in words or "QUADRANT" in words:
            parts = block(words, T, B)
        elif "SHADE" in words:
            parts = shade(words[0], T, B)
        if not parts:
            continue
        new[u] = pathops.op(union(*parts), rect(0, B, CELL, T), pathops.PathOp.INTERSECTION)
    glyf, hmtx = font["glyf"], font["hmtx"]
    order = font.getGlyphOrder()
    gdef = font["GDEF"].table.GlyphClassDef.classDefs
    for u, path in new.items():
        gname = f"uni{u:04X}"
        if gname in glyf.glyphs:
            continue
        order.append(gname)
        pen = TTGlyphPen(None)
        tpen = pen if area(path) * CW_SIGN > 0 else ReverseContourPen(pen)
        path.draw(Cu2QuPen(tpen, max_err=0.5, reverse_direction=False))
        glyf[gname] = pen.glyph()
        glyf[gname].recalcBounds(glyf)
        hmtx[gname] = (CELL, glyf[gname].xMin)
        gdef[gname] = 1
        for t in font["cmap"].tables:
            if t.isUnicode() and t.format in (4, 12) and (u <= 0xFFFF or t.format == 12):
                t.cmap[u] = gname
    font.setGlyphOrder(order)
    print("box drawing:", len(new), "glyphs")


def double_box(arms, cx, cy, T, B):
    """U+2550–256C. Each double arm is a thick bar with its middle channel cut
    out; channels run past the centre so junctions open up the right way.
    Single arms are added last and reach the far line of a crossing double."""
    t, D = BOX_LIGHT, BOX_DOUBLE
    vert = {d: arms[d] for d in "UD" if d in arms}
    horz = {d: arms[d] for d in "LR" if d in arms}

    def reach(perp):
        """How far past the centre an arm runs, given the crossing arms."""
        if "DOUBLE" in perp.values():
            return D + t / 2
        return t / 2 if perp else 0

    def bar(d, half, ext):
        if d == "U": return rect(cx - half, cy - ext, cx + half, T)
        if d == "D": return rect(cx - half, B, cx + half, cy + ext)
        if d == "L": return rect(0, cy - half, cx + ext, cy + half)
        return rect(cx - ext, cy - half, CELL, cy + half)

    thick, chan, single = [], [], []
    for d, wt in arms.items():
        ext = reach(horz if d in "UD" else vert)
        if wt == "DOUBLE":
            thick.append(bar(d, D + t / 2, ext))
            # without crossing arms the two lines just run on
            chan.append(bar(d, D - t / 2, ext - t if ext else D))
        else:
            single.append(bar(d, t / 2, ext))
    out = pathops.Path()
    if thick:
        out = union(*thick)
        if chan:
            out = pathops.op(out, union(*chan), pathops.PathOp.DIFFERENCE)
    return [out] + single if thick else single


def line_poly(p0, p1, t):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy)
    nx, ny = -dy / L * t, dx / L * t
    p = pathops.Path()
    pen = p.getPen()
    pen.moveTo((p0[0] + nx, p0[1] + ny))
    pen.lineTo((p1[0] + nx, p1[1] + ny))
    pen.lineTo((p1[0] - nx, p1[1] - ny))
    pen.lineTo((p0[0] - nx, p0[1] - ny))
    pen.closePath()
    return p


def arc(v, h, cx, cy, T, B):
    """Rounded corner: v is DOWN/UP (the vertical arm), h is LEFT/RIGHT."""
    R, t = CELL / 2 * 0.98, BOX_LIGHT / 2
    sy = -1 if v == "DOWN" else 1
    sx = 1 if h == "RIGHT" else -1
    ox, oy = cx + sx * R, cy + sy * R          # arc centre
    pts = []
    n = 24
    for i in range(n + 1):
        a = math.pi / 2 * i / n
        pts.append((ox - sx * (R + t) * math.cos(a), oy - sy * (R + t) * math.sin(a)))
    for i in range(n, -1, -1):
        a = math.pi / 2 * i / n
        pts.append((ox - sx * (R - t) * math.cos(a), oy - sy * (R - t) * math.sin(a)))
    p = pathops.Path()
    pen = p.getPen()
    pen.moveTo(pts[0])
    for q in pts[1:]:
        pen.lineTo(q)
    pen.closePath()
    end_v = T if v == "UP" else B
    parts = [p, rect(cx - t, min(oy, end_v), cx + t, max(oy, end_v)),
             rect(min(ox, CELL if h == "RIGHT" else 0), cy - t, max(ox, CELL if h == "RIGHT" else 0), cy + t)]
    return union(*parts)


def block(words, T, B):
    H = T - B
    frac = {"ONE": 1, "TWO": 2, "THREE": 3, "FIVE": 5, "SIX": 6, "SEVEN": 7, "HALF": 4,
            "QUARTER": 2, "THREE QUARTERS": 6}
    if "QUADRANT" in words:
        q = " ".join(words[words.index("QUADRANT") + 1:]).split(" AND ")
        out = []
        for part in q:
            yy = (B + H / 2, T) if part.startswith("UPPER") else (B, B + H / 2)
            xx = (0, CELL / 2) if part.endswith("LEFT") else (CELL / 2, CELL)
            out.append(rect(xx[0], yy[0], xx[1], yy[1]))
        return out
    if words[0] == "FULL":
        return [rect(0, B, CELL, T)]
    side = words[0]
    if words[1] == "HALF":
        n = 4
    else:
        n = frac.get(words[1])
        if n is None:
            return []
    if side == "UPPER":
        return [rect(0, T - H * n / 8, CELL, T)]
    if side == "LOWER":
        return [rect(0, B, CELL, B + H * n / 8)]
    if side == "LEFT":
        return [rect(0, B, CELL * n / 8, T)]
    if side == "RIGHT":
        return [rect(CELL - CELL * n / 8, B, CELL, T)]
    return []


def shade(kind, T, B):
    cols, rows = 8, 16
    w, h = CELL / cols, (T - B) / rows
    out = []
    for i in range(cols):
        for j in range(rows):
            if kind == "MEDIUM":
                on = (i + j) % 2 == 0
            else:
                on = (i % 2 == 0 and j % 2 == 0)
                if kind == "DARK":
                    on = not on
            if on:
                out.append(rect(i * w, B + j * h, (i + 1) * w, B + (j + 1) * h))
    return out


# ------------------------------------------------------------- the tables
def finish(font):
    hmtx = font["hmtx"]
    for name in font.getGlyphOrder():
        if hmtx[name][0] not in (0, CELL):
            raise ValueError(f"{name} has width {hmtx[name][0]}")
    # untouched zero-width marks still carry Inter's overlapping contours
    glyf = font["glyf"]
    removeOverlaps(font, [n for n in font.getGlyphOrder()
                          if hmtx[n][0] == 0 and not glyf[n].isComposite()])
    font["post"].isFixedPitch = 1
    font["head"].fontRevision = float(VERSION)
    os2 = font["OS/2"]
    os2.panose.bFamilyType = 2
    os2.panose.bProportion = 9
    os2.recalcAvgCharWidth(font)
    os2.recalcUnicodeRanges(font)
    style = font["name"].getDebugName(17) or font["name"].getDebugName(2)
    rename(font, style)


COPYRIGHT = ("Copyright 2016 The Inter Project Authors (https://github.com/rsms/inter). "
             "Monospaced adaptation copyright 2026 The Inter Mono Project Authors.")
DESIGNER = "Claude (an AI model made by Anthropic), after Inter by Rasmus Andersson"
ABOUT = ("Inter Mono is an unofficial monospaced adaptation of Inter, the typeface by "
         "Rasmus Andersson (https://rsms.me/inter). It is not affiliated with or endorsed "
         "by the Inter project.")


def rename(font, style):
    name = font["name"]
    family = "Inter Mono"
    ps = "InterMono-" + style.replace(" ", "")
    for nid in (16, 17, 21, 22, 25):
        name.removeNames(nameID=nid)
    name.removeNames(platformID=1)
    for nid, val in ((1, family), (2, style), (3, f"{VERSION};{ps}"), (4, f"{family} {style}"),
                     (5, f"Version {VERSION}; monospaced, derived from Inter 4.001"), (6, ps),
                     (0, COPYRIGHT), (8, "The Inter Mono Project"), (9, DESIGNER), (10, ABOUT)):
        name.setName(val, nid, 3, 1, 0x409)
    for nid in (11, 12):          # Inter's own addresses: this is not their release
        name.removeNames(nameID=nid)
    font["OS/2"].achVendID = "NONE"


def main(src, dst):
    global CW_SIGN
    sq = rect(0, 0, 10, 10)   # drawn up the left side first: clockwise
    CW_SIGN = math.copysign(1, area(sq))
    font = Mono(src).build()
    font.save(dst)
    print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
