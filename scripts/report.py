#!/usr/bin/env python3
"""职业病危害预评价报告、职业病防护设施设计专篇：新建项目、导入旧报告、检查、生成 Word／PDF（目录页码自动回填）、自测。

两种报告类型（项目信息.md 的“- 报告类型：”，没写的按预评价）：
  预评价     GBZ/T 196—2025，正文/01～11章，一卷
  设计专篇   WS/T 882—2013，主报告 正文/01～05章＋资料性附件册（项目文件夹/附件册/，正文“# 附件1 ……”起）两卷；
             build、wps、出稿 对主报告文件夹运行时两卷都出，各自核对目录页码；只出附件册就对 附件册/ 运行

用法（python3 scripts/report.py 命令 …）：
  new    项目文件夹 [--名称 项目名称] [--类型 设计专篇]
                                        新建项目：复制骨架（项目信息.md、检查规则.md、正文/、资料/、图/；设计专篇连 附件册/）
  import 旧报告.docx 项目文件夹         把已有报告转成项目文件夹（当样板改编，或核对旧稿）
  check  项目文件夹                     只检查，结果写到 项目文件夹/输出/校验报告.md
  build  项目文件夹 [--不出PDF]         检查 → 生成 Word → 转 PDF（LibreOffice）→ 回填目录页码和“共N页” → 复核
  wps    项目文件夹 [--发布]            build --不出PDF 之后用：WPS 命令行导出两轮（嵌入字体）→ 回填目录页码 → 逐页核对
                                        → 查字体和插图；加 --发布 才把 Word 和 PDF 放进 输出/（流程见 SKILL.md 第3节）
  finalize 项目文件夹 [--核对]          PDF 由 Microsoft Word 导出时用：按 输出/ 里同名 PDF 回填目录页码和“共N页”；
                                        重新导出后加 --核对 逐页核对（流程见 SKILL.md 第3节）
  compare 旧稿.docx 新稿.docx [--条数 N] 两份 Word 正文逐段比对，按章列出不同之处（每轮开工前核对电脑上的稿子是不是上轮交付的版本）
  coverage 原稿项目文件夹 新稿项目文件夹 [--下限 0.8]
                                        改写用户原稿时逐章比对字数、表格数、表行数，低于下限报错（防“改错变删减”），
                                        结果写到 新稿项目文件夹/输出/篇幅核对.md；原稿先用 import 转成项目文件夹
  test   [--快速]                       自测：检查规则、骨架出稿、马沟回归（--快速 不渲染 PDF）
流水线用（结果都很短，给子助手和主对话看）：
  new 项目文件夹 --方法 风险评估法        用风险评估法骨架建预评价项目（默认类比法；设计专篇不分方法）
  check 项目文件夹 --json [--章 02,05]   按文件分组列错误，可只看某几章；不写校验报告.md
  资料 项目文件夹 [文件或文件夹…]        可研等资料转成文字、分块，放 资料/文本/（附 索引.json）
  出稿 项目文件夹 [--只核对]             检查为 0 才出：build → WPS 两轮导出核对 → 发布到 输出/（多项目排队）
  总览                                  项目登记.md 里各项目的错误数、正文修改时间、最新 PDF
  用量 [--天 7]                          最近几天各项目 token 用量
"""
import argparse
import datetime
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent / '_vendor'))   # 自带的 fontTools；本机装了的优先用装好的
from pe import mdparse, docxgen, checks, render, finalize, pipeline, doctype  # noqa: E402
from pe.model import Project  # noqa: E402

SKILL = Path(__file__).resolve().parents[1]
SKELETON = SKILL / 'templates' / '项目骨架'
SKELETONS = {'类比法': SKELETON, '风险评估法': SKILL / 'templates' / '项目骨架_风险评估法'}


def fail(msg):
    print('出错：' + msg)
    sys.exit(2)


def safe(s):
    return re.sub(r'[\\/:*?"<>|\s]+', '', s or '')


def out_stem(info):
    """出稿文件名：项目名称＋类型名称后缀（预评价“职业病危害预评价报告”、专篇“职业病防护设施设计专篇”）＋_编号。"""
    stem = (safe(info.get('项目名称')) or '项目') + doctype.conf(doctype.type_of_info(info))['名称后缀']
    no = info.get('报告编号', '')
    if no and '待' not in no:
        stem += '_' + safe(no)
    return stem


