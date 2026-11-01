"""正文 Markdown → 块列表。完整写法见 references/01_正文写法格式.md。

- 一行一段；空行可有可无。
- # 一级标题（章）  ## 二级  ### 三级  #### 四级（四级不进目录）。
- “1. ”开头为自动编号列表；序号为 1 时另起新列表。
- 表格：| 行 |；分隔行（|---|）之前的行都是表头行。单元格内 <br> 分段，^ 与上格合并，< 与左格合并，\\| 表示竖线。
- 紧接在表格前、以“表X.Y-Z”开头的一段是表题；紧接在图片后、以“图X.Y-Z”开头的一段是图题。
- 图片：![说明](图/文件名.png){宽=15cm}
- 指令注释（独占一行）：<!-- 横向 --> <!-- 纵向 --> <!-- 分页 --> <!-- 空行 -->
  <!-- 表格 列宽=… 字号=… 对齐=… 表后空行=否 -->  <!-- 段落 居中 / 不缩进 / 字号=五号（行距、缩进按比例缩小） -->
- 其他 <!-- … --> 注释（可跨行）是写作提示，不输出。
"""
import re
from pathlib import Path

DIRECTIVES = ('横向', '纵向', '分页', '空行', '表格', '段落')
RE_HEAD = re.compile(r'^(#{1,4})\s+(.+?)\s*$')
RE_LIST = re.compile(r'^(\d{1,3})[.．]\s+(.+)$')
RE_IMG = re.compile(r'^!\[(?P<alt>[^\]]*)\]\((?P<path>[^)]+)\)\s*(?:\{(?P<attrs>[^}]*)\})?\s*$')
RE_SEP = re.compile(r'^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')
RE_TCAP = re.compile(r'^(?:续)?表\s*([0-9A-Z]+(?:\.\d+)*-\d+)')
RE_FCAP = re.compile(r'^图\s*([0-9A-Z]+(?:\.\d+)*-\d+)')
RE_DIR = re.compile(r'^<!--\s*(\S+)(.*?)-->\s*$')


class ParseError(Exception):
    pass


def split_cells(line):
    s = line.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|') and not s.endswith('\\|'):
        s = s[:-1]
    cells, cur, i = [], '', 0
    while i < len(s):
        ch = s[i]
        if ch == '\\' and i + 1 < len(s) and s[i + 1] in '|^<':
            cur += '\\' + s[i + 1] if s[i + 1] in '^<' else '|'
            i += 2
            continue
        if ch == '|':
            cells.append(cur)
            cur = ''
        else:
            cur += ch
        i += 1
    cells.append(cur)
    return [c.strip() for c in cells]


def parse_attrs(s):
    """'列宽=1,2 字号=19 表后空行=否 居中' → dict；没有等号的词值为 True。"""
    out = {}
    for tok in re.findall(r'(\S+?=(?:"[^"]*"|\S+)|\S+)', s or ''):
        if '=' in tok:
            k, v = tok.split('=', 1)
            out[k.strip()] = v.strip().strip('"')
        else:
            out[tok] = True
    return out


