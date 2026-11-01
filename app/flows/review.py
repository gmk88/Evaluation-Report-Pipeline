"""审稿：逐个文件对照审稿清单、要点卡、用户口径审，按意见改；跨章一致；全文机器检查清零；可接着出稿。"""
import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import llm, tools
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
    out, cancelled = [None] * len(items), False
    with ThreadPoolExecutor(max(1, len(items))) as ex:
        futs = [ex.submit(fn, x) for x in items]
        for i, f in enumerate(futs):
            try:
                out[i] = f.result()
            except llm.Cancelled:
                cancelled = True
            except Exception as e:  # noqa: BLE001  一个文件没成不拖垮整轮，记下来
                job.info('出错：%s' % str(e)[:200])
    if cancelled or job.cancelled:
        raise llm.Cancelled()
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
        if pool is None:
            pool = re.sub(r'\s+', '', '\n'.join(t for _, t in tools.source_texts(root)))
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


def _record(root, fname, ops):
    rows = ['# 审稿记录 ' + fname, '', '| 行 | 级别 | 问题 | 依据 | 原文 | 改成 | 结果 |', '|---|---|---|---|---|---|---|']
    for o in ops:
        rows.append('| ' + ' | '.join(str(o.get(k, '')).replace('|', '\\|').replace('\n', ' ')[:300]
                                      for k in ('行', '级别', '问题', '依据', '原文', '新文', '结果')) + ' |')
    if not ops:
        rows.append('| | | 没有意见 | | | | |')
    tools.write(Path(root) / '审查' / '审稿记录' / fname, '\n'.join(rows) + '\n')


def review_file(job, root, fname, method, focus, modify):
    text = tools.read(Path(root) / '正文' / fname)
    std = C.system(('审稿清单', C.std('审稿清单.md')), ('本章要点卡', C.card(method, fname)))
    material = [*C.fact_docs(root), C.names_table(root), C.outline(root)]
    task = ('任务：审 正文/%s（%s）。逐段对照审稿清单各维度、要点卡的红线和常见错误、用户口径。%s\n'
            '只报有依据的问题，每条写：行号、级别（必改／建议）、问题、依据（审稿清单 R 号、要点卡红线、K 号或事实出处）、'
            '原文、新文（改好的文字）。标点、编号、计数句这类机器检查能查的不用报。没问题就返回空列表，不凑数。\n'
            '“建议”级只报改了确实更好的。事实只取给你的资料，不编。\n'
            '- 引用别的章、节、表（“同第2.2节”“按第5章”“见表4.1-1”）对不对，照“报告各章标题和表题”核对；对得上的不改，拿不准的不报。\n'
            '- 新文里新写进去的数字（个数、浓度、人数等），依据里写出处；数表格行数的写成计数占位（如 {{计数:表2.1-6}}），不自己数。\n'
            '- 事实库只摘了和职业卫生有关的内容（电气、土建等不摘）：事实库里没有的，不等于资料里没有，拿不准的不以“查不到”为由删。\n%s'
            % (fname, method, ('这次特别看：' + focus) if focus else '', C.EDIT_RULES))
    r = llm.call_retry(job, '审 ' + fname, std, '\n\n'.join(material + [C.doc('正文/%s（带行号）' % fname, tools.numbered(text)), task]),
                       effort='high', schema=REVIEW, phase='逐章审')
    ops = r['意见']
    res = {'文件': fname, '必改': sum(o['级别'] == '必改' for o in ops), '建议': sum(o['级别'] == '建议' for o in ops), '改': 0, '跳过': []}
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
    _record(root, fname, ops)
    return res


