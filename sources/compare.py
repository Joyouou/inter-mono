"""Glyph-by-glyph comparison of Inter Mono with the Inter it was made from.

Usage: python3 compare.py <inter-src.ttf> <mono.ttf> <outdir>

Writes <outdir>/report.tsv (one row per character, with flags) and
<outdir>/sheet-<block>.png: each tile shows Inter (grey, at its own width)
and Inter Mono (black, in its cell). Italic is measured de-slanted.
"""
import math
import os
import sys
import unicodedata

import pathops
from fontTools.misc.transform import Transform
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_mono as mm  # noqa: E402
from serif_helpers import flatten, intervals  # noqa: E402

CELL = mm.CELL
BLOCKS = [
    ("ascii", 0x20, 0x7E), ("latin1", 0xA0, 0xFF), ("extA", 0x100, 0x17F),
    ("extB", 0x180, 0x24F), ("greek", 0x370, 0x3FF), ("cyrillic", 0x400, 0x52F),
    ("extAdd", 0x1E00, 0x1EFF), ("punct", 0x2000, 0x206F), ("supsub", 0x2070, 0x209F),
    ("currency", 0x20A0, 0x20CF), ("letterlike", 0x2100, 0x218F), ("arrows", 0x2190, 0x21FF),
    ("math", 0x2200, 0x22FF), ("tech", 0x2300, 0x23FF), ("shapes", 0x25A0, 0x25FF),
    ("misc", 0x2600, 0x2BFF),
]


def path_of(gs, name, tf):
    p = pathops.Path()
    gs[name].draw(TransformPen(p.getPen(glyphSet=gs), tf))
    p.simplify()
    return p


def area(p):
    a = AreaPen()
    p.draw(a)
    return abs(a.value)


def spans(flat, y):
    return [(a, b) for a, b in intervals(flat, y) if b - a > 4]


def profile(p):
    """Thinnest vertical-stroke width, thinnest horizontal-stroke height and
    narrowest counter, sampled across the ink."""
    if p.bounds == (0, 0, 0, 0):
        return None
    x0, y0, x1, y1 = p.bounds
    flat = flatten(p, Transform())
    flip = flatten(p, Transform(0, 1, 1, 0, 0, 0))   # swap x and y
    sv, sh, gaps = [], [], []
    for f in (0.25, 0.35, 0.5, 0.65, 0.75):
        sp = spans(flat, y0 + f * (y1 - y0))
        sv += [b - a for a, b in sp]
        gaps += [b[0] - a[1] for a, b in zip(sp, sp[1:])]
        sh += [b - a for a, b in spans(flip, x0 + f * (x1 - x0))]
    return (min(sv) if sv else 0, min(sh) if sh else 0, min(gaps) if gaps else None)


def contours(p):
    return sum(1 for _ in p.contours)


