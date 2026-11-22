"""补类比资料：类比法报告的第3章要用类比项目（规模、工艺相近，已投运的别的项目）的控评、检测、体检报告。
这些是别的项目的资料，不能当本项目的事实（补资料只拿新资料对照本项目事实库，对它们一条也列不出来，第3章照样【待填】）。

一次做完，不用中间确认：
1. 拷进 资料/类比/原件/<月日-时分>/，转成文字放 资料/类比/文本/，记在 资料/类比/类比资料.json。
   不进 资料/文本/索引.json、不当事实来源、转资料时不转（scripts/pe/pipeline.py）。以前当本项目资料补进来的同一份，挪到 资料/类比/移出/。
2. 摘类比事实 → 资料/类比/类比事实.md（写作、审稿、改稿的事实材料里都带着，注明是类比项目的）。
3. 先备份正文，照骨架和要点卡重写第3章；再改别的章引用第3章、类比结果的地方；全文检查清零。
断了点“接着做”：做完的步骤不重做（摘过的类比事实、写好的第3章、改好的文件）。"""
import datetime
import hashlib
import json
import re
import shutil
import threading
from pathlib import Path

from .. import gaps, llm, tools
from . import common as C
from . import facts as F
from . import new as N
from . import publish as P
from . import review as RV
from . import supplement as S

STATE = '类比资料.json'
FACTS = '类比事实.md'
METHOD = '类比法'
DESIGN_NO = '设计专篇用承接的预评价报告的类比事实（新建专篇时已从预评价拷来），不单独补类比资料；要换类比项目，先在预评价项目里补'
# 别的章里引用第3章、类比结果的地方（第3章重写以后要跟着改）
RE_REF = re.compile(r'(?<!\d)第3章|(?<![\d.])表3\.\d|类比(项目|企业|资料|检测|调查)')

TASK = '''任务：上面是类比项目（和本项目规模、工艺相近，已经投运的另一个项目）的资料。把写第3章“类比企业（项目）调查与分析”要用的事实摘成“类比事实”。
每条一行，写成“- 事实（出处：资料名 节号或表号、页码）”。照下面这些节写（照抄节标题），资料没写的节留空：
## 资料：每份资料的名称、编号、出具机构、报告日期、检测或采样日期、由哪几部分组成
## 类比项目概况：单位名称、项目名称、建设地点、规模、投运时间、主要装置
## 自然环境
## 产品及原辅材料
## 生产工艺
## 生产设备及布局
## 劳动定员和工作制度：各工种人数、班制、工作时间
## 职业病危害因素接触情况：工种、工作地点、因素、接触方式、接触频度、接触时间
## 职业病危害因素检测结果：逐项照抄检测对象、检测位置或岗位、结果、限值、原报告判定，写明检测日期和检测工况
## 职业健康检查：体检日期、机构、应检和实检人数、检查项目、结论、异常的处置
## 职业病防护设施
## 个人使用的职业病防护用品
## 应急救援设施
## 警示标识和告知卡
## 职业卫生管理：机构和人员、制度、培训、档案
## 资料没写的：写第3章要用、资料里找不到的事项
- 只摘资料里写明的，数字、名称、单位照抄，不推算、不编。
- 不写和本项目的比较、评价、建议；不摘和职业病危害识别、评价无关的（财务、电气、土建结构、消防、安全生产、环保排放）。
- 体检报告原件没转成文字的，在“## 资料”里写一句；体检结果从别的资料（控评报告的职业健康监护一节等）摘。
输出：“# 类比事实”开头的 Markdown 全文。'''

CH3 = ('第3章里类比项目的内容只取“类比事实.md”（写明出处）；本项目的内容照事实库、口径表、岗位因素表，'
       '骨架里本项目一栏已经写了的，和事实库核对后照用。资料确实没有的，照要点卡写所用的替代资料或适用边界，不写“待填”“待补”“未提供”。'
       '判定依据写标准编号和年份（如 GBZ 2.2—2007），不写“现行GBZ 2.2”；不确定度表里的因素和第4章识别的因素同名，不另列第4章没有识别的因素')

REF_TASK = '''任务：第3章已经按类比项目的资料重写（上面的“正文/%s”）。改 正文/%s 里引用第3章、类比项目、类比检测结果的地方：
- 类比项目名称、资料名称和编号、表号、检测结果和判定，和第3章一致；
- 引用了第3章里没有的表、结果的，改成第3章里有的；第3章没有对应内容的，删掉这处引用；
- 第4章的预期接触水平表：第3章写明可以类推的场所和因素，在那一行写上第3章的类比检测值（注明表号）；
  第3章写明不能类推、类比项目没有检测的场所和因素，不写类比值，也不外推；
- 结论、小结里引用类比结果的，和第3章、第4章写的一致；
- 和类比结果无关的地方不动，不顺手润色。本项目的事实只取事实库，类比项目的只取第3章和类比事实；
- 拿不准、要用户定的，写进“跳过”（写明是哪一处、为什么）。只输出“改动”“跳过”两项，不另写说明、不在别处问。
%s'''
# 改引用只回一小段 JSON（田林成了的几次输出都在 6000 以内）。田林有 5 次想了 7～13 万 token、改用文字回答，
# 每次白花 2～3 美元：用中等用力，到这个长度就停（停了这个文件算没改成，点“接着做”再改）
REF_MAX = 32000


