"""生成前检查（只读）：判定档位、计数句、编号、引用、口径用语、标点格式、项目规则、待填项。
每条问题是 (级别, 位置, 内容)；级别：错误（必须改）、提示（人工确认）、待办（【待填】【待核】等）。"""
import re
from collections import defaultdict
from pathlib import Path

from .common import LEVELS
from .model import plain, table_grid, judge_col, judged_rows, lead_tally, count_clause, body_rows, column_sum, fmt_num, column_kind
from . import mdparse, doctype

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
NEED_FIGS = [   # (图名, 图题里要有的字, 缺了要什么)；K018、K019、K020
    ('区域位置图', r'区域位置|地理位置|位置示意', '要带审图号的底图，注明来源，标出厂址'),
    ('风频玫瑰图', r'玫瑰', '要当地气象站近三年全年和夏季风向频率统计；正文写进风频表后出稿时自动画'),
    ('总平面布置图', r'总平面|平面布置', '要初设或可研的总平面布置图，放横向页'),
    ('工艺流程图', r'工艺流程|生产流程|流程及职业病危害', '照可研工艺画，标出各环节的职业病危害因素'),
]
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

# ---- 设计专篇（主报告、附件册）另用的规则；预评价不走这些 ----
# 专篇是设计文件：设计文件没写的设施由专篇按标准写成设计要求，不写缺口句（用户 2026-11-04 定）
DESIGN_ERRORS = [
    ('本公司', '正文不用“本公司”，用“本专篇”或无主语句（判例 PL-PRE-0001）'),
    ('需补充完善', '专篇是设计文件：设计文件缺的设施由专篇按标准写成设计要求（名称、规格型号、数量、位置、控制性能、依据条款），不写“需补充完善”'),
    ('投运前', '时间节点统一写“投产前”'),
    ('待取得', '不写“待取得”：缺的参数由专篇按标准算出最低值，厂家才能定的写成“采购技术协议应满足……”'),
]
# “控制效果评价”（前不引后）、“初设没有”“设计单位确认”等缺口句在 标准/设计专篇/检查规则.md，由 model 并进项目规则
DESIGN_NEED_FIGS = [NEED_FIGS[2]]   # 主报告第2.2.1节只放总平面布置图，其余图在附件册第2章
ANNEX_NEED_FIGS = [f for f in NEED_FIGS if f[0] != '总平面布置图']   # 附件册第2章：区域位置图、风频玫瑰图、工艺流程图（figures.PLACES）
# 专篇第4.3.5节要写“不设置防电离辐射设施”的理由：否定说法不报
RE_RAD_NEGATE = re.compile(r'不设置|不设|不涉及|不存在|不产生|没有|无[^，。；]{0,12}(?:电离辐射|放射)|未涉及')
# 外部文件的表、图：前面是这些字的放过（“预评价报告表10.8-1”“附件册表1.1-1”“初设图1.3-1”）
DESIGN_REF_OWNERS = ('预评价报告', '预评价', '附件册', '主报告', '初设', '可研')
RE_ANNEX_REF = re.compile(r'(附件册)?附件\s*(\d+)')
RE_CLAUSE_END = re.compile(r'.*(?:[。；|\n]|<br>)', re.S)


def ref_owner_prefix(before, local):
    """表号属于哪份文件：紧挨着写了“主报告”“预评价报告”等的照写的算；没紧挨着写、本卷又没有这张表的，
    看同一分句里前面最近提到的文件（“主报告第4.4.2节表4.4-2”“主报告表4.3-1第1、2项，表4.4-2第9项”）。
    返回到那个文件名为止的前文（交给 other_tables），不属于别的文件返回 ''。"""
    if before.endswith(DESIGN_REF_OWNERS):
        return before
    if local:
        return ''
    m = RE_CLAUSE_END.match(before)
    start = m.end() if m else 0
    best = max((before.rfind(w, start) + len(w) for w in DESIGN_REF_OWNERS if before.rfind(w, start) >= 0), default=-1)
    return before[:best] if best >= 0 else ''


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


