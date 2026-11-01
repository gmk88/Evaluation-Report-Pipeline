"""新报告：建项目、资料转文字 → 事实库 → 岗位因素表 → 各章按先后关系写 → 检查清零 → 审稿 → 出稿 → 交稿说明。"""
import json
import re
import shutil
import threading
from pathlib import Path

from .. import config, jobs, llm, tools
from . import common as C
from . import facts as F
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


def _write_file(job, root, method, sample, fname, deps, gaps, written=None):
    """写一章。written(结果)：正文一写进文件就调，记下这章写好了（后面清零出错也不丢“可研没有写”的事项）。"""
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
输出格式：先输出一行 <<<正文>>>，接着是文件全文；再输出一行 <<<可研没有>>>，接着每行一条本章记下的“可研没有写”事项（没有就写“无”）。''' % (fname, part)
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
    """各章写完的结果（字数、本章记下的“可研没有写”事项），放在新报告的中间结果文件夹里（转资料时不当资料）。"""
    return Path(root) / '资料' / F.DRAFT / '写作记录.json'


def write_all(job, root, method, sample, only=None):
    """按先后关系并行写各章。和骨架不一样的文件是上次写好的，不重写（接着做时省钱）；只写几章的照写。"""
    files = tools.body_files(root)
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
            deps = [d for c in DEPS.get(f[:2], []) for d in by_ch.get(c, [])]
            for d in deps:
                if d in done:
                    done[d].wait()
            job.check_cancel()
            lack = [d for d in deps if d in done and not res.get(d)]
            if lack:
                # 前面的章没写成：照骨架硬写，表号、名称、判定承接不上，写了还会记成“写好了”，接着做时不重写
                job.step('写 %s（等 %s 写成再写）' % (f, '、'.join(lack)), '写作').update(状态='跳过', 说明='前面的章没写成')
                return None
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
    gaps = re.search(r'^## 可研没有写的.*?(?=^## |\Z)', kj, re.M | re.S)
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
    if gaps:
        lines += ['', '## 可研没有写的（报告里已写补充措施；需要建设单位补的看这里）', '', gaps.group(0).split('\n', 1)[1].strip()]
    tools.write(Path(root) / '输出' / '交稿说明.md', '\n'.join(lines) + '\n')


def job_new(job):
    form = job.params
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


__all__ = ['job_new', 'write_all']
