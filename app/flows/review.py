"""审稿：逐个文件对照审稿清单、要点卡、用户口径审，按意见改；跨章一致；全文机器检查清零；可接着出稿。"""
import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import llm, tools
from ..tools import doctype as DT
from . import common as C
from . import facts as F
from . import publish as P

# 审和改在同一次调用里：每条意见直接给出“原文→新文”，省掉第二次把全部材料再发一遍
REVIEW = {
    'type': 'object', 'additionalProperties': False, 'required': ['意见'],
    'properties': {'意见': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['行', '级别', '问题', '依据', '原文', '新文'],
        'properties': {'行': {'type': 'integer'}, '级别': {'type': 'string', 'enum': ['必改', '建议']},
                       '问题': {'type': 'string'}, '依据': {'type': 'string'},
                       '原文': {'type': 'string'}, '新文': {'type': 'string'}}}}},
}
CROSS = {
    'type': 'object', 'additionalProperties': False, 'required': ['改动', '没改'],
    'properties': {
        '改动': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['文件', '原文', '新文', '说明'],
            'properties': {'文件': {'type': 'string'}, '原文': {'type': 'string'}, '新文': {'type': 'string'}, '说明': {'type': 'string'}}}},
        '没改': {'type': 'array', 'items': {'type': 'string'}},
    },
}


def pmap(job, fn, items):
    """并行跑，单个出错记日志、结果为 None；有取消就整体取消。"""
    out, cancelled, fatal = [None] * len(items), False, None
    with ThreadPoolExecutor(max(1, len(items))) as ex:
        futs = [ex.submit(fn, x) for x in items]
        for i, f in enumerate(futs):
            try:
                out[i] = f.result()
            except llm.Cancelled:
                cancelled = True
            except llm.CallError as e:
                if e.fatal:
                    fatal = fatal or e      # 余额不够、密钥不对：别的文件也一样，整个任务停下
                else:
                    job.info('出错：%s' % str(e)[:200])
            except Exception as e:  # noqa: BLE001  一个文件没成不拖垮整轮，记下来
                job.info('出错：%s' % str(e)[:200])
    if cancelled or job.cancelled:
        raise llm.Cancelled()
    if fatal:
        raise fatal
    return out


RE_REFNUM = re.compile(r'(第\s*)\d+(?:\.\d+)*(\s*[章节])|([表图]\s*)\d+(?:\.\d+)*-\d+')
# 意见的理由是“资料里没有”：事实库不摘电气等内容，审稿只拿事实库比，曾把可研里明写的“金属铠装”当成查不到删掉
RE_NOSRC = re.compile(r'查不到|找不到|没有出处|无出处|出处不明|未见出处|无依据|没有依据|编造|凭空|'
                      r'(?:资料|可研|初设|事实库)(?:里|中|上)?(?:没有|未见|未提及|未涉及|没写|没提|不含|未收)')


def _removed(old, new):
    """原文里被删掉、换掉的片段（去掉空白后至少 2 个字）。"""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    bits = [re.sub(r'\s+', '', old[i1:i2]) for tag, i1, i2, _j1, _j2 in sm.get_opcodes() if tag in ('delete', 'replace')]
    return [b for b in bits if len(b) >= 2]


def _in_source(root, edits):
    """以“资料里查不到”为理由删改的，删掉的字在资料原文里搜得到就不自动改（只搜资料：别的章重复写了同一个没出处的说法，照样该删）。
    返回 (能改的, [(改动, 原因)])。"""
    ok, held, pool = [], [], None
    for e in edits:
        if not RE_NOSRC.search((e.get('问题') or '') + (e.get('依据') or '')):
            ok.append(e)
            continue
        if pool is None:      # 第3章引的类比资料也算资料
            pool = re.sub(r'\s+', '', '\n'.join(t for _, t in tools.source_texts(root) + tools.analog_texts(root)))
        hit = [b for b in _removed(e.get('原文') or '', e.get('新文') or '') if b in pool]
        if hit:
            held.append((e, '说资料里查不到，可“%s”资料原文里有，没自动改' % hit[0][:20]))
        else:
            ok.append(e)
    return ok, held


def _ref_only(old, new):
    """原文、新文只差在“第几章／第几节”“表几／图几”的号上。"""
    norm = lambda s: RE_REFNUM.sub(lambda m: (m.group(1) or m.group(3) or '') + '#' + (m.group(2) or ''), s)  # noqa: E731
    return old != new and norm(old) == norm(new)


