"""改稿：用户一条意见 → 记进标准（用户口径 K 号、检查规则、要点卡）→ 找到各项目要改的地方 → 逐个文件改 → 检查清零 → 出稿。

禁用词由程序把关，免得误伤：
- 加之前，在没参与这次修改的定稿项目里查一遍，有命中的说明太宽，改放“提示”；
- 改完以后，参与修改的项目里还有命中（修改助手判为正常用法、没改）的，也降为“提示”。
"""
import datetime
import re
from pathlib import Path

from .. import config, jobs, llm, rules, tools
from ..tools import doctype as DT
from . import common as C
from . import facts as F
from . import publish as P
from . import review as RV

COMMON = lambda: config.STD / '通用检查规则.md'  # noqa: E731


DESIGN_RULES_HEADER = '# 设计专篇检查规则\n\n<!-- 设计专篇两卷都适用的机器检查，叠在通用检查规则上。 -->'


def rule_file(kind):
    """通用意见的检查规则写到哪：只适用设计专篇的写 标准/设计专篇/检查规则.md，别的（全部、只适用预评价）写通用规则。"""
    return C.std_path(DT.DESIGN, '类型检查规则') if kind == DT.DESIGN else COMMON()


def _of_kind(paths, kind):
    """按适用类型挑项目（验证规则太不太宽用）：全部＝不挑。"""
    return [p for p in paths if kind in ('全部', '', None) or tools.report_type(p) == kind]
DECIDE = {
    'type': 'object', 'additionalProperties': False,
    'required': ['范围', '适用类型', '标题', '规矩', '例外', '取代', '规则', '要点卡', '改法', '关键词', '涉及章', '要核事实'],
    'properties': {
        '范围': {'type': 'string', 'enum': ['通用', '项目']},
        '适用类型': {'type': 'string', 'enum': ['全部', DT.PRE, DT.DESIGN]},
        '标题': {'type': 'string'}, '规矩': {'type': 'string'}, '例外': {'type': 'string'},
        '取代': {'type': 'array', 'items': {'type': 'string'}},
        '规则': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['节', '词'],
            'properties': {'节': {'type': 'string', 'enum': ['禁用', '正则禁用', '提示', '必备']}, '词': {'type': 'string'}}}},
        '要点卡': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['卡', '条目'],
            'properties': {'卡': {'type': 'string'}, '条目': {'type': 'string'}}}},
        '改法': {'type': 'string'},
        '关键词': {'type': 'array', 'items': {'type': 'string'}},
        '涉及章': {'type': 'array', 'items': {'type': 'string'}},
        '要核事实': {'type': 'boolean'},
    },
}
TASK = '''任务：把这条意见变成标准。
- 范围：以后所有报告都要这样写的写“通用”；只针对某个项目的具体内容（某张表、某个数字）写“项目”。
- 适用类型：这条意见管哪种报告：预评价报告和设计专篇都适用写“全部”；只针对预评价（评价判定、补充建议、可研缺口写法等）写“预评价”；
  只针对职业病防护设施设计专篇（设施设计参数、采纳情况、投资概算、附件册等）写“设计专篇”。
- 标题、规矩、例外：写进用户口径的一条。规矩写成“写什么、不写什么、怎么判”，一两句。推翻了用户口径里哪几条，把 K 号写进“取代”。
- 规则：能用词语或正则查出来的写法，给检查规则：要删掉的说法放“禁用”（要具体，不误伤正常用法，比如不禁单独的“核实”）；
  要按上下文判断的放“提示”；正则放“正则禁用”；“必备”只用于范围为“项目”的。查不了的给空列表。
- 要点卡：与某章写法有关的，给对应卡加一句红线（卡写文件名，如 05.md、03_风险评估法.md；设计专篇的卡写“设计专篇/04.md”“设计专篇/附件.md”）。
- 改法：给修改助手看的具体改法，一段话。
- 关键词：在正文里找要改的地方用的词或正则，比规则宽一些，宁可多找。
- 涉及章：只和某几章有关时写章号前缀（如 "05"、"10"）；和全文有关写空列表。
- 要核事实：改的时候要不要回资料核对事实。'''


def _strip_comments(text):
    return re.sub(r'<!--.*?-->', '', text, flags=re.S)


def count_hits(root, sec, word):
    """正文里（不算注释）命中某条规则的次数。"""
    try:
        rx = re.compile(word) if sec == '正则禁用' else None
    except re.error:
        return 0
    n = 0
    for f in tools.body_files(root):
        for ln in _strip_comments(tools.read(Path(root) / '正文' / f)).split('\n'):
            if (rx.search(ln) if rx else word in ln):
                n += 1
    return n


