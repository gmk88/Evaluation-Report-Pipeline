"""生成前检查（只读）：判定档位、计数句、编号、引用、口径用语、标点格式、项目规则、待填项。
每条问题是 (级别, 位置, 内容)；级别：错误（必须改）、提示（人工确认）、待办（【待填】【待核】等）。"""
import re
from collections import defaultdict

from .common import LEVELS
from .model import plain, table_grid, judge_col, judged_rows, count_clause, body_rows, column_sum, fmt_num, column_kind
from . import mdparse

# GBZ/T 196—2025 规定的 11 章标题（每章可用的写法）。
# 第2章：改建、扩建、技改项目并入第10.2.2条“现有企业概况”；
# 第3章：采用风险评估法（第8.4条）设“职业健康风险评估分析与评价”，采用类比法（第10.2.4条）设“类比企业（项目）调查与分析”
STANDARD_CHAPTERS = [('总论',), ('建设项目工程分析', '现有企业概况及建设项目工程分析'),
                     ('类比企业（项目）调查与分析', '职业健康风险评估分析与评价'), ('职业病危害因素及危害程度分析',),
                     ('职业病防护设施分析与评价',), ('应急救援措施的分析与评价',), ('个人使用的职业病防护用品的分析与评价',),
                     ('职业卫生管理的分析与评价',), ('职业病危害关键控制点分析',), ('职业病防治措施的补充建议',), ('结论与建议',)]
GENERIC_ERRORS = [
    ('本公司', '正文不用“本公司”，用“评价组”或无主语句（判例 PL-PRE-0001）'),
    ('按可研方案建成', '已建成项目的已定参数按实际值写，可研值作标注（判例 PL-PRE-0032）'),
    ('需补充完善', '判定档名统一为“按补充措施完善后符合”'),
    ('投运前', '时间节点统一写“投产前”'),
]
GENERIC_NOTES = [
    ('受限空间', '确认确有作业人员需进入的受限空间；没有就不写（判例 PL-GEN-0040）'),
]
RE_COUNT = re.compile(r'(?P<subj>表\d+(?:\.\d+)*-\d+|[\u4e00-\u9fa5]{2,10})共评价(?P<n>\d+)项，'
                      r'(?P<rest>其中(?:[\u4e00-\u9fa5]+\d+项、?)+|均判为[\u4e00-\u9fa5]+?(?=[。；，]|$))')
RE_KCP = re.compile(r'(\d+)个职业病危害关键控制点')
RE_FUND = re.compile(r'表\s*(\d+(?:\.\d+)*-\d+)[^。；]*?约\s*(\d+(?:\.\d+)?)\s*万元')
RE_STD = re.compile(r'(?<![A-Za-z])(GBZ/T|GBZ|GB/T|GB|WS/T|AQ/T|AQ|HJ|DB\d{2}/T|SH/T|SY/T)\s?(\d+(?:\.\d+)*)\s?([—\-－–])\s?((?:19|20)\d{2})')
PAIRS = [('（', '）'), ('(', ')'), ('《', '》'), ('“', '”'), ('【', '】'), ('〔', '〕'), ('［', '］')]
RE_DUP_PUNCT = re.compile(r'([，。；：、！？])\1|[，；：、][。，；：]|。[，；：、]')
RE_REF = re.compile(r'(?<![续附])(表|图)\s*(\d+(?:\.\d+)*-\d+)')
RE_ATT = re.compile(r'附件\s*(\d+)')
RE_TODO = re.compile(r'【(?:待填|待核|待补|待定|待确认)[^】]*】')
DETECT_VALUES = {'合格', '不合格', '达标', '超标', '符合', '不符合', '—', '/', '／', '-', ''}
RE_CONTROL = re.compile(r'需落实|应设置|应配备|应加装|应增设|设计应明确')
# 放射性（电离辐射）因素：职业卫生评价不评价，另做放射性职业病危害评价（GBZ/T 181）。“非电离辐射”不算。
RE_RADIATION = re.compile(r'(?<!非)电离辐射|(?<!非)放射性|放射源|放射防护|放射卫生|放射诊疗|放射工作|射线装置|'
                          r'[XxΧγβα]\s*射线|个人剂量|辐射剂量|剂量当量|放射性核素')
