"""提炼标准：把用户历次意见、操作手册、判例和定稿成品的写法提炼成 标准/ 下的文件。

三步，每步做完就写盘；--只做 时别的步骤的文件不动：
- 口径：feedback、user 记忆原文＋各项目检查规则＋可读性改写要求＋判定规则与口径 → 用户口径.md（K 号）、
  审稿清单.md（R 号）、通用检查规则.md。检查词由程序验证：两个定稿项目、两份骨架里有命中的，
  禁用降为提示、正则禁用不收（骨架新建的项目要能零错误出稿，自测查这个）；写错的正则不收。
- 各章：按卡组并行，每组发两份骨架本章文件、本章写法、判定规则和常见错误里与本章有关的行、
  本章判例（每组材料不超过 15 万字）、成品本章的标题表题表头 → 要点卡七节；超过 4000 字的让模型压一次。
- 补漏：标准全部文件＋操作手册第 2、8、9 节＋判例卡标题 → 补到哪个文件、补什么；文件间矛盾以用户口径为准改。
"""
import datetime
import hashlib
import json
import re
from pathlib import Path

from .. import config, llm, rules, tools
from . import common as C
from .review import pmap
from .revise import count_hits

REF = config.ROOT / 'references'
CARDS = config.STD / '各章要点'
COMMON = config.STD / '通用检查规则.md'
STEPS = ('口径', '各章', '补漏')
K_HEADER = ('# 用户口径\n\n<!-- 用户定过的写法，全部项目适用。新意见由“改稿”追加，编号只增不改；'
            '推翻旧条时，旧条标题后加“（已被 K0xx 取代）”。 -->\n')
MAX_K, MAX_R, MAX_DIMS, MAX_CARD, MAX_FILLED, BUDGET = 15000, 6000, 10, 4000, 4500, 150000
# 用户 10-31 定的几件事：用户口径里查不到这些词就在结果里报出来
MUST = [(r'待补资料', '待补资料清单不进报告'), (r'可研没有|可研没写|可研未写', '可研没写的照写“可研没有……”'),
        (r'待核', '待核、待补充、确认后全清'), (r'消防', '消防内容全删'), (r'医疗救护', '医疗救护和应急撤离照留'),
        (r'计量', '安泰公司情况（1.9 批准设立、检测设备和计量检定、人员专业）不能删'),
        (r'口径表', '不影响危害识别的可研矛盾在口径表写一次'), (r'编者', '不写编者视角、资料边界、条号堆砌')]
GROUPS = [
    {'key': '01-02', '章': [1, 2], '方法': '', '文件': ['01.md', '02.md']},
    {'key': '03类比', '章': [3], '方法': '类比法', '文件': ['03_类比法.md']},
    {'key': '03风险', '章': [3], '方法': '风险评估法', '文件': ['03_风险评估法.md']},
    {'key': '04-05', '章': [4, 5], '方法': '', '文件': ['04.md', '05.md']},
    {'key': '06-07', '章': [6, 7], '方法': '', '文件': ['06.md', '07.md']},
    {'key': '08-09', '章': [8, 9], '方法': '', '文件': ['08.md', '09.md']},
    {'key': '10-11', '章': [10, 11], '方法': '', '文件': ['10.md', '11.md']},
]
NAMES = {'01.md': '总论', '02.md': '建设项目工程分析', '03_类比法.md': '类比企业（项目）调查与分析',
         '03_风险评估法.md': '职业健康风险评估分析与评价', '04.md': '职业病危害因素及危害程度分析',
         '05.md': '职业病防护设施分析与评价', '06.md': '应急救援措施的分析与评价',
         '07.md': '个人使用的职业病防护用品的分析与评价', '08.md': '职业卫生管理的分析与评价',
         '09.md': '职业病危害关键控制点分析', '10.md': '职业病防治措施的补充建议', '11.md': '结论与建议'}
# 要点卡七节：(结构化输出的字段, 卡里的节标题)。标题开头一字不差，改稿按“## 4 红线”找红线节
SECS = [('必答问题', '1 必答问题'), ('必备', '2 必备的节、表和表头'), ('怎么判', '3 怎么判'), ('红线', '4 红线'),
        ('常见错误', '5 常见错误'), ('跨章衔接', '6 跨章衔接'), ('方法差异', '7 方法差异')]


# ---------------- 读材料 ----------------

def _fm(text):
    return text.split('---', 2)[1] if text.startswith('---') and text.count('---') >= 2 else ''


def _field(fm, name):
    m = re.search(r'^\s*%s:\s*(.*)$' % re.escape(name), fm, re.M)
    return m.group(1).strip().strip('"\'') if m else ''


def memories():
    """各记忆目录里 type 为 feedback、user 的记忆 [(文件名, 日期, 说明, 正文)]，按日期排；正文相同的只留日期最新的一份。"""
    rows = []
    for f in (Path.home() / '.claude' / 'projects').glob('*/memory/*.md'):
        try:
            t = tools.read(f)
        except (OSError, UnicodeDecodeError):
            continue
        fm = _fm(t)
        if _field(fm, 'type') not in ('feedback', 'user'):
            continue
        try:      # frontmatter 里是 UTC，换成本机时间：用户说的“10-31 02:09”是本机时间
            d = datetime.datetime.fromisoformat(_field(fm, 'modified')).astimezone().strftime('%Y-%m-%d %H:%M')
        except ValueError:
            d = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        rows.append((f.name, d, _field(fm, 'description'), t.split('---', 2)[2].strip()))
    best = {}
    for r in sorted(rows, key=lambda r: r[1], reverse=True):
        best.setdefault(hashlib.md5(r[3].encode('utf-8')).hexdigest(), r)
    return sorted(best.values(), key=lambda r: (r[1], r[0]))


COLD = Path.home() / 'Desktop/200MW超临界发电项目/收尾工作区/_冷轧参考/晋钢冷轧预评价_工作区/报告工程/晋钢冷轧'


def _project_docs():
    """各登记项目（和没登记的冷轧）的 检查规则.md、审查/可读性改写要求.md（在项目文件夹或上一级的 审查/ 里）。"""
    out = []
    rows = tools.registered()
    if (COLD / '检查规则.md').exists() and not any(Path(r['文件夹']).expanduser() == COLD for r in rows):
        rows = rows + [{'项目': '晋钢冷轧（未登记）', '文件夹': str(COLD)}]
    for r in rows:
        root = Path(r['文件夹']).expanduser()
        if (root / '检查规则.md').exists():
            out.append(C.doc('项目检查规则 %s' % r['项目'], tools.read(root / '检查规则.md')))
        for q in (root / '审查' / '可读性改写要求.md', root.parent / '审查' / '可读性改写要求.md'):
            if q.exists():
                day = datetime.date.fromtimestamp(q.stat().st_mtime).isoformat()
                out.append(C.doc('%s 可读性改写要求（%s）' % (r['项目'], day), tools.read(q)))
                break
    return out


