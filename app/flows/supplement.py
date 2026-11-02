"""补充资料：报告写好以后建设单位补来的资料（初设、设备表、定员表、检测报告……）。

两步，中间等用户确认：
1. 补资料：新资料拷进 资料/原件/补充_月日-时分/，转成文字记进项目资料；只拿新资料对照事实库、口径表，
   摘出变化（新增、更正、补上了“可研没有写的”），写进 资料/事实片段/补资料/待确认.json。不改事实库，也不改正文。
2. 按新资料改：用户勾上的变化改进事实库、口径表、岗位因素表，再只改用到这些事实的章，检查清零。
整份重摘事实库的花费按全部资料算（田林约 7 美元），这里只按新资料和要改的章算。"""
import datetime
import hashlib
import json
import re
import shutil
import threading
from pathlib import Path

from .. import config, gaps, llm, tools
from . import common as C
from . import facts as F
from . import publish as P
from . import review as RV

DIR = '事实片段/补资料'          # 资料/事实片段/补资料/：转换的中间结果、各批摘的变化、待确认的变化（转资料时不当资料）
PENDING = '待确认.json'
STATE = '进行.json'             # 拷好、转好的新资料：接着做时不再拷一遍
KINDS = ('新增', '更正', '补上')
# 这几节的变化会影响岗位因素表（评价单元、工作场所、工种、定员、接触时间、因素）
TABLE_HEADS = ('产品、原辅材料', '生产工艺', '主要设备', '劳动定员', '职业病防护设施', '检修')

CHANGES = {
    'type': 'object', 'additionalProperties': False, 'required': ['变化'],
    'properties': {'变化': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False, 'required': ['类别', '节', '原文', '新文', '影响', '说明'],
        'properties': {'类别': {'type': 'string', 'enum': list(KINDS)}, '节': {'type': 'string'},
                       '原文': {'type': 'string'}, '新文': {'type': 'string'},
                       '影响': {'type': 'array', 'items': {'type': 'string'}}, '说明': {'type': 'string'}}}}},
}
TASK = '''任务：上面是建设单位补来的新资料（只给了新资料）。拿它对照现有的事实库和口径表，列出变化：
- 新增：新资料里有、事实库里没有的事实。节＝事实库的节标题（照抄），原文写空，新文写成“- 事实（出处：资料名 节号）”。
- 更正：新资料和事实库说法不一的（数字、名称、数量、班制等）。原文照抄事实库里那一整行（以“- ”开头），
  新文写按新资料改好的整行，出处换成新资料；说明写旧说法和新说法各是什么。初设、设备表、布置图比可研具体的，按新资料。
- 补上：口径表“## 可研没有写的”里的事项，新资料写了的。原文照抄口径表里那一整行（以“- ”开头），
  新文写进事实库的那条（“- 事实（出处：…）”），节＝它该放进事实库的节标题。
影响：这条变化要改哪些正文文件，从下面“正文文件”里挑文件名（照抄），拿不准的宁多勿少；只影响事实库、不影响正文的给空列表。
不列：和事实库说法一样的、只是写法不同的；与职业病危害识别和评价无关的（财务、电气、土建结构、消防、安全生产、环保排放）。
没有变化就返回空列表。'''


def _dir(root):
    return Path(root) / '资料' / DIR


def pending(root):
    """待确认的变化（没有返回 None）。网页的补资料面板和“按新资料改”用。"""
    p = _dir(root) / PENDING
    try:
        return json.loads(tools.read(p)) if p.is_file() else None
    except ValueError:
        return None


def discard(root):
    """丢掉待确认的变化和中间结果（资料本身已经放进项目了，不删）。"""
    d = _dir(root)
    if d.is_dir():
        shutil.rmtree(d)
    F.drop_empty(root)
    try:
        (Path(root) / '资料' / '事实片段').rmdir()
    except OSError:
        pass


def _save(root, name, data):
    tools.write(_dir(root) / name, json.dumps(data, ensure_ascii=False, indent=1, default=str))


def _load(root, name):
    p = _dir(root) / name
    try:
        return json.loads(tools.read(p)) if p.is_file() else None
    except ValueError:
        return None


# ---------------- 1. 拷进项目、转文字、记进项目资料 ----------------

