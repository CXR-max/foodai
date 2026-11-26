"""演示视频素材预处理：详情/结算页是全屏固定容器，用高视口截出的长图底部会留一条纯色空白，
这里按行方差找出这段空白并裁掉，把底部操作栏直接接在内容后面。

用法：python scripts/video_trim.py
输入：.tmp_video/shots/detail.png、settle.png
输出：.tmp_video/shots/detail_tall.png、settle_tall.png
"""
from pathlib import Path

import numpy as np
from PIL import Image

SHOTS = Path(__file__).resolve().parent.parent / '.tmp_video' / 'shots'


def find_blank(img: Image.Image, min_run: int = 80) -> tuple[int, int] | None:
    """返回最长的纯色空白行区间（top, bottom），没有就返回 None。"""
    a = np.asarray(img.convert('RGB')).astype(int)
    plain = a.std(axis=(1, 2)) < 2.0
    best, start = None, None
    for i, v in enumerate(plain):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_run and (best is None or i - start > best[1] - best[0]):
                best = (start, i)
            start = None
    if start is not None and len(plain) - start >= min_run:
        if best is None or len(plain) - start > best[1] - best[0]:
            best = (start, len(plain))
    return best


def trim(name: str) -> None:
    src = SHOTS / f'{name}.png'
    if not src.exists():
        print(f'skip {name}: 缺少 {src}')
        return
    im = Image.open(src)
    blank = find_blank(im)
    if not blank:
        print(f'{name}: 没有空白带，直接复制')
        im.save(SHOTS / f'{name}_tall.png')
        return
    top = im.crop((0, 0, im.width, blank[0]))
    bottom = im.crop((0, blank[1], im.width, im.height))
    out = Image.new('RGB', (im.width, top.height + bottom.height), '#f5f7fa')
    out.paste(top, (0, 0))
    out.paste(bottom, (0, top.height))
    out.save(SHOTS / f'{name}_tall.png')
    print(f'{name}: 裁掉 {blank[1] - blank[0]}px 空白 -> {out.size}')


if __name__ == '__main__':
    SHOTS.mkdir(parents=True, exist_ok=True)
    for n in ('detail', 'settle'):
        trim(n)
