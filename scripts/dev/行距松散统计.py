"""统计 Word 导出 PDF 里正文两端对齐行的字距拉开程度：每行（字间多出的空白）÷（字数−1），以字号为单位。
python3.12 行距松散统计.py 旧.pdf 新.pdf  → 各档行数，和拉得最开的行。只看正文（四号字）行，不看段末行、表格。"""
import sys
import re
import pymupdf as fitz


def lines(pdf):
    out = []
    for pno, pg in enumerate(fitz.open(pdf)):
        if pno < 6:
            continue
        W = pg.rect.width
        for b in pg.get_text('rawdict')['blocks']:
            for ln in b.get('lines', []):
                chars = [c for s in ln['spans'] for c in s['chars'] if abs(s['size'] - 14) < 0.6]
                if len(chars) < 12:
                    continue
                x0, x1 = chars[0]['bbox'][0], chars[-1]['bbox'][2]
                if W - x1 > 100:            # 没排到右边线：段末行或表格里的行
                    continue
                adv = sum(c['bbox'][2] - c['bbox'][0] for c in chars)
                gap = ((x1 - x0) - adv) / (len(chars) - 1) / 14
                out.append((gap, pno + 1, ''.join(c['c'] for c in chars)))
    return out


def report(tag, ls):
    bins = [0.05, 0.10, 0.15, 0.25]
    cnt = [sum(1 for g, _, _ in ls if g > b) for b in bins]
    print('%s：正文满行 %d 行；字间多出 >0.05字 %d 行，>0.10字 %d 行，>0.15字 %d 行，>0.25字 %d 行'
          % (tag, len(ls), *cnt))
    for g, p, t in sorted(ls, reverse=True)[:12]:
        print('    %.2f字  第%d页  %s' % (g, p, t[:40]))


for f in sys.argv[1:]:
    report(f.split('/')[-1][-30:], lines(f))