def _check_roots():
    """(定稿项目, 两份骨架)：检查词在这些正文里有命中就是太宽。骨架新建项目后要零错误（自测查这个），
    占位符【待填】【待核】在骨架正文里；马沟示例豁免了全部通用规则，不拿来验证。"""
    extra = [config.ROOT / 'templates' / '项目骨架', config.ROOT / 'templates' / '项目骨架_风险评估法']
    return tools.finished_projects(), [p for p in extra if (p / '正文').is_dir()]
def _today():
    return datetime.date.today().isoformat()


def _clean(s):
    """一行文字：去换行、行首的 # 和列表符，免得排版后变成标题或嵌套列表。"""
    s = re.sub(r'\s*\n\s*', ' ', str(s or '')).strip()
    return re.sub(r'^[#\-*\s]+', '', s)


def _chapters(text):
    """文字里提到的章号集合；没写章号、写“全文”“任意章节”这类的返回 None（不限章）。
    标准的条号（GBZ/T 196—2025 第10.2节、第5.1条）不算章号。"""
    t = re.sub(r'(?:GBZ/T|GBZ|GB/T|GB|WS/T|AQ/T|AQ|HJ|DB\d*/T)\s*[\d.]+(?:\s*[—\-–]\s*\d{4})?'
               r'(?:\s*(?:第\s*)?[\d.]+\s*[条节款])?', ' ', text or '')
    t = re.sub(r'第\s*[\d.]+\s*条', ' ', t)
    out = set()
    for inner in re.findall(r'第\s*([0-9、，,\s～~—–\-－至到和及]+?)\s*章', t) + \
            re.findall(r'(?<![\d.])(\d{1,2}\s*[～~—–\-－至到]\s*\d{1,2})\s*章', t):
        for a, b in re.findall(r'(\d{1,2})\s*(?:[～~—–\-－至到]\s*(\d{1,2}))?', inner):
            lo, hi = int(a), int(b or a)
            out.update(range(lo, hi + 1) if lo <= hi <= 11 else [lo])
    for m in re.finditer(r'(?<![\d.A-Za-z])(?<![A-Za-z] )(?<![A-Za-z]/)(\d{1,2})\.[\dx]', t):
        out.add(int(m.group(1)))
    out = {x for x in out if 1 <= x <= 11}
    if not out and re.search(r'全文|全部|任意|任何|所有|全报告|报告整体', t):
        return None
    return out


def _md_section(md, num):
    """操作手册里“## num 标题”这一节（到下一个“## 数字”为止）。"""
    m = re.search(r'^## %d\s.*?(?=^## \d+\s|\Z)' % num, md, re.M | re.S)
    return m.group(0).strip() if m else ''


def outline(md):
    """成品正文的标题、表题和表头（表题下的第一行表格）。"""
    out, want = [], False
    for ln in md.split('\n'):
        if ln.startswith('#'):
            out.append(ln)
            want = False
        elif re.match(r'^(续)?表\s*[0-9A-Z]', ln):
            out.append(ln[:120])
            want = True
        elif want and ln.startswith('|'):
            out.append(ln[:300])
            want = False
    return '\n'.join(out)


# ---------------- 第一步：口径 ----------------

GUIDE = '''你在给职业病危害预评价报告流水线提炼质量标准。成果给以后写报告、审报告、改稿的模型看，每次调用都带着，要短、准、能照着做。
- 只写通用写法。项目的具体事实（项目名、设备、数字）只能出现在“例：”里，一条例子不超过一句。
- 每条落到“写什么、不写什么、怎么判”。中文短句，不写解释和套话。
- 出处只写文件名、判例号（PL-…）或 K 号，不长段引用。
- 资料说法不一致时：用户口径、用户记忆（日期新的为准）＞定稿成品＞操作手册、判例卡、写法规范。'''
def _obj(fields):
    """结构化输出按键名的码位顺序生成：键名前加序号，模型才按这个顺序写（不加时“日期”“标题”的值会写串）。
    fields：[(名, schema)]；读结果时用 _plain 去掉序号。"""
    props = {'%d%s' % (i, k): v for i, (k, v) in enumerate(fields, 1)}
    return {'type': 'object', 'additionalProperties': False, 'required': list(props), 'properties': props}


def _plain(x):
    """去掉 _obj 加的序号（嵌套的也去）。"""
    if isinstance(x, list):
        return [_plain(v) for v in x]
    if isinstance(x, dict):
        return {re.sub(r'^\d+', '', k): _plain(v) for k, v in x.items()}
    return x


STR, LST = {'type': 'string'}, {'type': 'array', 'items': {'type': 'string'}}
K_ENTRY = _obj([('主题', STR), ('标题', STR), ('日期', STR), ('规矩', STR), ('用户原话', STR), ('例外', STR), ('出处', STR),
                ('检查', {'type': 'array', 'items': _obj([('节', {'type': 'string', 'enum': ['禁用', '正则禁用', '提示']}),
                                                          ('词', STR)])}),
                ('审稿', LST)])
R_DIM = _obj([('名称', STR), ('查什么', STR), ('怎么判', STR), ('对', STR), ('错', STR),
              ('级别', {'type': 'string', 'enum': ['必改', '建议']})])