def parse_text(text, fname='正文.md'):
    lines = text.split('\n')
    blocks = []
    pending_table = None
    pending_para = None
    i, n = 0, len(lines)

    def add(b, ln):
        b['src'] = '%s:%d' % (fname, ln + 1)
        blocks.append(b)
        return b

    while i < n:
        line = lines[i].rstrip('\r').strip()
        if not line:
            i += 1
            continue
        if line.startswith('<!--'):
            m = RE_DIR.match(line)
            if m and m.group(1) in DIRECTIVES:
                kind, rest = m.group(1), m.group(2)
                if kind in ('横向', '纵向'):
                    add({'t': 'sect', 'orient': 'landscape' if kind == '横向' else 'portrait'}, i)
                elif kind == '分页':
                    add({'t': 'pagebreak'}, i)
                elif kind == '空行':
                    add({'t': 'blank'}, i)
                elif kind == '表格':
                    pending_table = (parse_attrs(rest), i)
                elif kind == '段落':
                    pending_para = parse_attrs(rest)
                i += 1
                continue
            j = i
            while j < n and '-->' not in lines[j]:
                j += 1
            if j >= n:
                raise ParseError('%s:%d 注释没有结束符 -->' % (fname, i + 1))
            if lines[j].split('-->', 1)[1].strip():
                raise ParseError('%s:%d 注释结束符 --> 后面不要接正文，正文另起一行' % (fname, j + 1))
            i = j + 1
            continue
        if line.startswith('|'):
            start = i
            raw = []
            while i < n and lines[i].strip().startswith('|'):
                raw.append((i, lines[i].strip()))
                i += 1
            sep = [k for k, (_, l) in enumerate(raw) if RE_SEP.match(l)]
            if not sep:
                raise ParseError('%s:%d 表格缺少分隔行（如 |---|---|），分隔行之前是表头' % (fname, start + 1))
            hdr = sep[0]
            ncol = len(split_cells(raw[hdr][1]))
            rows, lns = [], []
            for k, (ln, l) in enumerate(raw):
                if k == hdr:
                    continue
                cells = split_cells(l)
                if len(cells) != ncol:
                    raise ParseError('%s:%d 这一行有 %d 格，表格应为 %d 格（合并格用 ^ 或 < 占位，竖线写成 \\|）'
                                     % (fname, ln + 1, len(cells), ncol))
                rows.append(cells)
                lns.append(ln + 1)
            attrs = pending_table[0] if pending_table else {}
            pending_table = None
            header = hdr
            if str(attrs.get('表头行', '')).isdigit():
                header = int(attrs['表头行'])
            b = add({'t': 'table', 'rows': rows, 'header': header, 'attrs': attrs, 'lines': lns}, start)
            if len(blocks) >= 2 and blocks[-2]['t'] == 'p' and RE_TCAP.match(blocks[-2]['text']) \
                    and not blocks[-2].get('attrs'):
                blocks[-2]['t'] = 'caption'
                blocks[-2]['kind'] = '表'
                b['caption'] = blocks[-2]['text']
            continue
        if pending_table:
            raise ParseError('%s:%d “表格”指令后面应紧接表格' % (fname, pending_table[1] + 1))
        m = RE_HEAD.match(line)
        if m:
            add({'t': 'h', 'level': len(m.group(1)), 'text': m.group(2).strip()}, i)
            pending_para = None
            i += 1
            continue
        m = RE_IMG.match(line)
        if m:
            add({'t': 'img', 'path': m.group('path').strip(), 'alt': m.group('alt'),
                 'attrs': parse_attrs(m.group('attrs'))}, i)
            i += 1
            continue
        m = RE_LIST.match(line)
        if m:
            add({'t': 'li', 'num': int(m.group(1)), 'text': m.group(2).strip()}, i)
            i += 1
            continue
        text = line
        if text.startswith('\\') and len(text) > 1 and text[1] in '#!|<0123456789':
            text = text[1:]
        b = {'t': 'p', 'text': text}
        if pending_para:
            b['attrs'] = pending_para
            pending_para = None
        add(b, i)
        if len(blocks) >= 2 and blocks[-2]['t'] == 'img' and RE_FCAP.match(text) and not b.get('attrs'):
            b['t'] = 'caption'
            b['kind'] = '图'
            blocks[-2]['caption'] = text
        i += 1
    if pending_table:
        raise ParseError('%s:%d “表格”指令后面应紧接表格' % (fname, pending_table[1] + 1))
    return blocks


def body_files(body_dir):
    return sorted(p for p in Path(body_dir).glob('*.md') if not p.name.startswith(('_', '.')))


def parse_project_body(body_dir):
    """读取 正文/*.md（按文件名排序）。返回 (blocks, files)。"""
    files = body_files(body_dir)
    blocks = []
    for f in files:
        blocks += parse_text(f.read_text(encoding='utf-8'), fname=f.name)
    return blocks, files