def cross(job, root, method, modify):
    files = tools.body_files(root)
    whole = [C.doc('正文/%s（带行号）' % f, tools.numbered(tools.read(Path(root) / '正文' / f))) for f in files]
    task = ('任务：全文跨章一致性检查%s。\n1. 评价单元、工作场所、工种、职业病危害因素名称：以岗位因素表（有的话）、第1章表1.8-1、第4章因素识别表为准，查第2～11章写法。\n'
            '2. 定员、班制、接触时间、设备台数、限值、风险等级等在多章出现的数字是否一致。\n'
            '3. 第9章关键控制点、第10章问题和建议、第11章结论，与第3～8章的判定是否一致（不多、不少、不矛盾）。\n'
            '4. “见10.x”“见表x.x-x”这类引用是否指向存在的内容；第1章评价依据与正文引用的标准是否对得上。\n'
            '以事实和前面章节为准改后面的。“文件”写正文文件名。拿不准或改动太大的写进“没改”。\n%s'
            % ('，发现就改' if modify else '，只记录', C.EDIT_RULES))
    r = llm.call_retry(job, '跨章一致', C.system(('审稿清单', C.std('审稿清单.md'))),
                       '\n\n'.join([*C.fact_docs(root), C.names_table(root)] + whole + [task]), effort='high', schema=CROSS, phase='跨章')
    lines = ['# 跨章一致', ''] + ['- %s：%s → %s（%s）' % (x['文件'], x['原文'][:40], x['新文'][:40], x['说明']) for x in r['改动']] + \
            [''] + ['- 没改：' + x for x in r['没改']]
    tools.write(Path(root) / '审查' / '审稿记录' / '跨章一致.md', '\n'.join(lines) + '\n')
    if not modify:
        return {'意见': len(r['改动']), '改': 0, '没改': r['没改'][:8]}
    by = {}
    for x in r['改动']:
        by.setdefault(x['文件'], []).append(x)
    n = 0
    for f, edits in by.items():
        if f in files:
            k, _ = C.apply(job, root, f, edits, '跨章一致 ' + f)
            n += k
    pmap(job, lambda f: C.fix_errors(job, root, f, method), [f for f in by if f in files])
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


def run(job, root, modify=True, chapters='', focus='', publish=False, new_report=False):
    root = Path(root)
    method = tools.method_of(root)
    job.stage('准备')
    before = tools.check(root)
    files = tools.body_files(root)
    only = [x for x in chapters.replace('，', ',').replace('、', ',').split(',') if x.strip()]
    files = [f for f in files if not only or any(f.startswith(x.strip()) for x in only)]
    job.info('%s，审 %d 个文件；审前检查：错误 %d、提示 %d、待办 %d' % (method, len(files), before['错误'], before['提示'], before['待办']))
    backup = str(tools.backup_body(root, '审稿前')) if modify else ''
    F.ensure(job, root)
    job.stage('逐章审')
    results = pmap(job, lambda f: review_file(job, root, f, method, focus, modify), files)
    ok = [x for x in results if x]
    cr = None
    if not only:
        job.stage('跨章')
        cr = cross(job, root, method, modify)
    job.stage('复查')
    last = clear_all(job, root, method) if modify else tools.check(root)
    out = {'方法': method, '审了': len(ok), '没审成': [f for f, x in zip(files, results) if not x],
           '必改': sum(x['必改'] for x in ok), '建议': sum(x['建议'] for x in ok), '已改': sum(x['改'] for x in ok),
           '跨章': cr, '检查': {k: last[k] for k in ('错误', '提示', '待办')}, '记录': str(root / '审查' / '审稿记录'), '备份': backup,
           '跳过': [s for x in ok for s in x['跳过']][:10]}
    if publish and modify and last['错误'] == 0:
        job.stage('出稿')
        out['出稿'] = P.publish(job, root)
    lines = ['审了 %d 个文件：必改 %d 条、建议 %d 条，改了 %d 处' % (out['审了'], out['必改'], out['建议'], out['已改']),
             '跨章一致：%s' % ('意见 %d 条、改 %d 处' % (cr['意见'], cr['改']) if cr else '这次只审了部分章，没做'),
             '检查：错误 %d、提示 %d、待办 %d' % (last['错误'], last['提示'], last['待办'])]
    if out.get('出稿'):
        lines.append('PDF：%s' % ('共 %s 页，核对通过' % out['出稿']['页数'] if out['出稿']['通过'] else '没通过'))
    if not new_report:
        P.note(root, '审稿' if modify else '审稿（只看不改）', lines)
    return out


def job_review(job):
    p = job.params
    return run(job, job.project, modify=p.get('修改', True), chapters=p.get('章', ''), focus=p.get('重点', ''), publish=p.get('出稿', False))