def _hit_lines(root, pats):
    """各正文文件里命中关键词的行号。关键词按正则查，写错的按字面查。"""
    out = {}
    rxs = []
    for w in pats:
        if w.strip():
            try:
                rxs.append(re.compile(w))
            except re.error:
                rxs.append(re.compile(re.escape(w)))
    for f in tools.body_files(root):
        # 注释换成同样多的空行再查：多行注释中间的行不算命中，行号也不变（和 count_hits 口径一致）
        text = re.sub(r'<!--.*?-->', lambda m: '\n' * m.group(0).count('\n'), tools.read(Path(root) / '正文' / f), flags=re.S)
        for i, ln in enumerate(text.split('\n')):
            if any(r.search(ln) for r in rxs):
                out.setdefault(f, set()).add(i + 1)
    return out


def _next_k():
    nums = [int(x) for x in re.findall(r'^## K(\d+)', C.std('用户口径.md'), re.M)]
    return 'K%03d' % (max(nums) + 1 if nums else 1)


def _add_k(k, d, words, date, final):
    p = config.STD / '用户口径.md'
    text = tools.read(p) if p.exists() else ('# 用户口径\n\n<!-- 用户定过的写法，全部项目适用。新意见由“改稿”追加，编号只增不改；'
                                            '推翻旧条时，旧条标题后加“（已被 K0xx 取代）”。 -->\n')
    for old in d['取代']:
        text = re.sub(r'^(## %s\b.*?)$' % re.escape(old), lambda m: m.group(1) if '取代' in m.group(1) else m.group(1) + '（已被 %s 取代）' % k,
                      text, count=1, flags=re.M)
    how = '、'.join('%s“%s”' % (s, w) for s, w in final) or '审稿'
    entry = ['', '## %s %s（%s）' % (k, d['标题'], date), '- 规矩：' + d['规矩'], '- 用户原话：“%s”' % words,
             '- 怎么查：' + how, '- 例外：' + (d['例外'] or '无')]
    if d.get('适用类型') not in (None, '', '全部'):
        entry.insert(3, '- 适用：只适用%s' % d['适用类型'])
    tools.write(p, text.rstrip('\n') + '\n' + '\n'.join(entry) + '\n')


def _card_file(name):
    """要点卡文件：“设计专篇/04.md”是设计专篇的卡（doctype 配的卡目录），别的是预评价的 标准/各章要点/。"""
    name = name if name.endswith('.md') else name + '.md'
    if name.startswith(DT.DESIGN + '/'):
        return C.std_path(DT.DESIGN, '卡目录') / name[len(DT.DESIGN) + 1:]
    return config.STD / '各章要点' / name


def _add_cards(items, k):
    for x in items:
        p = _card_file(x['卡'])
        if not p.exists():
            continue
        text = tools.read(p)
        line = '- %s（%s）' % (x['条目'].strip().lstrip('- '), k)
        m = re.search(r'^## 4 红线.*?(?=^## |\Z)', text, re.M | re.S)
        if m:
            text = text[:m.end()].rstrip('\n') + '\n' + line + '\n\n' + text[m.end():].lstrip('\n')
        else:
            text = text.rstrip('\n') + '\n' + line + '\n'
        tools.write(p, text)


def targets(projects, kind='全部'):
    """要改的项目。“全部”按意见的适用类型挑：kind＝预评价／设计专篇时只取这一类的项目。"""
    if projects in ('全部', ['全部'], None, [], ''):
        roots = [Path(r['文件夹']).expanduser() for r in tools.registered()
                 if kind in ('全部', '', None) or r.get('类型', DT.PRE) == kind]
    else:
        roots = [Path(p).expanduser() for p in ([projects] if isinstance(projects, str) else projects)]
    return [r.resolve() for r in roots if (r / '正文').is_dir()]


def _heads_of(root):
    return '\n'.join('%s：%s' % (f, '；'.join(h.lstrip('#').strip() for h in tools.headings(tools.read(Path(root) / '正文' / f), 2)[:12]))
                     for f in tools.body_files(root)) if root else ''


