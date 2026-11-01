"""检查规则文件（标准/通用检查规则.md、项目 检查规则.md）的增删：加规则、删规则、禁用降为提示。

规则行的写法照 scripts/pe/info.py 的 read_rules：节标题“## 禁用”等，每项一行“- `词`”，上一行用 <!-- --> 注明出处。
"""
import re
from pathlib import Path

from . import tools

COMMON_HEADER = ('# 通用检查规则\n\n<!-- 所有项目都适用的机器检查，程序自动并进每个项目的检查。'
                 '项目要豁免某项，在自己的 检查规则.md 加“## 豁免”一节列出该项。 -->')
SECTIONS = ('禁用', '正则禁用', '提示', '必备', '豁免')


def _text(path, header):
    p = Path(path)
    if p.exists():
        return tools.read(p)
    return header + '\n\n## 禁用\n\n## 正则禁用\n\n## 提示\n'


def _find_section(lines, sec):
    for i, ln in enumerate(lines):
        m = re.match(r'^#{1,3}\s*(.+?)\s*$', ln)
        if m and next((k for k in ('豁免', '正则禁用', '禁用', '必备', '提示') if k in m.group(1)), None) == sec:
            return i
    return None


def items(path):
    """[(节, 词, 行号)]"""
    out, cur = [], None
    if not Path(path).exists():
        return out
    in_comment = False
    for i, ln in enumerate(tools.read(path).split('\n')):
        s = ln.strip()
        if in_comment:
            in_comment = '-->' not in s
            continue
        if s.startswith('<!--'):
            in_comment = '-->' not in s
            continue
        m = re.match(r'^#{1,3}\s*(.+?)\s*$', s)
        if m:
            cur = next((k for k in ('豁免', '正则禁用', '禁用', '必备', '提示') if k in m.group(1)), None)
            continue
        m = re.match(r'^[-*]\s+(.+)$', s)
        if m and cur:
            w = m.group(1).strip()
            if len(w) >= 2 and w[0] == w[-1] == '`':
                w = w[1:-1]
            out.append((cur, w, i))
    return out


def add(path, rules, note, header=COMMON_HEADER):
    """rules：[(节, 词)]。已有的不重复加。返回加了几条。"""
    have = {(s, w) for s, w, _ in items(path)}
    lines = _text(path, header).split('\n')
    n = 0
    for sec, word in rules:
        if sec not in SECTIONS or not word.strip() or (sec, word) in have:
            continue
        i = _find_section(lines, sec)
        if i is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += ['', '## ' + sec]
            i = len(lines) - 1
        j = next((x for x in range(i + 1, len(lines)) if re.match(r'^#{1,3}\s', lines[x])), len(lines))
        while j > i + 1 and not lines[j - 1].strip():
            j -= 1
        lines[j:j] = ['<!-- %s -->' % note.replace('--', '—'), '- `%s`' % word]
        have.add((sec, word))
        n += 1
    tools.write(path, '\n'.join(lines).rstrip('\n') + '\n')
    return n


def remove(path, word, sections=('禁用', '正则禁用', '提示')):
    """删掉某个词的规则行，连同紧挨在它上面、只为它写的注释。"""
    hit = [(s, w, i) for s, w, i in items(path) if w == word and s in sections]
    if not hit:
        return 0
    lines = tools.read(path).split('\n')
    drop = set()
    rule = lambda k: 0 <= k < len(lines) and lines[k].strip().startswith('- ')  # noqa: E731
    for _, _, i in hit:
        drop.add(i)
        # 上面的注释只管这一条（下一行不再紧跟规则）才一起删；几条共用的注释留给剩下的规则
        if i > 0 and lines[i - 1].strip().startswith('<!--') and lines[i - 1].strip().endswith('-->') \
                and not (rule(i + 1) and i + 1 not in {j for _, _, j in hit}):
            drop.add(i - 1)
    tools.write(path, '\n'.join(ln for k, ln in enumerate(lines) if k not in drop).rstrip('\n') + '\n')
    return len(hit)


def demote(path, word, note):
    """禁用 → 提示（提示按字面查，正则禁用的直接删掉）。返回 '提示' 或 '删除'。"""
    was = {s for s, w, _ in items(path) if w == word}
    remove(path, word, ('禁用', '正则禁用'))
    if '禁用' in was:
        add(path, [('提示', word)], note)
        return '提示'
    return '删除'


def remove_by_note(path, keys):
    """删掉注释里写着这些 K 号的规则（新意见取代旧意见时）。"""
    if not Path(path).exists() or not keys:
        return 0
    lines, out, n, skip = tools.read(path).split('\n'), [], 0, False
    for ln in lines:
        s = ln.strip()
        if s.startswith('<!--') and s.endswith('-->') and any(re.search(r'(?<![0-9A-Z])%s(?![0-9])' % re.escape(k), s) for k in keys):
            skip = True
            continue
        if skip and re.match(r'^[-*]\s+', s):
            n += 1
            continue
        skip = False
        out.append(ln)
    tools.write(path, '\n'.join(out).rstrip('\n') + '\n')
    return n


def valid_regex(path):
    """正则禁用里写错的正则删掉，返回删掉的。"""
    bad = []
    for s, w, _ in items(path):
        if s == '正则禁用':
            try:
                re.compile(w)
            except re.error:
                bad.append(w)
    for w in bad:
        remove(path, w, ('正则禁用',))
    return bad


def hits_in(root):
    """用检查程序本身查一个项目：通用规则的禁用、正则禁用各命中哪些词。返回 {词: 次数}。"""
    out = {}
    try:
        pj = tools.Project(root).resolve()
        issues = tools._checks.run(pj)
    except Exception:  # noqa: BLE001  正文读不了的项目不拿来验证
        return out
    for lv, _, msg in issues:
        m = re.match(r'^通用规则禁用“(.+)”$', msg) or re.match(r'^通用规则禁用 /(.+)/：', msg)
        if lv == '错误' and m:
            out[m.group(1)] = out.get(m.group(1), 0) + 1
    return out
