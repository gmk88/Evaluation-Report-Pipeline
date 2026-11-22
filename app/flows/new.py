"""新报告：建项目、资料转文字 → 事实库 → 岗位因素表 → 各章按先后关系写 → 检查清零 → 审稿 → 出稿 → 交稿说明。
设计专篇（类型＝设计专篇）另走 job_design：从承接的预评价项目拷项目信息和资料 → 事实库补摘专篇才要的几节
→ 写主报告各文件 → 写附件册各文件 → 配图 → 两卷检查清零 → 两卷审稿 → 出稿 → 交稿说明。"""
import json
import os
import re
import shutil
import threading
from pathlib import Path

from .. import config, gaps, jobs, llm, tools
from ..tools import doctype as DT
from . import common as C
from . import facts as F
from . import figures as FG
from . import publish as P
from . import review as RV

DEPS = {'02': [], '03': ['02'], '04': ['02', '03'], '08': ['02'], '05': ['02', '04'], '06': ['02', '04'], '07': ['02', '04'],
        '09': ['03', '04', '05', '06', '07'], '10': ['03', '04', '05', '06', '07', '08', '09'],
        '11': ['03', '04', '05', '06', '07', '08', '09', '10'], '01': ['03', '04', '05', '06', '07', '08', '09', '10']}
INFO = {
    'type': 'object', 'additionalProperties': False, 'required': ['项目名称', '封面名称', '建设单位', '报告编号', '报告日期', '方法线索'],
    'properties': {'项目名称': {'type': 'string'}, '封面名称': {'type': 'array', 'items': {'type': 'string'}},
                   '建设单位': {'type': 'string'}, '报告编号': {'type': 'string'}, '报告日期': {'type': 'string'},
                   '方法线索': {'type': 'string'}},
}


def _fill_info(job, root, sample, form):
    """项目信息.md：名称、建设单位从资料里找；评价单位、人员表照样板抄；报告编号、日期用表单填的。"""
    s = tools.read(Path(root) / '项目信息.md')
    if not re.search(r'^- (项目名称|封面名称)：【待填】', s, re.M):
        # 接着上次做：项目信息已经填过（封面名称是这一步填的），不再调模型。资料里找不到建设单位的，
        # 上次就留着【待填】（田林那次），不能因为它再填一遍
        m = re.search(r'^- 项目名称：(.*)$', s, re.M)
        name = m.group(1).strip() if m else Path(root).name
        job.project_name = form.get('简称') or name
        return {'项目名称': name, '方法线索': ''}
    src = tools.source_texts(root)
    head = '\n\n'.join(C.doc(n, t[:6000]) for n, t in src[:12])
    r = llm.call_retry(job, '填项目信息', C.ROLE, head + '\n\n任务：从上面资料（委托书、合同、备案证、可研封面等）找项目信息。'
                       '项目名称照备案证或可研封面全称；封面名称把项目名称断成 2～3 行（建设单位一行，项目名一两行）；'
                       '报告编号、报告日期资料里没有的写空字符串；方法线索：委托书、合同里写的评价方法原话（没有写空）。',
                       effort='low', schema=INFO, phase='准备')
    p = Path(root) / '项目信息.md'
    s = tools.read(p)
    name = form.get('名称') or r['项目名称']
    vals = {'项目名称': name, '建设单位': r['建设单位'], '报告编号': form.get('编号') or r['报告编号'], '报告日期': form.get('日期') or r['报告日期']}
    for k, v in vals.items():
        if v:
            s = re.sub(r'^- %s：.*$' % k, '- %s：%s' % (k, v), s, count=1, flags=re.M)
    covers = [x for x in r['封面名称'] if x.strip()]
    if covers:
        # 原有的封面名称整组换掉（只换第一行的话，再填一遍会叠出重复的行）
        lines = s.split('\n')
        at = [i for i, ln in enumerate(lines) if ln.startswith('- 封面名称：')]
        if at:
            keep = [ln for i, ln in enumerate(lines) if i not in at]
            keep[at[0]:at[0]] = ['- 封面名称：' + x for x in covers]
            s = '\n'.join(keep)
    if sample and (Path(sample) / '项目信息.md').exists():
        t = tools.read(Path(sample) / '项目信息.md')
        for k in ('评价单位', '法定代表人'):
            m = re.search(r'^- %s：.*$' % k, t, re.M)
            if m:
                s = re.sub(r'^- %s：.*$' % k, m.group(0), s, count=1, flags=re.M)
        a, b = re.search(r'^## 评价人员.*?(?=^## )', t, re.M | re.S), re.search(r'^## 评价人员.*?(?=^## )', s, re.M | re.S)
        if a and b:
            s = s[:b.start()] + a.group(0) + s[b.end():]
    tools.write(p, s)
    job.project_name = form.get('简称') or name
    return {'项目名称': name, '方法线索': r['方法线索']}


def _table_counts(out):
    rows = [ln for ln in out.split('\n') if ln.startswith('|') and not set(ln) <= set('|-: ')][1:]
    col = lambda i: {r.strip('|').split('|')[i].strip() for r in rows if len(r.strip('|').split('|')) > i}  # noqa: E731
    return {'行': len(rows), '单元': len(col(0) - {'^', ''}), '场所': len(col(1) - {'^', ''}), '工种': len(col(2) - {'^', ''})}


def _post_table(job, root, method, sample, redo=False):
    p = Path(root) / '资料' / '岗位因素表.md'
    if p.exists() and not redo:
        job.info('接着上次做：岗位因素表上次已写好，这次不重写')
        return _table_counts(tools.read(p))
    fx = [C.doc(n, t) for n, t in tools.facts(root) if n != '岗位因素表.md']
    ex = []
    if sample:
        for f in sorted((Path(sample) / '正文').glob('0[14]*.md')):
            ex += tools.tables_by_caption(tools.read(f), r'^表(1\.8-1|4\.1-[12])\s')
    task = '''任务：定评价单元、工作场所、工种，写岗位因素表。这张表是全报告名称和因素的唯一来源，第1、3～10章都照它。
表头：｜评价单元｜工作场所｜工种｜定员及班制｜工作地点｜接触时间（h/d）｜职业病危害因素｜检测方式｜出处｜
- 评价单元按工艺系统和辅助系统分；工作场所按有人作业、巡检的区域或设施分，操作室、值班室是所在场所的工作地点，不单列。
- 因素名照《职业病危害因素分类目录》写法；只写职业病危害因素。
- 资料没写的班制、定员、接触时间，按可研和不利情况定，出处栏写“按不利情况”，表后加一节“## 按不利情况定的”逐项说明。
样板的表只看怎么分、怎么写，不抄事实。
输出：“# 岗位因素表”开头的 Markdown 全文。'''
    out = llm.call_retry(job, '岗位因素表', C.system(('第1章要点卡', C.card(method, '01')), ('第4章要点卡', C.card(method, '04'))),
                         '\n\n'.join(fx + [C.doc('样板的表1.8-1、表4.1（只学写法）', '\n\n'.join(ex))] + [task]), effort='high', phase='骨架')
    out = out.strip()
    if not out.startswith('#'):
        out = '# 岗位因素表\n\n' + out
    tools.write(p, out + '\n')
    res = _table_counts(out)
    job.info('岗位因素表：评价单元 %(单元)d 个、工作场所 %(场所)d 个、工种 %(工种)d 个，共 %(行)d 行' % res)
    return res