def heading_num(text, annex=False):
    """标题编号和标题文字。annex：附件册的一级标题写“附件1 设计输入资料及差异处理”，编号取 1。"""
    if annex:
        m = doctype.RE_ANNEX_H1.match(plain(text).strip())
        if m:
            return m.group(1), m.group(2).strip()
    m = re.match(r'^(\d+(?:\.\d+)*)\s+(.+)$', plain(text).strip())
    return (m.group(1), m.group(2).strip()) if m else (None, plain(text).strip())


def units(blocks, annex=False):
    """逐段（表格逐格）给出 (位置, 类型, 文字, 所在一级/二级标题编号)。"""
    h1 = h2 = ''
    for b in blocks:
        t = b['t']
        if t == 'h':
            num, _ = heading_num(b['text'], annex and b['level'] == 1)
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


def adopt_col(tb):
    """采纳情况表的“处理方式／采纳情况”列号（doctype.ADOPT_HEADS）；没有返回 None。"""
    return head_col(tb, doctype.ADOPT_HEADS)


def head_col(tb, heads):
    grid, _ = table_grid(tb)
    h = tb.get('header', 1)
    if not grid or h < 1:
        return None
    return next((c for c, x in enumerate(grid[h - 1]) if re.sub(r'\s+|[（(].*$', '', x) in heads), None)


def design_tables(project, add):
    """设计专篇的表格档位：采纳情况只能是 采纳／调整采纳／不采纳，“不采纳”要写理由（WS/T 882 第7.4.6条）；
    预期结论按 doctype.EXPECT_LEVELS 四项（符合，或条件结论；不写“不符合”，WS/T 882 第4.2.4条）。"""
    for b in [x for x in project.blocks if x['t'] == 'table']:
        grid, _ = table_grid(b)
        c = adopt_col(b)
        if c is not None:
            why = head_col(b, ('理由', '不采纳理由', '理由及说明', '说明'))
            for r, v in judged_rows(b, c):
                if RE_TODO.search(v):
                    continue
                if v not in doctype.ADOPT_LEVELS:
                    add('错误', row_src(b, r), '%s 处理方式“%s”不是规定写法（%s）' % (tname(b), v or '空', '／'.join(doctype.ADOPT_LEVELS)))
                elif v == '不采纳' and (why is None or not re.sub(r'[\s—\-－/／]', '', grid[r][why])):
                    add('错误', row_src(b, r), '%s 这一条“不采纳”没写理由：对未采纳的措施应当说明理由（WS/T 882 第7.4.6条）' % tname(b))
        c = head_col(b, doctype.EXPECT_HEADS)
        if c is not None:
            for r, v in judged_rows(b, c):
                if RE_TODO.search(v):
                    continue
                if v == '不符合':
                    add('错误', row_src(b, r), '%s 预期结论写了“不符合”：设计要保证投产后达到国家职业卫生标准，'
                        '推算超限的先看工程措施能否达到；达不到的按口径 D17 先写限制接触时间使 LEX,8h 符合（结论“限制接触时间后符合”），'
                        '仍超限的噪声写“超限，须个体防护”并另写未计护听器的 LEX,8h 和佩戴后有效声级（WS/T 882 第4.2.4条、第7.5条）' % tname(b))
                elif v not in doctype.EXPECT_LEVELS:
                    add('错误', row_src(b, r), '%s 预期结论“%s”不是规定写法（%s）' % (tname(b), v or '空', '／'.join(doctype.EXPECT_LEVELS)))
                elif v == '超限，须个体防护':
                    fc = head_col(b, ('职业病危害因素', '危害因素', '因素'))
                    if fc is not None and '噪声' not in grid[r][fc]:
                        add('错误', row_src(b, r), '%s 预期结论“超限，须个体防护”只准用于噪声：化学因素、粉尘、高温不能用个体防护抵扣 '
                            'GBZ 2.1、GBZ 2.2 限值，写工程措施或工艺调整（口径 D17）' % tname(b))


