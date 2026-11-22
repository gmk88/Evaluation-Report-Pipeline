"""篇幅与覆盖核对：新稿与原稿（用户原稿 import 出的项目文件夹）逐章比对正文字数、表格数和表格行数。
防止“改错变删减”：改写原稿时，订正错误不等于删掉内容（判例 PL-PRE-0042）。
用法：report.py coverage 原稿项目文件夹 新稿项目文件夹 [--下限 0.8]

- 章按章名关键词对齐到 GBZ/T 196—2025 的 11 章（原稿常为 10 章：第9章“补充措施与建议”、第10章“结论”），认不出的按章号。
  设计专篇（项目信息“报告类型：设计专篇”）对齐到 WS/T 882—2013 附录C 的 5 章；附件册按“附件N”的 N 对齐。
- 字数只数汉字、数字和字母；同一章里重复出现的段落或单元格只算一次（原稿常把同一句依据、同一段危害特性抄几十遍）。
- 字数（去重后）低于下限报“错误”：逐节对照原稿找回被删内容。
- 表格数、表行数低于下限只报“提示”：几张小表合并成一张、表格改写成段落都会让它变少，人工确认没有丢数据即可。
- 新稿检查规则.md 里可写“## 篇幅豁免”，每行“- 第3章：理由”（按新稿章号）。豁免的章只提示不报错，理由要写清删了什么、为什么。
"""
import re
from pathlib import Path

from . import mdparse, doctype

RE_CJK = re.compile(r'[一-鿿0-9A-Za-z]')
# 关键词 → GBZ/T 196—2025 §10.2 章号；先判“关键控制点”“结论”，再判“补充”
KEYS = (('关键控制点', 9), ('结论', 11), ('补充', 10), ('总论', 1), ('工程分析', 2), ('类比', 3), ('风险评估', 3),
        ('危害因素', 4), ('应急', 6), ('防护用品', 7), ('防护设施', 5), ('职业卫生管理', 8))


def _plain(s):
    s = re.sub(r'<br>', '', s)
    s = re.sub(r'\{\{[^}]*\}\}', '某', s)
    return s.replace('**', '')


def _key(s):
    return ''.join(RE_CJK.findall(_plain(s)))


# 设计专篇：WS/T 882—2013 附录C 的 5 章。“防护设施设计”要先于“危害因素”判（第3章名里没有“设施”）
DESIGN_KEYS = (('概述', 1), ('概况', 2), ('工程分析', 2), ('危害因素', 3), ('危害程度', 3), ('防护设施设计', 4),
               ('预期效果', 5))


def canon(no, title, keys=KEYS):
    return next((k for w, k in keys if w in title), no)


def chapters(folder):
    """按一级标题切章：{标准章号: {'no','title','chars','raw','tables','rows','figs'}}。
    chars 为去重后的字数，raw 为不去重的字数。"""
    blocks, _ = mdparse.parse_project_body(Path(folder) / '正文')
    kind = doctype.type_of(folder)
    keys = DESIGN_KEYS if kind == doctype.DESIGN else KEYS
    out, cur, seen = {}, None, set()

    def count(c, s):
        k = _key(s)
        c['raw'] += len(k)
        if len(k) >= 4 and k in seen:
            return
        seen.add(k)
        c['chars'] += len(k)

    for b in blocks:
        t = b['t']
        if t == 'h' and b['level'] == 1:
            m = re.match(r'^(?:附件\s*)?(\d+)\s+(.+)$' if kind == doctype.ANNEX else r'^(\d+)\s+(.+)$', b['text'].strip())
            if m:
                no, title = int(m.group(1)), m.group(2).strip()
                cur = no if kind == doctype.ANNEX else canon(no, title, keys)
                seen = set()
                out.setdefault(cur, {'no': no, 'title': title, 'chars': 0, 'raw': 0, 'tables': 0, 'rows': 0, 'figs': 0})
            continue
        if cur is None:
            continue
        c = out[cur]
        if t in ('h', 'p', 'li', 'caption'):
            count(c, b.get('text', ''))
        elif t == 'table':
            c['tables'] += 1
            body = b['rows'][b.get('header', 1):]
            c['rows'] += sum(1 for r in body if not (len(r) > 1 and all(x == '<' for x in r[1:])))
            for r in b['rows']:
                for cell in r:
                    if cell not in ('^', '<'):
                        count(c, cell)
        elif t == 'img':
            c['figs'] += 1
    return out