def load(folder):
    try:
        return Project(folder).resolve()
    except (FileNotFoundError, mdparse.ParseError) as e:
        fail(str(e))


def write_report(pj, issues, extra=None, seconds=None):
    out = pj.root / '输出'
    out.mkdir(exist_ok=True)
    c = checks.summary(issues)
    L = ['# 校验报告', '',
         '- 项目：%s' % pj.info.get('项目名称', ''),
         '- 时间：%s%s' % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
                         '（用时 %.0f 秒）' % seconds if seconds is not None else '')]
    for k, v in (extra or {}).items():
        L.append('- %s：%s' % (k, v))
    L += ['- 检查结果：**错误 %d 条、提示 %d 条、待办 %d 条**' % (c['错误'], c['提示'], c['待办']), '']
    names = {'错误': '错误（必须改）', '提示': '提示（请人工看一下）', '待办': '待办（【待填】【待核】等）'}
    for lv in ('错误', '提示', '待办'):
        L += ['## ' + names[lv], '']
        items = [(s, m) for l, s, m in issues if l == lv]
        L += ['%d. 【%s】%s' % (i, s, m) for i, (s, m) in enumerate(items, 1)] or ['（无）']
        L.append('')
    if pj.resolved:
        L += ['## 自动计算的文字（来自占位符，改表后会自动跟着变）', '', '| 位置 | 占位符 | 生成的文字 |', '|---|---|---|']
        L += ['| %s | `%s` | %s |' % (s, p, v) for s, p, v in pj.resolved]
        L.append('')
    path = out / '校验报告.md'
    path.write_text('\n'.join(L), encoding='utf-8')
    return path, c


def cmd_new(a):
    dst = Path(a.folder)
    if dst.exists() and any(dst.iterdir()):
        fail('文件夹已存在且不是空的：%s' % dst)
    kind = getattr(a, '类型', '') or doctype.PRE
    if kind not in (doctype.PRE, doctype.DESIGN):
        fail('--类型 只能是：%s、%s' % (doctype.PRE, doctype.DESIGN))
    if kind == doctype.PRE and a.方法 not in SKELETONS:
        fail('--方法 只能是：%s' % '、'.join(SKELETONS))
    src = SKELETONS[a.方法] if kind == doctype.PRE else doctype.skeleton(kind)
    if not src.is_dir():
        fail('没有%s骨架：%s' % (kind, src))
    shutil.copytree(src, dst, dirs_exist_ok=True)   # 设计专篇的骨架里带 附件册/
    if a.名称:
        p = dst / '项目信息.md'
        s = p.read_text(encoding='utf-8').replace('- 项目名称：【待填】', '- 项目名称：' + a.名称, 1)
        p.write_text(s, encoding='utf-8')
    print('已新建项目：%s' % dst.resolve())
    print('下一步：①资料放进 资料/；②填 项目信息.md；③按章写 正文/；④运行 build 出稿。')


def cmd_import(a):
    from pe.importer import Importer
    imp = Importer(a.docx)
    info, files = imp.run(a.folder)
    print('已导入：%s（%d 章）' % (Path(a.folder).resolve(), len(files)))
    print('项目名称：%s；评价人员 %d 人；附件 %d 个' % (info.get('项目名称', ''), len(info.get('评价人员', [])),
                                           len(info.get('附件', []))))
    for n in imp.notes[:20]:
        print('注意：' + n)


def cmd_check(a):
    t = time.time()
    pj = load(a.folder)
    issues = checks.run(pj)
    if a.json or a.章:
        return pipeline.check_view(pj, issues, a.章, a.json)
    path, c = write_report(pj, issues, seconds=time.time() - t)
    print('错误 %d、提示 %d、待办 %d → %s' % (c['错误'], c['提示'], c['待办'], path))
    for lv, s, m in issues:
        if lv == '错误':
            print('  错误【%s】%s' % (s, m))
    return 1 if c['错误'] else 0


def cmd_build(a):
    """设计专篇主报告：主报告出完接着出附件册（两卷各自检查、生成、核对目录）。退出码取两卷里大的。"""
    rcs = [build_one(v, a) for v in doctype.volumes(a.folder)]
    return max(rcs)