# ---------------- 设计专篇的篇幅和可读性（口径 D17、D18、D19；全部“提示”，只有行首“- ”是“错误”） ----------------
RE_HAN = re.compile(r'[\u4e00-\u9fff]')
RE_DASH_ITEM = re.compile(r'^[-－–—]\s')
RE_TAG = re.compile(r'（(?:采购指标|计算值)）')
RE_LIMIT_AS_SPEC = re.compile(r'[≤<＜]=?\s*85\s*dB|不(?:大|超)于\s*85\s*dB|WBGT|接触限值')
RE_UPPER = re.compile(r'(?:[≤＜<]=?|不(?:大|超)于|小于)\s*(\d+(?:\.\d+)?)')
RE_UNIT_TAIL = re.compile(r'L\s*EX\s*,?\s*(?:8|40)\s*h|LEX,?\s*(?:8|40)h|(?<![\d.])(?:8|40)\s*h\b|TWA|PC-TWA|STEL|MAC')
PARA_MAX, CELL_MAX, COLS_MAX = 250, 100, 7
LIST_SHOW = 3


def han(text):
    return len(RE_HAN.findall(text or ''))


def _nums(s):
    return tuple(sorted(re.findall(r'\d+(?:\.\d+)?', RE_UNIT_TAIL.sub('', s or ''))))


def _list_hint(add, level, items, head, tail=''):
    """items: [(位置, 说明)]；合并成一条，写出总数和前几处。"""
    if not items:
        return
    show = '；'.join('%s %s' % x for x in items[:LIST_SHOW])
    add(level, items[0][0], '%s（共 %d 处）：%s%s%s' % (head, len(items), show, '……' if len(items) > LIST_SHOW else '', tail))


def design_style(project, add, annex):
    """篇幅、段落和单元格长度、列数、行首“- ”、标签过多、指标可行性、预期值等于限值。"""
    blocks = project.blocks
    rtype = project.type
    root = project.root
    tier = doctype.design_tier(root)
    # ---- 篇幅：每个文件的汉字数（含表格，不含注释和指令行）----
    count, order = defaultdict(int), []
    for b in blocks:
        f = b['src'].split(':')[0]
        if f not in order:
            order.append(f)
        t = b['t']
        if t in ('h', 'p', 'li', 'caption'):
            count[f] += han(plain(b['text']))
        elif t == 'table':
            count[f] += sum(han(plain(c)) for row in b['rows'] for c in row if c not in ('^', '<'))
    for f in order:
        bud = doctype.budget_for(root, ('附件册/正文/' if annex else '正文/') + f)
        if bud and count[f] > bud * doctype.OVER_RATIO:
            add('提示', f, '篇幅超预算：%s 共 %d 字（汉字，含表），%s档预算约 %d 字，超过 1.2 倍（%d 字）：'
                '先删重复的表，再把过程性内容移附件册，再把逐台逐地点明细归并，最后删空话（口径 D19）'
                % (f, count[f], tier, bud, int(bud * doctype.OVER_RATIO)))
    # ---- 段落、单元格长度；行首“- ”；标签过多 ----
    long_p, long_c, dash, tags = defaultdict(list), defaultdict(list), [], defaultdict(list)
    for src, kind, text, _, _ in units(blocks, annex):
        f = src.split(':')[0]
        if kind in ('p', 'li'):
            if RE_DASH_ITEM.match(text):
                dash.append(src)
            n = han(text)
            if n > PARA_MAX:
                long_p[f].append((src, '%d 字' % n))
        elif kind == 'cell':
            n = han(text)
            if n > CELL_MAX:
                long_c[f].append((src, '%d 字' % n))
            if RE_TAG.search(text):
                tags[f].append(src)
    for src in dash:
        add('错误', src, '正文行首写了“- ”：Word 里会印成字面的横杠。并列要求改成（1）（2）……条目（口径 D19）')
    for f, items in long_p.items():
        _list_hint(add, '提示', items, '%s 有段落超过 %d 字' % (f, PARA_MAX), '：一段只说一件事，拆开或改成（1）（2）条目（口径 D19）')
    for f, items in long_c.items():
        _list_hint(add, '提示', items, '%s 有单元格超过 %d 字' % (f, CELL_MAX), '：单元格只写短语或一两个数，说明放表注或表后条目（口径 D19）')
    for f, items in tags.items():
        if len(items) > 3:
            add('提示', items[0], '%s 有 %d 个单元格写了“（采购指标）”“（计算值）”：单元格只写数值，改在表注按项号说明哪几项是计算值、哪几项是采购技术要求（口径 D03、D19）'
                % (f, len(items)))
    # ---- 主报告表格列数 ----
    if rtype == doctype.DESIGN:
        wide = [(b['src'], '%s %d 列' % (tname(b), len(b['rows'][0]))) for b in blocks
                if b['t'] == 'table' and b['rows'] and len(b['rows'][0]) > COLS_MAX]
        _list_hint(add, '提示', wide, '主报告表格超过 %d 列' % COLS_MAX, '：拆成窄表，或把次要列并进表注（口径 D19）')
    # ---- 指标可行性：“采购”与接触限值同处（口径 D03、D17）----
    feas = []
    for b in blocks:
        if b['t'] in ('p', 'li'):
            tx = plain(b['text'])
            if '采购' in tx and RE_LIMIT_AS_SPEC.search(tx):
                feas.append((b['src'], tx[:20]))
        elif b['t'] == 'table':
            for r, row in enumerate(b['rows']):
                tx = ' '.join(plain(c) for c in row if c not in ('^', '<'))
                if '采购' in tx and RE_LIMIT_AS_SPEC.search(tx):
                    feas.append((row_src(b, r), tname(b)))
    _list_hint(add, '提示', feas, '“采购”与“≤85 dB(A)”“WBGT”“接触限值”出现在同一行（段）',
               '：接触限值不作设备或作业位置的采购指标；源强已高于限值的，先算所需降噪量并核对措施可达范围，达不到的不写作业位置达标值，'
               '改写声源和传播途径措施、值班室按房间设计值、限制接触时间、护听器（口径 D03、D17）')
    # ---- 预期接触水平与同行职业接触限值相同（口径 D17）----
    same = []
    for b in blocks:
        if b['t'] != 'table':
            continue
        grid, _ = table_grid(b)
        h = b.get('header', 1)
        if not grid or h < 1:
            continue
        ce = next((c for c, x in enumerate(grid[h - 1]) if '预期' in x and '结论' not in x), None)
        cl = next((c for c, x in enumerate(grid[h - 1]) if '接触限值' in x), None)
        if ce is None or cl is None:
            continue
        for r in range(h, len(grid)):
            e, l = re.sub(r'\s+', '', grid[r][ce]), re.sub(r'\s+', '', grid[r][cl])
            lim = {float(x) for x in _nums(l)}
            if not e or not lim:
                continue
            up = {float(m.group(1)) for m in RE_UPPER.finditer(RE_UNIT_TAIL.sub('', e))}
            if e == l or up & lim:
                same.append((row_src(b, r), '%s“%s”' % (tname(b), grid[r][ce][:24])))
    _list_hint(add, '提示', same, '预期接触水平与同行职业接触限值相同',
               '：预期值不得直接写成限值本身，写类比值、计算值或范围，并注明来源（口径 D17）')