def _record(root, fname, ops, append=''):
    p = Path(root) / '审查' / '审稿记录' / fname
    head = ['', '## ' + append, ''] if append and p.exists() else ['# 审稿记录 ' + fname, '']
    rows = head + ['| 行 | 级别 | 问题 | 依据 | 原文 | 改成 | 结果 |', '|---|---|---|---|---|---|---|']
    for o in ops:
        rows.append('| ' + ' | '.join(str(o.get(k, '')).replace('|', '\\|').replace('\n', ' ')[:300]
                                      for k in ('行', '级别', '问题', '依据', '原文', '新文', '结果')) + ' |')
    if not ops:
        rows.append('| | | 没有意见 | | | | |')
    old = tools.read(p).rstrip('\n') + '\n' if append and p.exists() else ''
    tools.write(p, old + '\n'.join(rows) + '\n')


# 一章分几遍审，每遍只管几条 R：11-04 第2.3节试验，一遍全审两次各报 2 条、只重合 1 条，分类审报出 9 个有依据的问题（$3.08）
PASSES = (
    ('事实和资料缺口', (1, 5), '正文和每张表每一格里的数字、设备、设施、做法，逐个回资料找出处；'
                              '“可研没有……”的说法先检索可研和初设全文再判；资料缺口按两类写法判。'),
    ('判定和安全内容', (6, 7), '评价表从第一行到最后一行，每一行都看：判定档和分析栏、补充建议栏对不对得上，'
                              '可研没写的却判“符合”“基本符合”的要报；小结和表里对得上；有没有安全、消防内容。'),
    ('名称、引用和写作规矩', (2, 3, 4, 8, 9), '每个工作场所、工种、危害因素名称和第4章、表1.8-1对照；每处“见表”“见第x节”和报告各章标题表题对照；'
                                            '表注、表题、图题体例；按用户口径逐条（K号）核。写法上的意见只报两种：违反了某条K号的；'
                                            '不改读者会看错、看不懂的。只是“可以写得更好”的不报。'),
)


# 设计专篇：几遍的分法（R 号）不变，各遍查什么按专篇换（标准/设计专篇/审稿清单.md 保留 R1～R9 编号、换了内容）
HOW_DESIGN = {
    '事实和资料缺口': '正文和每张表每一格里的数字、设备、尺寸、设施参数，逐个回资料（可研、初设、施工图）或承接的预评价报告找出处；'
                      '依据条款回标准核（条款号、限值、换气次数等对得上）；设计文件没写的设施有没有按标准写成设计要求'
                      '（完整不等于多：同类合并、验收核不了的移出、可有可无的新增设施删去，口径 D18），'
                      '写了“初设没有写”“由设计单位确认”“待取得”的要报。',
    '判定和安全内容': '设计完整性：每项防护设施、应急救援设施的名称、规格型号、数量、设置位置、技术参数及控制性能、依据条款一项不缺'
                      '（WS/T 882—2013 第7.4.2条；完整指不缺项，不是加设施，口径 D18），设备固有参数没有出处的写成“采购时要求厂家提供××保证值和测试报告”；采纳情况表覆盖预评价第10章每一条，'
                      '“不采纳”的写了理由；投资概算四类费用齐全；预期结论按口径 D12 写（“符合”“限制接触时间后符合”，噪声仍超限的“超限，须个体防护”，不写“不符合”；个体防护后“符合”不成立），'
                      '预期值写成限值本身的要报；有没有安全、消防内容。',
    '名称、引用和写作规矩': '每个工作场所、工种、危害因素名称和岗位因素表、表3.1-1 对照（和预评价一字不差）；每处“见表”“见第x节”“附件册表x.x-x”'
                            '“附件册附件N”“预评价报告表x.x-x”和报告各章标题表题对照；表注、表题、图题体例；按用户口径和设计专篇口径逐条（K号）核。'
                            '写法上的意见报这几种：违反了某条K号的；不改读者会看错、看不懂的；R10（指标可行性和验收可核：逐个带数的指标和采购要求问出处、能不能做到、验收能不能核）'
                            '和 R11（篇幅和可读性：超预算、章首没有结论、段落过长、表格过宽、行首“- ”）的问题照报，不因“只是写法”而不报。'
                            '纯属个人偏好的措辞不报。',
}