RE_RAD_EXCLUDE = re.compile(r'另行|不在本报告|不属于本报告|不纳入|不包括|不含|不进行')
# GBZ 188 体检项目里的胸片（粉尘、刺激性气体作业必检项目）是检查手段，不是放射性危害因素
RE_RAD_HEALTH_EXAM = re.compile(r'(?:后前位)?[XxΧ]\s*射线(?:高千伏)?(?:胸片|胸部摄影|检查)|胸部\s*[XxΧ]\s*射线(?:检查|摄影)?')
RAD_WHY = '职业卫生评价报告不评价放射性（电离辐射）因素，放射性因素另做放射性职业病危害评价（判例 PL-PRE-0044）'


def unbalanced(p, l, r):
    """逐字扫描；没有对应左括号的“a）”“1)”“10）”按条目序号放过。"""
    depth = 0
    for i, ch in enumerate(p):
        if ch == l:
            depth += 1
        elif ch == r:
            if depth:
                depth -= 1
                continue
            j, k = i - 1, 0
            while j >= 0 and k < 3 and p[j].isascii() and p[j].isalnum():
                j, k = j - 1, k + 1
            if 1 <= k <= 2 and (j < 0 or not (p[j].isascii() and p[j].isalnum())):
                continue
            return True
    return depth != 0


def heading_num(text):
    m = re.match(r'^(\d+(?:\.\d+)*)\s+(.+)$', plain(text).strip())
    return (m.group(1), m.group(2).strip()) if m else (None, plain(text).strip())


def units(blocks):
    """逐段（表格逐格）给出 (位置, 类型, 文字, 所在一级/二级标题编号)。"""
    h1 = h2 = ''
    for b in blocks:
        t = b['t']
        if t == 'h':
            num, _ = heading_num(b['text'])
            if b['level'] == 1:
                h1, h2 = num or '', ''
            elif b['level'] == 2:
                h2 = num or ''
            yield b['src'], 'h', plain(b['text']), h1, h2
        elif t in ('p', 'li', 'caption'):
            yield b['src'], t, plain(b['text']), h1, h2
        elif t == 'table':
            fname = b['src'].split(':')[0]
            for r, row in enumerate(b['rows']):
                src = '%s:%d' % (fname, b['lines'][r]) if r < len(b.get('lines', [])) else b['src']
                for cell in row:
                    if cell not in ('^', '<'):
                        yield src, 'cell', plain(cell), h1, h2


def row_src(b, r):
    fname = b['src'].split(':')[0]
    return '%s:%d' % (fname, b['lines'][r]) if r < len(b.get('lines', [])) else b['src']


def tname(b):
    m = mdparse.RE_TCAP.match(b.get('caption', '') or '')
    return '表' + m.group(1) if m else '（无表题的表格）'