def _state(root):
    p = tools.analog_dir(root) / STATE
    try:
        return json.loads(tools.read(p)) if p.is_file() else {}
    except ValueError:
        return {}


def _save(root, st):
    tools.write(tools.analog_dir(root) / STATE, json.dumps(st, ensure_ascii=False, indent=1, default=str))


def unfinished(root):
    """上次的补类比资料没做完（接着做时可以不再给资料）。"""
    run = _state(root).get('本次') or {}
    return bool(run) and not run.get('做完')


def _ch3(root):
    """类比法报告第3章的文件（风险评估法的报告没有，返回 None）。"""
    return next((f for f in tools.body_files(root) if f.startswith('03') and '类比' in f), None)
# ---------------- 1. 拷进来、转文字；以前当本项目资料补进来的挪走 ----------------

def _take_in(job, root, given):
    """拷进 资料/类比/原件/、转成文字、记进 类比资料.json；同一批资料上次没做完的不再拷。返回这一次的记录。"""
    st = _state(root)
    run = st.get('本次')
    if run and not run.get('做完') and (not given or run.get('资料') == given or job.params.get('接着做')):
        job.info('接着上次做：类比资料上次已拷进项目、转成文字')
        return st
    if not given:
        raise RuntimeError('没有给类比资料')
    tag = datetime.datetime.now().strftime('%m%d-%H%M')
    adir = tools.analog_dir(root)
    folder = adir / '原件' / tag
    S._copy(given, folder)
    texts, skipped = S._convert(job, root, folder, adir / '转换')
    if not texts:
        raise RuntimeError('类比资料没转出文字（扫描件要先识别成文字）：%s。资料已拷进 %s'
                           % ('；'.join(skipped[:6]) or '没有能转成文字的文件', folder))
    rows = []
    for i, (name, src, text) in enumerate(texts, 1):
        stem = re.sub(r'[\\/:*?"<>|\s()（）]+', '', Path(name).stem)[:20] or '资料'
        fn = '%s_%02d_%s.txt' % (tag, i, stem)
        tools.write(adir / '文本' / fn, text)
        rows.append({'资料': name, '来源': src, '文件': fn, '字数': len(text)})
    new = {x['资料'] for x in rows}
    for x in st.get('文本', []):
        if x['资料'] in new:          # 同名资料又给了一遍：换成这次的
            (adir / '文本' / x['文件']).unlink(missing_ok=True)
    st['文本'] = [x for x in st.get('文本', []) if x['资料'] not in new] + rows
    st['没转'] = [s for s in st.get('没转', []) if s.split('：')[0] not in new] + [s for s in skipped if s not in st.get('没转', [])]
    st['本次'] = {'资料': given, '文件夹': str(folder), '时间': tag, '类比资料': sorted(new), '没转': skipped, '进度': {}}
    _save(root, st)
    return st