def _how(t, p):
    """这一遍查什么（预评价用 PASSES 里写的，设计专篇用 HOW_DESIGN）。"""
    return p[2] if t == DT.PRE else HOW_DESIGN.get(p[0], p[2])


def checklist_of(t):
    """审稿清单：预评价 标准/审稿清单.md；设计专篇 doctype 配的（没写好时退回预评价的）。"""
    return C.type_std(t, '审稿清单') or C.std('审稿清单.md')


def _sections(checklist, nums):
    """审稿清单里只留这几条 R。"""
    parts = re.split(r'(?m)^(?=## R\d+ )', checklist)
    return '\n'.join(x for x in parts if (m := re.match(r'## R(\d+) ', x)) and int(m.group(1)) in nums)


def _merge(found):
    """几遍的意见合到一起，按行号排；原文一样的只留一条（必改优先），其余记成重复。返回 (留下的, 重复的)。"""
    norm = lambda t: re.sub(r'\s+', '', t or '')  # noqa: E731
    keep, dup = {}, []
    for o in sorted(found, key=lambda o: (o['级别'] != '必改', o['行'])):
        k = norm(o['原文'])
        if k and k in keep:
            dup.append(o)
        else:
            keep[k or id(o)] = o
    return sorted(keep.values(), key=lambda o: o['行']), dup


def review_file(job, root, fname, method, focus, modify, passes=None):
    """passes：只审这几遍（遍名）；不给就三遍都审。补审上次没成的遍时，审稿记录接在上次的后面。"""
    t = tools.report_type(root)
    use = [p for p in PASSES if not passes or p[0] in passes]
    text = tools.read(Path(root) / '正文' / fname)
    checklist, card = checklist_of(t), C.card(t, method, fname, root=root)
    material = [*C.fact_docs(root), C.names_table(root), C.outline(root)]
    if t == DT.ANNEX:
        material.append(C.outline(DT.main_root(root)).replace('报告各章标题和表题', '主报告各章标题和表题', 1))
    elif t == DT.DESIGN and DT.annex_root(root):
        material.append(C.outline(DT.annex_root(root)).replace('报告各章标题和表题', '附件册各章标题和表题', 1))
    material += C.pre_docs(root, fname, whole=False)
    material.append(C.doc('正文/%s（带行号）' % fname, tools.numbered(text)))
    kind = method if t == DT.PRE else ('设计专篇主报告' if t == DT.DESIGN else '设计专篇附件册')

    def one(p):
        name, nums, _ = p
        if p is PASSES[-1]:      # 提炼标准以后多出来、哪遍都没分到的 R，归最后一遍，不漏
            nums = tuple(nums) + tuple(int(x) for x in re.findall(r'(?m)^## R(\d+) ', checklist) if not any(int(x) in q[1] for q in PASSES))
        std = C.system(t, ('审稿清单（这一遍只管这几条）', _sections(checklist, nums)), ('本章要点卡', card))
        task = ('任务：审 正文/%s（%s），这一遍只查：%s。%s\n%s\n'
                '按行号从头到尾逐行过，不跳行；同一种问题出现几处就报几处，不只报第一处。别的方面的问题这一遍不管。\n'
                '只报有依据的问题，每条写：行号、级别（必改／建议）、问题、依据（审稿清单 R 号、要点卡红线、K 号或事实出处）、'
                '原文、新文（改好的文字）。标点、编号、计数句这类机器检查能查的不用报。没问题就返回空列表，不凑数。\n'
                '“建议”级只报改了确实更好的。事实只取给你的资料，不编。\n'
                '- 引用别的章、节、表（“同第2.2节”“按第5章”“见表4.1-1”）对不对，照“报告各章标题和表题”核对；对得上的不改，拿不准的不报。\n'
                '- 新文里新写进去的数字（个数、浓度、人数等），依据里写出处；数表格行数的写成计数占位（如 {{计数:表2.1-6}}），不自己数。\n'
                '- 事实库只摘了和职业卫生有关的内容（电气、土建等不摘）：事实库里没有的，不等于资料里没有，拿不准的不以“查不到”为由删。\n%s'
                % (fname, kind, name, ('这次特别看：' + focus) if focus else '', _how(t, p), C.EDIT_RULES))
        r = llm.call_retry(job, '审 %s%s·%s' % ('附件册/' if t == DT.ANNEX else '', fname, name), std, '\n\n'.join(material + [task]), effort='high', schema=REVIEW, phase='逐章审')
        return [dict(o, 遍=name) for o in r['意见']]

    found, missed = [], []
    with ThreadPoolExecutor(len(use)) as ex:
        futs = [(p[0], ex.submit(one, p)) for p in use]
        for name, f in futs:
            try:
                found += f.result()
            except llm.Cancelled:
                raise
            except llm.CallError as e:
                if e.fatal:
                    raise
                missed.append(name)
                job.info('审 %s·%s 没成：%s' % (fname, name, str(e)[:200]))
            except Exception as e:  # noqa: BLE001  一遍没成，别的遍照用，记下来
                missed.append(name)
                job.info('审 %s·%s 没成：%s' % (fname, name, str(e)[:200]))
    if len(missed) == len(use):
        raise llm.CallError('审 %s：%d 遍都没成' % (fname, len(use)))
    ops, dup = _merge(found)
    res = {'文件': fname, '必改': sum(o['级别'] == '必改' for o in ops), '建议': sum(o['级别'] == '建议' for o in ops), '改': 0, '跳过': [],
           '没审的遍': missed}
    if modify and ops:
        p = Path(root) / '正文' / fname
        todo = [o for o in ops if o['新文'] != o['原文']]
        # 只改了章节号、表号的不自动改：一章里看不全，试审时把对的“同第2.2节”“按第5章”改错过；全文审稿的跨章一步会查
        held = [(o, '只改了章节号或表号，要对照别的章核对，没自动改') for o in todo if _ref_only(o['原文'], o['新文'])]
        todo = [o for o in todo if not _ref_only(o['原文'], o['新文'])]
        todo, held3 = _in_source(root, todo)
        todo, held2 = C.guard(root, todo, hints=True)
        new, done, failed = tools.apply_edits(tools.read(p), todo, fname)
        failed = held + held3 + held2 + failed
        if done:
            tools.write(p, new)
        for o in ops:
            o['结果'] = '已改' if any(o is d for d in done) else next((why for e, why in failed if e is o), '没改')
        job.info('按审稿意见改 %s：改 %d 处%s' % (fname, len(done), '，没改成 %d 处' % len(failed) if failed else ''))
        res.update(改=len(done), 跳过=['%s（%s）' % (e['原文'][:30], why) for e, why in failed][:10])
        res['剩余错误'] = C.fix_errors(job, root, fname, method)
    for o in dup:
        o['结果'] = '和同一处的另一条意见重复，按那条'
    _record(root, fname, sorted(ops + dup, key=lambda o: o['行']), append='补审：' + '、'.join(passes) if passes else '')
    return res