def _adopt_counts(tables):
    """逐条采纳明细的条数、各档条数和还没写处理方式的条数（只数有“处理方式”列的表、不数分组行）。"""
    n, by, todo = 0, defaultdict(int), 0
    for tb in tables:
        c = adopt_col(tb)
        if c is None:
            continue
        for _, v in judged_rows(tb, c):
            n += 1
            if not v or RE_TODO.search(v):
                todo += 1          # 附件册还是骨架或没写完：这一格是【待填】
                continue
            by[v] += 1
    return n, by, todo


def pre_items(tb):
    """预评价措施表的条数：不含表头、合计行、整行合并的分组行（“一、职业病危害识别”）和纵向合并的续行。"""
    grid, mark = table_grid(tb)
    n = 0
    for r in range(tb.get('header', 1), len(grid)):
        first = re.sub(r'\s+', '', grid[r][0]) if grid[r] else ''
        if first in ('合计', '总计', '小计') or (mark[r] and mark[r][0] == '^'):
            continue
        if len(mark[r]) > 1 and all(m == '<' for m in mark[r][1:]):
            continue
        n += 1
    return n


def other_volume(project):
    """设计专篇另一卷的 Project（主报告→附件册，附件册→主报告）；没有返回 (None, '')，读不了返回 (None, 原因)。
    同一次检查里只读一次（缓存在 project 上）。"""
    if hasattr(project, '_other_vol'):
        return project._other_vol
    rtype = getattr(project, 'type', doctype.PRE)
    root = doctype.annex_root(project.root) if rtype == doctype.DESIGN else (
        doctype.main_root(project.root) if rtype == doctype.ANNEX else None)
    out = (None, '')
    if root is not None:
        try:
            from .model import Project
            out = (Project(root).resolve(), '')
        except Exception as e:  # noqa: BLE001  另一卷格式坏了（正在写、正在改）：本卷照查，只提示
            out = (None, str(e)[:200] or type(e).__name__)
    project._other_vol = out
    return out