def decide(job, words, date):
    sample = tools.sample_for('风险评估法') or tools.sample_for('类比法')
    heads = _heads_of(sample)
    design = tools.sample_for('', kind=DT.DESIGN) or DT.skeleton(DT.DESIGN)      # 设计专篇的节：样板，没有样板看骨架
    if (Path(design) / '正文').is_dir() and tools.body_files(design):
        heads += '\n设计专篇主报告：\n' + _heads_of(design)
    cards = sorted(p.name for p in (config.STD / '各章要点').glob('*.md')) if (config.STD / '各章要点').is_dir() else []
    dcard = C.std_path(DT.DESIGN, '卡目录')
    cards += sorted('%s/%s' % (DT.DESIGN, p.name) for p in dcard.glob('*.md')) if dcard and dcard.is_dir() else []
    return llm.call_retry(job, '定口径', C.system(('通用检查规则', C.std('通用检查规则.md'))), '\n\n'.join([
        C.doc('报告各章的节（样板）', heads), '各章要点卡文件：%s' % ('、'.join(cards) or '还没有'),
        '用户的修改意见（原话，%s）：“%s”' % (date, words), TASK]), effort='high', schema=DECIDE, phase='定口径')


def _modify(job, root, fname, words, d, k, lines):
    method, t = tools.method_of(root), tools.report_type(root)
    text = tools.read(Path(root) / '正文' / fname)
    parts = (C.fact_docs(root) if d['要核事实'] else []) + [C.doc('正文/%s（带行号）' % fname, tools.numbered(text))]
    where = ('关键词命中的行：%s' % '、'.join(str(x) for x in sorted(lines))) if lines else '全文按意见查改'
    task = ('用户的修改意见（原话）：“%s”\n改法：%s\n%s\n任务：按意见改这个文件。只改与这条意见有关的地方，前后文读顺；涉及事实的照资料，不编。'
            '命中的行如果其实是正常用法、不属于这条意见要改的，不改，写进“跳过”并说明理由。\n%s' % (words, d['改法'], where, C.EDIT_RULES))
    e = llm.call_retry(job, '改 %s %s' % (tools.project_name(root)[:8], fname),
                       C.system(t, C.write_format(), ('这条意见（%s）' % k, '%s %s：%s' % (k, d['标题'], d['规矩'])),
                                ('本章要点卡', C.card(t, method, fname, root=root))),
                       '\n\n'.join(parts + [task]), effort='high', schema=C.EDITS, phase='修改')
    n, bad = C.apply(job, root, fname, e['改动'], '按意见改 ' + fname)
    return {'项目': str(root), '文件': fname, '改': n, '跳过': e['跳过'] + bad}