CROSS_PRE = ('''1. 评价单元、工作场所、工种、职业病危害因素名称：以岗位因素表（有的话）、第1章表1.8-1、第4章因素识别表为准，查第2～11章写法。
2. 定员、班制、接触时间、设备台数、限值、风险等级等在多章出现的数字是否一致。
3. 第9章关键控制点、第10章问题和建议、第11章结论，与第3～8章的判定是否一致（不多、不少、不矛盾）。
4. “见10.x”“见表x.x-x”这类引用是否指向存在的内容；第1章评价依据与正文引用的标准是否对得上。''')
CROSS_DESIGN = ('''1. 第3章识别的每个职业病危害因素（表3.1-1、表3.1-2）和第4章各类防护设施逐项对应：每个因素有对应的防护设施或不设置的理由（第4.3.5节），
   第4章的设施不对应不存在的因素；名称以岗位因素表为准，和预评价一字不差。
2. 第5章预期效果、预期接触水平（表5.1-1、表5.2-1）和第3.3节预期接触水平、第4章设计控制性能一致；预期结论只用口径 D12 的三种写法（符合／限制接触时间后符合／超限，须个体防护）；写“限制接触时间后符合”的，附件册附件4 有对应的 LEX,8h／接触时间计算，且表3.3-1 措施前水平、表5.2-1 措施后水平与附件册表4.1-1 同数。
3. 第4.8节采纳情况（表4.8-1、表4.8-2）和承接的预评价报告第10章对策措施、附件册附件6 逐条明细一致：条数、处理方式、理由对得上。
4. 第1章设计依据和正文引用的标准一致（引用了的都列了，列了的有用到）；“见表x.x-x”“附件册表x.x-x”“附件册附件N”指向存在的内容。
5. 主报告和附件册的数字一致：设施数量、风量、换气次数、投资合计、预期接触水平等。''')