# 先写审稿维度再写条目：条目里的审稿 R 号要对得上维度的顺序
K_SCHEMA = _obj([('审稿维度', {'type': 'array', 'items': R_DIM}), ('条目', {'type': 'array', 'items': K_ENTRY})])
K_TASK = '''任务：把上面的用户意见整理成审稿维度和用户口径条目（先写维度，再写条目），程序排版写文件。
一、用户口径条目（写进 标准/用户口径.md，全文不超过 %(maxk)d 字；以后每次调模型都带着它）
- 只收用户对报告的决定和要求：写法、范围、删什么留什么、交付物。对话怎么回复、程序和排版的技术细节（出稿工具、字体、渲染、文件夹、子助手）不收。
- 同一件事合成一条，以日期最新的说法为准；被用户后来推翻的旧说法不写，也不写“以前……现在……”。
- 按主题排，同主题的相邻；程序按给出的顺序编号 K001、K002……
- 标题：这条规矩是什么，十来个字（如“消防内容全删”），不写日期。日期：这件事用户最近一次表态的日期（YYYY-MM-DD，取记忆的修改日期或正文里写的日期）。
- 规矩：写什么、不写什么、怎么判，一两句。项目的具体事实只能出现在“例：”里。
- 用户原话：取最能说明的一句（记忆里引号中的话优先），不超过 60 字，不带引号。
- 例外：没有写“无”。
- 检查：能用词语查出来的写法给检查词，没有给空列表。“禁用”＝出现即报错，要具体，不误伤正常用法（如不禁单独的“核实”“确认”）；
  “正则禁用”＝Python 正则；“提示”＝要按上下文判断的。项目自己的旧数字、旧名称、旧场所名不收。
  程序已内置这些检查，不用再给：%(builtin)s。骨架正文里有【待填】【待核】这类占位符（写稿时填掉），检查词不能命中占位符，
  要查“待核”这类字样就用正则把【】里的排除掉，如 (?<!【)待核。程序会拿定稿报告和骨架验证，有正常用法的禁用词降为提示。
- 审稿：查不了或词语查不全的，写审稿维度的 R 号（按下面维度的顺序）。
下面是用户 10-31 定的，每一项都要有自己的一条（标题直接点明这件事），规矩写对：
%(must)s
二、审稿维度（写进 标准/审稿清单.md，不超过 %(maxr)d 个，全文不超过 %(maxrc)d 字），程序按顺序编号 R1、R2……
每个写：名称、查什么、怎么判、对（一句正例）、错（一句反例）、级别（必改／建议）。必须覆盖：事实有出处、与事实库一致；
名称口径全文一致（评价单元、工作场所、工种，因素名对应分类目录）；用户口径逐条；可读性（编者口吻、资料边界说明、方法取舍理由、
正文点名条号、自造概念）；资料缺口两类写法；只评职业病危害；四档判定与表内一致、计数句；跨章衔接（第9、10、11章与前文一致）。'''
MUST_TEXT = '''- 待补资料清单不进报告。
- 可研没写的照写“可研没有……”并提补充措施；等建设单位提供、确认的（待核、待补充、确认后）全清。
- 消防内容全删（连依托消防站、消防泵站、消防控制室也删）；医疗救护和应急撤离照留。
- 安泰公司情况不能删：1.9 质量控制里的批准设立、检测设备和计量检定、人员专业是安泰公司事实，不当编者口吻或资料边界删。
- 不影响危害识别的可研矛盾（数字、名称前后不一），在口径表写一次取哪个，不列待确认，正文不逐处解释。
- 不写编者视角、资料边界说明、条号堆砌。'''
def _builtin():
    return [w for w, _ in tools._checks.GENERIC_ERRORS + tools._checks.GENERIC_NOTES]


def _k_text(entries, hows):
    out = [K_HEADER.rstrip('\n')]
    for i, (e, how) in enumerate(zip(entries, hows), 1):
        words = _clean(e['用户原话']).strip('“”"「」\'')
        out += ['', '## K%03d %s（%s）' % (i, _clean(e['标题']), e['日期']), '- 规矩：' + _clean(e['规矩']),
                '- 用户原话：“%s”' % words, '- 怎么查：' + how, '- 例外：' + (_clean(e['例外']) or '无')]
    return '\n'.join(out) + '\n'


def _refs(refs, n_dims=MAX_DIMS):
    """条目里写的审稿维度号，规范成 R1、R2（超出维度个数的不要）。"""
    out = []
    for x in re.findall(r'R\s*(\d+)', ' '.join(refs or [])):
        r = 'R%d' % int(x)
        if 1 <= int(x) <= n_dims and r not in out:
            out.append(r)
    return out


def _how(rules_, refs, n_dims=MAX_DIMS):
    """K 条目的“怎么查”：照 revise._add_k 写成 禁用“词”、提示“词”；词语查不了的写审稿 R 号。"""
    how = '、'.join('%s“%s”' % (s, w) for s, w in rules_)
    rs = _refs(refs, n_dims)
    if rs:
        how = (how + '；' if how else '') + '审稿 ' + '、'.join(rs)
    return how or '审稿'


def _r_text(dims, entries, date):
    out = ['# 审稿清单', '', '<!-- 审稿逐条对照的维度。必改＝违反用户口径、事实错误或判定矛盾；建议＝改了确实更好。'
           '提炼标准生成（%s）。 -->' % date]
    for i, d in enumerate(dims, 1):
        r = 'R%d' % i
        ks = ['K%03d' % j for j, e in enumerate(entries, 1) if r in _refs(e['审稿'], len(dims))]
        out += ['', '## %s %s（%s）' % (r, _clean(d['名称']), d['级别']), '- 查什么：' + _clean(d['查什么']),
                '- 怎么判：' + _clean(d['怎么判']), '  - 对：' + _clean(d['对']), '  - 错：' + _clean(d['错'])]
        if ks:
            out.append('- 相关口径：' + '、'.join(ks))
    return '\n'.join(out) + '\n'


def _fix_dates(entries):
    for e in entries:
        s = e.get('日期') or ''
        m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', s) or re.search(r'()(\d{1,2})-(\d{1,2})', s)
        e['日期'] = '%s-%02d-%02d' % (m.group(1) or _today()[:4], int(m.group(2)), int(m.group(3))) if m else _today()


def _by_theme(entries):
    """同主题的排在一起（主题按第一次出现的顺序），程序再按这个顺序编 K 号。"""
    order = []
    for e in entries:
        if e['主题'].strip() not in order:
            order.append(e['主题'].strip())
    return sorted(entries, key=lambda e: order.index(e['主题'].strip()))


def _rough_how(entries):
    return [_how([(r['节'], r['词']) for r in e['检查']], e['审稿']) for e in entries]


