"""项目文件夹：读取项目信息和正文；表格索引、档位计数、占位符。"""
import re
from collections import Counter
from pathlib import Path

from . import mdparse
from .common import LEVELS
from .info import read_info, read_rules, merge_common

INFO_FILE = '项目信息.md'
RULES_FILE = '检查规则.md'
COMMON_RULES = Path(__file__).resolve().parents[2] / '标准' / '通用检查规则.md'
RE_PH = re.compile(r'\{\{\s*([^{}]+?)\s*\}\}')
JUDGE_HEADS = ('判定', '判定结果', '评价结果', '结论')


def plain(s):
    """去掉 **加粗** 标记、<br> 和转义。"""
    s = (s or '').replace('**', '')
    s = re.sub(r'<br\s*/?>', '', s)
    return s.replace('\\^', '^').replace('\\<', '<').replace('\\*', '*')


def table_grid(tb):
    """表格逐行文字（合并格展开：^ 取上格，< 取左格），并给出合并标记。"""
    grid, mark = [], []
    for r, row in enumerate(tb['rows']):
        g, m = [], []
        for c, cell in enumerate(row):
            if cell == '^' and r > 0:
                g.append(grid[r - 1][c]); m.append('^')
            elif cell == '<' and c > 0:
                g.append(g[c - 1]); m.append('<')
            else:
                g.append(plain(cell)); m.append('')
        grid.append(g); mark.append(m)
    return grid, mark


def judge_col(tb):
    """“符合性”列的列号（表头以“符合性”开头，或叫“判定/评价结果/结论”）。"""
    grid, _ = table_grid(tb)
    h = tb.get('header', 1)
    if not grid or h < 1:
        return None
    for c, x in enumerate(grid[h - 1]):
        xc = re.sub(r'\s+', '', x)
        if xc.startswith('符合性') or xc in JUDGE_HEADS:
            return c
    return None


def judged_rows(tb):
    """[(行号, 档位)]：跳过表头、整行合并的分组行、纵向合并的续格。"""
    c = judge_col(tb)
    if c is None:
        return []
    grid, mark = table_grid(tb)
    out = []
    for r in range(tb.get('header', 1), len(grid)):
        if mark[r][c]:
            continue
        if len(mark[r]) > 1 and all(m == '<' for m in mark[r][1:]):
            continue
        out.append((r, re.sub(r'\s+', '', grid[r][c])))
    return out


def tally(tb):
    return Counter(v for _, v in judged_rows(tb))


def count_clause(tb, subject):
    """“表X共评价N项，其中符合a项、基本符合b项”；只有一档时写“均判为X”；0 档不写。"""
    cnt = tally(tb)
    total = sum(cnt.values())
    order = [k for k in LEVELS if cnt.get(k)] + [k for k in cnt if k not in LEVELS]
    s = '%s共评价%d项，' % (subject, total)
    if len(order) == 1:
        return s + '均判为%s' % order[0]
    return s + '其中' + '、'.join('%s%d项' % (k, cnt[k]) for k in order)


def body_rows(tb):
    """表体行数：不含表头和“合计”行，纵向合并的续行不另计。"""
    grid, mark = table_grid(tb)
    n = 0
    for r in range(tb.get('header', 1), len(grid)):
        first = re.sub(r'\s+', '', grid[r][0]) if grid[r] else ''
        if first in ('合计', '总计', '小计'):
            continue
        if mark[r] and mark[r][0] == '^':
            continue
        n += 1
    return n


def to_number(s):
    m = re.search(r'-?\d+(?:\.\d+)?', (s or '').replace(',', ''))
    return float(m.group()) if m else None


def column_sum(tb, colname):
    grid, mark = table_grid(tb)
    head = grid[tb.get('header', 1) - 1]
    c = next((k for k, h in enumerate(head) if colname in h), None)
    if c is None:
        raise KeyError('表中没有“%s”列' % colname)
    total = 0.0
    for r in range(tb.get('header', 1), len(grid)):
        if mark[r][c]:
            continue
        if re.sub(r'\s+', '', ''.join(grid[r][:max(c, 1)])).startswith(('合计', '总计')):
            continue
        v = to_number(grid[r][c])
        if v is not None:
            total += v
    return total


def fmt_num(x):
    return ('%.2f' % x).rstrip('0').rstrip('.')