def design_adopt_numbers(project, add):
    """主报告表4.8-1 的条数＝附件册附件6 逐条明细行数（各档也对上）；与预评价表10.8-1 等的条数对不上只提示。
    附件册不存在时只查表4.8-1 自己各行加得对不对。"""
    key = doctype.ADOPT_SUMMARY
    tb = project.tables.get(key)
    if tb is None:
        add('提示', '正文', '没找到表%s（预评价报告对策措施及建议的采纳情况汇总，WS/T 882 第7.4.6条）' % key)
        return
    grid, mark = table_grid(tb)
    h = tb.get('header', 1)
    head = [re.sub(r'\s+', '', x) for x in grid[h - 1]]
    cols = {k: head.index(k) for k in ['条数'] + doctype.ADOPT_LEVELS if k in head}
    if '条数' not in cols:
        add('提示', tb['src'], '表%s 没有“条数”列，核对不了采纳条数' % key)
        return
    total, sums, blank = 0, defaultdict(float), set()   # blank：有行还没填数的档，不拿去对附件册
    for r in range(h, len(grid)):
        if mark[r][0] == '^' or re.sub(r'\s+', '', grid[r][0]).startswith(('合计', '总计')):
            continue
        vals = {k: to_num(grid[r][c]) for k, c in cols.items()}
        if vals['条数'] is None:
            continue
        total += vals['条数']
        parts = [vals.get(k) for k in doctype.ADOPT_LEVELS if k in cols]
        for k in doctype.ADOPT_LEVELS:
            if k in cols and vals.get(k) is not None:
                sums[k] += vals[k]
            elif k in cols:
                blank.add(k)
        if len(parts) == len(doctype.ADOPT_LEVELS) and None not in parts and sum(parts) != vals['条数']:
            add('错误', row_src(tb, r), '表%s“%s”这一行：采纳＋调整采纳＋不采纳＝%s，与条数 %s 不符'
                % (key, grid[r][0], fmt_num(sum(parts)), fmt_num(vals['条数'])))
    ap, why = other_volume(project)
    if why:
        add('提示', '附件册', '附件册读不了，没核对采纳条数：%s' % why)
    if ap is not None:
        pre = doctype.ANNEX_ADOPT_CHAPTER
        detail = [t_ for k, t_ in ap.tables.items() if re.match(r'^%s[.\-]' % pre, k)]
        n, by, todo = _adopt_counts(detail)
        if not n:
            add('提示', '附件册', '附件册附件%s 没有带“处理方式”列的逐条采纳明细表，核对不了表%s 的条数' % (pre, key))
        elif todo:
            # 写主报告时附件册还是骨架（附件册在主报告全部写好后才写）：不拿骨架的行数报错，免得把对的数改成骨架的
            add('提示', '附件册', '附件册附件%s 还有 %d 条没写处理方式，没核对表%s 的条数（两卷都写好后再核）' % (pre, todo, key))
        else:
            if n != total:
                add('错误', tb['src'], '表%s 条数合计 %s，附件册附件%s 逐条明细是 %d 条' % (key, fmt_num(total), pre, n))
            for k in doctype.ADOPT_LEVELS:
                if k in cols and k not in blank and sums[k] != by.get(k, 0):
                    add('错误', tb['src'], '表%s“%s”合计 %s，附件册附件%s 里是 %d 条' % (key, k, fmt_num(sums[k]), pre, by.get(k, 0)))
    pre_root = doctype.pre_root(project.root)
    if pre_root is None:
        add('提示', '流水线设置.json', '没登记承接的预评价项目（{"预评价": "文件夹"}），没核对采纳条数与预评价措施条数')
        return
    try:
        from .model import Project
        pp = Project(pre_root)
    except Exception as e:  # noqa: BLE001
        add('提示', '流水线设置.json', '预评价项目读不了：%s' % e)
        return
    got = [(k, pre_items(pp.tables[k])) for k in doctype.PRE_ADOPT_TABLES if k in pp.tables]
    for r in range(h, len(grid)):   # 分组行写了“预评价报告表10.8-1”的，条数与该表行数对照
        m = re.search(r'表\s*(\d+(?:\.\d+)*-\d+)', grid[r][0])
        n = to_num(grid[r][cols['条数']])
        if m and n is not None and m.group(1) in pp.tables and pre_items(pp.tables[m.group(1)]) != n:
            add('提示', row_src(tb, r), '表%s“%s”条数 %s，预评价报告表%s 有 %d 条' % (
                key, grid[r][0], fmt_num(n), m.group(1), pre_items(pp.tables[m.group(1)])))
    if not got:
        add('提示', tb['src'], '预评价报告里没找到表%s，没核对采纳条数' % '、表'.join(doctype.PRE_ADOPT_TABLES))
    elif sum(n for _, n in got) != total:
        add('提示', tb['src'], '表%s 条数合计 %s，预评价报告%s 共 %d 条：确认逐条都有处理方式'
            % (key, fmt_num(total), '、'.join('表%s %d 条' % x for x in got), sum(n for _, n in got)))


