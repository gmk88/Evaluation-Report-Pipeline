"""按 VS Code Office Viewer（Word-DoB-pM9Q.js 的 RS/DS/BS）的断行规则粗算正文各行的空余：
只在 半角空格、'-'、制表符 之后和 run 边界断行；放不下整行的词逐字填满。两端对齐时空余被拉散到字间。"""
import re, sys, unicodedata
sys.path[:0] = ['.deps', '生成器/scripts']
from pe.model import Project
from pe import docxgen

LINE = 8300 / 20 / 14          # 正文宽 8300 twip，四号 14 pt → 每行约 29.6 字宽
NARROW = {' ': .25, ' ': .25, '.': .25, ',': .25, '/': .28, '(': .33, ')': .33, '-': .33, '°': .4,
          '%': .83, '²': .3, '³': .3, '~': .54, '±': .56, '×': .56, '≥': .55, '≤': .55}


def w(ch):
    if ch in NARROW: return NARROW[ch]
    if ch.isdigit() and ord(ch) < 128: return .5
    if 'a' <= ch <= 'z': return .45
    if 'A' <= ch <= 'Z': return .68
    return 1.0 if unicodedata.east_asian_width(ch) in 'WFA' else .5


def lines(runs, first_indent=2.0):
    out, cur, avail = [], 0.0, LINE - first_indent
    def brk():
        nonlocal cur, avail
        out.append((cur, avail)); cur, avail = 0.0, LINE
    for text in runs:
        cuts = [i + 1 for i, c in enumerate(text) if c in ' -\t'] + [len(text)]
        o = 0
        for t in cuts:
            if t <= o: continue
            tok = text[o:t]; o = t
            tw = sum(w(c) for c in tok)
            if tw > LINE:                      # 词比整行还长：逐字填满
                for c in tok:
                    if cur + w(c) > avail and cur > 0: brk()
                    cur += w(c)
            else:
                if cur > 0 and cur + tw > avail: brk()
                cur += tw
    out.append((cur, avail))
    return out[:-1]                            # 末行不拉散


def scan(fix):
    bad = []
    for b in pj.blocks:
        if b['t'] not in ('p', 'li'): continue
        text = docxgen.nobreak_spaces(b['text']) if fix else b['text']
        runs = [x for x in re.split(r'\*\*', text) if x]
        for k, (used, avail) in enumerate(lines(runs)):
            if avail - used > 3:               # 空出 3 个字宽以上，拉散后肉眼可见
                bad.append((avail - used, k + 1, b['text'][:34]))
    return bad


pj = Project('晋钢200MW_v2源文件')
if hasattr(pj, 'resolve'): pj.resolve()
before, after = scan(False), scan(True)
print('空出 3 字宽以上的行：改前 %d 行，改后 %d 行' % (len(before), len(after)))
print('改前示例：'); [print('   空%4.1f字 第%d行 %s' % x) for x in sorted(before, reverse=True)[:6]]
print('改后剩余：'); [print('   空%4.1f字 第%d行 %s' % x) for x in sorted(after, reverse=True)[:30]]