CENTER_HEADS = ('序号', '编号', '代码', '符合性', '适用性', '优先级', '单位', '数量', '是否')
CENTER_EXACT = ('判定', '判定结果', '评价结果', '结论', '限值')  # 限值列格内较长也居中，与同表实测值列一致
RE_SHORT_VALUE = re.compile(r'^[\s\d.,，～~<>≤≥±×%/\-—–()（）a-zA-Zμ³²·℃]*$')
CENTER_MAX = 6     # 一格最多几个字仍算“短值”，可居中
NUM_MAX = 12       # 数值格超过这么长（多个数值、带 PC-TWA 前缀）不自动判居中，交给“对齐=”
LEFT_MIN = 20      # 一格达这么多字（折成三行左右）的列靠左；两行的短语（检查项目、资料出处）冷轧也居中
LEFT_STRICT = 30   # 检查只在这么长时才提示靠左：冷轧“资料出处”最长 29 字仍居中


def column_kind(head, values, strict=False):
    """单元格对齐判据（2026-10-28 排版审查，对照冷轧 v17）：
    'center'：固定词表列（序号、符合性、适用性…）、数值列、每格都很短的列；
    'left'：任一格会折成两行以上的描述列（多行居中两边都参差）；
    None：介于两者之间，按作者写的为准。
    strict：只认固定词表、数值和长文本三种硬依据（检查用）；“每格都短”只用于未写“对齐=”时的默认值，
    冷轧也有短词列靠左（卫生特征、分析环节），不据此提示。"""
    h = re.sub(r'\s+', '', plain(head))
    vals = [re.sub(r'\s+', '', plain(v)) for v in values if v not in ('^', '<') and plain(v).strip()]
    if h.startswith(CENTER_HEADS) or re.sub(r'[（(].*$', '', h) in CENTER_EXACT:
        return 'center'
    if not vals:
        return None
    if all(RE_SHORT_VALUE.match(v) for v in vals) and max(len(v) for v in vals) <= NUM_MAX:
        return 'center'
    if not strict and max(len(v) for v in vals) <= CENTER_MAX:
        return 'center'
    if max(len(v) for v in vals) >= (LEFT_STRICT if strict else LEFT_MIN):
        return 'left'
    return None


class Project:
    def __init__(self, root):
        self.root = Path(root).resolve()
        info_path = self.root / INFO_FILE
        if not info_path.exists():
            raise FileNotFoundError('项目文件夹里没有 %s：%s' % (INFO_FILE, self.root))
        self.info = read_info(info_path)
        self.rules = read_rules(self.root / RULES_FILE)
        self.common_rules = merge_common(self.rules, COMMON_RULES)
        self.blocks, self.files = mdparse.parse_project_body(self.root / '正文')
        self.tables, self.dup_tables = {}, []
        for b in self.blocks:
            if b['t'] == 'table' and b.get('caption'):
                key = mdparse.RE_TCAP.match(b['caption']).group(1)
                if key in self.tables:
                    self.dup_tables.append((key, b['src']))
                else:
                    self.tables[key] = b
        self.unresolved = []
        self.resolved = []

    def resolve(self):
        """把正文里的 {{…}} 换成实际文字；解析不了的记入 self.unresolved。"""
        def repl(m, src):
            try:
                v = self.value(m.group(1))
                self.resolved.append((src, m.group(0), v))
                return v
            except Exception as e:  # noqa: BLE001
                self.unresolved.append((src, m.group(0), str(e).strip("'\"")))
                return m.group(0)

        for b in self.blocks:
            src = b['src']
            if 'text' in b:
                b['raw'] = b['text']
                b['text'] = RE_PH.sub(lambda m: repl(m, src), b['text'])
            if b['t'] == 'table':
                b['rows'] = [[RE_PH.sub(lambda m: repl(m, src), c) for c in row] for row in b['rows']]
        return self

    def table(self, key):
        key = key.strip().lstrip('表').strip()
        if key not in self.tables:
            raise KeyError('找不到表%s（表题要紧挨在表格上一行）' % key)
        return self.tables[key]

    def value(self, key):
        parts = [p.strip() for p in re.split(r'[:：]', key.strip())]
        kind = parts[0]
        if kind == '计数':
            tb = self.table(parts[1])
            if judge_col(tb) is None:
                raise KeyError('表%s没有“符合性”列' % parts[1].lstrip('表'))
            subject = parts[2] if len(parts) > 2 and parts[2] else '表' + parts[1].lstrip('表')
            return count_clause(tb, subject)
        if kind == '行数':
            return str(body_rows(self.table(parts[1])))
        if kind == '合计':
            return fmt_num(column_sum(self.table(parts[1]), parts[2]))
        if kind == '项目' and len(parts) > 1:
            kind = parts[1]
        v = self.info.get(kind)
        if v is None or isinstance(v, (list, dict)):
            raise KeyError('项目信息.md 里没有“%s”' % kind)
        return str(v)