def _verify(job, entries):
    """检查词验证：写错、太短、内置已有的不收；定稿项目或骨架里有命中的，禁用降为提示、正则禁用不收。
    返回 (每条的 [(节, 词)], 降级, 不收)。"""
    finished, extra = _check_roots()
    builtin = set(_builtin())
    cache, demoted, dropped, final = {}, [], [], []

    def hits(sec, w):
        if (sec, w) not in cache:
            cache[(sec, w)] = (sum(count_hits(p, sec, w) for p in finished), sum(count_hits(p, sec, w) for p in extra))
        return cache[(sec, w)]

    for i, e in enumerate(entries, 1):
        k, out = 'K%03d' % i, []
        for r in e['检查']:
            sec, w = r['节'], (r['词'] or '').strip()
            if len(w) >= 2 and w[0] == w[-1] == '`':
                w = w[1:-1].strip()
            if '\n' in w or len(w) < 2:
                if w:
                    dropped.append('%s“%s”（%s，太短或带换行）' % (sec, w, k))
                continue
            if w in builtin:
                dropped.append('%s“%s”（%s，程序内置已查）' % (sec, w, k))
                continue
            if sec == '正则禁用':
                try:
                    rx = re.compile(w)
                except re.error as ex:
                    dropped.append('正则“%s”（%s，写错了：%s）' % (w, k, ex))
                    continue
                if rx.search(''):
                    dropped.append('正则“%s”（%s，能匹配空字符串）' % (w, k))
                    continue
            if sec in ('禁用', '正则禁用'):
                a, b = hits(sec, w)
                if a or b:
                    where = '、'.join(x for x in ('定稿项目 %d 处' % a if a else '', '骨架 %d 处' % b if b else '') if x)
                    if sec == '禁用':
                        out.append(('提示', w))
                        demoted.append('“%s”（%s，%s，降为提示）' % (w, k, where))
                    else:
                        dropped.append('正则“%s”（%s，%s，不收）' % (w, k, where))
                    continue
            if (sec, w) not in out:
                out.append((sec, w))
        final.append(out)
    job.info('检查词验证：降为提示 %d 个，不收 %d 个' % (len(demoted), len(dropped)))
    return final, demoted, dropped
LEAK = re.compile(r'烧结|新材料(?:项目|供暖|科技)|200\s?MW|马沟|冷轧|太行|晋钢|加气站|LNG|AT-2026')


def leaks(text):
    """项目事实漏到“例：”之外的行（按项目名、编号查）。"""
    out = []
    for ln in text.split('\n'):
        if ln.lstrip().startswith(('#', '<!--', '- 用户原话', '- 对：', '- 错：')):
            continue
        m = LEAK.search(ln)
        cut = min([i for i in (ln.find('例：'), ln.find('例:')) if i >= 0] or [len(ln)])
        if m and m.start() < cut:
            out.append(ln.strip()[:60])
    return out


def _system():
    k = C.std('用户口径.md')
    return '\n\n'.join(x for x in (GUIDE, C.doc('用户口径（最高优先）', k) if k else '') if x)


def _k_materials():
    """记忆原文、各项目检查规则和可读性改写要求、判定规则与口径；已有用户口径的也给（改稿追加的用户意见只记在那里）。"""
    mems = memories()
    parts = [C.doc('用户记忆 %s（修改时间 %s）' % (n, d), ('说明：%s\n\n' % desc if desc else '') + body)
             for n, d, desc, body in mems]
    parts += _project_docs()
    parts.append(C.doc('references/判定规则与口径.md', tools.read(REF / '判定规则与口径.md')))
    # 现有用户口径里改稿追加的条目（用户原话在记忆里找不到的）也要收；从记忆提炼出来的不再给，免得旧稿的错带进来
    seen = '\n'.join(parts)
    extra = []
    for blk in re.split(r'(?m)^(?=## K\d+)', C.std('用户口径.md')):
        q = re.search(r'^- 用户原话：“(.+?)”\s*$', blk, re.M)
        head = blk.split('\n', 1)[0]
        # 提炼写的条目标题后只有一个（日期）；改稿追加的同样格式，靠用户原话在记忆里找不到来认。
        # 标题本身是日期的（字段写串的旧稿）不收
        if (blk.startswith('## K') and '取代' not in head and not re.match(r'^## K\d+ \d{4}-\d', head)
                and q and len(q.group(1)) >= 6 and q.group(1)[:15] not in seen):
            extra.append(blk.strip())
    if extra:
        parts.append(C.doc('改稿时追加的用户口径（用户原话是用户说的，照收）', '\n\n'.join(extra)))
    return parts, len(mems)


def _shrink_k(job, data, why):
    task = ('任务：上面是整理好的审稿维度和用户口径条目（JSON），%s。改写后按同样的结构返回：排版后用户口径全文不超过 %d 字、'
            '审稿清单不超过 %d 字；条目和维度的顺序、个数不变，主题、日期、用户原话、检查词和审稿 R 号原样保留；'
            '规矩、例外、查什么、怎么判改得更短；项目的具体事实只留在“例：”后面。' % (why, MAX_K - 1500, MAX_R - 500))
    return _plain(llm.call_retry(job, '用户口径压缩', GUIDE,
                                 '\n\n'.join([C.doc('现稿', json.dumps(data, ensure_ascii=False)), task]),
                                 effort='high', schema=K_SCHEMA, phase='口径'))