def to_num(s):
    m = re.search(r'-?\d+(?:\.\d+)?', s or '')
    return float(m.group()) if m else None


def other_tables(project, before):
    """“附件册表X”（主报告里）、“主报告表X”（附件册里）：返回 (卷名, 另一卷的表号集合)；别的外部表或另一卷读不了返回 None。"""
    rtype = getattr(project, 'type', doctype.PRE)
    name = '附件册' if rtype == doctype.DESIGN else ('主报告' if rtype == doctype.ANNEX else '')
    if not name or not before.endswith(name):
        return None
    ap, _ = other_volume(project)
    return (name, set(ap.tables)) if ap is not None else None


def annex_count(project):
    """主报告引用“附件册附件N”时核对用：附件册一级标题个数；附件册自己就数自己的。没有附件册返回 None。"""
    if project.type == doctype.ANNEX:
        return sum(1 for b in project.blocks if b['t'] == 'h' and b['level'] == 1)
    ap, _ = other_volume(project)
    if ap is None:
        return None             # 没有附件册，或附件册读不了（读不了的在采纳条数核对里提示）
    return sum(1 for b in ap.blocks if b['t'] == 'h' and b['level'] == 1)


RE_ANNEX4_SEC = re.compile(r'附件册附件\s*4\s*第\s*(4\.\d+)\s*节')


def annex4_sections(project):
    """主报告核对“附件册附件4第4.N节”用：附件册里的二级标题节号集合；没有附件册返回 None。"""
    ap, _ = other_volume(project)
    if ap is None:
        return None
    return {m.group(1) for b in ap.blocks if b['t'] == 'h' and b['level'] == 2
            for m in [re.match(r'(4\.\d+)(?:\s|$)', plain(b['text']).strip())] if m}