def run(job, words, projects='全部', publish=True, date=None):
    date = date or datetime.date.today().isoformat()
    roots = targets(projects)
    if not roots:
        raise RuntimeError('没有可改的项目')
    jobs.lock_more(job, roots)
    job.stage('定口径')
    d = decide(job, words, date)
    kind = d.get('适用类型') or '全部'
    if projects in ('全部', ['全部'], None, [], '') and kind != '全部':
        roots = [r for r in roots if r in targets(projects, kind)]       # 只适用一种报告的意见，“全部”只改这一类
        job.info('这条意见只适用%s，改 %d 个项目' % (kind, len(roots)))
    k = _next_k() if d['范围'] == '通用' else '项目'
    asked = [(r['节'], r['词'].strip()) for r in d['规则'] if r['词'].strip()]
    broad = []
    if d['范围'] == '通用':
        tools.backup_std('改稿' + k)
        mine = {str(r) for r in roots}
        rf = rule_file(kind)
        others = [p for p in _of_kind(tools.finished_projects(), kind) if str(p.resolve()) not in mine]
        if kind == DT.DESIGN and not others:
            others = [DT.skeleton(DT.DESIGN)]       # 还没有定稿的专篇：拿专篇骨架验证（骨架要零错误）
        final = []
        for s, w in asked:
            if s == '必备':
                continue
            n = sum(count_hits(p, s, w) for p in others) if s in ('禁用', '正则禁用') else 0
            if n:
                broad.append('%s（定稿项目里有 %d 处正常用法）' % (w, n))
                if s == '禁用':
                    final.append(('提示', w))
                continue
            final.append((s, w))
        _add_k(k, d, words, date, final)
        for f in {COMMON(), rule_file(DT.DESIGN)}:
            rules.remove_by_note(f, d['取代'])
        rules.add(rf, final, '%s %s' % (k, date), **({} if rf == COMMON() else {'header': DESIGN_RULES_HEADER}))
        rules.valid_regex(rf)
        if kind == DT.PRE:
            # 只适用预评价的：通用规则照加，设计专篇在自己的检查规则里“豁免通用”
            rules.add(rule_file(DT.DESIGN), [('豁免', w) for s, w in final if s in ('禁用', '正则禁用', '提示')],
                      '%s %s：只适用预评价' % (k, date), header=DESIGN_RULES_HEADER)
        _add_cards(d['要点卡'], k)
    else:
        final = asked
        for r in roots:
            rules.add(r / '检查规则.md', asked, '用户 %s：%s' % (date, words[:30].replace('\n', ' ')), '# 检查规则')
    job.info('%s：%s（规则 %d 条%s）' % (k, d['标题'], len(final), '，太宽改提示：' + '、'.join(broad) if broad else ''))
    job.stage('修改')
    pats = d['关键词'] + [w if s == '正则禁用' else re.escape(w) for s, w in final if s != '必备']
    tasks, before = [], {}
    roots = [v for r in roots for v in DT.volumes(r)]       # 设计专篇的附件册跟着主报告改
    for r in roots:
        hits = _hit_lines(r, pats) if pats else {}
        files = tools.body_files(r)
        if d['涉及章']:
            pick = {f: hits.get(f, set()) for f in files if any(f.startswith(c) for c in d['涉及章'])}
            pick.update({f: v for f, v in hits.items() if f not in pick})
        elif pats:
            pick = hits
        else:
            pick = {f: set() for f in files}
        job.info('%s：%d 个文件要看' % (tools.project_name(r), len(pick)))
        if not pick:
            continue
        before[str(r)] = tools.check(r)
        tools.backup_body(r, '改稿前')
        if d['要核事实']:
            F.ensure(job, r)
        tasks += [(r, f, v) for f, v in pick.items()]
    done = [x for x in RV.pmap(job, lambda t: _modify(job, t[0], t[1], words, d, k, t[2]), tasks) if x]
    job.stage('复查')
    demoted = []
    if d['范围'] == '通用':
        for s, w in final:
            if s not in ('禁用', '正则禁用'):
                continue
            n = sum(count_hits(Path(r), s, w) for r in before)
            if n:
                how = rules.demote(rf, w, '%s %s：改完后还有 %d 处正常用法，由禁用降为提示' % (k, date, n))
                demoted.append('%s（%d 处，%s）' % (w, n, '降为提示' if how == '提示' else '删掉'))
        if demoted:
            job.info('改完还有正常用法的禁用词：' + '、'.join(demoted))
    out = {'口径号': k, '标题': d['标题'], '规则': ['%s：%s' % x for x in final], '太宽改提示': broad, '降为提示': demoted, '项目': []}
    # 先给各卷清零（附件册在主报告前：主报告表4.8-1 要核附件册附件6 的条数），再按报告出稿
    cleared = {}
    for r in sorted(roots, key=lambda x: tools.report_type(x) != DT.ANNEX):
        if str(r) not in before:
            continue
        b = before[str(r)]
        if b.get('格式错误'):
            cleared[str(r)] = (b, '这个项目正文格式读不了，没做检查清零')
        else:
            cleared[str(r)] = (RV.clear_all(job, r, tools.method_of(r), ignore=b), '')
    for r in roots:
        name = tools.project_name(r)
        vols = {str(v) for v in DT.volumes(r)[1:]} if tools.report_type(r) == DT.DESIGN else set()
        if str(r) not in before and not (vols & set(before)):
            out['项目'].append({'名称': name, '改': 0, '说明': '没有要改的地方'})
            continue
        mine = [x for x in done if x['项目'] == str(r)]
        n = sum(x['改'] for x in mine)
        c, note = cleared.get(str(r)) or (tools.check(r), '')     # 设计专篇只改了附件册：主报告照查
        item = {'名称': name, '改': n, '检查': {x: c[x] for x in ('错误', '提示', '待办')}, '跳过': [s for x in mine for s in x['跳过']][:6]}
        if note:
            item['说明'] = note
        # 附件册改了的，跟着主报告一起出稿（出稿主报告时接着出附件册）；两卷合计零错误才出
        n_all = n + sum(x['改'] for x in done if x['项目'] in vols)
        errs = c['错误'] + sum(tools.check(v)['错误'] for v in DT.volumes(r)[1:]) if vols else c['错误']
        if publish and n_all and errs == 0 and tools.report_type(r) != DT.ANNEX:
            job.stage('出稿')
            item['出稿'] = P.publish(job, r)
        P.note(r, '按意见修改 %s' % k, ['用户原话：“%s”' % words,
                                     '改 %d 处：%s' % (n, '、'.join('%s %d 处' % (x['文件'], x['改']) for x in mine if x['改']) or '无'),
                                     '检查：错误 %d、提示 %d、待办 %d' % (c['错误'], c['提示'], c['待办']),
                                     ('PDF：共 %s 页' % item['出稿']['页数']) if item.get('出稿') and item['出稿']['通过'] else ''])
        out['项目'].append(item)
    return out


def job_revise(job):
    p = job.params
    return run(job, p['意见'], p.get('项目') or '全部', publish=p.get('出稿', True), date=p.get('日期'))