def build_one(folder, a):
    t = time.time()
    pj = load(folder)
    issues = checks.run(pj)
    out = pj.root / '输出'
    docx = out / (out_stem(pj.info) + '.docx')
    try:
        r = docxgen.build(pj, docx)
    except docxgen.BuildError as e:
        fail(str(e))
    for k in r['missing']:
        issues.append(('错误', '模板', '模板里的 {{%s}} 在项目信息.md 里没有' % k))
    extra = {'Word': str(docx.relative_to(pj.root))}
    if a.不出PDF:
        issues.append(('提示', '目录', '没有转 PDF，目录页码和页脚“共N页”还没回填：用 Word 导出同名 PDF 放进 输出/ 后跑 finalize；'
                       '能用 LibreOffice 时去掉 --不出PDF 再生成一次即可'))
    else:
        try:
            tmp = out / '_渲染'
            pdf1, note = render.to_pdf(docx, tmp)
            finalize.apply(docx, pdf1, r['headings'])
            pdf2, _ = render.to_pdf(docx, out)
            res = finalize.apply(docx, pdf2, r['headings'], check=True)
            shutil.rmtree(tmp, ignore_errors=True)
            extra['PDF'] = str(pdf2.relative_to(pj.root))
            extra['页数'] = '共 %d 页（封面至目录 %d 页，正文 %d 页）；目录 %d 条页码已回填并按 PDF 复核' % (
                res['总页数'], res['前置页数'], res['正文页数'], res['目录条数'])
            extra['字体'] = note
            for e in res['错误']:
                issues.append(('错误', 'PDF', e))
        except (render.RenderError, ValueError) as e:
            issues.append(('提示', 'PDF', '没有转成 PDF：%s；目录页码未回填' % e))
    path, c = write_report(pj, issues, extra, seconds=time.time() - t)
    print('已生成：%s' % docx)
    if 'PDF' in extra:
        print('PDF：%s；%s' % (pj.root / extra['PDF'], extra['页数']))
    print('检查：错误 %d、提示 %d、待办 %d → %s' % (c['错误'], c['提示'], c['待办'], path))
    return 1 if c['错误'] else 0


def cmd_compare(a):
    from pe import compare
    for f in (a.old, a.new):
        if not Path(f).is_file():
            fail('找不到文件：' + f)
    sa, sb = compare.sequence(a.old), compare.sequence(a.new)
    chs = compare.by_chapter(sa, sb)
    if not chs:
        print('正文一致（%d 项：段落、标题和表格单元格）。' % len(sa))
        return 0
    print('正文有不同（旧稿 %d 项，新稿 %d 项）。按章：' % (len(sa), len(sb)))
    for c, n in chs:
        print('  %s：%d 项' % (c or '（第1章之前）', n))
    print('前 %d 处：' % a.条数)
    names = {'replace': '改', 'delete': '删', 'insert': '增'}

    def cut(t, p):  # 从第一个不同的字前 20 字起，取 80 字
        t = t.split('|', 1)[-1]
        p = max(0, p - 2 - 20)
        return ('…' if p else '') + t[p:p + 80] + ('…' if len(t) > p + 80 else '')

    for op, x, y in compare.diff(sa, sb, a.条数):
        if op == 'replace' and len(x) == len(y) == 1:
            p = next((i for i, (m, n) in enumerate(zip(x[0], y[0])) if m != n), min(len(x[0]), len(y[0])))
            xs, ys = cut(x[0], p), cut(y[0], p)
        else:
            xs, ys = ' ／ '.join(cut(t, 0) for t in x), ' ／ '.join(cut(t, 0) for t in y)
        print('  [%s] 旧：%s' % (names.get(op, op), xs or '—'))
        print('       新：%s' % (ys or '—'))
    return 1