def _copy(given, dst):
    """拷法和新报告一样：资料文件夹里的快捷方式不拷（可能指向资料以外的文件）。"""
    dst.mkdir(parents=True, exist_ok=True)
    for x in given:
        s = Path(x).expanduser()
        if s.is_dir():
            shutil.copytree(s, dst / s.name, dirs_exist_ok=True,
                            ignore=lambda d, names: [n for n in names if (Path(d) / n).is_symlink()])
        elif s.is_file():
            shutil.copy2(s, dst / s.name)


def _convert(job, root, folder, tmp=None):
    """把 folder 里的新资料转成文字。在中间结果文件夹 tmp 里转（转资料的程序会先删掉 资料/文本/ 里原有的块，不能直接转）。
    返回 ([(资料名, 来源路径, 全文)], [没转成的说明])。"""
    tmp = tmp or _dir(root) / '转换'
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    rc, out = tools.report('资料', tmp, folder)
    if out.strip():
        job.info(out.strip()[:400])
    idx = tmp / '资料' / '文本' / '索引.json'
    data = json.loads(tools.read(idx)) if idx.is_file() else {'块': [], '跳过': []}
    groups = {}
    for b in data.get('块', []):
        p = idx.parent / b['文件']
        if p.is_file():
            groups.setdefault(b['来源'], []).append(tools.read(p))
    shutil.rmtree(tmp, ignore_errors=True)
    return [(Path(k).name, k, '\n'.join(v)) for k, v in groups.items()], data.get('跳过', [])


def _register(job, root, tag, texts):
    """新资料的文字存进 资料/文本/补充/，并记进项目资料，以后审稿、整份重摘事实库都读得到：
    - 有 资料/文本/索引.json 的（新报告建的项目）：在索引里加块；
    - 设了“事实来源”的（老项目）：加进事实来源；
    - 靠上一级 提取文本/ 的：原来的那些和新资料一起写成“事实来源”（不然以后只认得新资料）。"""
    root = Path(root)
    tdir = root / '资料' / '文本' / '补充'
    names = []
    for i, (name, src, text) in enumerate(texts, 1):
        stem = re.sub(r'[\\/:*?"<>|\s()（）]+', '', Path(name).stem)[:20] or '资料'
        fn = '%s_%02d_%s.txt' % (tag, i, stem)
        tools.write(tdir / fn, text)
        names.append((fn, src, len(text)))
    sets = tools.project_settings(root)
    idx = root / '资料' / '文本' / '索引.json'
    if sets.get('事实来源') or not idx.is_file():
        old = sets.get('事实来源') or [str(root.parent / '提取文本' / n) for n, _ in tools.source_texts(root)]
        add = ['资料/文本/补充/' + fn for fn, _, _ in names]
        sets['事实来源'] = list(old) + [x for x in add if x not in old]
        tools.write(root / tools.SETTINGS_FILE, json.dumps(sets, ensure_ascii=False, indent=2))
        job.info('新资料已加进项目设置的“事实来源”（%d 份）' % len(add))
        return
    data = json.loads(tools.read(idx))
    have = {b['文件'] for b in data.get('块', [])}
    data.setdefault('块', []).extend({'文件': '补充/' + fn, '来源': src, '字数': n, '标题': []}
                                    for fn, src, n in names if '补充/' + fn not in have)
    tools.write(idx, json.dumps(data, ensure_ascii=False, indent=1))
    job.info('新资料已记进资料索引（%d 份）' % len(names))


def _take_in(job, root, given):
    """拷资料、转文字、记进项目资料；接着做时（同样的资料）不再做。返回 进行.json 的内容。"""
    st = _load(root, STATE)
    if st and (st.get('资料') == given or not given):
        job.info('接着上次做：新资料上次已拷进项目、转成文字')
        return st
    tag = datetime.datetime.now().strftime('%m%d-%H%M')
    folder = Path(root) / '资料' / '原件' / ('补充_' + tag)
    _copy(given, folder)
    texts, skipped = _convert(job, root, folder)
    if not texts:
        why = '；'.join(skipped[:6]) or '没有能转成文字的文件'
        raise RuntimeError('新资料没转出文字（扫描件要先识别成文字）：%s。资料已拷进 %s' % (why, folder))
    _register(job, root, tag, texts)
    st = {'资料': given, '文件夹': str(folder), '新资料': [n for n, _, _ in texts], '没转': skipped,
          '字数': sum(len(t) for _, _, t in texts), '时间': tag}
    _save(root, STATE, st)
    tools.write(_dir(root) / '新资料.txt', '\n\n'.join(C.doc(n, t) for n, _, t in texts))
    _save(root, '新资料.json', [[n, t] for n, _, t in texts])
    return st