def _move_out(job, root, names, tag):
    """以前当本项目资料补进来的同一份（资料/文本/索引.json 的块、流水线设置.json 的事实来源、资料/原件/补充_*/ 里的原件）
    挪到 资料/类比/移出/<tag>/，不再算本项目资料。names：这次类比资料的文件名。返回挪走的资料名。"""
    if not names:
        return []
    root = Path(root)
    out = tools.analog_dir(root) / '移出' / tag
    moved = set()
    idx = root / '资料' / '文本' / '索引.json'
    try:
        data = json.loads(tools.read(idx)) if idx.is_file() else None
    except ValueError:
        data = None
    if data:
        hit = [b for b in data.get('块', []) if Path(b.get('来源', '')).name in names]
        if hit:
            tools.write(out / '索引.json', tools.read(idx))          # 改之前的索引留一份
            for b in hit:
                p = idx.parent / b['文件']
                if p.is_file():
                    (out / '文本' / b['文件']).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(out / '文本' / b['文件']))
                moved.add(Path(b['来源']).name)
            data['块'] = [b for b in data['块'] if b not in hit]
            tools.write(idx, json.dumps(data, ensure_ascii=False, indent=1))
    sets = tools.project_settings(root)
    if sets.get('事实来源'):
        stems = {re.sub(r'[\\/:*?"<>|\s()（）]+', '', Path(n).stem)[:20] or '资料': n for n in names}
        drop = [x for x in sets['事实来源'] if x.startswith('资料/文本/补充/') and any(x.endswith('_%s.txt' % s) for s in stems)]
        if drop:
            tools.write(out / tools.SETTINGS_FILE, json.dumps(sets, ensure_ascii=False, indent=2))
            for x in drop:
                p = root / x
                if p.is_file():
                    (out / '文本').mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(out / '文本' / p.name))
                moved.update(n for s, n in stems.items() if x.endswith('_%s.txt' % s))
            sets['事实来源'] = [x for x in sets['事实来源'] if x not in drop]
            tools.write(root / tools.SETTINGS_FILE, json.dumps(sets, ensure_ascii=False, indent=2))
    raw = root / '资料' / '原件'
    for d in sorted(raw.glob('补充_*')) if raw.is_dir() else []:
        for p in sorted(x for x in d.rglob('*') if x.is_file() and x.name in names):
            dst = out / '原件' / d.name / p.relative_to(d)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dst))
            moved.add(p.name)
        for x in sorted((y for y in d.rglob('*') if y.is_dir()), reverse=True) + [d]:
            try:
                x.rmdir()                 # 挪空了的文件夹删掉（还有别的资料的不动）
            except OSError:
                pass
    pend = S.pending(root)
    if pend and not pend.get('变化') and pend.get('新资料') and set(pend['新资料']) <= set(names):
        S.discard(root)                   # 这几份当本项目资料对照的那次，什么也没列出来：待确认的清单不要了
        job.info('补资料那次对照的就是这几份类比资料、没有列出变化，那份待确认的清单已经丢掉')
    if moved:
        job.info('这几份以前当本项目资料记进来了，已挪到 %s，不再算本项目资料：%s' % (out, '、'.join(sorted(moved))))
    return sorted(moved)
# ---------------- 2. 摘类比事实；3. 写第3章、改引用的地方 ----------------

def _extract(job, root, texts, skipped):
    """类比资料摘成类比事实。资料太长的分几批，后面的批在前面摘好的上面补。"""
    parts = F._batches(F._pieces(texts, F.CHECK_BATCH), F.CHECK_BATCH)
    sysmsg = C.system(('第3章要点卡', C.card(METHOD, '03')))
    note = ('这几份类比资料没转成文字，没有给内容：' + '；'.join(skipped)) if skipped else ''
    got = ''
    for i, part in enumerate(parts):
        job.check_cancel()
        before = [C.doc('前面几批已经摘好的类比事实（照留，在上面补这一批的）', got)] if got else []
        out = llm.call_retry(job, '摘类比事实' + ('' if len(parts) == 1 else ' 第%d批' % (i + 1)), sysmsg,
                             C.join([C.doc('类比资料 ' + n, t) for n, t in part] + before + [note, TASK]), effort='high', phase='类比事实')
        out = re.sub(r'^```\w*\n|\n```\s*$', '', out.strip()).strip()
        if not out.startswith('#') or '\n## ' not in out:
            raise llm.CallError('摘类比事实：模型给的不是“# 类比事实”开头、分节写的 Markdown')
        got = out
    return got


def _write_ch3(job, root, fname, written):
    """照骨架、要点卡、样板重写第3章（就是新报告写一章那一步，事实材料里带着类比事实）。
    written(结果)：正文一写进文件就调（后面清零出错，接着做时也不再重写）。"""
    sample = tools.sample_for(METHOD, exclude=root)
    deps = [f for f in tools.body_files(root) if f[:2] in N.DEPS['03']]
    return N._write_file(job, root, METHOD, sample, fname, deps, [], written=written, extra=CH3)


def _refs(root, ch3):
    """别的章里引用第3章、类比结果的文件（注释里的不算）。"""
    out = []
    for f in tools.body_files(root):
        if f == ch3:
            continue
        text = re.sub(r'<!--.*?-->', '', tools.read(Path(root) / '正文' / f), flags=re.S)
        if RE_REF.search(text):
            out.append(f)
    return out


def _fix_refs(job, root, fname, ch3, method):
    text = tools.read(Path(root) / '正文' / fname)
    e = llm.call_retry(job, '改引用 ' + fname, C.system(C.write_format(), ('本章要点卡', C.card(method, fname))),
                       '\n\n'.join(C.fact_docs(root) + [C.doc('正文/%s（刚写好的第3章）' % ch3, tools.read(Path(root) / '正文' / ch3)),
                                                        C.doc('正文/%s（带行号）' % fname, tools.numbered(text)),
                                                        REF_TASK % (ch3, fname, C.EDIT_RULES)]),
                       effort='medium', schema=C.EDITS, max_tokens=REF_MAX, phase='修改')
    n, bad = C.apply(job, root, fname, e['改动'], '改引用 ' + fname)
    return {'文件': fname, '改': n, '跳过': e['跳过'] + bad}