def step_k(job):
    job.stage('口径')
    parts, n_mem = _k_materials()
    job.info('口径材料：记忆 %d 份，共 %d 字' % (n_mem, sum(len(p) for p in parts)))
    task = K_TASK % {'maxk': MAX_K, 'maxr': MAX_DIMS, 'maxrc': MAX_R, 'must': MUST_TEXT,
                     'builtin': '、'.join(_builtin()) + '、【待填】【待核】【待补】【待定】【待确认】这类占位符'}
    data = _plain(llm.call_retry(job, '用户口径·审稿清单·检查词', GUIDE, '\n\n'.join(parts + [task]),
                                 effort='high', schema=K_SCHEMA, phase='口径'))
    _fix_dates(data['条目'])
    data['审稿维度'] = data['审稿维度'][:MAX_DIMS]
    k_len = len(_k_text(data['条目'], _rough_how(data['条目'])))
    r_len = len(_r_text(data['审稿维度'], data['条目'], _today()))
    leak = leaks('\n'.join(_clean(e['规矩']) + ' ' + _clean(e['例外']) for e in data['条目']))
    if k_len > MAX_K or r_len > MAX_R or leak:
        why = '；'.join(x for x in ('用户口径 %d 字，超过 %d' % (k_len, MAX_K) if k_len > MAX_K else '',
                                   '审稿清单 %d 字，超过 %d' % (r_len, MAX_R) if r_len > MAX_R else '',
                                   '这几处项目事实不在“例：”里：' + '；'.join(leak[:8]) if leak else '') if x)
        job.info('压缩一次：' + why)
        try:
            new = _shrink_k(job, data, why)
            if len(new['条目']) >= len(data['条目']) * 0.8 and new['审稿维度']:
                _fix_dates(new['条目'])
                new['审稿维度'] = new['审稿维度'][:MAX_DIMS]
                data = new
        except llm.CallError as e:
            job.info('压缩没成，用原稿：%s' % str(e)[:80])
    entries, dims = _by_theme(data['条目']), data['审稿维度']
    final, demoted, dropped = _verify(job, entries)
    # 同一个词：别的条目已禁用的不再加提示，重复的只记在第一条；整份规则重写（备份在 标准/_历史/）
    banned, seen = {w for rs in final for s, w in rs if s == '禁用'}, set()
    for i, rs in enumerate(final):
        keep = []
        for s, w in rs:
            if (s, w) in seen or (s == '提示' and w in banned):
                continue
            seen.add((s, w))
            keep.append((s, w))
        final[i] = keep
    tools.write(COMMON, rules.COMMON_HEADER + '\n\n## 禁用\n\n## 正则禁用\n\n## 提示\n')
    for i, (e, rs) in enumerate(zip(entries, final), 1):
        if rs:
            rules.add(COMMON, rs, 'K%03d %s' % (i, e['日期']))
    for w in rules.valid_regex(COMMON):
        dropped.append('正则“%s”（写错了，不收）' % w)
    # 用检查程序本身再查一遍：定稿项目和骨架里还报“通用规则禁用”的，降为提示
    finished, extra = _check_roots()
    for root in finished + extra:
        row = tools.project_row(root)
        name = row['项目'] if row else Path(root).name
        for w, n in rules.hits_in(root).items():
            k = next(('K%03d %s' % (i, e['日期']) for i, (e, rs) in enumerate(zip(entries, final), 1)
                      if any(x == w for _, x in rs)), _today())
            how = rules.demote(COMMON, w, '%s：%s 里有 %d 处正常用法，由禁用降为提示' % (k, name, n))
            demoted.append('“%s”（%s，检查程序在 %s 查出 %d 处，%s）' % (w, k.split()[0], name, n, '降为提示' if how == '提示' else '删掉'))
            for rs in final:
                for j, (s, x) in enumerate(rs):
                    if x == w and s in ('禁用', '正则禁用'):
                        rs[j] = ('提示', w) if how == '提示' else None
                rs[:] = [r for r in rs if r]
    hows = [_how(rs, e['审稿'], len(dims)) for e, rs in zip(entries, final)]
    tools.write(config.STD / '用户口径.md', _k_text(entries, hows))
    tools.write(config.STD / '审稿清单.md', _r_text(dims, entries, _today()))
    k_text = C.std('用户口径.md')
    missing = [why for w, why in MUST if not re.search(w, k_text)]
    if missing:
        job.info('用户口径里没找到这几件事（请核对）：' + '；'.join(missing))
    items = rules.items(COMMON)
    out = {'用户口径': {'条数': len(entries), '字数': len(k_text)},
           '审稿清单': {'维度': len(dims), '字数': len(C.std('审稿清单.md'))},
           '通用检查规则': {s: sum(1 for x in items if x[0] == s) for s in ('禁用', '正则禁用', '提示')},
           '降为提示': demoted, '不收': dropped, '口径缺项': missing,
           '项目事实不在例里': leaks('\n'.join(ln for ln in k_text.split('\n') if ln.startswith(('- 规矩', '- 例外'))))[:10]}
    job.info('用户口径 %d 条 %d 字；审稿清单 %d 个维度；通用规则 %s' % (len(entries), len(k_text), len(dims), out['通用检查规则']))
    return out
# ---------------- 第二步：各章要点卡 ----------------

def cases():
    """判例卡 {判例号: {字段…, 全文}}；预评价用不上的（控评、设计专篇）不要。风险评估法版有同号卡的另存一份。"""
    out = {}
    for base, tag in ((REF / '判例卡', ''), (REF / '风险评估法版' / '判例卡', '风险')):
        for p in sorted(base.glob('PL-*.md')):
            t = tools.read(p)
            fm = _fm(t)
            if _field(fm, '报告类型') in ('控制效果评价', '设计专篇'):
                continue
            body = t.split('---', 2)[2] if fm else t
            m = re.search(r'^#\s+(.+)$', body, re.M)
            d = {f: _field(fm, f) for f in ('判例类型', '涉及章节', '可复用规则', '报告类型')}
            d.update(号=p.stem, 标题=m.group(1).strip() if m else '', 全文=t)
            out[p.stem + ('#风险' if tag else '')] = d
    return out


def _case_pick(group, allc):
    """本组要看全文的判例：涉及章节写到本组章号的（排版图件类不要）；超出预算的只给摘要。返回 (全文列表, 摘要行)。"""
    want = set(group['章'])
    method = group['方法']
    full, brief = [], []
    for key, d in allc.items():
        if '#风险' in key and method != '风险评估法':
            continue
        if method == '风险评估法' and '#风险' not in key and (key + '#风险') in allc:
            continue
        if d['判例类型'] == '排版图件':
            continue
        ch = _chapters(d['涉及章节'])
        if ch and ch & want:
            score = len(ch & want) / len(ch)
            full.append((-score, len(d['全文']), d))
        elif ch is None or not ch:
            rule_ch = _chapters(d['可复用规则'])
            if rule_ch and rule_ch & want:
                full.append((-0.1, len(d['全文']), d))
            elif d['判例类型'] != '工作流':
                brief.append('%s %s｜%s' % (d['号'], d['标题'], d['可复用规则']))
    full.sort(key=lambda x: (x[0], x[1]))
    return [d for _, _, d in full], brief


def _writing_files(group):
    """references/各章写法/ 里本组的文件（第5章生成提示词已被重写稿取代，不用），加总览里本组各章那一节。"""
    out = []
    d = REF / '各章写法'
    for p in sorted(d.glob('*.md')):
        m = re.match(r'^(?:范例_)?第(\d+)章', p.name)
        if not m or int(m.group(1)) not in group['章'] or '生成提示词' in p.name:
            continue
        if int(m.group(1)) == 3 and group['方法']:
            if ('风险评估' in p.name) != (group['方法'] == '风险评估法'):
                continue
        out.append((p.name, tools.read(p)))
    for base, tag in ((d, '类比法'), (REF / '风险评估法版' / '各章写法', '风险评估法')):
        if group['方法'] and tag != group['方法']:
            continue
        p = base / '总览_报告骨架与各章要点.md'
        if not p.exists():
            continue
        t = tools.read(p)
        for n in group['章']:
            m = re.search(r'^### %d\s.*?(?=^###? |\Z)' % n, t, re.M | re.S)
            if m:
                out.append(('总览（%s版）第%d章' % (tag, n), m.group(0).strip()))
    seen, uniq = set(), []
    for name, text in out:          # 两个版本的总览同一节一样时只给一份
        h = hashlib.md5(text.encode('utf-8')).hexdigest()
        if h not in seen:
            seen.add(h)
            uniq.append((name, text))
    return uniq