def cmd_finalize(a):
    """PDF 由 Microsoft Word 导出时用：按 输出/ 里同名 PDF 回填 docx 目录页码和“共N页”，或（--核对）逐页核对。"""
    t = time.time()
    pj = load(a.folder)
    out = pj.root / '输出'
    docx = Path(a.docx) if a.docx else out / (out_stem(pj.info) + '.docx')
    pdf = Path(a.pdf) if a.pdf else docx.with_suffix('.pdf')
    for f, what in ((docx, 'Word'), (pdf, 'PDF')):
        if not f.is_file():
            fail('找不到%s：%s（先 build --不出PDF，再用 Word 导出同名 PDF 放进 输出/，见 SKILL.md 第3节）' % (what, f))
    issues = checks.run(pj)
    if a.核对 and pdf.stat().st_mtime < docx.stat().st_mtime:
        issues.append(('错误', 'PDF', 'PDF 比 Word 旧：回填目录后还没重新导出 PDF。关掉 Word 里的旧窗口，重新打开 docx 导出后再核对'))
    headings = [(b['level'], b['text'].replace('**', '')) for b in pj.blocks if b['t'] == 'h']
    try:
        res = finalize.apply(docx, pdf, headings, check=a.核对)
    except ValueError as e:
        fail(str(e))
    for e in res['错误']:
        issues.append(('错误', 'PDF', e))
    for h in res.get('找不到的标题', []):
        issues.append(('错误', 'PDF', 'PDF 里找不到标题“%s”（PDF 可能不是这一版 Word 导出的）' % h))
    extra = {'Word': str(docx), 'PDF': str(pdf),
             '页数': '共 %d 页（封面至目录 %d 页，正文 %d 页）；目录 %d 条页码%s' % (
                 res['总页数'], res['前置页数'], res['正文页数'], res['目录条数'],
                 '已按 PDF 逐页核对' if a.核对 else '已回填，请重新导出 PDF 后加 --核对 再跑一次')}
    path, c = write_report(pj, issues, extra, seconds=time.time() - t)
    print(extra['页数'])
    print('各章起始页：' + '，'.join('%s %d' % (k.split()[0], v) for k, v in res['标题页码'].items() if v))
    print('检查：错误 %d、提示 %d、待办 %d → %s' % (c['错误'], c['提示'], c['待办'], path))
    for lv, s, m in issues:
        if lv == '错误':
            print('  错误【%s】%s' % (s, m))
    return 1 if c['错误'] else 0


def cmd_wps(a):
    """WPS 命令行出 PDF：两轮导出、回填、核对，加 --发布 才放进 输出/。设计专篇主报告接着导出附件册
    （附件册的核对文件夹放在 --核对目录 下的 附件册/）；--docx 只用于主报告。主报告没通过就不导附件册。"""
    vols = doctype.volumes(a.folder)
    rc = 0
    for i, v in enumerate(vols):
        if i and rc == 2:
            break
        if i:
            print('—— 附件册 ——')
        out = (str(Path(a.核对目录) / doctype.ANNEX_DIR) if a.核对目录 else '') if i else a.核对目录
        rc = max(rc, wps_one(v, a, a.docx if not i else '', out))
    return rc


def wps_one(folder, a, docx_arg, check_dir):
    from pe import wps
    t = time.time()
    pj = load(folder)
    docx = Path(docx_arg) if docx_arg else pj.root / '输出' / (out_stem(pj.info) + '.docx')
    headings = [(b['level'], b['text'].replace('**', '')) for b in pj.blocks if b['t'] == 'h']
    try:
        res = wps.run(pj.root, docx, headings, out=check_dir or None, publish=a.发布)
    except (wps.WpsError, ValueError, OSError) as e:
        print('未通过：%s' % e)
        return 2
    pages = '共 %d 页（封面至目录 %d 页，正文 %d 页）；目录 %d 条' % (
        res['总页数'], res['前置页数'], res['正文页数'], res['目录条数'])
    print('WPS 两轮导出核对通过：%s；目录页码、页脚逐页核对错误 0；插图 %d 张；字体：%s' % (
        pages, res['插图'], res['字体']))
    print('各章起始页：' + '，'.join('%s %d' % (k.split()[0], v) for k, v in res['标题页码'].items() if v))
    print('核对文件夹：%s' % res['核对文件夹'])
    if not a.发布:
        print('这次只做了核对，没有改 输出/；要交付的，加 --发布 再跑一次')
        return 0
    issues = checks.run(pj)
    extra = {'Word': str(docx.relative_to(pj.root)), 'PDF': str(docx.with_suffix('.pdf').relative_to(pj.root)),
             '页数': pages + '页码已回填并按 PDF 逐页核对（WPS 命令行导出）',
             '字体': res['字体'], '核对文件夹': str(res['核对文件夹'])}
    path, c = write_report(pj, issues, extra, seconds=time.time() - t)
    print('已放进 输出/：Word（目录页码已回填）和 PDF；原来的文件存在 %s' % res['发布'])
    print('检查：错误 %d、提示 %d、待办 %d → %s' % (c['错误'], c['提示'], c['待办'], path))
    return 1 if c['错误'] else 0


