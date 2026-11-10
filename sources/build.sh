#!/bin/sh
# Builds Inter Mono from the Inter 18pt static fonts in src/.
# Needs: python3 with fonttools, skia-pathops, ttfautohint-py, brotli, pillow.
set -e
cd "$(dirname "$0")"
mkdir -p build ../fonts/ttf ../fonts/ttf-unhinted ../fonts/webfonts
for S in Regular Italic; do
  python3 make_mono.py src/Inter_18pt-$S.ttf build/InterMono-$S.ttf
  cp build/InterMono-$S.ttf ../fonts/ttf-unhinted/
  python3 -m ttfautohint -W -n -D latn -f latn build/InterMono-$S.ttf ../fonts/ttf/InterMono-$S.ttf
done
python3 finish_font.py ../fonts/ttf/InterMono-*.ttf ../fonts/ttf-unhinted/InterMono-*.ttf
for S in Regular Italic; do
  python3 -c "from fontTools.ttLib import TTFont; f=TTFont('../fonts/ttf/InterMono-$S.ttf'); f.flavor='woff2'; f.save('../fonts/webfonts/InterMono-$S.woff2')"
done
python3 proof.py build/proof.png ../fonts/ttf/InterMono-Regular.ttf ../fonts/ttf/InterMono-Italic.ttf src/Inter_18pt-Regular.ttf