def _lines_for(md, want):
    """判定规则与口径、常见错误与教训里与本组有关的行：标题行、表头，和提到本组章号或不限章的条目。"""
    out = []
    for ln in md.split('\n'):
        s = ln.strip()
        if not s:
            continue
        if s.startswith(('#', '>')) or set(s) <= set('|-: '):
            out.append(ln)
            continue
        cells = [c.strip() for c in s.strip('|').split('|')] if s.startswith('|') else []
        probe = cells[2] if len(cells) >= 4 else s
        ch = _chapters(probe)
        if ch is None or not ch or ch & want:
            out.append(ln)
    return '\n'.join(out)


def _skeleton(group):
    """两份骨架本章文件（第3章两种方法也都给：方法差异要对照着写）。"""
    out = []
    for sk, tag in (('项目骨架', '类比法'), ('项目骨架_风险评估法', '风险评估法')):
        for p in sorted((config.ROOT / 'templates' / sk / '正文').glob('*.md')):
            if p.name[:2].isdigit() and int(p.name[:2]) in group['章']:
                out.append(('骨架（%s）%s' % (tag, p.name), tools.read(p)))
    return out


def _finished(group):
    """成品本章的标题、表题和表头：风险评估法看烧结（样板），类比法看 200MW（样板）。"""
    out = []
    for method in ('风险评估法', '类比法'):
        if group['方法'] and method != group['方法']:
            continue
        root = tools.sample_for(method)
        if not root:
            continue
        name = tools.project_name(root)
        for f in tools.body_files(root):
            if f[:2].isdigit() and int(f[:2]) in group['章']:
                out.append(('成品 %s（%s）%s 的标题、表题和表头' % (name, method, f), outline(tools.read(Path(root) / '正文' / f))))
    return out
def _card_schema(files):
    card = _obj([('文件', {'type': 'string', 'enum': list(files)})] + [(k, LST) for k, _ in SECS] + [('来源', STR)])
    return _obj([('卡', {'type': 'array', 'items': card})])


CARD_TASK = '''任务：写%(章)s的要点卡（%(文件)s），程序按七节排版，写进 标准/各章要点/。每张卡全文不超过 %(max)d 字。
以后写这一章、审这一章、改这一章，模型都带着这张卡和用户口径，所以每条都要能照着做。
- 涉及用户决定的（系统提示里的用户口径），只写“见 K0xx”加一句话，不重抄。
- 必答问题：按节列，“节号 标题：这一节要回答什么”。
- 必备：必备的节、表和表头，写成“节号 标题”或“表x.x-x 表名｜列1｜列2｜…”。表头以成品为准；骨架和成品不一致时写成品的，后面注“（骨架为……）”。
- 怎么判：档位和判据，计数句怎么对表；本章没有判定的给空列表。
- 红线：违反就返工的，每条末尾括号写判例号或 K 号。
- 常见错误：“错例 → 正例”，一条一句。
- 跨章衔接：本章的名称、数字从哪章来，给哪几章用。
- 方法差异：类比法和风险评估法在本章的不同处；没有给空列表。%(diff)s
- 来源：用到的文件名、判例号，一行。
- 只写通用写法；项目的具体事实（项目名、设备、数字）只能放在“例：”后面，一条例子不超过一句。
- 列表每项一句或两句，不写编号，不用 Markdown 标题。不同资料说法不一时：用户口径＞成品＞写法规范、判例卡；手册、判例里旧的档名、旧做法照用户口径和成品的改。'''


def _card_text(c, date):
    f = c['文件']
    title = NAMES.get(f, '')
    n = int(f[:2])
    tag = '（%s）' % f[3:-3] if f.startswith('03_') else ''
    src = re.sub(r'^(来源[:：]\s*)+', '', _clean(c['来源'])).replace('--', '—')[:200]
    out = ['# 第%d章 %s 要点卡%s' % (n, title, tag), '<!-- 来源：%s；提炼日期 %s -->' % (src, date)]
    for key, head in SECS:
        out += ['', '## ' + head]
        items = [_clean(x) for x in c.get(key) or [] if _clean(x)]
        out += ['- ' + x for x in items] or ['- 无']
    return '\n'.join(out) + '\n'


def _parse_card(text):
    """要点卡 Markdown → 结构化（补漏后压缩用）。"""
    c = {k: [] for k, _ in SECS}
    m = re.search(r'<!--\s*来源：(.*?)；提炼日期.*?-->', text)
    c['来源'] = m.group(1) if m else ''
    cur = None
    for ln in text.split('\n'):
        h = re.match(r'^##\s*(\d)\s', ln)
        if h:
            cur = SECS[int(h.group(1)) - 1][0] if 1 <= int(h.group(1)) <= 7 else None
            continue
        if cur and ln.startswith('- ') and ln[2:].strip() != '无':
            c[cur].append(ln[2:].strip())
    return c


def _shrink_card(job, c, why, limit):
    task = ('任务：上面这张要点卡%s。压到 %d 字以内，按同样的七项返回：意思不丢，措辞更短，合并重复的条目；'
            '“见 K0xx”、判例号原样保留；项目的具体事实只留在“例：”后面。' % (why, limit))
    r = _plain(llm.call_retry(job, '压缩 %s' % c['文件'], _system(),
                              '\n\n'.join([C.doc(c['文件'], _card_text(c, _today())), task]),
                              effort='medium', schema=_card_schema([c['文件']]), phase='各章'))
    return r['卡'][0] if r.get('卡') else None


def _fit_card(job, c, limit, hard):
    """超过 hard 字或项目事实不在“例：”里的，让模型压一次；压完还超的照写，记下来。"""
    text = _card_text(c, _today())
    lk = leaks(text)
    if len(text) <= hard and not lk:
        return text, ''
    why = '；'.join(x for x in ('有 %d 字，超过 %d' % (len(text), hard) if len(text) > hard else '',
                               '这几处项目事实不在“例：”里：' + '；'.join(lk[:6]) if lk else '') if x)
    try:
        new = _shrink_card(job, c, why, limit)
    except llm.CallError as e:
        return text, '压缩没成（%s）' % str(e)[:60]
    if new:
        new['文件'] = c['文件']
        t2 = _card_text(new, _today())
        if len(t2) < len(text) or (len(t2) <= hard and len(leaks(t2)) < len(lk)):
            text = t2
    return text, ('压缩后仍有 %d 字' % len(text)) if len(text) > hard else ''