def cross(job, root, method, modify):
    t = tools.report_type(root)
    vols = C.vol_files(root)                    # 设计专篇：两卷一起核
    files = [label for label, _, _ in vols]
    whole = [C.doc('正文/%s（带行号）' % label, tools.numbered(tools.read(Path(vol) / '正文' / f))) for label, vol, f in vols]
    rules = CROSS_PRE if t == DT.PRE else CROSS_DESIGN
    extra = []
    pre = DT.pre_root(root) if t != DT.PRE else None
    if pre:
        extra = [C.doc('承接的预评价报告 正文/%s（全文）' % f, tools.read(pre / '正文' / f)) for f in tools.body_files(pre) if f.startswith('10')]
    task = ('任务：全文跨章一致性检查%s。\n%s\n'
            '以事实和前面章节为准改后面的。“文件”写正文文件名%s。拿不准或改动太大的写进“没改”。\n%s'
            % ('，发现就改' if modify else '，只记录', rules, '（附件册的写“附件册/文件名”）' if t != DT.PRE else '', C.EDIT_RULES))
    r = llm.call_retry(job, '跨章一致', C.system(t, ('审稿清单', checklist_of(t))),
                       '\n\n'.join([*C.fact_docs(root), C.names_table(root)] + extra + whole + [task]), effort='high', schema=CROSS, phase='跨章')
    lines = ['# 跨章一致', ''] + ['- %s：%s → %s（%s）' % (x['文件'], x['原文'][:40], x['新文'][:40], x['说明']) for x in r['改动']] + \
            [''] + ['- 没改：' + x for x in r['没改']]
    tools.write(Path(root) / '审查' / '审稿记录' / '跨章一致.md', '\n'.join(lines) + '\n')
    if not modify:
        return {'意见': len(r['改动']), '改': 0, '没改': r['没改'][:8]}
    by = {}
    for x in r['改动']:
        # 模型常写成“正文/01_总论.md”：按文件名对，不然整批对不上、一处也不改（11-01 起的审稿都这样）
        name = str(x['文件']).replace('\\', '/')
        name = name[len('正文/'):] if name.startswith('正文/') else name
        lab = name if name in files else Path(name).name
        if '附件册' in name and ('附件册/' + Path(name).name) in files:
            lab = '附件册/' + Path(name).name
        by.setdefault(lab, []).append(x)
    where = {label: (vol, f) for label, vol, f in vols}
    n = 0
    for f, edits in by.items():
        if f in files:
            k, _ = C.apply(job, where[f][0], where[f][1], edits, '跨章一致 ' + f)
            n += k
        else:
            job.info('跨章一致：%s 不是正文里的文件，%d 处没改' % (f, len(edits)))
    pmap(job, lambda f: C.fix_errors(job, where[f][0], where[f][1], method), [f for f in by if f in files])
    return {'意见': len(r['改动']), '改': n, '没改': r['没改'][:8]}


def _plain(msg):
    return re.sub(r'^第\d+行 ', '', msg)


def clear_all(job, root, method, rounds=3, ignore=None):
    """全文机器检查清零：每轮把有错误的文件并行交给模型改。
    ignore＝修改前的检查结果：那时就有的错误不管（改稿只清自己带出来的错误，不动别处）。"""
    old = {}
    if ignore and not ignore.get('格式错误'):
        old = {f: {_plain(e) for e in d.get('错误', [])} for f, d in ignore['按文件'].items()}

    def new_errors(c, f):
        return [e for e in tools.errors_of(c, f) if _plain(e) not in old.get(f, set())]

    for r in range(rounds):
        c = tools.check(root)
        bad = [f for f in tools.body_files(root) if new_errors(c, f)]
        if not bad:
            return c
        job.info('全文检查：错误 %d 条，分 %d 个文件改' % (sum(len(new_errors(c, f)) for f in bad), len(bad)))
        pmap(job, lambda f: C.fix_errors(job, root, f, method, rounds=1, only=list(old.get(f, set())) or None), bad)
    return tools.check(root)


def _sum(checks):
    out = dict(checks[0])
    for c in checks[1:]:
        for k in ('错误', '提示', '待办'):
            out[k] += c[k]
    return out