# ---------------- 2. 摘变化 ----------------

def _file_list(root):
    out = []
    for f in tools.body_files(root):
        hs = [h.lstrip('#').strip() for h in tools.headings(tools.read(Path(root) / '正文' / f), 2)][:10]
        out.append('%s：%s' % (f, '；'.join(hs)))
    return C.doc('正文文件（影响里照抄文件名）', '\n'.join(out))


def _gap_lines(kj):
    m = re.search(r'^## 可研没有写的.*?$(.*?)(?=^## |\Z)', kj, re.M | re.S)
    return [ln for ln in (m.group(1) if m else '').split('\n') if ln.startswith('- ')]


def _changed_tokens(old, new):
    """更正前后变了的数字（带单位），在正文里找用到旧说法的地方。太短的（单个数字）不算，免得到处命中。"""
    pat = r'\d+(?:\.\d+)?\s*[^\d\s，。；、,.（）()：:|]{0,2}'
    a, b = set(re.findall(pat, F._norm(old))), set(re.findall(pat, F._norm(new)))
    return sorted(x for x in a - b if len(x) >= 3)


def _hits(root, words):
    """正文里出现这些字样的文件（注释不算）。"""
    out = set()
    for f in tools.body_files(root):
        text = re.sub(r'<!--.*?-->', '', tools.read(Path(root) / '正文' / f), flags=re.S)
        if any(w in text for w in words):
            out.add(f)
    return out


def _filter(job, root, raw, fact_md, kj):
    """模型给的变化逐条过一遍：更正的原文要是事实库里的整行、补上的原文要是口径表里的一行、新增不重复。
    影响只留正文里真有的文件，再加上正文里还写着旧数字的文件。"""
    body = set(tools.body_files(root))
    gap = set(_gap_lines(kj))
    have = {F._norm(ln) for ln in fact_md.split('\n') if ln.startswith('- ')}
    seen, out = set(), []
    for x in raw:
        k, old, new = x.get('类别'), (x.get('原文') or '').strip(), (x.get('新文') or '').strip()
        why = ''
        if k not in KINDS:
            why = '类别不对'
        elif not new.startswith('- ') or '\n' in new or new.lstrip('- ').startswith('#'):
            why = '新文不是以“- ”开头的一整条事实'
        elif k == '更正' and not F._fix_ok(fact_md, [{'原文': old, '新文': new}])[0]:
            why = '原文不是事实库里的整条事实'
        elif k == '补上' and old not in gap:
            why = '原文不是口径表“可研没有写的”里的一行'
        elif k != '更正' and F._norm(new) in have:
            why = '事实库里已经有了'
        elif (k, F._norm(old), F._norm(new)) in seen:
            why = '重复'
        if why:
            job.info('补资料：这条变化没列（%s）：%s' % (why, (new or old)[:50]))
            continue
        seen.add((k, F._norm(old), F._norm(new)))
        hit = set(x.get('影响') or []) & body
        if k == '更正':
            hit |= _hits(root, _changed_tokens(old, new))
        out.append({'类别': k, '节': (x.get('节') or '').strip().lstrip('#').strip(), '原文': old, '新文': new,
                    '影响': sorted(hit), '说明': (x.get('说明') or '').strip()})
    return out


def _estimate(root, files):
    """每个要改的文件大概花多少美元（按官方价粗估：事实材料、正文、变化清单一起发；输出按 4000 token 算，再加一轮清零）。"""
    model = config.load().get('模型') or 'claude-opus-5-5'
    base = sum(len(t) for _, t in tools.facts(Path(root)))
    out = {}
    for f in files:
        n = len(tools.read(Path(root) / '正文' / f))
        out[f] = round(llm.cost(model, base + n * 2 + 6000, 4000) + llm.cost(model, n + 4000, 2000), 2)
    return out