def _group_materials(group, allc, sys_len):
    want = set(group['章'])
    fixed = [C.doc(n, t) for n, t in _skeleton(group)]
    fixed += [C.doc('本章写法 ' + n, t) for n, t in _writing_files(group)]
    fixed.append(C.doc('判定规则与口径（与本章有关的条目）', _lines_for(tools.read(REF / '判定规则与口径.md'), want)))
    if group['方法'] == '风险评估法' or not group['方法']:
        r2 = REF / '风险评估法版' / '判定规则与口径.md'
        if r2.exists():
            a, b = set(tools.read(REF / '判定规则与口径.md').split('\n')), _lines_for(tools.read(r2), want)
            diff = '\n'.join(ln for ln in b.split('\n') if ln not in a and ln.strip())
            if diff:
                fixed.append(C.doc('判定规则与口径（风险评估法版与上面不同的条目）', diff))
    fixed.append(C.doc('常见错误与教训（判例速查里与本章有关的行）', _lines_for(tools.read(REF / '常见错误与教训.md'), want)))
    fixed += [C.doc(n, t) for n, t in _finished(group)]
    full, brief = _case_pick(group, allc)
    room = BUDGET - sys_len - sum(len(x) for x in fixed) - 4000
    picked, rest = [], []
    for d in full:
        if len(d['全文']) + 40 <= room:
            picked.append(C.doc('判例 %s' % d['号'], d['全文']))
            room -= len(d['全文']) + 40
        else:
            rest.append('%s %s｜%s' % (d['号'], d['标题'], d['可复用规则']))
    lines = rest + brief
    tail = []
    for ln in lines:
        if room - len(ln) - 1 < 0:
            break
        tail.append(ln)
        room -= len(ln) + 1
    if tail:
        picked.append(C.doc('其他判例（标题｜可复用规则）', '\n'.join(tail)))
    return fixed + picked, len(full), len(picked) - (1 if tail else 0)


def _one_group(job, group, allc):
    sys_ = _system()
    parts, n_rel, n_full = _group_materials(group, allc, len(sys_))
    chs = '、'.join('第%d章 %s' % (int(f[:2]), NAMES[f]) for f in group['文件'])
    diff = ''
    if group['方法']:
        diff = '本卡只给%s用；方法差异写本章与另一方法的同章有什么不同（写法、表、判定），好让写稿时不串用。' % group['方法']
    task = CARD_TASK % {'章': chs, '文件': '、'.join(group['文件']), 'max': MAX_CARD, 'diff': diff}
    job.info('要点卡 %s：材料 %d 字，判例全文 %d 张（涉及本章 %d 张）' % (group['key'], sum(len(p) for p in parts), n_full, n_rel))
    r = _plain(llm.call_retry(job, '要点卡 ' + group['key'], sys_, '\n\n'.join(parts + [task]),
                              effort='high', schema=_card_schema(group['文件']), phase='各章'))
    out = []
    for f in group['文件']:
        c = next((x for x in r['卡'] if x['文件'] == f), None)
        if not c:
            out.append({'文件': f, '字数': 0, '说明': '模型没给这张卡'})
            continue
        text, note = _fit_card(job, c, MAX_CARD - 400, MAX_CARD)
        tools.write(CARDS / f, text)
        out.append({'文件': f, '字数': len(text), '条数': sum(1 for ln in text.split('\n') if ln.startswith('- ')),
                    '说明': note, '红线': [ln[2:60] for ln in _section(text, 4)[:3]]})
    return out


def _section(text, n):
    m = re.search(r'^## %d\s.*?(?=^## |\Z)' % n, text, re.M | re.S)
    return [ln for ln in (m.group(0).split('\n') if m else []) if ln.startswith('- ')]


def step_cards(job):
    job.stage('各章')
    if not C.std('用户口径.md'):
        raise RuntimeError('标准/用户口径.md 还没有，先做“口径”这一步')
    CARDS.mkdir(parents=True, exist_ok=True)
    allc = cases()
    res = pmap(job, lambda g: _one_group(job, g, allc), GROUPS)
    cards = [c for r in res if r for c in r]
    miss = [g['key'] for g, r in zip(GROUPS, res) if not r]
    if miss:
        job.info('这几组没写成：' + '、'.join(miss))
    return {'卡': cards, '没写成': miss}
# ---------------- 第三步：补漏 ----------------

FILL_TASK = '''任务：查漏补缺。上面是 标准/ 下现有的全部文件（文件名后是字数），和两份操作手册的第2节总规矩、第8节口径红线、第9节自检清单，以及判例卡的标题和可复用规则。
逐条对照手册和判例里的规矩：每条都应能在某张要点卡、用户口径或审稿清单里找到（意思对上就算，不要求原话）。
- 找不到的写进“补充”：补到最合适的文件和节。要点卡的“节”写 1～7（4＝红线、5＝常见错误……）；审稿清单的“节”写 R 号。内容一句，出处写手册节号或判例号。
- 只补写报告、审报告用得上的规矩；出稿、排版、图件、程序和工作流程（建项目、build、WPS、文件夹、子助手、云端）的不补。
- 用户口径已有或已被用户推翻的（旧档名、【待核】写法、待补资料清单等）不补。
- 文件之间说法矛盾的（要点卡、审稿清单与用户口径不一致），以用户口径为准，写进“矛盾”：原文照抄要改的文件里的一段（在文件里只出现一次，一般取整行），新文是改好的。用户口径和通用检查规则不改。
- 要点卡补后每张不超过 %(max)d 字，快超的只补最要紧的。只补缺、改矛盾，不大改结构。
- 核对条数：你逐条对照过的手册和判例规矩条数。'''


def _std_files():
    out = [n for n in ('用户口径.md', '审稿清单.md', '通用检查规则.md') if (config.STD / n).exists()]
    if CARDS.is_dir():
        out += ['各章要点/' + p.name for p in sorted(CARDS.glob('*.md'))]
    return out


def _fill_schema(targets):
    f = {'type': 'string', 'enum': targets}
    return _obj([('核对条数', {'type': 'integer'}),
                 ('补充', {'type': 'array', 'items': _obj([('文件', f), ('节', STR), ('内容', STR), ('出处', STR)])}),
                 ('矛盾', {'type': 'array', 'items': _obj([('文件', f), ('原文', STR), ('新文', STR), ('说明', STR)])})])