def run(job, root, modify=True, chapters='', focus='', publish=False, new_report=False, passes=None):
    """passes：{文件标签: [遍名]}，只补审这几个文件的这几遍（上次断线没成的）；跨章一致照做。"""
    root = Path(root)
    method = tools.method_of(root)
    t = tools.report_type(root)
    vols = DT.volumes(root)                  # 设计专篇：主报告＋附件册，两卷都审；“章”写“附件册/02”只审附件册的
    job.stage('准备')
    before = _sum([tools.check(v) for v in vols])
    only = [x.strip() for x in chapters.replace('，', ',').replace('、', ',').split(',') if x.strip()]
    # 网页审稿表单的附件册章键是“附04”：换成“附件册/04”
    only = [re.sub(r'^附(\d)', DT.ANNEX_DIR + r'/\1', x) if re.match(r'^附\d', x) else x for x in only]
    if passes:
        names, labels = {p[0] for p in PASSES}, {label for label, _, _ in C.vol_files(root)}
        bad = ['%s=%s' % (f, n) for f, ns in passes.items() for n in ns if f not in labels or n not in names]
        if bad:
            raise RuntimeError('补审的文件或遍名不对：%s（遍名只有 %s）' % ('；'.join(bad), '、'.join(p[0] for p in PASSES)))
    items = [(vol, f, passes.get(label)) for label, vol, f in C.vol_files(root) if label in passes] if passes else \
        [(vol, f, None) for label, vol, f in C.vol_files(root)
             if not only or any(label.startswith(x) or (not x.startswith('附件册/') and Path(vol) == root and f.startswith(x)) for x in only)]
    kind = method if t == DT.PRE else ('设计专篇（%d 卷）' % len(vols))
    job.info('%s，审 %d 个文件；审前检查：错误 %d、提示 %d、待办 %d' % (kind, len(items), before['错误'], before['提示'], before['待办']))
    backup = ''
    if modify:
        backup = '；'.join(str(tools.backup_body(v, '审稿前')) for v in vols if any(Path(x) == Path(v) for x, _, _ in items)) or \
            str(tools.backup_body(root, '审稿前'))
    F.ensure(job, root)
    job.stage('逐章审')
    results = pmap(job, lambda it: review_file(job, it[0], it[1], method, focus, modify, it[2]), items)
    files = [f for _, f, _ in items]
    ok = [x for x in results if x]
    cr = None
    if not only:
        job.stage('跨章')
        cr = cross(job, root, method, modify)
    job.stage('复查')
    last = _sum([clear_all(job, v, method) if modify else tools.check(v) for v in vols])
    out = {'方法': method, '审了': len(ok), '没审成': [f for f, x in zip(files, results) if not x],
           '必改': sum(x['必改'] for x in ok), '建议': sum(x['建议'] for x in ok), '已改': sum(x['改'] for x in ok),
           '跨章': cr, '检查': {k: last[k] for k in ('错误', '提示', '待办')}, '记录': str(root / '审查' / '审稿记录'), '备份': backup,
           '跳过': [s for x in ok for s in x['跳过']][:10],
           '没审全': ['%s（%s 没审）' % (x['文件'], '、'.join(x['没审的遍'])) for x in ok if x['没审的遍']]}
    if publish and modify and last['错误'] == 0:
        job.stage('出稿')
        out['出稿'] = P.publish(job, root)
    lines = ['审了 %d 个文件：必改 %d 条、建议 %d 条，改了 %d 处' % (out['审了'], out['必改'], out['建议'], out['已改']),
             '跨章一致：%s' % ('意见 %d 条、改 %d 处' % (cr['意见'], cr['改']) if cr else '这次只审了部分章，没做'),
             '检查：错误 %d、提示 %d、待办 %d' % (last['错误'], last['提示'], last['待办'])]
    if out['没审全']:
        lines.append('没审全：%s' % '；'.join(out['没审全']))
    if out.get('出稿'):
        lines.append('PDF：%s' % ('共 %s 页，核对通过' % out['出稿']['页数'] if out['出稿']['通过'] else '没通过'))
    if not new_report:
        P.note(root, '审稿' if modify else '审稿（只看不改）', lines)
    return out


def job_review(job):
    p = job.params
    return run(job, job.project, modify=p.get('修改', True), chapters=p.get('章', ''), focus=p.get('重点', ''), publish=p.get('出稿', False),
               passes=p.get('遍') or None)