def run(project):
    issues = []

    def add(level, src, msg):
        issues.append((level, src, msg))

    rtype = getattr(project, 'type', doctype.PRE)
    design = rtype != doctype.PRE
    annex = rtype == doctype.ANNEX
    blocks = project.blocks
    us = list(units(blocks, annex))
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
        if annex and lvl == 1:
            num, title = heading_num(b['text'], True)
            if not doctype.RE_ANNEX_H1.match(plain(b['text']).strip()):
                add('错误', b['src'], '附件册的一级标题写“附件N 标题”（如“附件1 设计输入资料及差异处理”）：%s' % plain(b['text']))
                continue
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
    if not design:
        for i, std in enumerate(STANDARD_CHAPTERS):
            got = titles[i] if i < len(titles) else None
            if got not in std:
                add('提示', h1s[i][1] if i < len(h1s) else '正文',
                    '第%d章标题“%s”与 GBZ/T 196—2025 规定的“%s”不一致' % (i + 1, got or '（缺这一章）', '”或“'.join(std)))
        if len(titles) > len(STANDARD_CHAPTERS):
            add('提示', h1s[len(STANDARD_CHAPTERS)][1], '章数多于 GBZ/T 196—2025 规定的 11 章')
    elif doctype.conf(rtype)['章']:
        chs, basis = doctype.conf(rtype)['章'], doctype.conf(rtype)['依据']
        for i, std in enumerate(chs):
            got = titles[i] if i < len(titles) else None
            if got not in std:
                add('提示', h1s[i][1] if i < len(h1s) else '正文',
                    '第%d章标题“%s”与 %s 的“%s”不一致' % (i + 1, got or '（缺这一章）', basis, '”或“'.join(std)))
        if len(titles) > len(chs):
            add('提示', h1s[len(chs)][1], '章数多于 %s 的 %d 章' % (basis, len(chs)))
    # ---- 2b 表注：②③…另起一行就不算表注了，出稿时会掉到表外 ----
    for i, b in enumerate(blocks[:-1]):
        nxt = blocks[i + 1]
        if b['t'] == 'p' and re.match(r'^(备)?注[：:]', b['text'].replace('**', '')) and \
                nxt['t'] == 'p' and re.match(r'^[②③④⑤⑥⑦⑧⑨⑩]', nxt['text'].replace('**', '').lstrip()):
            add('错误', nxt['src'], '表注的%s另起了一行，出稿时会掉到表外：接在上一行“注：”段后面，不换行' % nxt['text'].lstrip()[:1])
    # ---- 3 评价表：档位、序号 ----
    tables = [b for b in blocks if b['t'] == 'table']
    if design:
        design_tables(project, add)
        design_style(project, add, annex)
        try:
            from . import formula
        except ImportError:          # formula.py 由公式功能提供；没有就不查公式
            formula = None
        if formula is not None:
            formula.check(blocks, add, annex)
    for b in tables:
        c = judge_col(b) if not design else None   # 专篇不按预评价档位查，见 design_tables
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
                sub = re.fullmatch(r'(\d+)\.\d+', x)
                if sub and exp is not None:     # “35.1”“35.2”：第35项的分项，算一个序号
                    major = int(sub.group(1))
                    if major == exp:
                        exp += 1
                    elif major != exp - 1:
                        add('错误', row_src(b, r), '%s 序号不连续：这一行是 %s，应为 %d' % (tname(b), x, exp))
                        break
                    continue
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
            cnt = None if judge_col(tb) is not None else lead_tally(tb)
            if judge_col(tb) is None and cnt is None:
                continue
            exp = count_clause(tb, subj, cnt)
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
            num, _ = heading_num(b['text'], annex and b['level'] == 1)
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
    n_annex = annex_count(project) if design else None
    secs = None
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
            if text[max(0, m.start() - 2):m.start()] in ('可研', '初设'):
                continue  # “引自初设图1.3-1”“可研表3.2-1”是资料里的图表
            owner = ref_owner_prefix(text[:m.start()], k in (table_keys if m.group(1) == '表' else fig_keys)) if design else ''
            if owner:
                vol = other_tables(project, owner)
                if vol is not None and m.group(1) == '表' and k not in vol[1]:
                    add('错误', src, '引用的%s表%s不存在（%s里没有这张表）' % (vol[0], k, vol[0]))
                continue  # 专篇引“预评价报告表10.8-1”：是另一份文件的表；另一卷的表上面核过
            if '.' not in k and not dotless:
                continue
            if m.group(1) == '表' and k not in table_keys:
                add('错误', src, '引用的表%s不存在（表题要紧挨在表格上一行）' % k)
            elif m.group(1) == '图' and k not in fig_keys:
                fig_missing.append('图' + k)
        if rtype == doctype.DESIGN and RE_ANNEX4_SEC.search(text):
            if secs is None:
                secs = annex4_sections(project) or set()
            for m in RE_ANNEX4_SEC.finditer(text):
                if secs and m.group(1) not in secs:
                    add('提示', src, '引用了“附件册附件4第%s节”，附件册附件4 没有“## %s”这一节' % (m.group(1), m.group(1)))
        if design:   # 专篇的附件在附件册：按附件册一级标题个数核对（WS/T 882 第8.1.3条）
            for m in RE_ANNEX_REF.finditer(text):
                if text[:m.start()].endswith(('预评价报告', '预评价') + DESIGN_REF_OWNERS + ('初步设计', '可行性研究报告')):
                    continue
                if n_annex is not None and int(m.group(2)) > n_annex:
                    add('错误', src, '引用了附件%s，附件册只有 %d 个附件（一级标题“附件N ……”）' % (m.group(2), n_annex))
            continue
        for m in RE_ATT.finditer(text):
            if int(m.group(1)) > n_att:
                add('错误', src, '引用了附件%s，项目信息.md 只列了 %d 个附件' % (m.group(1), n_att))
    if fig_missing:
        add('提示', '正文', '这些图号在文字里找不到图题（图题可能画在图片里，请人工确认）：%s'
            % '、'.join(sorted(set(fig_missing), key=fig_missing.index)))
    # ---- 6b 第2章该有的图（样板第2章：区域位置图、风频玫瑰图、总平面布置图、工艺流程图） ----
    ch2 = [Path(f).name for f in project.files if Path(f).name.startswith('02')]
    need_figs = {doctype.DESIGN: DESIGN_NEED_FIGS, doctype.ANNEX: ANNEX_NEED_FIGS}.get(rtype, NEED_FIGS)
    if ch2:
        names = ' '.join(plain(b.get('alt') or '') if b['t'] == 'img' else plain(b['text']) for b in blocks
                         if b['src'].startswith('02') and (b['t'] == 'img' or b['t'] == 'caption' and b.get('kind') == '图'))
        for name, pat, need in need_figs:
            if not re.search(pat, names):
                add('待办', ch2[0], '第2章没有%s（%s）' % (name, need))
    # ---- 7 用语：通用禁用、项目规则、第4.3节、放射性因素、待填 ----
    rules = project.rules or {}
    common = getattr(project, 'common_rules', set())
    typed = getattr(project, 'type_rules', set())
    generic = DESIGN_ERRORS if design else GENERIC_ERRORS

    def origin(w):
        return '通用规则' if w in common else ('%s规则' % doctype.DESIGN if w in typed else '项目规则')

    full = '\n'.join(t for _, _, t, _, _ in us)
    for src, kind, text, h1, h2 in us:
        for w, why in generic:
            if w in text:
                add('错误', src, '“%s”：%s' % (w, why))
        for w, why in GENERIC_NOTES:
            if w in text:
                add('提示', src, '“%s”：%s' % (w, why))
        for w in rules.get('禁用', []):
            if w in text:
                add('错误', src, '%s禁用“%s”' % (origin(w), w))
        for w in rules.get('正则禁用', []):
            try:
                hit = re.search(w, text)
            except re.error as e:
                add('错误', '检查规则.md', '正则“%s”写错了：%s' % (w, e))
                rules['正则禁用'] = [x for x in rules['正则禁用'] if x != w]
                continue
            if hit:
                add('错误', src, '%s禁用 /%s/：“%s”' % (origin(w), w, hit.group(0)))
        for w in rules.get('提示', []):
            if w in text:
                add('提示', src, '人工确认“%s”%s' % (w, '（通用规则）' if w in common else (
                    '（%s规则）' % doctype.DESIGN if w in typed else '')))
        if not design and h2 == '4.3' and kind in ('p', 'li', 'cell') and RE_CONTROL.search(text):
            add('提示', src, '第4.3节只写评价结论，控制要求（“%s”等）写到第9、10章（判例 PL-GEN-0032）'
                % RE_CONTROL.search(text).group(0))
        rad = RE_RADIATION.search(RE_RAD_HEALTH_EXAM.sub('', text))
        if rad and design and (RE_RAD_NEGATE.search(text) or RE_RAD_EXCLUDE.search(text)):
            rad = None   # 专篇第4.3.5节“不设置防电离辐射设施”的理由、第3.1.5节“不涉及电离辐射”要写
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
    if rtype == doctype.DESIGN:
        design_adopt_numbers(project, add)
    return issues


def summary(issues):
    c = {'错误': 0, '提示': 0, '待办': 0}
    for lv, _, _ in issues:
        c[lv] = c.get(lv, 0) + 1
    return c