def cmd_coverage(a):
    from pe import coverage
    for f in (a.old, a.new):
        if not (Path(f) / '正文').is_dir():
            fail('不是项目文件夹（缺 正文/）：%s；原稿 DOCX 先用 import 转成项目文件夹' % f)
    try:
        text, issues = coverage.report(a.old, a.new, a.下限)
    except mdparse.ParseError as e:
        fail(str(e))
    out = Path(a.new) / '输出'
    out.mkdir(exist_ok=True)
    path = out / '篇幅核对.md'
    path.write_text(text, encoding='utf-8')
    print(text)
    print('→ %s' % path)
    return 1 if any(lv == '错误' for lv, _, _ in issues) else 0


def cmd_test(a):
    sys.path.insert(0, str(SKILL / 'tests'))
    import run_tests
    return run_tests.main(quick=a.快速)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('new', help='新建项目')
    p.add_argument('folder')
    p.add_argument('--名称', default='')
    p.add_argument('--方法', default='类比法')
    p.add_argument('--类型', default=doctype.PRE, help='预评价（默认）或 设计专篇')
    p = sp.add_parser('import', help='导入已有报告 DOCX')
    p.add_argument('docx')
    p.add_argument('folder')
    p = sp.add_parser('check', help='只检查')
    p.add_argument('folder')
    p.add_argument('--json', action='store_true')
    p.add_argument('--章', default='')
    p = sp.add_parser('资料', help='资料转文字并分块')
    p.add_argument('folder')
    p.add_argument('files', nargs='*')
    p.add_argument('--块长', type=int, default=12000)
    p = sp.add_parser('出稿', help='检查为 0 → Word → WPS 两轮导出 → 发布')
    p.add_argument('folder')
    p.add_argument('--只核对', action='store_true')
    p.add_argument('--等待', type=int, default=900)
    sp.add_parser('总览', help='各项目检查结果和出稿情况')
    p = sp.add_parser('用量', help='最近几天 token 用量')
    p.add_argument('--天', type=int, default=7)
    p = sp.add_parser('build', help='检查并生成 Word/PDF')
    p.add_argument('folder')
    p.add_argument('--不出PDF', action='store_true')
    p = sp.add_parser('compare', help='两份 Word 正文逐段比对')
    p.add_argument('old')
    p.add_argument('new')
    p.add_argument('--条数', type=int, default=20)
    p = sp.add_parser('finalize', help='按 Word 导出的 PDF 回填或核对目录页码')
    p.add_argument('folder')
    p.add_argument('--核对', action='store_true')
    p.add_argument('--docx', default='')
    p.add_argument('--pdf', default='')
    p = sp.add_parser('wps', help='WPS 命令行导出 PDF（两轮，回填并核对目录页码）')
    p.add_argument('folder')
    p.add_argument('--发布', action='store_true')
    p.add_argument('--核对目录', default='')
    p.add_argument('--docx', default='')
    p = sp.add_parser('coverage', help='与原稿逐章比对篇幅和表格覆盖')
    p.add_argument('old')
    p.add_argument('new')
    p.add_argument('--下限', type=float, default=0.8)
    p = sp.add_parser('test', help='自测')
    p.add_argument('--快速', action='store_true')
    a = ap.parse_args()
    rc = {'new': cmd_new, 'import': cmd_import, 'check': cmd_check, 'build': cmd_build, 'compare': cmd_compare,
          'finalize': cmd_finalize, 'wps': cmd_wps, 'coverage': cmd_coverage, 'test': cmd_test,
          '资料': pipeline.cmd_materials, '出稿': pipeline.cmd_publish, '总览': pipeline.cmd_overview,
          '用量': pipeline.cmd_usage}[a.cmd](a)
    sys.exit(rc or 0)


if __name__ == '__main__':
    main()