def _insert(text, head, line):
    """把一行加到标题匹配 head（正则）的那一节末尾；这一节只有“- 无”的，换掉它。找不到这一节返回 None。"""
    lines = text.split('\n')
    i = next((k for k, ln in enumerate(lines) if re.match(head, ln)), None)
    if i is None:
        return None
    j = next((k for k in range(i + 1, len(lines)) if re.match(r'^#{1,2}\s', lines[k])), len(lines))
    body = [k for k in range(i + 1, j) if lines[k].strip()]
    if len(body) == 1 and lines[body[0]].strip() == '- 无':
        lines[body[0]] = line
        return '\n'.join(lines)
    while j > i + 1 and not lines[j - 1].strip():
        j -= 1
    lines.insert(j, line)
    return '\n'.join(lines)


def _heads(text):
    return re.findall(r'^#{1,2} .*$', text, re.M)


def step_fill(job):
    job.stage('补漏')
    names = _std_files()
    if not any(n.startswith('各章要点/') for n in names):
        raise RuntimeError('标准/各章要点/ 还没有卡，先做“各章”这一步')
    parts = [C.doc('标准/%s（%d 字）' % (n, len(C.std(n))), C.std(n)) for n in names]
    for m in ('操作手册_类比法.md', '操作手册_风险评估法.md'):
        t = tools.read(REF / m)
        parts += [C.doc('%s 第%d节' % (m, n), _md_section(t, n)) for n in (2, 8, 9) if _md_section(t, n)]
    rows = ['%s %s｜%s' % (d['号'], d['标题'], d['可复用规则'][:160]) for k, d in sorted(cases().items()) if '#风险' not in k]
    parts.append(C.doc('判例卡（判例号 标题｜可复用规则）', '\n'.join(rows)))
    targets = [n for n in names if n not in ('用户口径.md', '通用检查规则.md')]
    r = _plain(llm.call_retry(job, '查漏补缺', GUIDE, '\n\n'.join(parts + [FILL_TASK % {'max': MAX_FILLED}]),
                              effort='high', schema=_fill_schema(targets), phase='补漏'))
    added, failed, fixed, unfixed = [], [], [], []
    for x in r['补充']:
        p = config.STD / x['文件']
        body = _clean(x['内容'])
        if not body or not p.exists():
            continue
        text = tools.read(p)
        if body in text:
            continue
        line = '- %s%s' % (body, '（%s）' % _clean(x['出处']) if _clean(x['出处']) and _clean(x['出处']) not in body else '')
        if x['文件'] == '审稿清单.md':
            m = re.search(r'R\s*(\d+)', x['节'])
            new = _insert(text, r'^## R%s\s' % m.group(1), line.replace('- ', '- 另查：', 1)) if m else None
        else:
            m = re.search(r'[1-7]', x['节'])
            new = _insert(text, r'^## %s\s' % (m.group(0) if m else '4'), line)
        if new is None:
            failed.append('%s %s：%s（找不到这一节）' % (x['文件'], x['节'], body[:40]))
            continue
        tools.write(p, new)
        added.append('%s %s：%s' % (x['文件'], x['节'], body[:80]))
    for x in r['矛盾']:
        p = config.STD / x['文件']
        if not p.exists():
            continue
        old = tools.read(p)
        new, done, bad = tools.apply_edits(old, [x])
        if done and _heads(new) != _heads(old):
            done, bad = [], [(x, '改动碰到了节标题')]
        if done:
            tools.write(p, new)
            fixed.append('%s：%s' % (x['文件'], _clean(x['说明'])[:80]))
        else:
            unfixed.append('%s：%s（%s）' % (x['文件'], _clean(x['说明'])[:60], bad[0][1] if bad else ''))
    shrunk = []
    for n in names:
        if not n.startswith('各章要点/'):
            continue
        text = C.std(n)
        if len(text) <= MAX_FILLED:
            continue
        c = _parse_card(text)
        c['文件'] = n.split('/', 1)[1]
        new, note = _fit_card(job, c, MAX_CARD - 200, MAX_FILLED)
        if len(new) < len(text):
            tools.write(config.STD / n, new)
        shrunk.append('%s：%d → %d 字%s' % (n, len(text), len(C.std(n)), '，' + note if note else ''))
    job.info('补漏：补 %d 条，改矛盾 %d 处，压缩 %d 张卡' % (len(added), len(fixed), len(shrunk)))
    return {'核对条数': r['核对条数'], '补了': added, '没补成': failed, '改矛盾': fixed, '矛盾没改成': unfixed, '压缩': shrunk}


# ---------------- 入口 ----------------

def summary():
    """标准/ 各文件的条数和字数。"""
    out = {}
    for n in _std_files():
        t = C.std(n)
        if n == '用户口径.md':
            k = len(re.findall(r'^## K\d+', t, re.M))
        elif n == '审稿清单.md':
            k = len(re.findall(r'^## R\d+', t, re.M))
        elif n == '通用检查规则.md':
            its = rules.items(COMMON)
            k = '、'.join('%s %d' % (s, sum(1 for x in its if x[0] == s)) for s in ('禁用', '正则禁用', '提示'))
        else:
            k = sum(1 for ln in t.split('\n') if ln.startswith('- ') and ln.strip() != '- 无')
        out[n] = '%s 条，%d 字' % (k, len(t))
    return out


def job_distill(job):
    only = job.params.get('只做') or []
    if isinstance(only, str):
        only = re.split(r'[,，、\s]+', only)
    only = [x.strip() for x in only if x and x.strip()]
    bad = [x for x in only if x not in STEPS]
    if bad:
        raise ValueError('不认识的步骤：%s（只能是 %s）' % ('、'.join(bad), '、'.join(STEPS)))
    todo = [s for s in STEPS if not only or s in only]
    config.STD.mkdir(parents=True, exist_ok=True)
    bk = tools.backup_std('提炼标准')
    job.info('要做：%s；标准/ 备份在 %s' % ('、'.join(todo), bk))
    out = {'做了': todo, '备份': str(bk or '')}
    if '口径' in todo and '各章' not in todo and list(CARDS.glob('*.md')):
        job.info('只重做口径：K 号可能变了，要点卡里的“见 K0xx”请接着用 --只做 各章 补漏 重做')
    if '口径' in todo:
        out['口径'] = step_k(job)
    if '各章' in todo:
        out['各章'] = step_cards(job)
    if '补漏' in todo:
        out['补漏'] = step_fill(job)
    out['文件'] = summary()
    return out