def main(src, mono_path, outdir):
    os.makedirs(outdir, exist_ok=True)
    m = mm.Mono(src)                      # Inter, subset and with mono defaults baked
    sq = mm.rect(0, 0, 10, 10)
    mm.CW_SIGN = math.copysign(1, mm.area(sq))
    mono = TTFont(mono_path)
    mgs, mcmap = mono.getGlyphSet(), mono.getBestCmap()
    ocmap, ogs = m.orig.getBestCmap(), m.ogs
    tf = m.deslant
    rows = []
    for u in sorted(set(ocmap) & set(mcmap)):
        if 0x2500 <= u <= 0x259F or unicodedata.category(chr(u)) in ("Mn", "Me", "Cc", "Cf"):
            continue
        on, mn = ocmap[u], mcmap[u]
        if m.ohmtx[on][0] == 0:
            continue
        O, M = path_of(ogs, on, tf), path_of(mgs, mn, tf)
        if O.bounds == (0, 0, 0, 0):
            continue
        adv = m.ohmtx[on][0]
        ox0, oy0, ox1, oy1 = O.bounds
        mx0, my0, mx1, my1 = M.bounds
        po, pm = profile(O), profile(M)
        r = {
            "u": u, "ch": chr(u), "name": mn, "adv": adv,
            "wr": (mx1 - mx0) / max(1, ox1 - ox0),
            "dy": max(abs(my0 - oy0), abs(my1 - oy1)),
            "co": contours(O), "cm": contours(M),
            "dens_o": area(O) / (adv * max(1, oy1 - oy0)),
            "dens_m": area(M) / (CELL * max(1, my1 - my0)),
            "vo": po[0], "vm": pm[0], "ho": po[1], "hm": pm[1],
            "go": po[2], "gm": pm[2],
            "lsb": mx0, "rsb": CELL - mx1,
            "olsb": ox0, "orsb": adv - ox1,
        }
        flags = []
        if r["co"] != r["cm"]:
            flags.append(f"contours {r['co']}->{r['cm']}")
        if r["dy"] > 6 and chr(u) not in mm.UNIFORM + mm.FULL_RULES:
            flags.append(f"height moved {r['dy']:.0f}")
        if r["vo"] > 40 and r["vm"] / r["vo"] < 0.78:
            flags.append(f"stem thin {r['vm']:.0f}/{r['vo']:.0f}")
        if r["vo"] > 40 and r["vm"] / r["vo"] > 1.15:
            flags.append(f"stem heavy {r['vm']:.0f}/{r['vo']:.0f}")
        if r["ho"] > 40 and abs(r["hm"] / r["ho"] - 1) > 0.15:
            flags.append(f"bar {r['hm']:.0f}/{r['ho']:.0f}")
        if r["gm"] is not None and r["gm"] < 110 and (r["go"] or 999) >= 110:
            flags.append(f"counter shut {r['gm']:.0f}/{r['go']:.0f}")
        if r["dens_o"] > 0 and r["dens_m"] / r["dens_o"] > 1.35:
            flags.append(f"dark x{r['dens_m'] / r['dens_o']:.2f}")
        if mx0 < -2 or mx1 > CELL + 2:
            flags.append("outside cell")
        osum, msum = r["olsb"] + r["orsb"], r["lsb"] + r["rsb"]
        if osum > 0 and msum > 0 and r["olsb"] >= 0 and r["orsb"] >= 0:
            off = abs(r["lsb"] / msum - r["olsb"] / osum)
            if off > 0.2 and abs(r["lsb"] - r["rsb"]) > 120:
                flags.append(f"off-centre {r['lsb']:.0f}|{r['rsb']:.0f}")
        r["flags"] = "; ".join(flags)
        rows.append(r)
    with open(os.path.join(outdir, "report.tsv"), "w") as f:
        keys = ["u", "ch", "name", "adv", "wr", "dens_o", "dens_m", "vo", "vm", "ho", "hm",
                "go", "gm", "co", "cm", "lsb", "rsb", "flags"]
        f.write("\t".join(keys) + "\n")
        for r in rows:
            f.write("\t".join(f"{r[k]:.2f}" if isinstance(r[k], float) else
                              ("U+%04X" % r[k] if k == "u" else str(r[k])) for k in keys) + "\n")
    print(len(rows), "glyphs,", sum(1 for r in rows if r["flags"]), "flagged")
    sheets(rows, m, mgs, ocmap, mcmap, tf, outdir)


# ------------------------------------------------------------------ drawing
SC = 0.034          # px per unit
TOP, BOT = 2050, -560


def fill(d, p, ox, oy, colour):
    """Scanline-fill a simplified path (even-odd is exact after simplify)."""
    if p.bounds == (0, 0, 0, 0):
        return
    flat = flatten(p, Transform())
    _, y0, _, y1 = p.bounds
    py0, py1 = int((TOP - y1) * SC), int((TOP - y0) * SC) + 1
    for py in range(py0, py1 + 1):
        y = TOP - (py + 0.5) / SC
        for a, b in intervals(flat, y):
            d.line([(ox + a * SC, oy + py), (ox + b * SC, oy + py)], fill=colour)


def sheets(rows, m, mgs, ocmap, mcmap, tf, outdir):
    lab = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
    th = int((TOP - BOT) * SC)
    tw = int((2400 + CELL) * SC) + 30
    per = 9
    by = {r["u"]: r for r in rows}
    for bname, lo, hi in BLOCKS:
        us = [u for u in sorted(by) if lo <= u <= hi]
        if not us:
            continue
        nrow = (len(us) + per - 1) // per
        img = Image.new("RGB", (per * tw + 20, nrow * (th + 22) + 20), "white")
        d = ImageDraw.Draw(img)
        for i, u in enumerate(us):
            r = by[u]
            x, y = 10 + (i % per) * tw, 10 + (i // per) * (th + 22)
            d.text((x, y), f"{u:04X} {r['name'][:14]}", font=lab,
                   fill=(200, 0, 0) if r["flags"] else (110, 110, 110))
            y += 14
            O = path_of(m.ogs, ocmap[u], tf)
            ow = int(r["adv"] * SC)
            d.rectangle([x, y, x + ow, y + th], outline=(225, 225, 225))
            fill(d, O, x, y, (150, 150, 150))
            mx = x + int(2400 * SC) - int(CELL * SC) // 2 + 10
            mx = max(mx, x + ow + 6)
            d.rectangle([mx, y, mx + int(CELL * SC), y + th], outline=(200, 200, 255))
            fill(d, path_of(mgs, mcmap[u], tf), mx, y, (0, 0, 0))
        img.save(os.path.join(outdir, f"sheet-{bname}.png"))


if __name__ == "__main__":
    main(*sys.argv[1:])