def _write_file(job, root, method, sample, fname, deps, gaps, written=None, extra=''):
    """写一章。written(结果)：正文一写进文件就调，记下这章写好了（后面清零出错也不丢“可研没有写”的事项）。
    extra：任务里另加的一条要求（补类比资料重写第3章时用）。"""
    skel = tools.read(Path(root) / '正文' / fname)
    parts = C.fact_docs(root)
    parts.append(C.doc('正文/%s 现有骨架' % fname, skel))
    for n, t in C.sample_chapter(sample, fname):
        parts.append(C.doc('样板 %s（只学结构、表头和写法深浅，不抄任何事实）' % n, t))
    for d in deps:
        p = Path(root) / '正文' / d
        if p.exists():
            parts.append(C.doc('已写好的 %s（摘要：标题、表格、小结）' % d, tools.digest(tools.read(p))))
    if gaps:
        parts.append('前面各章记下的“可研没有写”的事项，本章要逐项承接（问题和补充建议）：\n' + '\n'.join('- ' + g for g in gaps))
    part = '（第%d章的一部分，只写本文件骨架里的节）' % int(fname[:2]) if re.match(r'^\d\d_\d', fname) else ''
    task = '''任务：写 正文/%s%s 的完整内容。
- 标题结构照骨架；骨架注释里的写法提示照做，写完删掉提示注释和【待填】；版面指令（<!-- 横向 --> <!-- 纵向 --> <!-- 分页 --> 等）和表格指令照留。
- 事实只取事实库、口径表；评价单元、工作场所、工种、因素名称一律照岗位因素表。
- 已写好的前面各章：表号、名称、判定和数字要承接一致。
- 计数句用占位符（如 {{计数:表5.3-1}}），写法照“正文写法格式”。
%s输出格式：先输出一行 <<<正文>>>，接着是文件全文；再输出一行 <<<可研没有>>>，接着每行一条本章记下的“可研没有写”事项（没有就写“无”）。''' % (
        fname, part, '- %s\n' % extra if extra else '')
    out = llm.call_retry(job, '写 ' + fname, C.system(C.write_format(), ('本章要点卡', C.card(method, fname))),
                         '\n\n'.join(parts + [task]), effort='high', phase='写作')
    sec = llm.sections(out, ['正文', '可研没有'])
    body = sec.get('正文', '').strip()
    if len(body) < max(300, len(skel) // 3):
        raise llm.CallError('写 %s：模型给的正文太短（%d 字）' % (fname, len(body)))
    body = C.fix_format(job, fname, body)   # 格式不对整份正文都读不了，别的章也没法检查，先改对再写进去
    tools.write(Path(root) / '正文' / fname, body + '\n')
    gap = [x.strip('-• ').strip() for x in sec.get('可研没有', '').split('\n') if x.strip() and x.strip('-• ').strip() not in ('无', '')]
    res = {'字数': len(body), '可研没有': gap, '剩余错误': None}
    if written:
        written(res)
    res['剩余错误'] = C.fix_errors(job, root, fname, method)
    return res


def _skeleton(method):
    return config.ROOT / 'templates' / ('项目骨架_风险评估法' if method == '风险评估法' else '项目骨架')


def _record_path(root):
    """各章写完的结果（字数、本章记下的“可研没有写”事项），放在新报告的中间结果文件夹里（转资料时不当资料）。
    设计专篇附件册没有自己的 资料/，记在主报告那里（写作记录_附件册.json）。"""
    if tools.report_type(root) == DT.ANNEX:
        return Path(DT.main_root(root)) / '资料' / F.DRAFT / '写作记录_附件册.json'
    return Path(root) / '资料' / F.DRAFT / '写作记录.json'


def write_all(job, root, method, sample, only=None):
    """按先后关系并行写各章。和骨架不一样的文件是上次写好的，不重写（接着做时省钱）；只写几章的照写。"""
    t = tools.report_type(root)
    all_files = tools.body_files(root)
    files = list(all_files)
    if only:
        files = [f for f in files if any(f.startswith(x) for x in only)]
    by_ch = {}
    for f in tools.body_files(root):
        by_ch.setdefault(f[:2], []).append(f)
    done = {f: threading.Event() for f in files}
    res, mu = {}, threading.Lock()
    rec_p = _record_path(root)
    try:
        rec = json.loads(tools.read(rec_p)) if rec_p.exists() else {}
    except ValueError:
        rec = {}
    if not only:
        # 写作记录里有的是上次写好的（写完一个记一个），不重写；没有记录的照写
        kept = [f for f in files if isinstance(rec.get(f), dict) and (Path(root) / '正文' / f).exists()]
        for f in kept:
            res[f] = rec[f]
            done[f].set()
        if kept:
            job.info('接着上次做：%d 个文件上次已写好，这次不重写' % len(kept))

    def keep(f, r):
        with mu:                          # 写完一个记一个：任务断了，下次接着做时照样能把这章的“可研没有写”交给第10章
            res[f] = rec[f] = r
            tools.write(rec_p, json.dumps(rec, ensure_ascii=False, indent=1, default=str))

    def work(f):
        try:
            if t == DT.PRE:
                deps = [d for c in DEPS.get(f[:2], []) for d in by_ch.get(c, [])]
            else:
                deps = DT.deps_of(t, f, all_files)         # 设计专篇：按文件前缀（04_2 等）定先后
            for d in deps:
                if d in done:
                    done[d].wait()
            job.check_cancel()
            lack = [d for d in deps if d in done and not res.get(d)]
            if lack:
                # 前面的章没写成：照骨架硬写，表号、名称、判定承接不上，写了还会记成“写好了”，接着做时不重写
                job.step('写 %s（等 %s 写成再写）' % (f, '、'.join(lack)), '写作').update(状态='跳过', 说明='前面的章没写成')
                return None
            if t != DT.PRE:
                out = _write_design(job, root, t, sample, f, deps, written=lambda r: keep(f, r))
                keep(f, out)
                return out
            gaps = [g for k, v in res.items() if v and k[:2] < '10' and k[:2] != '01' for g in v['可研没有']] if f[:2] == '10' else []
            out = _write_file(job, root, method, sample, f, deps, gaps, written=lambda r: keep(f, r))
            keep(f, out)                  # 清零后的剩余错误数也记上
            return out
        finally:
            done[f].set()

    RV.pmap(job, work, [f for f in files if f not in res])
    return res


def _note(root, form, info, chk, rv, pub, table):
    pt = tools.read(Path(root) / '资料' / '岗位因素表.md') if (Path(root) / '资料' / '岗位因素表.md').exists() else ''
    kj = tools.read(Path(root) / '资料' / '口径.md') if (Path(root) / '资料' / '口径.md').exists() else ''
    worst = re.search(r'^## 按不利情况定的.*?(?=^## |\Z)', pt, re.M | re.S)
    gap_sec = re.search(r'^## 可研没有写的.*?(?=^## |\Z)', kj, re.M | re.S)   # 别叫 gaps：会遮住 gaps 模块
    idx = Path(root) / '资料' / '文本' / '索引.json'
    srcs = sorted({Path(b['来源']).name for b in json.loads(tools.read(idx))['块']}) if idx.exists() else []
    todo = [x for x in tools.check(root)['按文件'].get('项目信息.md', {}).get('待办', [])]
    lines = ['# 交稿说明', '', '## 报告', '',
             '- 项目：%s' % info['项目名称'], '- 评价方法：%s' % form['方法'],
             '- PDF：%s' % (('共 %s 页，目录页码逐页核对通过' % pub['页数']) if pub and pub.get('通过') else '没出或没通过'),
             '- 资料依据：%s' % ('、'.join(srcs) or '—'),
             '- 检查：错误 %d、提示 %d、待办 %d' % (chk['错误'], chk['提示'], chk['待办']),
             '- 审稿：必改 %d 条、建议 %d 条，改了 %d 处' % (rv['必改'], rv['建议'], rv['已改']) if rv else '- 审稿：没做',
             '- 岗位因素表：评价单元 %d 个、工作场所 %d 个、工种 %d 个' % (table['单元'], table['场所'], table['工种']),
             '', '## 请你确认', '', '- 评价人员是照样板项目抄的，请核对。']
    miss = F.skipped_checks(root)
    if miss:
        lines.append('- 事实库第二遍核对时，第 %s 批资料网络那头断了没核成，这部分事实只摘了一遍；'
                     '第2章的设备、定员、原辅材料数字请对照资料多看一眼。' % miss)
    lines += ['- ' + x for x in todo]
    if worst:
        lines += ['', '## 按不利情况定的（报告里按这样写）', '', worst.group(0).split('\n', 1)[1].strip()]
    if gap_sec:
        lines += ['', '## 可研没有写的（报告里已写补充措施；需要建设单位补的看这里）', '', gap_sec.group(0).split('\n', 1)[1].strip()]
    g = gaps.write(root, chk)
    if g['要补']:
        lines[lines.index('## 请你确认') + 2:lines.index('## 请你确认') + 2] = [
            '- 还缺 %d 项资料，见同一文件夹的“资料欠缺清单”（可以直接转给建设单位）。' % len(g['要补'])]
    tools.write(Path(root) / '输出' / '交稿说明.md', '\n'.join(lines) + '\n')


def job_new(job):
    form = job.params
    if form.get('类型') == DT.DESIGN or (form.get('接着做') and tools.report_type(Path(form['文件夹']).expanduser()) == DT.DESIGN):
        return job_design(job)
    root = Path(form['文件夹']).expanduser().resolve()
    method = form['方法']
    job.project = str(root)
    job.stage('准备')
    if not (root / '项目信息.md').exists():
        # 任务开始时已在文件夹里放了运行锁，所以不用 report.py new（它要求文件夹是空的），直接复制骨架
        junk = (jobs.LOCK_NAME, '.DS_Store', 'desktop.ini', 'Thumbs.db')   # 系统自己生成的文件不算“不是空的”
        extra = [p for p in root.iterdir() if p.name not in junk and not p.name.startswith('._')] if root.exists() else []
        if extra:
            raise RuntimeError('文件夹已存在且不是空的：%s' % root)
        shutil.copytree(_skeleton(method), root, dirs_exist_ok=True)
        if form.get('名称'):
            p = root / '项目信息.md'
            tools.write(p, tools.read(p).replace('- 项目名称：【待填】', '- 项目名称：' + form['名称'], 1))
        job.info('已建项目：%s' % root)
    else:
        job.info('接着上次做：项目已建好（%s），做完的步骤不重做' % root)
    given = form.get('资料') or []
    out = ''
    if form.get('接着做') and (root / '资料' / '文本' / '索引.json').exists():
        job.info('接着上次做：资料上次已转成文字，这次不重转')
    elif given:
        raw = root / '资料' / '原件'
        raw.mkdir(parents=True, exist_ok=True)
        for x in given:
            s = Path(x).expanduser()
            if s.is_dir():
                # 资料文件夹里的快捷方式（符号链接）不拷：可能指向资料以外的文件，比如存密钥的设置
                shutil.copytree(s, raw / s.name, dirs_exist_ok=True,
                                ignore=lambda d, names: [n for n in names if (Path(d) / n).is_symlink()])
            elif s.is_file():
                shutil.copy2(s, raw / s.name)
        rc, out = tools.report('资料', root, raw)
    else:
        rc, out = tools.report('资料', root)
    if out.strip():
        job.info(out.strip()[:400])
    if not tools.source_texts(root):
        raise RuntimeError('资料没转出文字（扫描件要先识别成文字）：' + out[-300:])
    sample = tools.sample_for(method, exclude=root)
    job.info('样板：%s' % (sample or '没有（只按骨架和要点卡写）'))
    info = _fill_info(job, root, sample, form)
    tools.register_add(form.get('简称') or info['项目名称'], method, root)
    job.stage('事实')
    if not (root / '资料' / '事实库.md').exists() or form.get('重摘事实'):
        F.build(job, root)
    job.stage('骨架')
    table = _post_table(job, root, method, sample, redo=bool(form.get('重摘事实')))
    job.stage('写作')
    only = form.get('只写') or None
    wr = write_all(job, root, method, sample, only)
    if only:
        return {'项目': str(root), '岗位因素表': table, '写作': {k: {'字数': v['字数'], '剩余错误': v['剩余错误']} for k, v in wr.items() if v}}
    missing = [f for f in tools.body_files(root) if not wr.get(f)]
    if missing:
        # 缺章的报告审稿、出稿都是白花钱；任务记成失败，点“接着做”只补写这几个
        raise RuntimeError('有 %d 个文件没写成（%s），别的都写好了。点“接着做”只补写这几个' % (len(missing), '、'.join(missing)))
    job.stage('配图')
    FG.run(job, root)
    job.stage('检查')
    chk = RV.clear_all(job, root, method)
    rv = None
    if form.get('审稿', True):
        rv = RV.run(job, root, modify=True, publish=False, new_report=True)
        chk = tools.check(root)
    pub = None
    if form.get('出稿', True) and chk['错误'] == 0:
        job.stage('出稿')
        pub = P.publish(job, root)
    _note(root, form, info, chk, rv, pub, table)
    _record_path(root).unlink(missing_ok=True)      # 整份做完了，写作记录用不着了
    F.drop_empty(root)
    return {'项目': str(root), '岗位因素表': table, '写作': len([v for v in wr.values() if v]),
            '没写成': [f for f in tools.body_files(root) if not wr.get(f)],
            '检查': {k: chk[k] for k in ('错误', '提示', '待办')}, '审稿': rv and {k: rv[k] for k in ('必改', '建议', '已改')}, '出稿': pub,
            '交稿说明': str(root / '输出' / '交稿说明.md')}



# ================= 设计专篇 =================

DESIGN_SPLIT = 2        # 整章写不成时，按节分两半写；某一半还写不成的，再对半分（最多再分几层）
DESIGN_TASK = """任务：写 正文/%s%s 的完整内容（%s）。
- 标题结构照骨架；骨架注释里的写法提示照做，写完删掉提示注释和【待填】；版面指令（<!-- 横向 --> <!-- 纵向 --> <!-- 分页 --> 等）和表格指令照留。
- 事实只取事实库、口径表；评价单元、工作场所、工种、因素名称一律照岗位因素表，和承接的预评价报告一字不差。
%s- 承接的预评价报告：危害因素、接触人员、对策措施从它承接；引用预评价的表写“预评价报告表x.x-x”。预评价是承接依据，不照抄它的评价结论和判定。
  预评价措施与本专篇设施的对应写在附件册附件6，主报告设施表不设承接列。
  预评价措施或结论里把接触限值（85 dB(A)、WBGT）当作采购或作业位置指标的（如“作业位置噪声保证值以不超过85 dB(A)为目标”），不照抄，按口径 D17 改写：
  声源措施、限制接触时间、护听器、要求厂家提供声功率级保证值和位置声压级测试报告；这类条目在表4.8-1、表4.8-2 和附件册附件6 判“调整采纳”，理由写“接触限值不作设备或作业位置采购指标”。
- 设计文件（可研、初设）没写的职业病防护设施，只在危害识别和标准条款确实要求时按标准写成设计要求：名称、规格型号、数量、设置位置、技术参数及控制性能、依据条款；
  参数按标准和舱体、房间尺寸算出最低值（计算过程在附件册附件4 计算书）。设备固有参数（声功率级、噪声保证值、散热量）要有出处，没有出处的写“采购时要求厂家提供××保证值和测试报告”（测试工况、测点、方法），
  不编数；接触限值（85 dB(A)、WBGT 限值、OEL）不作设备或作业位置的采购指标。采购技术协议应满足的内容只针对建设单位采购的设备，施工单位自带设备不写采购指标。
  不写“初设没有写”“可研没有写”“由设计单位确认”“待取得”。
- 指标先过可行性（口径 D17）：设计值写之前对照源强（可研、初设、类比、厂家资料）；源强已高于限值、所需降噪量超过所写措施的可达范围的，不写作业位置达标值，
  改写声源和传播途径措施、值班室按房间设计值、限制接触时间或轮换、护听器、停机检修；限制接触时间后 LEX,8h 符合的，预期结论写“限制接触时间后符合”；
  仍超限的噪声，预期结论写“超限，须个体防护”，并另写未计护听器的 LEX,8h 和佩戴后有效声级 L'Ax（GB 23466—2025 表2）；个体防护的衰减不计入限值比较，
  化学因素、粉尘、高温不得写“个体防护后符合”，写工程措施或工艺调整。85 dB(A) 只用于 LEX,8h 的接触评价。
  预期值不写成限值本身，写类比值、计算值或范围并注明来源。
- 验收能核（口径 D18）：设施表“技术参数及控制性能”只写竣工验收时能测、能点数、能查资料的内容，带数的写测量条件；
  隔声罩插入损失、减振垫压缩量、过滤效率、响应时间、管理频次和年耗量这类验收核不了的不进主报告设施表；不为“写全”加可有可无、现场难核的设施。
- 写法（口径 D19）：带“# N”章标题的文件，章标题下 2～3 句直接给本章结论（04_2 等没有章标题的文件、附件册各文件不加）；每节先一句结论或表号，再表，再“注”，再（1）（2）…条目（每节不超过 8 条）；
  一段只说一件事，段落一般不超过 150 字，句子一般不超过 50 字；并列要求用（1）（2）…条目，不用行首“- ”；
  表格一般不超过 7 列，单元格只写短语或一两个数（不超过 60 字），说明放表注；单元格不写“（采购指标）”“（计算值）”，在表注按项号说明；
  条款号集中在“依据条款”栏或表注，正文一句话里不堆 3 个以上标准号；交叉引用只在必要处写一次；缩写第一次出现写全称。
- 预评价按条件写的（如“储能区（如在本项目范围内建设）”），专篇按口径表“采用”的说法写定，不写条件句；岗位因素表里仍带“（如……）”的行，名称原样保留，不展开写条件句。
- 已写好的前面各章%s：表号、名称、数字要承接一致。
- 条数、合计用占位符：{{行数:表4.3-1}}（表体行数）、{{合计:表4.8-1:条数}}、{{合计:表4.9-1:小计（万元）}}；
  专篇的表没有“符合性”列，不用 {{计数:…}}（采纳情况的计数句照表4.8-1 的合计写）。
%s输出格式：先输出一行 <<<正文>>>，接着是文件全文。"""
DESIGN_KIND = {DT.DESIGN: '职业病防护设施设计专篇主报告：只放结论和结论表，技术性过程放资料性附件册（WS/T 882—2013 第8.1.2条）；'
                          '引用附件册的表写“附件册表1.1-1”，引用附件写“附件册附件1”；'
                          '附件册附件4 节号固定：4.1 预期接触水平计算、4.2 事故通风量计算、4.3 有害气体积聚和缺氧计算、4.4 噪声隔声计算、4.5 辅助卫生设施核算、4.6 照明计算，'
                          '表注引用“见附件册附件4第4.N节”照此写节号',
               DT.ANNEX: '职业病防护设施设计专篇资料性附件册：放技术性过程（WS/T 882—2013 第8.1.3条）——资料清单、计算过程、'
                         '逐条明细、核对表；数字、结论和主报告各章一致，表号按本册的节编（表1.1-1）。'
                         '计算书（附件4）公式写成公式块：<!-- 公式 编号=4.2-1 --> 后紧接单独一行 $$LaTeX$$，再写“式中：”和逐行“$符号$ —— 含义，单位”；'
                         '代入计算那一行用不编号的 <!-- 公式 -->；不用行首“- ”列公式，表格和段落里不写公式文字，写“按式（4.2-1）计算”。'
                         '每个二级节“4.N ××计算”：一句话说明算什么和结论 → #### 计算依据 → #### 计算公式 → #### 参数取值（表：符号｜参数｜取值｜单位｜来源；4.1 除外，逐地点参数写在表4.1-1“输入参数及来源”列）'
                         '→ #### 计算结果 → #### 结论；变量斜体，标签下标用 \\mathrm{}，分式 \\frac，lg ln 用命令，单位写在式中块'}


RE_GROUP_ROW = re.compile(r'^\|\s*[一二三四五六七八九十]+、[^|]*\|\s*[<^]\s*\|')


def _units(skel):
    """骨架按节拆开：[(标题行, 本节起始行号)]。先按“## ”拆（至少两节），不够再按“### ”拆；拆不开返回空。"""
    lines = skel.split('\n')
    for mark in ('## ', '### '):
        hs = [(ln.strip(), i) for i, ln in enumerate(lines) if ln.startswith(mark)]
        if len(hs) >= 2:
            return hs
    # 一个“## ”节里只有一张大表（附件册附件5）：按表里的分组行（“| 一、…… | < | < |”）拆
    rows = [(ln.strip(), i) for i, ln in enumerate(lines) if RE_GROUP_ROW.match(ln.strip())]
    return rows if len(rows) >= 2 else []


def _write_range(call, label, fname, units, lo, hi, depth, prior=''):
    """写 units[lo:hi] 这几节（第一段连文件开头的标题、写法提示一起写）。这一段又写不成的，再对半分（最多再分 DESIGN_SPLIT 层）。
    返回这几节的正文；prior＝前面几节已写好的正文（后面几节承接表号、数字用）。"""
    first, last = lo == 0, hi == len(units)
    table = units[0][0].startswith('|')
    unit = '分组' if table else '节'
    if table:    # 整个文件是一张大表：几段的表行首尾相接
        add = ('\n- 这一次只写文件的一部分，文件里的大表分几次写完：从%s写到%s。不要写“待续”“以下略”“前文”之类的话，也不写没轮到的分组。'
               % ('文件开头（文件标题、写法提示照常处理，表题、表格指令、表头、分隔行和第一个分组一起写）' if first else
                  '分组“%s”开始接着往下写表行（不写文件标题、表题、表格指令、表头和分隔行，第一行就是这个分组行；表行之间不空行）' % units[lo][0].split('|')[1].strip(),
                  '文件末尾（表后的内容、版面指令一起写；骨架没列出、但资料里有的分组也写在这一段）' if last else
                  '分组“%s”结束（含，写完这个分组的全部表行就停）' % units[hi - 1][0].split('|')[1].strip()))
        if not first:
            add += '前面几段已写好，见“已写好的前半部分”，表号、序号、名称接着往下排。'
    else:
        add = '\n- 这一次只写文件的一部分：从%s写到%s，别的节另写（不要写“待续”“以下略”“前文”之类的话，也不写没轮到的节）。%s' % (
            '文件开头（文件标题、写法提示照常处理，第一节一起写）' if first else '“%s”这一节开始（不写文件标题和前面的节）' % units[lo][0],
            '文件末尾' if last else '“%s”这一节结束（含）' % units[hi - 1][0], '' if first else '前面几节已写好，见“已写好的前半部分”。')
    try:
        got = call('%s·第%d～%d%s' % (label, lo + 1, hi, unit), [C.doc('已写好的前半部分（全文）', prior)] if prior else [], add)
        if len(got) < 50:
            raise llm.CallError('%s 第%d～%d%s：模型给的正文太短（%d 字）' % (label, lo + 1, hi, unit, len(got)))
        return got
    except llm.CallError as e:
        if e.fatal or '拒答' in str(e) or hi - lo < 2 or depth >= DESIGN_SPLIT:
            raise
    mid = (lo + hi) // 2
    a = _write_range(call, label, fname, units, lo, mid, depth + 1, prior)
    b = _write_range(call, label, fname, units, mid, hi, depth + 1, '\n\n'.join(x for x in (prior, a) if x))
    return _join_parts([a, b], units)


RE_TABLE_HEAD = re.compile(r'^(?:表\d[\d.\-]*\s|<!--\s*表格|\|\s*序号\s*\||\|[-: |]+\|\s*$)')


def _join_parts(pieces, units):
    """几段拼成一个文件；后一段开头重复了前面已有的标题行（模型又写了一遍文件标题）的去掉。
    整个文件是一张大表的（units 是分组行）：后一段开头重复的表题、表格指令、表头、分隔行去掉，表行首尾相接不空行。"""
    table = bool(units) and units[0][0].startswith('|')
    out, seen = [], set()
    for k, x in enumerate(pieces):
        lines = x.strip('\n').split('\n')
        while k and lines and (not lines[0].strip() or (lines[0].startswith('#') and lines[0].strip() in seen
                                                       and lines[0].strip() not in {h for h, _ in units})
                               or (table and RE_TABLE_HEAD.match(lines[0].strip()))):
            lines.pop(0)
        seen |= {ln.strip() for ln in lines if ln.startswith('#')}
        out.append('\n'.join(lines).strip('\n'))
    res = ''
    for x in out:
        if not x:
            continue
        res = x if not res else res + ('\n' if table and res.rstrip().split('\n')[-1].startswith('|') and x.startswith('|') else '\n\n') + x
    return res


def design_budget_line(root, t, fname):
    """DESIGN_TASK 里的篇幅一行（口径 D19）：目标 N 字、上限 1.2N，N 由 doctype.budget_for 按项目篇幅档算；算不出返回空。"""
    n = DT.budget_for(root, ('附件册/正文/' if t == DT.ANNEX else '正文/') + fname)
    if not n:
        return ''
    return '- 本文件篇幅目标约 %d 字（汉字，含表），最多不超过 %d 字（本项目按 %s 档）；超出时先删重复的表和过程性内容。\n' % (
        n, int(round(n * DT.OVER_RATIO)), DT.design_tier(root))


def _write_design(job, root, t, sample, fname, deps, written=None, extra=''):
    """设计专篇写一个文件：事实库、岗位因素表、承接的预评价对应章全文、前面写好的文件摘要；
    附件册另给主报告各章摘要。不收集“可研没有写”（专篇把缺的写成设计要求）。"""
    skel = tools.read(Path(root) / '正文' / fname)
    parts = C.fact_docs(root)
    parts.append(C.doc('正文/%s 现有骨架' % fname, skel))
    for n, text in C.sample_chapter(sample, fname):
        parts.append(C.doc('样板 %s（只学结构、表头和写法深浅，不抄任何事实）' % n, text))
    if t == DT.ANNEX:
        main = DT.main_root(root)
        mf = tools.body_files(main)
        full = DT.annex_main_full(fname, mf)
        for f in mf:
            text = tools.read(Path(main) / '正文' / f)
            parts.append(C.doc('主报告 正文/%s（全文）' % f, text) if f in full else
                         C.doc('主报告 正文/%s（摘要：标题、表格、小结）' % f, tools.digest(text, notes=True)))
    for d in deps:
        p = Path(root) / '正文' / d
        if p.exists():
            parts.append(C.doc('已写好的 %s（摘要：标题、表格、小结）' % d, tools.digest(tools.read(p), notes=(t != DT.PRE))))
    parts += C.pre_docs(root, fname)
    part = ('（第%d章的一部分，只写本文件骨架里的节）' % int(fname[:2])) if t == DT.DESIGN and re.match(r'^\d\d_\d', fname) else ''
    task = DESIGN_TASK % (fname, part, DESIGN_KIND[t], design_budget_line(root, t, fname), '和主报告' if t == DT.ANNEX else '',
                          '- %s\n' % extra if extra else '')
    label = '写 %s%s' % ('附件册/' if t == DT.ANNEX else '', fname)
    sysmsg = C.system(t, C.write_format(), ('本章要点卡', C.card(t, '', fname, root=root)))

    def call(lab, more, add):
        content = '\n\n'.join(parts + more + [task + add])
        out = llm.call_retry(job, lab, sysmsg, content, tries=1 if lab == label else 2, effort='high', phase='写作')
        got = llm.sections(out, ['正文']).get('正文', '').strip()
        if not got:
            # 11-07 200MW 附件册04：模型整轮都在思考（5.6 万 token、39 分钟），正常结束却一个字没写，当成“太短”再拆，白花约 4 美元。
            # 空回答（不是断流）降一档思考强度、加一句提醒再写一次
            job.info('%s 模型只思考没写出正文，降低思考强度再写一次' % lab)
            out = llm.call_retry(job, lab + '（降档重写）', sysmsg, content + '\n\n直接按“输出格式”写正文，开头先输出一行 <<<正文>>>，不要先长时间思考或列提纲。',
                                 tries=1, effort='medium', phase='写作')
            got = llm.sections(out, ['正文']).get('正文', '').strip()
        return got
    try:
        body = call(label, [], '')
    except llm.CallError as e:
        # 11-07 200MW 04_2：整章一次写，中转在 24～37 分钟时两次断流（RemoteProtocolError），白等了两个半小时。
        # 整章写不成的（断流、写不完），按节分两半各写一遍；再不行再对半分一次。
        # 骨架只有一个节、里面是一张大表的（附件册附件5），按表里的分组行拆；都拆不开的，整章再试一次
        if e.fatal or '拒答' in str(e):
            raise
        units = _units(skel)
        if len(units) < 2:
            job.info('%s 整章写不成（%s），骨架拆不开，整章再试一次' % (label, str(e)[:60]))
            body = call(label + '（整章再试）', [], '')
        else:
            job.info('%s 整章写不成（%s），按%s分两半写' % (label, str(e)[:60], '表的分组' if units[0][0].startswith('|') else '节'))
            mid = len(units) // 2
            first = _write_range(call, label, fname, units, 0, mid, 1)
            second = _write_range(call, label, fname, units, mid, len(units), 1, first)
            body = _join_parts([first, second], units)
    if len(body) < max(300, len(skel) // 3):
        raise llm.CallError('写 %s：模型给的正文太短（%d 字）' % (fname, len(body)))
    body = C.fix_format(job, fname, body, t=t)
    tools.write(Path(root) / '正文' / fname, body + '\n')
    res = {'字数': len(body), '可研没有': [], '剩余错误': None}
    if written:
        written(res)
    res['剩余错误'] = C.fix_errors(job, root, fname, '')
    return res


# 从预评价项目信息拷的键；人员类别对到专篇签字页（WS/T 882 附录D：项目负责人、专篇审核人）
INFO_KEYS = ('项目名称', '建设单位', '评价单位', '法定代表人', '资质证书编号')
PEOPLE_MAP = {'项目负责人': '项目负责人', '报告编写人': '专篇编写人', '报告审核人': '专篇审核人'}


def _info_val(text, key):
    m = re.search(r'^- %s：(.*)$' % key, text, re.M)
    return m.group(1).strip() if m else ''


def _set_info(text, key, val):
    if re.search(r'^- %s：' % key, text, re.M):
        return re.sub(r'^- %s：.*$' % key, lambda m: '- %s：%s' % (key, val), text, count=1, flags=re.M)
    return text


def _people_rows(text):
    """项目信息里人员表的数据行（预评价“## 评价人员”、专篇“## 专篇编写人员”）。"""
    m = re.search(r'^## [^\n]*人员.*?(?=^## |\Z)', text, re.M | re.S)
    rows = [ln for ln in (m.group(0) if m else '').split('\n') if ln.startswith('|')]
    return [ln for ln in rows[2:] if not set(ln) <= set('|-: ')]


def _design_info(job, root, pre, sample, form):
    """专篇项目信息：名称、封面名称、建设单位、评价单位、法定代表人、人员从预评价拷；资质证书编号照预评价或样板；
    报告编号、日期用表单。已填过（项目名称不是【待填】）的不重填。不调模型。"""
    p = Path(root) / '项目信息.md'
    s = tools.read(p)
    if not re.search(r'^- 项目名称：【待填】', s, re.M):
        job.project_name = form.get('简称') or _info_val(s, '项目名称') or Path(root).name
        return {'项目名称': _info_val(s, '项目名称')}
    t = tools.read(Path(pre) / '项目信息.md')
    smp = tools.read(Path(sample) / '项目信息.md') if sample and (Path(sample) / '项目信息.md').is_file() else ''
    for k in INFO_KEYS:
        v = _info_val(t, k) or (_info_val(smp, k) if k == '资质证书编号' else '')
        if v and not tools.TODO.fullmatch(v):
            s = _set_info(s, k, v)
    if form.get('名称'):
        s = _set_info(s, '项目名称', form['名称'])
    for k, v in (('报告编号', form.get('编号')), ('报告日期', form.get('日期'))):
        if v:
            s = _set_info(s, k, v)
    covers = re.findall(r'^- 封面名称：(.*)$', t, re.M)
    if covers:
        lines = s.split('\n')
        at = [i for i, ln in enumerate(lines) if ln.startswith('- 封面名称：')]
        if at:
            keep = [ln for i, ln in enumerate(lines) if i not in at]
            keep[at[0]:at[0]] = ['- 封面名称：' + x.strip() for x in covers]
            s = '\n'.join(keep)
    rows = []
    for ln in _people_rows(t):
        cells = [x.strip() for x in ln.strip().strip('|').split('|')]
        if cells and cells[0] in PEOPLE_MAP:
            rows.append('| ' + ' | '.join([PEOPLE_MAP[cells[0]]] + cells[1:]) + ' |')
    old = _people_rows(s)
    if rows and old:
        lines = s.split('\n')
        i = lines.index(old[0])
        lines[i:i + len(old)] = rows
        s = '\n'.join(lines)
    tools.write(p, s)
    name = _info_val(s, '项目名称')
    job.project_name = form.get('简称') or name
    return {'项目名称': name}


def _link_tree(src, dst):
    """原件太大，能硬链接的硬链接（不占地方），链不了的不拷：资料/文本/ 有文字，索引里记的来源还是预评价那份原件。"""
    n = 0
    for f in Path(src).rglob('*'):
        if f.is_file() and not f.is_symlink():
            to = Path(dst) / f.relative_to(src)
            if to.exists():
                continue
            to.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(f, to)
                n += 1
            except OSError:
                pass
    return n


# 预评价章号 → 专篇落在哪（口径表“可研没有写的”每条后面写的是预评价的章号）
PRE_TO_DESIGN = {'1': '第1章', '2': '第2章', '3': '第3.3节', '4': '第3章', '5': '第4.3节', '6': '第4.4节', '7': '第4.5节',
                 '8': '第4.3.7节', '9': '第3.2.2节', '10': '第4.8节', '11': '第5.3节'}


def _design_gaps(kj):
    """拷来的口径表：“## 可研没有写的”改叫“## 设计文件没有写的”，每条后面预评价的章号换成专篇的节号，删掉空条目。"""
    kj = kj.replace('## 可研没有写的', '## 设计文件没有写的', 1)
    m = re.search(r'^## 设计文件没有写的.*?$(.*?)(?=^## |\Z)', kj, re.M | re.S)
    if not m:
        return kj

    def one(ln):
        k, sep, used = ln.partition('：')
        if not sep:
            return ln
        got = []
        for n in re.findall(r'第(\d+)章', used):
            x = PRE_TO_DESIGN.get(n)
            if x and x not in got:
                got.append(x)
        return k + ('：' + '、'.join(got) if got else ('：' + used if not re.search(r'第\d+章', used) else ''))

    lines = [one(ln) if ln.startswith('- ') else ln for ln in m.group(1).split('\n') if ln.strip() not in ('-', '- ')]
    return kj[:m.start(1)] + '\n'.join(lines) + kj[m.end(1):]


def _copy_pre(job, root, pre):
    """资料从预评价拷：事实库、口径表、岗位因素表、类比/（不含类比原件和移出的）、文本/（含索引），原件硬链接。
    口径表“可研没有写的”一节改叫“设计文件没有写的”：专篇把这些写成设计要求。"""
    src, dst = Path(pre) / '资料', Path(root) / '资料'
    dst.mkdir(parents=True, exist_ok=True)
    got = []
    for name in ('事实库.md', '口径.md', '岗位因素表.md'):
        if (src / name).is_file() and not (dst / name).exists():
            text = tools.read(src / name)
            if name == '口径.md':
                text = _design_gaps(text)
            tools.write(dst / name, text)
            got.append(name)
    for name, ignore in (('类比', shutil.ignore_patterns('原件', '移出')), ('文本', None)):
        if (src / name).is_dir() and not (dst / name).exists():
            shutil.copytree(src / name, dst / name, ignore=ignore)
            got.append(name + '/')
    if (src / '原件').is_dir():
        n = _link_tree(src / '原件', dst / '原件')
        if n:
            got.append('原件 %d 份（硬链接）' % n)
        _relink_index(dst / '文本' / '索引.json', src / '原件', dst / '原件')
    if not (dst / '文本').is_dir() and not tools.project_settings(root).get('事实来源'):
        # 预评价靠 流水线设置.json 的“事实来源”或上一级 提取文本/ 取资料（老项目）：文字落一份到专篇里，记成专篇的事实来源
        texts = tools.source_texts(pre)
        if texts:
            rel = []
            for name, text in texts:
                fn = '资料/文本/预评价/' + (Path(name).stem or '资料') + '.txt'
                tools.write(Path(root) / fn, text)
                rel.append(fn)
            sets = tools.project_settings(root)
            sets['事实来源'] = rel
            tools.write(Path(root) / tools.SETTINGS_FILE, json.dumps(sets, ensure_ascii=False, indent=2))
            got.append('资料文字 %d 份（预评价的事实来源）' % len(rel))
    job.info('从预评价拷来：%s' % ('、'.join(got) or '没有要拷的（上次已拷）'))
    return got


# 预评价没有 资料/岗位因素表.md（老项目，正文是手工改出来的）：从预评价正文的表4.1-1 拼一张，不调模型。
# 岗位因素表的列序（和新报告写的一样）；预评价表4.1-1 的列名对到这些列（多个说法取第一个对上的）
POST_COLS = [('评价单元', ('评价单元',)), ('工作场所', ('工作场所',)), ('工种', ('工种',)),
             ('工作地点', ('工作地点',)), ('作业内容', ('作业内容',)), ('作业方式', ('作业方式',)),
             ('接触时间', ('接触时间',)), ('职业病危害因素', ('职业病危害因素', '危害因素')), ('来源', ('来源', '产生环节'))]


def _post_from_pre(pre):
    """从预评价正文拼岗位因素表：表4.1-1 换成岗位因素表的列序（前三列评价单元、工作场所、工种，_table_counts 照这三列数），
    后面附上表1.8-1、表4.1-2 原样。返回 Markdown；表4.1-1 找不到或列对不上的返回空串。"""
    from pe import mdparse
    texts = [tools.read(p) for p in sorted((Path(pre) / '正文').glob('0[14]*.md'))]
    t41 = next((x for t in texts for x in tools.tables_by_caption(t, r'^表4\.1-1\s')), '')
    rows = [ln for ln in t41.split('\n') if ln.startswith('|')]
    if len(rows) < 3:
        return ''
    head = [c.strip() for c in mdparse.split_cells(rows[0])]
    idx = []
    for name, keys in POST_COLS:
        i = next((j for j, h in enumerate(head) if any(k in h for k in keys)), None)
        idx.append((name, i))
    if any(i is None for name, i in idx[:3]) or idx[7][1] is None:
        return ''                                    # 单元、场所、工种、因素四列是必须的
    keep = [(name, i) for name, i in idx if i is not None]
    out = ['# 岗位因素表', '',
           '<!-- 承接的预评价没有岗位因素表：本表由程序从预评价报告表4.1-1 换列序拼成（不调模型，内容一字不改），'
           '后附预评价报告表1.8-1、表4.1-2 原表。专篇的评价单元、工作场所、工种、因素名称照本表，和预评价一字不差。 -->', '',
           '| ' + ' | '.join(head[i] for _, i in keep) + ' |', '|' + '---|' * len(keep)]
    for ln in rows[2:]:
        cells = [c.strip() for c in mdparse.split_cells(ln)]
        if len(cells) == len(head):
            out.append('| ' + ' | '.join(cells[i] for _, i in keep) + ' |')
    for pat, title in ((r'^表1\.8-1\s', '预评价报告表1.8-1'), (r'^表4\.1-2\s', '预评价报告表4.1-2')):
        got = next((x for t in texts for x in tools.tables_by_caption(t, pat)), '')
        if got:
            out += ['', '## %s（原表）' % title, '', got]
    return '\n'.join(out) + '\n'


def _ensure_post(job, root, pre):
    """专篇没有岗位因素表（预评价也没有）时，从预评价正文拼一张。已有的不动。返回是不是新拼的。"""
    p = Path(root) / '资料' / '岗位因素表.md'
    if p.exists():
        return False
    md = _post_from_pre(pre)
    if not md:
        job.info('承接的预评价没有岗位因素表，正文里也找不到能用的表4.1-1：名称照预评价正文的表写')
        return False
    tools.write(p, md)
    job.info('承接的预评价没有岗位因素表：照预评价报告表4.1-1 拼了一张（表1.8-1、表4.1-2 附后），没调模型')
    return True


def _relink_index(idx, src, dst):
    """拷来的资料索引里“来源”指向预评价的原件：换成专篇 资料/原件/ 里对应的硬链接（链不了的保留原路径）。"""
    if not Path(idx).is_file():
        return
    try:
        data = json.loads(tools.read(idx))
    except ValueError:
        return
    n = 0
    for b in data.get('块', []):
        try:
            rel = Path(b.get('来源', '')).resolve().relative_to(Path(src).resolve())
        except (ValueError, OSError):
            continue
        if (Path(dst) / rel).is_file():
            b['来源'] = str(Path(dst) / rel)
            n += 1
    if n:
        tools.write(idx, json.dumps(data, ensure_ascii=False, indent=1))


def _take_extra(job, root, given):
    """表单给的补充资料（初设新版、施工图）：拷进 资料/原件/补充_月日-时分/，转文字记进资料索引（和补资料一样），
    再拿它对照从预评价拷来的事实库，更正、新增、补上的变化直接改进事实库和口径表（专篇还没写，不用逐条确认）。
    接着做时同样的资料不再拷；拷了还没改事实库的，接着改。"""
    from . import supplement as S
    st = Path(root) / '资料' / F.DRAFT / '专篇补充资料.json'
    try:
        done = json.loads(tools.read(st)) if st.is_file() else None
    except ValueError:
        done = None
    if isinstance(done, list):                      # 老记录：只记了资料清单，当作全做完了
        done = {'资料': done, '事实': True}
    if not given or (done and done.get('资料') == given and done.get('事实')):
        return
    if done and done.get('资料') == given and done.get('标记'):
        tag = done['标记']
        tdir = Path(root) / '资料' / '文本' / '补充'
        texts = [(f.name, str(f), tools.read(f)) for f in sorted(tdir.glob('%s_*.txt' % tag))] if tdir.is_dir() else []
        job.info('接着上次做：补充资料上次已转成文字，接着对照事实库')
    else:
        import datetime
        tag = datetime.datetime.now().strftime('%m%d-%H%M')
        folder = Path(root) / '资料' / '原件' / ('补充_' + tag)
        S._copy(given, folder)
        texts, skipped = S._convert(job, root, folder)
        if texts:
            S._register(job, root, tag, texts)
        if skipped:
            job.info('补充资料没转成文字的：%s' % '；'.join(skipped[:6]))
        tools.write(st, json.dumps({'资料': given, '标记': tag, '事实': False}, ensure_ascii=False))
    if texts and (Path(root) / '资料' / '事实库.md').is_file():
        _extra_facts(job, root, [(n, t) for n, _, t in texts])
    tools.write(st, json.dumps({'资料': given, '标记': tag, '事实': True}, ensure_ascii=False))


def _extra_facts(job, root, texts):
    """补充资料（比预评价时新的初设、施工图）对照事实库：照补资料的“摘变化”，变化全部改进事实库和口径表
    （施工图优于初设、初设优于可研，按新资料）。返回改成的条数。"""
    from . import supplement as S
    fp, kp = Path(root) / '资料' / '事实库.md', Path(root) / '资料' / '口径.md'
    fact_md, kj = tools.read(fp), tools.read(kp) if kp.is_file() else ''
    ctx = '\n\n'.join([C.doc('事实库（现有，从承接的预评价拷来）', fact_md), C.doc('口径表（现有）', kj), S._file_list(root)])
    raw = []
    parts = F._batches(F._pieces(texts, F.CHECK_BATCH), F.CHECK_BATCH)
    for i, part in enumerate(parts):
        job.check_cancel()
        res = llm.call_retry(job, '补充资料对照事实库' + ('' if len(parts) == 1 else ' 第%d批' % (i + 1)), C.system(DT.DESIGN),
                             '\n\n'.join([C.doc(n, t) for n, t in part] + [ctx, S.task_of(root)]),
                             effort='high', schema=S.CHANGES, phase='事实')
        raw += res['变化']
    items = S._filter(job, root, raw, fact_md, kj)
    if not items:
        job.info('补充资料和事实库没有出入')
        return 0
    n, _ = S._update_facts(job, root, items)
    return n


def _note_design(root, form, info, pre, chk, rv, pub, table):
    """设计专篇的交稿说明：两卷、承接的预评价、补摘的事实、设计文件没写由专篇定的。"""
    kj = tools.read(Path(root) / '资料' / '口径.md') if (Path(root) / '资料' / '口径.md').exists() else ''
    own = re.search(r'^## 设计文件没有写的.*?(?=^## |\Z)', kj, re.M | re.S)
    todo = tools.check(root)['按文件'].get('项目信息.md', {}).get('待办', [])
    pdfs = []
    for vol in DT.volumes(root):
        found = sorted((Path(vol) / '输出').glob('*.pdf'), key=lambda x: x.stat().st_mtime) if (Path(vol) / '输出').is_dir() else []
        if found:
            pdfs.append(str(found[-1]))
    lines = ['# 交稿说明', '', '## 报告', '',
             '- 项目：%s' % info['项目名称'], '- 报告类型：职业病防护设施设计专篇（主报告＋资料性附件册两卷）',
             '- 承接的预评价报告：%s' % pre,
             '- PDF：%s' % (('共 %s 页，目录页码逐页核对通过' % pub['页数']) if pub and pub.get('通过') else '没出或没通过'),
             '- PDF 文件：%s' % ('；'.join(pdfs) or '—'),
             '- 检查：错误 %d、提示 %d、待办 %d（两卷合计）' % (chk['错误'], chk['提示'], chk['待办']),
             '- 审稿：必改 %d 条、建议 %d 条，改了 %d 处' % (rv['必改'], rv['建议'], rv['已改']) if rv else '- 审稿：没做',
             '- 名称：评价单元 %d 个、工作场所 %d 个、工种 %d 个（照预评价的岗位因素表）' % (table['单元'], table['场所'], table['工种']),
             '', '## 请你确认', '', '- 编写人员、项目信息是照预评价拷的（报告编写人→专篇编写人，报告审核人→专篇审核人），请核对。',
             '- 封面“设计单位资质证书编号”：项目信息.md 的“资质证书编号”，没有的是【待填】，交稿前填上。',
             '- 预评价报告的评审意见、备案（审核）回执有没有：WS/T 882—2013 第5.3条把它列为基础依据；有的话第1.2.3节补上编号、日期。',
             '- 投资（主报告表4.9-1、附件册附件7）：初设概算没有分项的，是按预评价报告表8.3-1估算拆成四类费用的，单价请建设单位或设计单位核。',
             '- 原始资料（委托书、立项文件、预评价评审意见、初设总平面图等）：附件册表1.2-1“是否影印”为“是”的，装订时附在附件册后（WS/T 882—2013 第8.1.3条）。',
             '- 两卷按 WS/T 882—2013 附录D.3 双面打印的，装订前核对偶数页装订边（模板没设对称页边距）。']
    pt = tools.read(Path(root) / '资料' / '岗位因素表.md') if (Path(root) / '资料' / '岗位因素表.md').is_file() else ''
    for x in gaps._bullets(pt, '按不利情况定的'):
        if re.search(r'如在本项目|如建设|如设置|是否纳入|写法不一|拟租赁', x):
            # 预评价按条件写的（储能纳不纳入、生活舱设不设），专篇是设计文件，要定下来
            lines.append('- 预评价按条件写的，专篇要定下来（现在按口径表“采用”写）：%s' % x[:120])
    lines += ['- ' + x for x in todo]
    if own:
        lines += ['', '## 设计文件没有写的（专篇已按标准写成设计要求，请设计、采购单位落实）', '', own.group(0).split('\n', 1)[1].strip()]
    g = gaps.write(root)               # 主报告的检查（附件册的待办 collect 自己并进来）
    if g['要补']:
        lines[lines.index('## 请你确认') + 2:lines.index('## 请你确认') + 2] = [
            '- 还缺 %d 项资料，见同一文件夹的“资料欠缺清单”（可以直接转给建设单位）。' % len(g['要补'])]
    tools.write(Path(root) / '输出' / '交稿说明.md', '\n'.join(lines) + '\n')


def _sum_check(root):
    """两卷检查结果合起来（错误、提示、待办）。"""
    out = {'错误': 0, '提示': 0, '待办': 0}
    for vol in DT.volumes(root):
        c = tools.check(vol)
        for k in out:
            out[k] += c[k]
    return out


def check_pre(pre, root):
    """承接的预评价项目要是预评价报告、有 正文/，专篇文件夹和它互不包含（网页、命令行、任务里都查）。返回出错说明，没错返回空。"""
    pre, root = Path(pre).expanduser().resolve(), Path(root).expanduser().resolve()
    if not (pre / '正文').is_dir():
        return '承接的预评价项目没有正文：%s' % pre
    if tools.report_type(pre) != DT.PRE:
        return '承接的项目不是预评价报告（是%s）：%s' % (tools.report_type(pre), pre)
    if pre == root or pre in root.parents or root in pre.parents:
        return '设计专篇不能建在预评价项目文件夹里面，也不能包含它：%s' % pre
    return ''


def _old_draft(root):
    """正文（主报告、附件册）都没有【待填】，且没有写作记录：是写完的旧稿，不是骨架。"""
    texts = []
    for r in (Path(root), DT.annex_root(root)):
        if r is not None:
            texts += [tools.read(Path(r) / '正文' / f) for f in tools.body_files(r)]
    if not texts or _record_path(root).exists():
        return False
    return not any('【待填】' in t for t in texts)


def job_design(job):
    form = job.params
    root = Path(form['文件夹']).expanduser().resolve()
    pre = Path(form['预评价']).expanduser() if form.get('预评价') else None
    job.project = str(root)
    job.stage('准备')
    if not (root / '项目信息.md').exists():
        junk = (jobs.LOCK_NAME, '.DS_Store', 'desktop.ini', 'Thumbs.db')
        extra = [p for p in root.iterdir() if p.name not in junk and not p.name.startswith('._')] if root.exists() else []
        if extra:
            raise RuntimeError('文件夹已存在且不是空的：%s' % root)
        shutil.copytree(DT.skeleton(DT.DESIGN), root, dirs_exist_ok=True)
        job.info('已建项目（主报告＋附件册）：%s' % root)
    else:
        job.info('接着上次做：项目已建好（%s），做完的步骤不重做' % root)
        if not form.get('只写') and _old_draft(root):
            job.info('注意：正文里已没有【待填】，也没有写作记录——这是写完的旧稿。接着做会把旧稿当骨架改写，新骨架和新口径落不了地；'
                     '要按新口径重来，先把 正文/、附件册/正文/ 备份，再用模板（templates/项目骨架_设计专篇）两卷的正文整个覆盖，保留项目信息、图、资料、设置。')
    sets = tools.project_settings(root)
    if pre is None and sets.get('预评价'):
        pre = Path(sets['预评价']).expanduser()          # 接着做：表单没带，按上次记的
    if pre is None:
        raise RuntimeError('设计专篇要给承接的预评价项目文件夹')
    bad = check_pre(pre, root)
    if bad:
        raise RuntimeError(bad)
    pre = pre.resolve()
    if sets.get('预评价') != str(pre):
        sets['预评价'] = str(pre)
        tools.write(root / tools.SETTINGS_FILE, json.dumps(sets, ensure_ascii=False, indent=2))
    sample = tools.sample_for('', exclude=root, kind=DT.DESIGN)
    job.info('承接的预评价：%s；样板：%s' % (pre, sample or '没有（按骨架、要点卡和预评价写）'))
    info = _design_info(job, root, pre, sample, form)
    _copy_pre(job, root, pre)
    _ensure_post(job, root, pre)
    _take_extra(job, root, form.get('资料') or [])
    if not tools.source_texts(root):
        raise RuntimeError('没有资料文字：预评价项目的 资料/文本/ 没拷过来，也没给补充资料')
    tools.register_add(form.get('简称') or info['项目名称'], '—', root, DT.DESIGN)
    job.stage('事实')
    if not (root / '资料' / '事实库.md').exists():
        F.build(job, root)
    F.build_extra(job, root, F.HEADS_DESIGN)
    job.stage('骨架')
    tp = root / '资料' / '岗位因素表.md'
    table = _table_counts(tools.read(tp)) if tp.exists() else {'行': 0, '单元': 0, '场所': 0, '工种': 0}
    job.info('岗位因素表照预评价：评价单元 %(单元)d 个、工作场所 %(场所)d 个、工种 %(工种)d 个' % table)
    job.stage('写作')
    only = form.get('只写') or None
    annex = DT.annex_root(root)
    if only:
        # “只写”：不带前缀的是主报告文件，“附件册/06”是附件册的文件
        pre_a = DT.ANNEX_DIR + '/'
        mo = [x for x in only if not x.startswith(pre_a)]
        ao = [x[len(pre_a):] for x in only if x.startswith(pre_a)]
        out = {}
        if mo:
            wr = write_all(job, root, '', sample, mo)
            out.update({k: {'字数': v['字数'], '剩余错误': v['剩余错误']} for k, v in wr.items() if v})
        if ao and annex:
            wa = write_all(job, annex, '', None, ao)
            out.update({pre_a + k: {'字数': v['字数'], '剩余错误': v['剩余错误']} for k, v in wa.items() if v})
        return {'项目': str(root), '写作': out}
    wr = write_all(job, root, '', sample, None)
    missing = [f for f in tools.body_files(root) if not wr.get(f)]
    wa = {}
    if annex and not missing:
        # 附件册在主报告全部写好后再写（要主报告各章的结论和表号）
        wa = write_all(job, annex, '', None)
        missing += ['附件册/' + f for f in tools.body_files(annex) if not wa.get(f)]
    if missing:
        raise RuntimeError('有 %d 个文件没写成（%s），别的都写好了。点“接着做”只补写这几个' % (len(missing), '、'.join(missing)))
    job.stage('配图')
    FG.run_all(job, root)
    job.stage('检查')
    # 先清附件册、再清主报告：主报告表4.8-1 的条数要核附件册附件6，附件册改完再核一次主报告
    for vol in DT.volumes(root)[::-1]:
        RV.clear_all(job, vol, '')
    chk = _sum_check(root)
    rv = None
    if form.get('审稿', True):
        rv = RV.run(job, root, modify=True, publish=False, new_report=True)
        chk = _sum_check(root)
    pub = None
    if form.get('出稿', True) and chk['错误'] == 0:
        job.stage('出稿')
        pub = P.publish(job, root)
    _note_design(root, form, info, pre, chk, rv, pub, table)
    for vol in DT.volumes(root):
        _record_path(vol).unlink(missing_ok=True)
    (root / '资料' / F.DRAFT / '专篇补充资料.json').unlink(missing_ok=True)
    F.drop_empty(root)
    return {'项目': str(root), '类型': DT.DESIGN, '预评价': str(pre), '岗位因素表': table,
            '写作': len([v for v in wr.values() if v]), '附件册': len([v for v in wa.values() if v]),
            '检查': chk, '审稿': rv and {k: rv[k] for k in ('必改', '建议', '已改')}, '出稿': pub,
            '交稿说明': str(root / '输出' / '交稿说明.md')}


__all__ = ['job_new', 'job_design', 'write_all']
