"""Render a terminal GIF from demo stdout; first model use may need network."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', type=Path, help='CJK font file; alternatively set GEOASK_FONT')
    args = parser.parse_args(argv)
    font_path = choose_font(args.font)
    output = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'demo.py')],
                            cwd=ROOT, env={**os.environ, 'PYTHONUTF8': '1'},
                            capture_output=True, text=True, encoding='utf-8', check=True).stdout
    docs = ROOT / 'docs'
    docs.mkdir(exist_ok=True)
    (docs / 'demo-output.txt').write_text(output, encoding='utf-8')
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


def choose_font(explicit=None):
    selected = explicit or os.getenv('GEOASK_FONT')
    if selected:
        path = Path(selected).expanduser()
        if not path.is_file():
            raise ValueError('指定的中文字体不存在，请核对 --font 或 GEOASK_FONT 路径。')
        return path
    candidates = [Path('C:/Windows/Fonts/msyh.ttc'),
                  Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
                  Path('/System/Library/Fonts/PingFang.ttc')]
    font = next((p for p in candidates if p.is_file()), None)
    if font is None:
        raise ValueError('请安装中文字体，或通过 --font / GEOASK_FONT 指定字体文件。')
    return font


if __name__ == '__main__':
    main()