def run(project):
    issues = []

    def add(level, src, msg):
        issues.append((level, src, msg))

    blocks = project.blocks
    us = list(units(blocks))
    # ---- 1 占位符、表题重复 ----
    for src, ph, why in project.unresolved:
        add('错误', src, '%s 生成不了：%s' % (ph, why))
    for key, src in project.dup_tables:
        add('错误', src, '表%s 的表题重复出现' % key)
    # ---- 2 标题：编号连续、11 章标题、标题下无内容、标题后紧跟分页 ----
    last, h1s = [0, 0, 0, 0], []
    for i, b in enumerate(blocks):
        if b['t'] != 'h':
            continue
        num, title = heading_num(b['text'])
        lvl = b['level']
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if nxt is None or (nxt['t'] == 'h' and nxt['level'] <= lvl):
            add('提示', b['src'], '“%s”下面没有内容' % plain(b['text']))
        elif nxt['t'] in ('sect', 'pagebreak'):
            add('提示', b['src'], '标题后面紧跟分页／横纵向切换，标题会留在上一页；把指令移到标题前面')
        if lvl == 1:
            h1s.append((title, b['src']))
        if not num:
            if lvl <= 3:
                add('错误', b['src'], '标题没有编号（或编号后缺空格）：%s' % plain(b['text']))
            continue
        parts = [int(x) for x in num.split('.')]
        if len(parts) != lvl:
            add('错误', b['src'], '%d 级标题应有 %d 段编号：%s' % (lvl, lvl, num))
            continue
        exp = last[:lvl - 1] + [last[lvl - 1] + 1]
        if parts != exp:
            add('错误', b['src'], '标题编号不连续：%s，按顺序应为 %s' % (num, '.'.join(map(str, exp))))
        last = parts + [0] * (4 - lvl)
    titles = [t for t, _ in h1s]
    for i, std in enumerate(STANDARD_CHAPTERS):
        got = titles[i] if i < len(titles) else None
        if got not in std:
            add('提示', h1s[i][1] if i < len(h1s) else '正文',
                '第%d章标题“%s”与 GBZ/T 196—2025 规定的“%s”不一致' % (i + 1, got or '（缺这一章）', '”或“'.join(std)))
    if len(titles) > len(STANDARD_CHAPTERS):
        add('提示', h1s[len(STANDARD_CHAPTERS)][1], '章数多于 GBZ/T 196—2025 规定的 11 章')
    # ---- 2b 表注：②③…另起一行就不算表注了，出稿时会掉到表外 ----
    for i, b in enumerate(blocks[:-1]):
        nxt = blocks[i + 1]
        if b['t'] == 'p' and re.match(r'^(备)?注[：:]', b['text'].replace('**', '')) and \
                nxt['t'] == 'p' and re.match(r'^[②③④⑤⑥⑦⑧⑨⑩]', nxt['text'].replace('**', '').lstrip()):
            add('错误', nxt['src'], '表注的%s另起了一行，出稿时会掉到表外：接在上一行“注：”段后面，不换行' % nxt['text'].lstrip()[:1])
    # ---- 3 评价表：档位、序号 ----
    tables = [b for b in blocks if b['t'] == 'table']
    for b in tables:
        c = judge_col(b)
        if c is not None:
            head = re.sub(r'\s+', '', table_grid(b)[0][b.get('header', 1) - 1][c])
            vals = {v for _, v in judged_rows(b)}
            if not head.startswith('符合性') and vals <= DETECT_VALUES:
                continue  # 检测结果表（合格／不合格、达标／超标）不按评价档位查
            for r, v in judged_rows(b):
                if RE_TODO.search(v):
                    continue  # 还没判的（【待填】）列入待办
                if v == '不符合':
                    add('错误', row_src(b, r), '%s 判了“不符合”：预评价对可研缺项写补充措施，判“按补充措施完善后符合”' % tname(b))
                elif v not in LEVELS:
                    add('错误', row_src(b, r), '%s 符合性“%s”不是规定档位（%s）' % (tname(b), v or '空', '／'.join(LEVELS)))
        grid, mark = table_grid(b)
        h = b.get('header', 1)
        if h >= 1 and grid and re.sub(r'\s+', '', grid[h - 1][0]) == '序号':
            exp = 1
            for r in range(h, len(grid)):
                if mark[r][0]:
                    continue
                if len(mark[r]) > 1 and all(m == '<' for m in mark[r][1:]):
                    exp = None  # 分组行之后可以接着编，也可以从 1 重编
                    continue
                x = re.sub(r'\s+', '', grid[r][0])
                if not x.isdigit():
                    continue
                if exp is None:
                    exp = int(x) if int(x) == 1 else exp
                    if exp is None:
                        exp = int(x)
                if int(x) != exp:
                    add('错误', row_src(b, r), '%s 序号不连续：这一行是 %s，应为 %d' % (tname(b), x, exp))
                    break
                exp += 1
    # ---- 3b 单元格对齐：写明的“对齐=”与列内容不符 ----
    for b in tables:
        al = str(b.get('attrs', {}).get('对齐', ''))
        h = b.get('header', 1)
        grid, mark = table_grid(b)
        if not grid or h < 1:
            continue
        heads = grid[h - 1]
        given = [x.strip() for x in re.split(r'[,，、]', al)] if al else []
        if given and len(given) != len(heads):
            continue
        body = [r for r in range(h, len(grid)) if not all(m == '<' for m in mark[r][1:]) or len(mark[r]) == 1]
        for c, head in enumerate(heads):
            if c > 0 and mark[h - 1][c] == '<':
                continue
            kind = column_kind(head, [b['rows'][r][c] for r in body], strict=True)
            name = re.sub(r'\s+|[（(].*$', '', head)
            if given:
                g = {'中': 'center', '居中': 'center', '左': 'left'}.get(given[c])
                if kind and g and g != kind:
                    add('提示', b['src'], '%s “%s”列写了%s对齐，按列内容应%s（序号、符合性、数值列居中，长描述列靠左）'
                        % (tname(b), name, given[c], '居中' if kind == 'center' else '靠左'))
    # ---- 4 计数句、关键控制点个数、经费 ----
    judged = [b for b in tables if judge_col(b) is not None and b.get('caption')]
    kcp = [b for b in tables if plain(b.get('caption', '')).rstrip().endswith('关键控制点')]
    last_judged = None
    for b in blocks:
        if b['t'] == 'table':
            if judge_col(b) is not None:
                last_judged = b
            continue
        if b['t'] not in ('p', 'li'):
            continue
        text = plain(b['text'])
        for m in RE_COUNT.finditer(text):
            subj = m.group('subj')
            if subj.startswith('表'):
                tb = project.tables.get(subj[1:])
                if tb is None:
                    add('错误', b['src'], '计数句引用的%s不存在' % subj)
                    continue
            else:
                cands = [x for x in judged if subj in plain(x['caption'])]
                tb = cands[0] if len(cands) == 1 else (last_judged if last_judged is not None and (
                    not cands or last_judged in cands) else None)
                if tb is None or (cands and tb not in cands) or (not cands and subj not in plain(tb.get('caption', ''))):
                    continue
            if judge_col(tb) is None:
                continue
            exp = count_clause(tb, subj)
            if m.group(0) != exp:
                add('错误', b['src'], '计数句与%s不一致：写的是“%s”，按表应为“%s”（可改用 {{计数:%s%s}} 自动生成）'
                    % (tname(tb), m.group(0), exp, tname(tb), '' if subj.startswith('表') else ':' + subj))
        if kcp:
            n = body_rows(kcp[-1])
            for m in RE_KCP.finditer(text):
                if int(m.group(1)) != n:
                    add('错误', b['src'], '写了 %s 个关键控制点，%s 列了 %d 个（可改用 {{行数:%s}}）'
                        % (m.group(1), tname(kcp[-1]), n, tname(kcp[-1])))
        for m in RE_FUND.finditer(text):
            tb = project.tables.get(m.group(1))
            if tb is None:
                continue
            grid, _ = table_grid(tb)
            head = grid[tb.get('header', 1) - 1] if grid else []
            col = next((x for x in head if '万元' in x), None)
            if col is None:
                continue
            total = column_sum(tb, col)
            if abs(total - float(m.group(2))) > 0.5:
                add('提示', b['src'], '“约%s万元”与表%s“%s”列合计 %s 万元不一致' % (m.group(2), m.group(1), col, fmt_num(total)))
    # ---- 5 表、图编号：唯一、连续、与所在节一致 ----
    seen, seq, cur_h1, cur_h2 = {}, defaultdict(list), '', ''
    fig_keys = set()
    for b in blocks:
        if b['t'] == 'h':
            num, _ = heading_num(b['text'])
            if b['level'] == 1:
                cur_h1, cur_h2 = num or '', ''
            elif b['level'] == 2:
                cur_h2 = num or ''
            continue
        if b['t'] != 'caption':
            continue
        text = plain(b['text'])
        m = (mdparse.RE_TCAP if b['kind'] == '表' else mdparse.RE_FCAP).match(text)
        if not m or text.startswith('续'):
            continue
        key = m.group(1)
        if b['kind'] == '图':
            fig_keys.add(key)
        prefix, idx = key.rsplit('-', 1)
        if (b['kind'], key) in seen:
            add('错误', b['src'], '%s%s 编号重复（前面 %s 已用过）' % (b['kind'], key, seen[(b['kind'], key)]))
            continue
        seen[(b['kind'], key)] = b['src']
        want = cur_h2 or cur_h1
        if want and prefix != want:
            add('提示', b['src'], '%s%s 在第 %s 节，编号一般以 %s- 开头' % (b['kind'], key, want, want))
        seq[(b['kind'], prefix)].append((int(idx), b['src'], key))
    for (kind, prefix), items in seq.items():
        for k, (idx, src, key) in enumerate(items, 1):
            if idx != k:
                add('错误', src, '%s%s 编号不连续：这里应为 %s%s-%d' % (kind, key, kind, prefix, k))
                break
    for b in blocks:
        if b['t'] == 'img':
            for m in mdparse.RE_FCAP.finditer(b.get('alt', '') or ''):
                fig_keys.add(m.group(1))
    # ---- 6 引用：表、图、附件 ----
    table_keys = set(project.tables)
    dotless = any('.' not in k for k in table_keys | fig_keys)
    n_att = len(project.info.get('附件', []))
    fig_missing = []
    for src, kind, text, h1, h2 in us:
        own = None
        if kind == 'caption':
            m = mdparse.RE_TCAP.match(text) or mdparse.RE_FCAP.match(text)
            own = m.group(1) if m else None
        for m in RE_REF.finditer(text):
            k = m.group(2)
            before = text[m.start() - 1] if m.start() > 0 else ''
            if k == own or (before and (before in '）)》' or before.isascii() and before.isalnum())):
                continue  # “《GBZ 2.1》表1”“报告单（1）表1-1”是外部文件的表
            if '.' not in k and not dotless:
                continue
            if m.group(1) == '表' and k not in table_keys:
                add('错误', src, '引用的表%s不存在（表题要紧挨在表格上一行）' % k)
            elif m.group(1) == '图' and k not in fig_keys:
                fig_missing.append('图' + k)
        for m in RE_ATT.finditer(text):
            if int(m.group(1)) > n_att:
                add('错误', src, '引用了附件%s，项目信息.md 只列了 %d 个附件' % (m.group(1), n_att))
    if fig_missing:
        add('提示', '正文', '这些图号在文字里找不到图题（图题可能画在图片里，请人工确认）：%s'
            % '、'.join(sorted(set(fig_missing), key=fig_missing.index)))
    # ---- 7 用语：通用禁用、项目规则、第4.3节、放射性因素、待填 ----
    rules = project.rules or {}
    common = getattr(project, 'common_rules', set())
    full = '\n'.join(t for _, _, t, _, _ in us)
    for src, kind, text, h1, h2 in us:
        for w, why in GENERIC_ERRORS:
            if w in text:
                add('错误', src, '“%s”：%s' % (w, why))
        for w, why in GENERIC_NOTES:
            if w in text:
                add('提示', src, '“%s”：%s' % (w, why))
        for w in rules.get('禁用', []):
            if w in text:
                add('错误', src, '%s禁用“%s”' % ('通用规则' if w in common else '项目规则', w))
        for w in rules.get('正则禁用', []):
            try:
                hit = re.search(w, text)
            except re.error as e:
                add('错误', '检查规则.md', '正则“%s”写错了：%s' % (w, e))
                rules['正则禁用'] = [x for x in rules['正则禁用'] if x != w]
                continue
            if hit:
                add('错误', src, '%s禁用 /%s/：“%s”' % ('通用规则' if w in common else '项目规则', w, hit.group(0)))
        for w in rules.get('提示', []):
            if w in text:
                add('提示', src, '人工确认“%s”%s' % (w, '（通用规则）' if w in common else ''))
        if h2 == '4.3' and kind in ('p', 'li', 'cell') and RE_CONTROL.search(text):
            add('提示', src, '第4.3节只写评价结论，控制要求（“%s”等）写到第9、10章（判例 PL-GEN-0032）'
                % RE_CONTROL.search(text).group(0))
        rad = RE_RADIATION.search(RE_RAD_HEALTH_EXAM.sub('', text))
        if rad and not (h1 == '1' and RE_RAD_EXCLUDE.search(text)):
            if h1 in ('2', '3'):
                add('提示', src, '“%s”：%s；这里只能作为工程事实（如设备清单）出现，不识别、不评价、不提防护要求'
                    % (rad.group(0), RAD_WHY))
            else:
                add('错误', src, '“%s”：%s。请删掉；项目确有放射源或射线装置的，只在第1章评价范围写一句'
                    '“放射性职业病危害因素另行评价，不在本报告评价范围内”' % (rad.group(0), RAD_WHY))
        for m in RE_TODO.finditer(text):
            add('待办', src, '%s：%s' % (m.group(0), text[:60]))
        # ---- 8 格式：括号引号、重复标点、标准号连接号 ----
        for l, r in PAIRS:
            if unbalanced(text, l, r):
                add('错误', src, '%s%s 不配对：%s' % (l, r, text[:60]))
        for m in RE_DUP_PUNCT.finditer(text):
            add('错误', src, '重复标点“%s”：…%s…' % (m.group(0), text[max(0, m.start() - 15):m.end() + 5]))
        for m in RE_STD.finditer(text):
            if m.group(3) != '—':
                add('错误', src, '标准号“%s”的连接号用“—”' % m.group(0))
    for w in rules.get('必备', []):
        if w not in full:
            add('错误', '检查规则.md', '项目规则要求出现“%s”，正文里没有' % w)
    for k, v in project.info.items():
        vals = v if isinstance(v, list) else [v]
        for x in vals:
            s = ' '.join(x.values()) if isinstance(x, dict) else str(x)
            if RE_TODO.search(s) or (isinstance(x, str) and not x.strip()):
                add('待办', '项目信息.md', '“%s”还没填：%s' % (k, s[:40] or '空'))
    for k in ('项目名称', '报告编号', '报告日期', '评价单位'):
        if not str(project.info.get(k) or '').strip():
            add('错误', '项目信息.md', '缺“%s”' % k)
    return issues


def summary(issues):
    c = {'错误': 0, '提示': 0, '待办': 0}
    for lv, _, _ in issues:
        c[lv] = c.get(lv, 0) + 1
    return c
