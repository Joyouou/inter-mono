"""Proof sheet for Inter Mono.

Usage: python3 proof.py <out.png> <regular.ttf> <italic.ttf> [inter-regular.ttf]

Draws code, confusable characters, the alphabet and box drawing at several
sizes, with a column grid behind the large sample so misaligned cells show.
"""
import sys

from PIL import Image, ImageDraw, ImageFont

CODE = """def parse(line: str) -> dict[str, int]:
    \"\"\"Split 'key=value' pairs; ignore #comments.\"\"\"
    out = {}
    for part in line.split(";"):
        k, _, v = part.partition("=")
        if k and not k.startswith("#"):
            out[k.strip()] = int(v or 0)  # 0x1F, 1_000
    return out

const total = items.filter(x => x.qty >= 1 && x.id !== null)
                   .reduce((sum, {price}) => sum + price * 1.18, 0);"""

LINES = [
    ("Confusables", "Il1|i!  O0oQ  rn m  cl d  vv w  5S 2Z 8B  {}[]()<>  `'\"  ,.;:  -_~=+*^"),
    ("Lowercase", "abcdefghijklmnopqrstuvwxyz  minimum illumination"),
    ("Capitals", "ABCDEFGHIJKLMNOPQRSTUVWXYZ  WWW MMM HHH"),
    ("Figures", "0123456789  $1,259.36  12.5%  #4087  @user  &&  ||  \\n"),
    ("Accents", "café naïve façade Ångström łódź ŀl ĳ Ĳ Œuvre ß  Привет  Ωμέγα"),
    ("Cyrillic", "ЖШЩЫЮ жшщыю  Съешь же ещё этих мягких булок  snake_case __init__"),
]

BOX = """╭──────┬──────╮  ┏━━━━┓  ░▒▓█▀▄▌▐
│ name │ size │  ┃ ok ┃  ▁▂▃▄▅▆▇█
├──────┼──────┤  ┗━━━━┛  ╱╲╳ ┄┅┆┇
│ a.py │ 1.2K │  └─┴─┘   ▖▗▘▙▚▛▜▝▞▟
╰──────┴──────╯   ╔═══╦═══╗  ╒═╤═╕
                  ║ a ║ b ║  │ │ │
                  ╠═══╬═══╣  ╞═╪═╡
                  ╚═══╩═══╝  ╘═╧═╛"""


def block(d, x, y, text, font, size, fill=0, grid=None):
    lh = int(size * 1.21)
    if grid:
        cw = font.getlength("M")
        cols = max(len(l) for l in text.split("\n"))
        for c in range(cols + 1):
            d.line([(x + c * cw, y), (x + c * cw, y + lh * (text.count("\n") + 1))], fill=grid)
    for i, line in enumerate(text.split("\n")):
        d.text((x, y + i * lh), line, font=font, fill=fill)
    return y + lh * (text.count("\n") + 1)


def main(out, reg, ita, inter=None):
    label = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 15)
    W = 1600
    img = Image.new("L", (W, 4000), 255)
    d = ImageDraw.Draw(img)
    y = 20
    for title, path in (("Regular", reg), ("Italic", ita)):
        d.text((24, y), f"Code · {title} · 20 px (column grid behind)", font=label, fill=120)
        y = block(d, 24, y + 22, CODE, ImageFont.truetype(path, 20), 20, grid=225) + 24
    for size in (28, 16, 12):
        for title, path in (("Regular", reg), ("Italic", ita)):
            f = ImageFont.truetype(path, size)
            d.text((24, y), f"{size} px · {title}", font=label, fill=120)
            y += 22
            for _, text in LINES:
                y = block(d, 24, y, text, f, size)
            y += 14
    d.text((24, y), "Box drawing and blocks · 24 px", font=label, fill=120)
    y = block(d, 24, y + 22, BOX, ImageFont.truetype(reg, 24), 24) + 24
    if inter:
        d.text((24, y), "Inter (proportional) above, Inter Mono below · 40 px", font=label, fill=120)
        y += 22
        for path in (inter, reg):
            y = block(d, 24, y, "Hamburgefontsiv Il1 0O rn m 1234", ImageFont.truetype(path, 40), 40)
        y += 20
    img.crop((0, 0, W, y)).save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(*sys.argv[1:])