def job_supplement(job):
    root = Path(job.project)
    given = job.params.get('资料') or []
    if not given and not _load(root, STATE):
        raise RuntimeError('没有给新资料')
    job.stage('准备')
    st = _take_in(job, root, given)
    if not (root / '资料' / '事实库.md').is_file():
        raise RuntimeError('新资料已放进项目，但这个项目还没有事实库，没法只对照新资料。'
                           '请点“整理项目情况”从全部资料摘一份（花费按全部资料算）')
    job.stage('事实')
    texts = [tuple(x) for x in (_load(root, '新资料.json') or [])]
    fact_md, kj = tools.read(root / '资料' / '事实库.md'), tools.read(root / '资料' / '口径.md') if (root / '资料' / '口径.md').is_file() else ''
    sysmsg = C.system()
    ctx = '\n\n'.join([C.doc('事实库（现有）', fact_md), C.doc('口径表（现有）', kj), _file_list(root)])
    parts = F._batches(F._pieces(texts, F.CHECK_BATCH), F.CHECK_BATCH)
    raw = []
    for i, part in enumerate(parts):
        job.check_cancel()
        label = '摘新资料' + ('' if len(parts) == 1 else ' 第%d批' % (i + 1))
        # 每批的结果按这批资料和现有事实库的指纹存：断了接着做时，做完的批不重来
        stamp = '<!-- %s -->' % hashlib.sha1((F._stamp(part) + fact_md + kj).encode('utf-8')).hexdigest()
        bp = _dir(root) / ('第%d批.json' % (i + 1))
        first, _, got = tools.read(bp).partition('\n') if bp.is_file() else ('', '', None)
        if first != stamp:
            res = llm.call_retry(job, label, sysmsg, '\n\n'.join([C.doc(n, t) for n, t in part] + [ctx, TASK]),
                                 effort='high', schema=CHANGES, phase='事实')
            tools.write(bp, stamp + '\n' + json.dumps(res['变化'], ensure_ascii=False))
            raw += res['变化']
        else:
            job.info('接着上次做：%s 上次已做完' % label)
            raw += json.loads(got)
    items = _filter(job, root, raw, fact_md, kj)
    for i, x in enumerate(items, 1):
        x['编号'] = i
    files = sorted({f for x in items for f in x['影响']})
    data = {'时间': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), '说明': job.params.get('说明', ''),
            '新资料': st['新资料'], '文件夹': st['文件夹'], '没转': st.get('没转', []), '字数': st.get('字数', 0),
            '变化': items, '预计': _estimate(root, files), '任务': job.id}
    _save(root, PENDING, data)
    for p in list(_dir(root).glob('第*批.json')) + [_dir(root) / STATE, _dir(root) / '新资料.json', _dir(root) / '新资料.txt']:
        p.unlink(missing_ok=True)
    count = {k: sum(1 for x in items if x['类别'] == k) for k in KINDS}
    job.info('新资料对照完：%s；要改 %d 个文件' % ('、'.join('%s %d 条' % kv for kv in count.items()), len(files)))
    return {'新资料': st['新资料'], '没转': st.get('没转', []), '变化': count, '要改': files, '预计美元': round(sum(data['预计'].values()), 2),
            '下一步': '到项目页看一遍变化，勾掉不要的，点“按这些改”' if items else '新资料和事实库没有出入，不用改'}


# ---------------- 3. 按新资料改 ----------------

def _add_disagree(kj, rows):
    """更正的旧说法记进口径表“## 说法不一”的表（没有这一节就新开一节）。"""
    if not rows:
        return kj
    lines = kj.rstrip('\n').split('\n') if kj.strip() else ['# 口径']
    start = next((i for i, ln in enumerate(lines) if ln.startswith('## 说法不一')), None)
    if start is None:
        lines += ['', '## 说法不一', '', '| 项目 | 说法1（出处） | 说法2（出处） | 采用 | 理由 |', '|---|---|---|---|---|']
        start, end = len(lines) - 4, len(lines)
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        if not any(ln.startswith('|') for ln in lines[start:end]):
            lines[end:end] = ['', '| 项目 | 说法1（出处） | 说法2（出处） | 采用 | 理由 |', '|---|---|---|---|---|']
            end += 3
    cell = lambda s: s.replace('|', '\\|').replace('\n', ' ').strip()  # noqa: E731
    lines[end:end] = ['| %s | %s | %s | 说法2（补充资料） | %s |' % (cell(x['节'] or '—'), cell(x['原文'][2:]), cell(x['新文'][2:]),
                                                                  cell(x['说明'] or '补充资料更具体')) for x in rows]
    return '\n'.join(lines) + '\n'