def job_analog(job):
    root = Path(job.project)
    if tools.is_design(root):
        raise RuntimeError(DESIGN_NO)
    ch3 = _ch3(root)
    if not ch3:
        raise RuntimeError('这份报告没有“类比企业（项目）调查与分析”这一章（用的是风险评估法），类比资料用不上')
    method = tools.method_of(root)
    job.stage('准备')
    st = _take_in(job, root, job.params.get('资料') or [])
    run = st['本次']
    names = {p.name for p in Path(run['文件夹']).rglob('*') if p.is_file()} if Path(run['文件夹']).is_dir() else set()
    moved = _move_out(job, root, names, run['时间'])
    if moved:
        run['移出'] = sorted(set(run.get('移出', [])) | set(moved))
        _save(root, st)
    texts = tools.analog_texts(root)
    if not texts:
        raise RuntimeError('类比资料没有文字：先把控评报告、检测报告等能复制文字的版本补进来')
    prog, mu = run.setdefault('进度', {}), threading.Lock()

    def mark(**kw):
        with mu:
            prog.update(kw)
            _save(root, st)

    job.stage('类比事实')
    stamp = hashlib.sha1(json.dumps(texts, ensure_ascii=False).encode('utf-8')).hexdigest()
    fp = tools.analog_dir(root) / FACTS
    if st.get('事实指纹') == stamp and fp.is_file():
        job.info('接着上次做：这些类比资料上次已摘成类比事实')
    else:
        tools.write(fp, _extract(job, root, texts, st.get('没转', [])) + '\n')
        st['事实指纹'] = stamp
        _save(root, st)
    job.stage('第3章')
    if not prog.get('备份'):
        mark(备份=str(tools.backup_body(root, '补类比资料前')), 检查前=tools.check(root))
    if not prog.get('第3章'):
        _write_ch3(job, root, ch3, lambda r: mark(第3章={'字数': r['字数']}))
    else:
        job.info('接着上次做：第3章上次已写好')
    job.stage('修改')
    refs = prog.get('引用')
    if refs is None:
        refs = _refs(root, ch3)
        mark(引用=refs)
    job.info('引用第3章、类比结果的文件：%s' % ('、'.join(refs) or '没有'))
    done = dict(prog.get('已改') or {})

    def one(f):
        r = _fix_refs(job, root, f, ch3, method)
        with mu:                  # 改完一个记一个（在锁里取快照，几个线程同时记时不互相盖掉）
            done[f] = r
            prog['已改'] = dict(done)
            _save(root, st)
        return r

    RV.pmap(job, one, [f for f in refs if f not in done])
    lack = [f for f in refs if f not in done]
    if lack:
        raise RuntimeError('有 %d 个文件引用第3章的地方没改成（%s），第3章和别的都改好了。点“接着做”只补改这几个' % (len(lack), '、'.join(lack)))
    job.stage('检查')
    before = prog.get('检查前')
    chk = RV.clear_all(job, root, method, ignore=before) if before and not before.get('格式错误') else tools.check(root)
    try:
        gaps.write(root, chk)
    except Exception as e:  # noqa: BLE001  清单出错不影响改好的报告
        job.info('资料欠缺清单没刷新：%s' % str(e)[:100])
    blank = tools.read(root / '正文' / ch3).count('【待填')
    changed = {f: r['改'] for f, r in sorted(done.items())}
    P.note(root, '补类比资料', [
        '类比资料：%s' % '、'.join(x['资料'] for x in st.get('文本', [])) + ('（%s）' % job.params['说明'] if job.params.get('说明') else ''),
        ('没转成文字、没用上的：%s' % '；'.join(st['没转'])) if st.get('没转') else '',
        ('以前当本项目资料记进来的 %s 已挪到 资料/类比/移出/，不算本项目资料' % '、'.join(run['移出'])) if run.get('移出') else '',
        '第3章按类比资料重写：%d 字%s' % (prog['第3章']['字数'], '，还有 %d 处【待填】' % blank if blank else ''),
        '引用第3章的地方：%s' % ('、'.join('%s %d 处' % kv for kv in changed.items() if kv[1]) or '没有要改的'),
        '检查：错误 %d、提示 %d、待办 %d' % (chk['错误'], chk['提示'], chk['待办'])])
    run['做完'] = True
    _save(root, st)
    return {'类比资料': [x['资料'] for x in st.get('文本', [])], '没转': st.get('没转', []), '移出': run.get('移出', []),
            '第3章': {'文件': ch3, '字数': prog['第3章']['字数'], '待填': blank}, '引用': changed,
            '跳过': [s for r in done.values() for s in r['跳过']][:8], '检查': {k: chk[k] for k in ('错误', '提示', '待办')},
            '备份': prog.get('备份')}


__all__ = ['job_analog', 'unfinished', 'STATE', 'FACTS']
