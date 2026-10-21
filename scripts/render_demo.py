"""Render a terminal GIF from actual offline-demo stdout (Pillow dev dependency)."""
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'demo.py')],
                            cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'},
                            capture_output=True, text=True, encoding='utf-8', check=True).stdout
    docs = ROOT / 'docs'
    docs.mkdir(exist_ok=True)
    (docs / 'demo-output.txt').write_text(output, encoding='utf-8')
    candidates = [Path('C:/Windows/Fonts/msyh.ttc'), Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')]
    font_path = next((p for p in candidates if p.is_file()), None)
    if font_path is None:
        raise RuntimeError('Install a CJK font; update font candidates in this script.')
    font = ImageFont.truetype(str(font_path), 20)
    small = ImageFont.truetype(str(font_path), 17)
    lines = output.strip().splitlines()
    frames = []
    for count in (1, 5, len(lines)):
        img = Image.new('RGB', (1120, 680), '#111827')
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((15, 15, 1105, 665), radius=15, fill='#172033', outline='#334155')
        for x, color in ((40, '#fb7185'), (65, '#fbbf24'), (90, '#34d399')):
            draw.ellipse((x, 35, x + 12, 47), fill=color)
        draw.text((125, 27), 'geoask / DeepSeek API interface', font=small, fill='#94a3b8')
        draw.text((35, 80), '$ geoask "我想喝咖啡" --near 116.46,39.92 --explain', font=font, fill='#67e8f9')
        for i, line in enumerate(lines[:count]):
            draw.text((35, 125 + i * 29), line, font=small if i == 0 else font,
                      fill='#fbbf24' if i == 0 else '#e2e8f0')
        frames.append(img)
    frames[0].save(docs / 'demo.gif', save_all=True, append_images=frames[1:],
                   duration=[1300, 1600, 5500], loop=0)
    frames[-1].save(docs / 'demo.png')
    print(output)


if __name__ == '__main__':
    main()