def _update_facts(job, root, items):
    """事实库、口径表照勾上的变化改。返回 (改成的条数, 没改成的说明)。"""
    fp, kp = Path(root) / '资料' / '事实库.md', Path(root) / '资料' / '口径.md'
    fact_md, kj = tools.read(fp), tools.read(kp) if kp.is_file() else ''
    n, bad = 0, []
    fixes = [x for x in items if x['类别'] == '更正']
    fact_md, done, failed = tools.apply_edits(fact_md, [{'原文': x['原文'], '新文': x['新文']} for x in fixes])
    n += len(done)
    bad += ['%s（%s）' % (e['原文'][:30], why) for e, why in failed]
    ok_old = {e['原文'] for e in done}
    for x in items:
        if x['类别'] in ('新增', '补上'):
            fact_md = F._insert(fact_md, x['节'] or '其他', x['新文'])
            n += 1
        if x['类别'] == '补上':
            kj = '\n'.join(ln for ln in kj.split('\n') if ln != x['原文'])
    kj = _add_disagree(kj, [x for x in fixes if x['原文'] in ok_old])
    tools.write(fp, fact_md.rstrip('\n') + '\n')
    tools.write(kp, kj.rstrip('\n') + '\n')
    job.info('事实库、口径表：改 %d 条%s' % (n, '，没改成 %d 条' % len(bad) if bad else ''))
    return n, bad


def _changes_doc(items):
    rows = []
    for x in items:
        if x['类别'] == '更正':
            rows.append('- 更正：%s → %s（%s）' % (x['原文'][2:], x['新文'][2:], x['说明']))
        elif x['类别'] == '补上':
            rows.append('- 原来“可研没有写”，新资料写了：%s（原来口径表里是：%s）' % (x['新文'][2:], x['原文'][2:]))
        else:
            rows.append('- 新增：%s' % x['新文'][2:])
    return C.doc('补充资料带来的变化（已改进事实库）', '\n'.join(rows))


def _update_table(job, root, items):
    p = Path(root) / '资料' / '岗位因素表.md'
    mine = [x for x in items if any(x['节'].startswith(h) for h in TABLE_HEADS)]
    if not p.is_file() or not mine:
        return 0
    res = llm.call_retry(job, '改 岗位因素表', C.system(), '\n\n'.join([
        _changes_doc(mine), C.doc('资料/岗位因素表.md（带行号）', tools.numbered(tools.read(p))),
        '任务：按上面的变化改岗位因素表（定员、班制、接触时间、设备、因素、工作地点）。和变化无关的不动；'
        '原来按不利情况定的、新资料写明了的，改成新资料的说法，出处换成新资料，“## 按不利情况定的”里删掉那一项。\n' + C.EDIT_RULES]),
        effort='high', schema=C.EDITS, phase='事实')
    text, done, failed = tools.apply_edits(tools.read(p), res['改动'])
    if done:
        tools.write(p, text)
    job.info('岗位因素表：改 %d 处%s' % (len(done), '，没改成 %d 处' % len(failed) if failed else ''))
    return len(done)


def _modify(job, root, fname, items, method):
    text = tools.read(Path(root) / '正文' / fname)
    task = ('任务：上面的变化已经改进事实库。按这些变化改正文/%s：用到旧说法的地方改成新的，计数、表格、判定、小结跟着改；'
            '原来写“可研没有写……”并提补充措施的，新资料写了的照新资料写，补充措施按需要改。'
            '和这些变化无关的地方不动，不顺手润色。事实只取事实库。\n%s' % (fname, C.EDIT_RULES))
    e = llm.call_retry(job, '按新资料改 ' + fname, C.system(C.write_format(), ('本章要点卡', C.card(method, fname))),
                       '\n\n'.join(C.fact_docs(root) + [_changes_doc(items), C.doc('正文/%s（带行号）' % fname, tools.numbered(text)), task]),
                       effort='high', schema=C.EDITS, phase='修改')
    n, bad = C.apply(job, root, fname, e['改动'], '按新资料改 ' + fname)
    return {'文件': fname, '改': n, '跳过': e['跳过'] + bad}