def exemptions(folder):
    p = Path(folder) / '检查规则.md'
    ex = {}
    if not p.exists():
        return ex
    text = re.sub(r'<!--.*?-->', '', p.read_text(encoding='utf-8'), flags=re.S)
    cur = False
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('#'):
            cur = '篇幅豁免' in s
            continue
        m = re.match(r'^[-*]\s+第(\d+)章[：:](.+)$', s)
        if cur and m:
            ex[int(m.group(1))] = m.group(2).strip()
    return ex


def _ratio(new, old):
    return new / old if old else 1.0


def run(old, new, low=0.8):
    a, b, ex = chapters(old), chapters(new), exemptions(new)
    rows, issues = [], []
    for n in sorted(set(a) | set(b)):
        x, y = a.get(n), b.get(n)
        if not y:
            issues.append(('错误', '原稿第%d章' % x['no'], '新稿没有对应的章（%s）' % x['title']))
            continue
        where = '第%d章' % y['no']
        if not x:
            issues.append(('提示', where, '原稿没有对应的章（%s），按新增章处理，不比篇幅' % y['title']))
            continue
        rc, rt, rr = _ratio(y['chars'], x['chars']), _ratio(y['tables'], x['tables']), _ratio(y['rows'], x['rows'])
        rows.append((y['no'], y['title'], x['no'], x['chars'], y['chars'], rc, x['tables'], y['tables'],
                     x['rows'], y['rows'], rr))
        if rc < low:
            if y['no'] in ex:
                issues.append(('提示', where, '字数为原稿第%d章的%.0f%%，已豁免：%s' % (x['no'], rc * 100, ex[y['no']])))
            else:
                issues.append(('错误', where, '字数（去重后）只有原稿第%d章的%.0f%%，低于%.0f%%。逐节对照原稿找回被删的内容；'
                               '确属不实、重复或跨项目残留而删除的，在检查规则.md“## 篇幅豁免”写明理由'
                               % (x['no'], rc * 100, low * 100)))
        if rr < low or (x['tables'] and rt < low):
            issues.append(('提示', where, '表格%d→%d张、表行%d→%d行。确认是合并或改写，原稿的数据和检查项都还在'
                           % (x['tables'], y['tables'], x['rows'], y['rows'])))
    ta, tb = sum(v['chars'] for v in a.values()), sum(v['chars'] for v in b.values())
    na, nb = sum(v['tables'] for v in a.values()), sum(v['tables'] for v in b.values())
    if ta and tb / ta < low:
        open_low = [w for lv, w, m in issues if lv == '错误' and '字数' in m]
        if open_low:
            issues.append(('错误', '全文', '全文字数（去重后）只有原稿的%.0f%%' % (tb / ta * 100)))
        else:
            issues.append(('提示', '全文', '全文字数（去重后）为原稿的%.0f%%，低于下限的各章均已写豁免理由' % (tb / ta * 100)))
    if na and nb / na < low:
        issues.append(('提示', '全文', '全文表格%d→%d张' % (na, nb)))
    return rows, (ta, tb, na, nb), issues


def report(old, new, low=0.8):
    rows, (ta, tb, na, nb), issues = run(old, new, low)
    L = ['# 篇幅与覆盖核对', '', '原稿：%s' % old, '新稿：%s' % new, '',
         '字数为去重后的汉字、数字和字母数；下限 %.0f%%。' % (low * 100), '',
         '| 新稿章 | 章名 | 原稿章 | 原稿字数 | 新稿字数 | 字数比 | 原稿表 | 新稿表 | 原稿表行 | 新稿表行 | 表行比 |',
         '|---|---|---|---|---|---|---|---|---|---|---|']
    for n, t, on, xc, yc, rc, xt, yt, xr, yr, rr in rows:
        L.append('| %d | %s | %d | %d | %d | %.0f%% | %d | %d | %d | %d | %.0f%% |'
                 % (n, t, on, xc, yc, rc * 100, xt, yt, xr, yr, rr * 100))
    L += ['', '全文：原稿%d字、%d表；新稿%d字、%d表。' % (ta, na, tb, nb), '']
    L += ['- %s【%s】%s' % i for i in issues] or ['- 通过']
    return '\n'.join(L), issues
