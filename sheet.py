#!/usr/bin/env python3
"""Contact sheet for game artwork, drawn with Pillow (the Linux counterpart of sheet.js).

Usage: python3 sheet.py spec.json out.jpg
spec.json: [{"label": "Q01 · Game name", "thumbs": ["a.png", ...up to 3], "icon": "icon.png"}, ...] (up to 5 rows)
"""
import json, sys
from PIL import Image, ImageDraw, ImageFont

ROW, W = 215, 1000


def font(size):
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', '/System/Library/Fonts/Supplemental/Arial Bold.ttf'):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def main(spec_path, out):
    spec = json.load(open(spec_path, encoding='utf-8'))
    sheet = Image.new('RGB', (W, len(spec) * ROW + 10), (31, 31, 31))
    draw, f = ImageDraw.Draw(sheet), font(20)
    for i, row in enumerate(spec):
        top = 10 + i * ROW
        draw.text((10, top), row['label'], font=f, fill='white')
        x = 10

        def paste(path, w, h):
            nonlocal x
            if path:
                try:
                    sheet.paste(Image.open(path).convert('RGB').resize((w, h)), (x, top + 34))
                except Exception:
                    pass
            x += w + 10

        paste(row.get('icon'), 146, 146)
        for p in (row.get('thumbs') or [])[:3]:
            paste(p, 260, 146)
    sheet.save(out, 'JPEG', quality=80)


if __name__ == '__main__':
    main(*sys.argv[1:3])