def job_apply(job):
    root = Path(job.project)
    data = pending(root)
    if not data:
        raise RuntimeError('没有待确认的补充资料变化（可能已经改过或丢掉了）')
    want = job.params.get('采用')
    items = [x for x in data['变化'] if want is None or x['编号'] in set(want)]
    if not items:
        raise RuntimeError('一条变化都没勾上')
    method = tools.method_of(root)
    prog = data.setdefault('进度', {})
    mu = threading.Lock()

    def mark(**kw):
        with mu:
            prog.update(kw)
            _save(root, PENDING, data)

    job.stage('事实')
    if not prog.get('备份'):
        before = tools.check(root)
        mark(备份=str(tools.backup_body(root, '补资料前')), 检查前=before)
    if not prog.get('事实库'):
        n, bad = _update_facts(job, root, items)
        mark(事实库=n, 事实库没改=bad)
    else:
        job.info('接着上次做：事实库、口径表上次已改好')
    if '岗位因素表' not in prog:
        mark(岗位因素表=_update_table(job, root, items))
    job.stage('修改')
    files = sorted({f for x in items for f in x['影响'] if f in tools.body_files(root)})
    done = dict(prog.get('已改') or {})
    if done:
        job.info('接着上次做：%d 个文件上次已改好' % len(done))

    def one(f):
        r = _modify(job, root, f, [x for x in items if f in x['影响']], method)
        with mu:                  # 改完一个记一个（在锁里取快照：几个线程同时记时，后记的不能盖掉先记的）
            done[f] = r
            prog['已改'] = dict(done)
            _save(root, PENDING, data)
        return r

    RV.pmap(job, one, [f for f in files if f not in done])
    lack = [f for f in files if f not in done]
    if lack:
        raise RuntimeError('有 %d 个文件没改成（%s），别的都改好了。点“接着做”只补改这几个' % (len(lack), '、'.join(lack)))
    job.stage('检查')
    before = prog.get('检查前')
    if before and not before.get('格式错误'):
        chk = RV.clear_all(job, root, method, ignore=before)
    else:
        chk = tools.check(root)
    try:
        gaps.write(root, chk)
    except Exception as e:  # noqa: BLE001  清单出错不影响改好的报告
        job.info('资料欠缺清单没刷新：%s' % str(e)[:100])
    pub = None
    if job.params.get('出稿') and chk['错误'] == 0:
        job.stage('出稿')
        pub = P.publish(job, root)
    n = sum(r['改'] for r in done.values())
    P.note(root, '补充资料', ['新资料：%s' % '、'.join(data['新资料']) + ('（%s）' % data['说明'] if data.get('说明') else ''),
                            '采用变化 %d 条（共 %d 条）；事实库、口径表改 %d 条' % (len(items), len(data['变化']), prog.get('事实库', 0)),
                            '正文改 %d 处：%s' % (n, '、'.join('%s %d 处' % (f, r['改']) for f, r in sorted(done.items()) if r['改']) or '无'),
                            '检查：错误 %d、提示 %d、待办 %d' % (chk['错误'], chk['提示'], chk['待办']),
                            ('PDF：共 %s 页' % pub['页数']) if pub and pub.get('通过') else ''])
    backup = prog.get('备份')
    discard(root)
    return {'采用': len(items), '事实库': prog.get('事实库', 0), '岗位因素表': prog.get('岗位因素表', 0),
            '正文': {f: r['改'] for f, r in sorted(done.items())}, '跳过': [s for r in done.values() for s in r['跳过']][:8],
            '检查': {k: chk[k] for k in ('错误', '提示', '待办')}, '出稿': pub, '备份': backup}


__all__ = ['job_supplement', 'job_apply', 'pending', 'discard', 'PENDING', 'DIR']
