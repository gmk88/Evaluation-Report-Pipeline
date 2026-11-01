#!/usr/bin/env python3
"""自测。用法：python3 scripts/report.py test [--快速]（或直接运行本文件，加 --快速 不渲染 PDF）
1 检查规则：故意写错的小项目，每类错误都要查出来；
2 解析与计数：表格合并格、指令、图题、计数句、项目信息读写；
3 骨架出稿：new → build，能出 Word（和 PDF），错误 0 条，列出待办；
4 马沟回归：导入马沟最终版 → 重新生成，正文逐段与原稿一致；compare 按章比对能定位改动；示例项目与导入结果一致；
  渲染后总页数、正文页数与原稿一致，各章起始页与原稿相差不超过 1 页，目录页码和页脚逐页核对通过。"""
import os
import re
import zipfile
import shutil
import sys
import tempfile
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
from pe import mdparse, docxgen, checks, render, finalize, compare  # noqa: E402
from pe.common import W, RE_SCRIPT, media_problems, wps_pack_problems  # noqa: E402
from lxml import etree as E  # noqa: E402
from pe.model import Project, count_clause  # noqa: E402
from pe.importer import Importer  # noqa: E402
from pe.info import read_info, write_info  # noqa: E402

EXAMPLE = SKILL / 'examples' / '马沟'
ORIG = EXAMPLE / '原稿' / '马沟加气站职业病危害预评价报告_AT-2026-Y013.docx'
ORIG_PDF = ORIG.with_suffix('.pdf')
RESULTS = []


def ok(name, cond, detail=''):
    RESULTS.append((name, bool(cond)))
    print(('  通过  ' if cond else '  失败  ') + name + ('：' + detail if detail else ''))
    return cond


BAD_INFO = '- 项目名称：测试项目\n- 报告编号：AT-2026-T001\n- 报告日期：2026年1月1日\n- 评价单位：某某公司\n'
BAD_RULES = '## 禁用\n\n- `旧数字`\n\n## 必备\n\n- `关键表述`\n'
BAD_BODY = '''# 1 总论

## 1.1 项目背景

本公司按可研方案建成，需补充完善，旧数字。

站内设X射线安检机，存在电离辐射。

依据GBZ 2.1-2019（括号不配对。重复标点，，结束。

表1.1-1  评价表
| 序号 | 检查项目 | 符合性 |
|---|---|---|
| 1 | 甲 | 符合 |
| 3 | 乙 | 不符合 |
| 4 | 丙 | 良好 |

表1.1-1共评价3项，其中符合3项。

表1.1-1  重复的表题
| 甲 | 乙 |
|---|---|
| 1 | 2 |

表1.1-3  职业病危害关键控制点
| 序号 | 控制点 |
|---|---|
| 1 | 卸液 |
| 2 | 加气 |

本项目确定3个职业病危害关键控制点。见表1.1-9，详见附件3。{{计数:表9.9-9}}

## 1.3 评价目的

目的。
'''
EXPECT = ['“本公司”', '“按可研方案建成”', '“需补充完善”', '连接号', '（） 不配对', '重复标点', '判了“不符合”',
          '“良好”不是规定档位', '序号不连续', '计数句与表1.1-1不一致', '表1.1-9不存在', '附件3', '生成不了',
          '标题编号不连续', '项目规则禁用“旧数字”', '要求出现“关键表述”', '表题重复', '关键控制点', '编号不连续',
          '不评价放射性']

GOOD_RAD = {
    '01_总论.md': '# 1 总论\n\n## 1.4 评价范围\n\n评价范围为本项目运行期。站内X射线安检机的放射性职业病危害因素'
                 '由建设单位另行委托开展放射性职业病危害评价，不在本报告评价范围内。\n',
    '02_建设项目工程分析.md': '# 2 建设项目工程分析\n\n## 2.1 工程概况\n\n站房设X射线安检机1台。\n',
    '05_职业病防护设施分析与评价.md': '# 5 职业病防护设施分析与评价\n\n## 5.1 职业病防护设施类别适用性分析\n\n'
                               '本项目需设置防毒、防噪声设施；低压配电，无射频、微波等非电离辐射发射源，'
                               '防非电离辐射设施无设置必要。\n',
    '08_职业卫生管理的分析与评价.md': '# 8 职业卫生管理的分析与评价\n\n## 8.1 职业健康监护\n\n'
                               '接尘作业上岗前必检后前位X射线高千伏胸片或DR胸片，刺激性气体作业做胸部X射线检查。\n',
}


def test_checks(tmp):
    print('【1】检查规则')
    root = tmp / 'bad'
    (root / '正文').mkdir(parents=True)
    (root / '项目信息.md').write_text(BAD_INFO, encoding='utf-8')
    (root / '检查规则.md').write_text(BAD_RULES, encoding='utf-8')
    (root / '正文' / '01_总论.md').write_text(BAD_BODY, encoding='utf-8')
    pj = Project(root).resolve()
    errs = [m for lv, s, m in checks.run(pj) if lv == '错误']
    for e in EXPECT:
        ok('查出' + e, any(e in m for m in errs))
    # 放射性：第1章写“另行评价”、第5章“防非电离辐射”不报；第2章设备清单里出现只提示
    root = tmp / 'good'
    (root / '正文').mkdir(parents=True)
    (root / '项目信息.md').write_text(BAD_INFO, encoding='utf-8')
    for name, text in GOOD_RAD.items():
        (root / '正文' / name).write_text(text, encoding='utf-8')
    rad = [(lv, s) for lv, s, m in checks.run(Project(root).resolve()) if 'PL-PRE-0044' in m]
    ok('放射性：第1章“另行评价”、“防非电离辐射”和体检胸片不误报，第2章只提示',
       [lv for lv, _ in rad] == ['提示'] and '02_' in str(rad[0][1]), str(rad))
    # 章名：技改项目第2章、风险评估法第3章用本 skill 的写法不报，缺的章照报
    root = tmp / 'titles'
    (root / '正文').mkdir(parents=True)
    (root / '项目信息.md').write_text(BAD_INFO, encoding='utf-8')
    for i, t in enumerate(['总论', '现有企业概况及建设项目工程分析', '职业健康风险评估分析与评价'], 1):
        (root / '正文' / ('%02d_%s.md' % (i, t))).write_text('# %d %s\n\n正文。\n' % (i, t), encoding='utf-8')
    hits = [m for lv, s, m in checks.run(Project(root).resolve()) if '章标题' in m]
    ok('章名：技改第2章、风险评估法第3章不报，缺的第4章照报',
       not any(('第2章' in m or '第3章' in m) for m in hits) and any('第4章' in m for m in hits), str(hits[:3]))



def test_parse(tmp):
    print('【2】解析与计数')
    text = ('表2.1-1  示例\n<!-- 表格 列宽=1,2,1 字号=五号 -->\n| 序号 | 项目 | 符合性 |\n|---|---|---|\n'
            '| 一、分组 | < | < |\n| 1 | 甲<br>乙 | 符合 |\n| ^ | 丙 | 基本符合 |\n| 2 | 丁\\|戊 | 符合 |\n\n'
            '![示意](图/a.png){宽=10cm}\n图2.1-1  示意图\n<!-- 段落 居中 -->\n居中段\n1. 第一条\n2. 第二条\n<!-- 横向 -->\n')
    bl = mdparse.parse_text(text)
    kinds = [b['t'] for b in bl]
    ok('块类型', kinds == ['caption', 'table', 'img', 'caption', 'p', 'li', 'li', 'sect'], str(kinds))
    tb = bl[1]
    ok('表格合并格与竖线', tb['rows'][1][1:] == ['<', '<'] and tb['rows'][3][0] == '^' and tb['rows'][4][1] == '丁|戊')
    ok('计数句（跳过分组行和合并续格）', count_clause(tb, '表2.1-1') == '表2.1-1共评价3项，其中符合2项、基本符合1项',
       count_clause(tb, '表2.1-1'))
    ok('段落指令', bl[4].get('attrs', {}).get('居中') is True)
    front = [['封面'], ['目录', '1总论........1', '1.1项目背景.....12', '1.2很长的标题折成', '两行..3'],
             ['1.3评价目的......0', '附件1委托书']]
    got = finalize.pdf_toc_numbers(front, ['1 总论', '1.1 项目背景', '1.2 很长的标题折成两行', '1.3 评价目的', '1.4 不在目录'])
    ok('读 PDF 目录上印的页码（按标题找；标题折行、点线只剩两个点也能读，找不到的为空）', got == [1, 12, 3, 0, None], str(got))
    p = tmp / 'info.md'
    info = {'项目名称': '甲项目', '封面名称': ['第一行', '第二行'], '报告编号': 'AT-1', '评价人员':
            [{'类别': '项目负责人', '姓名': '张三', '职称': '工程师', '专业': '安全', '证书号': 'X1'}],
            '附件': [{'名称': '委托书', '文件': '附件/1.pdf'}]}
    write_info(p, info)
    back = read_info(p)
    ok('项目信息读写', back['封面名称'] == info['封面名称'] and back['评价人员'] == info['评价人员']
       and back['附件'] == info['附件'] and back['项目名称'] == '甲项目')


def _mini(root, files, rules=''):
    (root / '正文').mkdir(parents=True)
    (root / '项目信息.md').write_text('- 项目名称：测试\n', encoding='utf-8')
    (root / '检查规则.md').write_text(rules, encoding='utf-8')
    for name, text in files.items():
        (root / '正文' / name).write_text(text, encoding='utf-8')


def test_coverage(tmp):
    print('【3】篇幅与覆盖核对（coverage）')
    from pe import coverage
    import subprocess
    dup = '该岗位接触噪声，长期接触可致听力损伤和噪声聋。\n'
    old, new = tmp / 'cov_old', tmp / 'cov_new'
    _mini(old, {
        '01.md': '# 1 总论\n\n表1.1-1  甲\n| 序号 | 场所 |\n|---|---|\n| 1 | 锅炉房零米层及煤气阀组区域 |\n'
                 '| 2 | 汽机房运转层及给水泵组区域 |\n\n'
                 '表1.1-2  乙\n| 序号 | 场所 |\n|---|---|\n| 1 | 化水车间加药间及酸碱计量间 |\n| 2 | 氨水站卸车区及氨水储罐区 |\n',
        '07.md': '# 7 个人使用的职业病防护用品的分析与评价\n\n' + dup * 8 + '另配备防化学飞溅护目镜和耐酸碱手套。\n',
        '09.md': '# 9 控制职业病危害的补充措施与建议\n\n' + ''.join(
            '补充措施第%s条：在%s设置固定式检测报警仪并与排风机联锁。\n' % (n, w)
            for n, w in (('一', '锅炉房'), ('二', '汽机房'), ('三', '化水车间'), ('四', '氨水站'), ('五', '酸碱库'))),
        '10.md': '# 10 结论和建议\n\n本项目职业病危害风险类别为严重。\n'})
    _mini(new, {
        '01.md': '# 1 总论\n\n表1.1-1  甲\n| 序号 | 场所 |\n|---|---|\n| 1 | 锅炉房零米层及煤气阀组区域 |\n'
                 '| 2 | 汽机房运转层及给水泵组区域 |\n| 3 | 化水车间加药间及酸碱计量间 |\n| 4 | 氨水站卸车区及氨水储罐区 |\n',
        '07.md': '# 7 个人使用的职业病防护用品的分析与评价\n\n' + dup + '另配备防化学飞溅护目镜和耐酸碱手套。\n',
        '09.md': '# 9 职业病危害关键控制点分析\n\n煤气接入区域为关键控制点。\n',
        '10.md': '# 10 职业病防治措施的补充建议\n\n补充措施第一条：在锅炉房设置固定式检测报警仪并与排风机联锁。\n',
        '11.md': '# 11 结论与建议\n\n本项目职业病危害风险类别为严重。\n'})
    rows, _, issues = coverage.run(old, new)
    by = {r[0]: r for r in rows}
    lv = lambda where: [(l, m) for l, w, m in issues if w == where]
    ok('章按章名对齐（原稿第9章补充措施→新稿第10章、第10章结论→第11章）', by.get(10, [0, 0, 0])[2] == 9
       and by.get(11, [0, 0, 0])[2] == 10, str([(r[0], r[2]) for r in rows]))
    ok('原稿没有的章（关键控制点）只提示', any(l == '提示' and '原稿没有对应的章' in m for l, m in lv('第9章')), str(lv('第9章')))
    ok('重复段落去重后计数，不误报删减', not [1 for l, m in lv('第7章') if l == '错误'], str(lv('第7章')))
    ok('两表合并为一表：不报错，只提示表格减少', not [1 for l, m in lv('第1章') if l == '错误']
       and any(l == '提示' and '表格2→1张' in m for l, m in lv('第1章')), str(lv('第1章')))
    ok('删了大段内容报错', any(l == '错误' and '字数' in m for l, m in lv('第10章')), str(lv('第10章')))
    (new / '检查规则.md').write_text('## 篇幅豁免\n\n- 第10章：原稿补充措施逐条重复第5章内容，已合并。\n', encoding='utf-8')
    _, _, issues2 = coverage.run(old, new)
    ok('写了篇幅豁免的章降为提示', not [1 for l, w, m in issues2 if l == '错误' and w == '第10章']
       and any(w == '第10章' and '已豁免' in m for l, w, m in issues2))
    (new / '检查规则.md').write_text('', encoding='utf-8')
    r = subprocess.run([sys.executable, str(SKILL / 'scripts' / 'report.py'), 'coverage', str(old), str(new)],
                       capture_output=True, text=True)
    ok('coverage 命令：有错误时返回1并写出 输出/篇幅核对.md', r.returncode == 1 and (new / '输出' / '篇幅核对.md').is_file(),
       'rc=%s' % r.returncode)


def test_skeleton(tmp, quick):
    print('【4】骨架出稿')
    import subprocess
    root = tmp / '新项目'
    py = [sys.executable, str(SKILL / 'scripts' / 'report.py')]
    subprocess.run(py + ['new', str(root), '--名称', '测试加气站'], check=True, capture_output=True)
    cmd = py + ['build', str(root)] + (['--不出PDF'] if quick else [])
    subprocess.run(cmd, capture_output=True, text=True)
    docs = list((root / '输出').glob('*.docx'))
    ok('生成 Word', len(docs) == 1, docs[0].name if docs else '')
    if docs:
        import zipfile
        core = zipfile.ZipFile(docs[0]).read('docProps/core.xml').decode('utf-8')
        ok('Word 属性没有模板残留（马沟、LNG、柒年）', not re.search('马沟|LNG|柒年', core),
           ' '.join(x for x in re.findall(r'>([^<>]+)<', core) if x.strip())[:160])
        app = zipfile.ZipFile(docs[0]).read('docProps/app.xml').decode('utf-8')
        words = re.search(r'<Words>(\d+)</Words>', app)
        ok('Word 统计值按本稿重算（不是模板的 57 页、14955 字）', words is not None and words.group(1) != '14955'
           and '<Pages>57</Pages>' not in app, ' '.join(re.sub(r'<[^>]+>', ' ', app).split()[:5]))
    rep = (root / '输出' / '校验报告.md').read_text(encoding='utf-8')
    m = re.search(r'错误 (\d+) 条、提示 (\d+) 条、待办 (\d+) 条', rep)
    ok('骨架无错误、列出待办', m and m.group(1) == '0' and int(m.group(3)) > 0, m.group(0) if m else rep[:200])
    if not quick:
        ok('生成 PDF 并核对目录', bool(list((root / '输出').glob('*.pdf'))) and '页码已回填' in rep)
    else:
        r = subprocess.run(py + ['finalize', str(root), '--核对'], capture_output=True, text=True)
        ok('finalize：还没有 Word 导出的 PDF 时明确报缺 PDF', r.returncode == 2 and '找不到PDF' in r.stdout,
           r.stdout.strip()[:120])


def orig_pages():
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(ORIG_PDF)
    m = re.search(r'共\s*(\d+)\s*页', doc[-1].get_text())
    toc = {}
    for kind, t in compare.sequence(ORIG, 'front'):
        mm = re.match(r'^(\d+)\s+(.+?)(\d+)$', t)
        if mm and '.' not in mm.group(1):
            toc[mm.group(1) + ' ' + mm.group(2).strip()] = int(mm.group(3))
    return len(doc), int(m.group(1)) if m else None, toc


def example_diffs():
    """examples/马沟/与原稿的差异.md 的登记行：[(文件, 原稿句, 范例句)]"""
    p = EXAMPLE / '与原稿的差异.md'
    rows = []
    for line in p.read_text(encoding='utf-8').splitlines() if p.exists() else []:
        m = re.match(r'^\|\s*([^|`]+?\.md)\s*\|\s*`([^`]+)`\s*\|\s*`([^`]*)`\s*\|', line)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3)))
    return rows


def test_magou(tmp, quick):
    print('【5】马沟回归')
    t = time.time()
    imp_root = tmp / '马沟导入'
    Importer(ORIG).run(imp_root)
    pj = Project(imp_root).resolve()
    ok('导入后占位符全部可解析', not pj.unresolved, str(pj.unresolved[:3]))
    out = imp_root / '输出' / 'test.docx'
    r = docxgen.build(pj, out)
    d = compare.diff(compare.sequence(ORIG), compare.sequence(out))
    ok('正文与原稿逐段一致', not d, '%d 项' % len(compare.sequence(ORIG)) if not d else str(d[:2]))
    with zipfile.ZipFile(out) as z:
        dxs = z.read('word/document.xml').decode('utf-8')
    left = [t for t in re.findall(r'<w:t(?:\s[^>]*)?>([^<]*)</w:t>', dxs) if RE_SCRIPT.search(t)]
    nva = len(re.findall(r'<w:vertAlign w:val="(?:superscript|subscript)"/>', dxs))
    ok('m³、SO₂ 等上下标字符都转成了 Word 上下标格式（仿宋没有这些字形）', not left and nva > 0,
       '剩 %d 段上下标字符，上下标格式 %d 处' % (len(left), nva))
    s0 = compare.sequence(ORIG)
    s1 = list(s0)
    i = next(k for k, c in enumerate(compare.chapter_of(s0)) if c.startswith('3 ') and s0[k][0] == 'p')
    s1[i] = ('p', s1[i][1] + '（改）')
    ch = compare.by_chapter(s0, s1)
    ok('compare 按章比对：自比对无差异，改一处能定位到所在章', not compare.by_chapter(s0, s0) and len(ch) == 1
       and ch[0][0].startswith('3 ') and ch[0][1] == 1, str(ch))
    with zipfile.ZipFile(out) as z:
        xs = {n: z.read(n) for n in z.namelist() if n.endswith(('.xml', '.rels'))}
    bad = [n for n, b in xs.items() if re.search(rb'xmlns:ns\d+=', b)]
    dx = xs['word/document.xml']
    nimg = dx.count(b'<w:drawing>')
    rids = re.findall(rb'Id="([^"]+)"', xs['word/_rels/document.xml.rels'])
    with zipfile.ZipFile(out) as z:
        media = [n.rsplit('.', 1)[0] for n in z.namelist() if n.startswith('word/media/')]
    wps_form = (dx.count(b'<a:graphic xmlns:a=') == nimg and dx.count(b'<pic:pic xmlns:pic=') == nimg
                and dx.count(b'<a:graphicFrameLocks xmlns:a=') == nimg and dx.count(b'<a:picLocks ') == nimg
                and not re.search(rb'<wp:inline[^>]*xmlns:', dx))
    ok('插图写法与 WPS 另存的一致（就地声明 a:/pic:、数字 rId、图片文件不重名）',
       nimg > 0 and not bad and wps_form and all(re.match(rb'rId\d+$', r) for r in rids)
       and len(media) == len(set(media)),
       '%d 张图；自动前缀部件：%s；非数字 rId：%s' % (nimg, bad, [r for r in rids if not re.match(rb'rId\d+$', r)][:3]))
    pk = wps_pack_problems(out)
    ok('打包方式与 WPS 另存的一致（目录项、DOS 属性、XML 声明双引号＋CRLF）', not pk, '；'.join(pk))
    mp = media_problems(out)
    ok('插图都转成普通 JPEG（基线 RGB、不写 dpi）且不超过 220 ppi（Mac 版 WPS 大 PNG 时有时无）', not mp, '；'.join(mp))
    root = E.fromstring(dx)
    sty = E.fromstring(xs['word/styles.xml'])
    toc_sid = {st.get(W + 'styleId'): int(st.find(W + 'name').get(W + 'val')[-1]) for st in sty.findall(W + 'style')
               if st.find(W + 'name') is not None and re.match(r'toc [123]$', st.find(W + 'name').get(W + 'val', ''))}
    lefts = {}
    for q in root.iter(W + 'p'):
        ps_ = q.find(W + 'pPr/' + W + 'pStyle')
        if ps_ is not None and ps_.get(W + 'val') in toc_sid:
            ind = q.find(W + 'pPr/' + W + 'ind')
            lefts.setdefault(toc_sid[ps_.get(W + 'val')], set()).add(
                (ind.get(W + 'left') if ind is not None else None, ind.get(W + 'leftChars') if ind is not None else None))
    sty_chars = [st.get(W + 'styleId') for st in sty.findall(W + 'style') if st.get(W + 'styleId') in toc_sid
                 and st.find(W + 'pPr/' + W + 'ind') is not None and st.find(W + 'pPr/' + W + 'ind').get(W + 'leftChars')]
    ok('目录缩进：一级 0、二级 212、三级 480 twip（2 字），样式里不带 leftChars',
       lefts.get(1) == {('0', None)} and lefts.get(2) == {('212', None)} and lefts.get(3) == {('480', None)} and not sty_chars,
       str(lefts) + ('；样式带 leftChars：%s' % sty_chars if sty_chars else ''))
    tbl = next(t for t in root.iter(W + 'tbl') if '人员类别' in ''.join(t.itertext()))
    hdr = tbl.find(W + 'tr')
    jcs = [j.get(W + 'val') for j in hdr.iter(W + 'jc')]
    ok('人员签字表表头居中', jcs and all(j == 'center' for j in jcs), str(jcs))
    tp_ = tbl.find(W + 'tblPr')
    gw = [int(g.get(W + 'w')) for g in tbl.iter(W + 'gridCol')]
    ok('人员签字表排满版心宽、表级居中、不浮动（模板原为浮动表，比版心宽约 11 磅）',
       tp_.find(W + 'tblpPr') is None and tp_.find(W + 'jc') is not None and tp_.find(W + 'jc').get(W + 'val') == 'center'
       and tp_.find(W + 'tblW').get(W + 'w') == str(docxgen.PORTRAIT_TEXT_WIDTH) and sum(gw) == docxgen.PORTRAIT_TEXT_WIDTH,
       '列宽 %s，合计 %d' % (gw, sum(gw)))
    def borders(t):
        bd = t.find(W + 'tblPr/' + W + 'tblBorders')
        return {x.tag[len(W):]: x.get(W + 'sz') for x in bd} if bd is not None else {}
    want_bd = {'top': '12', 'left': '12', 'bottom': '12', 'right': '12', 'insideH': '4', 'insideV': '4'}
    body_bd = [borders(t) for t in root.iter(W + 'tbl') if t is not tbl]
    ok('正文表格外框 1.5 磅、内线 0.5 磅', body_bd and all(b == want_bd for b in body_bd),
       '%d 张中 %d 张不符' % (len(body_bd), sum(b != want_bd for b in body_bd)))
    with zipfile.ZipFile(docxgen.TEMPLATE) as zt:
        tpl_tbl = next(t for t in E.fromstring(zt.read('word/document.xml')).iter(W + 'tbl')
                       if '人员类别' in ''.join(t.itertext()))
    ok('声明页人员签字表边线照模板不改（用户 2026-10-30：签字表不用改）', borders(tbl) == borders(tpl_tbl),
       '%s / 模板 %s' % (borders(tbl), borders(tpl_tbl)))
    instr = [x.text or '' for x in root.iter(W + 'instrText')]
    toc_ps = [q for q in root.iter(W + 'p') if q.find(W + 'pPr/' + W + 'pStyle') is not None
              and q.find(W + 'pPr/' + W + 'pStyle').get(W + 'val') in toc_sid]
    marks = {b.get(W + 'name') for b in root.iter(W + 'bookmarkStart')}
    refs = [re.search(r'PAGEREF\s+(\S+)', x).group(1) for x in instr if 'PAGEREF' in x]
    depth, neg = 0, False
    for fc in root.iter(W + 'fldChar'):
        depth += {'begin': 1, 'end': -1}.get(fc.get(W + 'fldCharType'), 0)
        neg = neg or depth < 0
    links = [q.find(W + 'hyperlink') for q in toc_ps]
    ok('目录是 Word 目录域：TOC 域 1 个，每条有指向标题书签的超链接和 PAGEREF 页码域，域首尾配对（Word、WPS 能更新页码）',
       sum(x.strip().startswith('TOC ') for x in instr) == 1 and len(refs) == len(toc_ps) > 0
       and all(r in marks for r in refs) and all(h is not None and h.get(W + 'anchor') in marks for h in links)
       and depth == 0 and not neg,
       '目录 %d 条，PAGEREF %d 个，指不到书签 %d 个，域深度 %d'
       % (len(toc_ps), len(refs), sum(r not in marks for r in refs), depth))
    def shape(e):  # 元素的标签和属性（不比命名空间声明：styles.xml 与 document.xml 的声明不同）
        return None if e is None else [(x.tag, sorted(x.attrib.items())) for x in e.iter()]
    sty_ok = {}
    for st in sty.findall(W + 'style'):
        if st.get(W + 'styleId') in toc_sid:
            lvl = toc_sid[st.get(W + 'styleId')]
            proto = next(q for q in toc_ps if toc_sid[q.find(W + 'pPr/' + W + 'pStyle').get(W + 'val')] == lvl)
            same = all(shape(st.find(W + 'pPr/' + W + t)) == shape(proto.find(W + 'pPr/' + W + t)) is not None
                       for t in ('tabs', 'spacing', 'ind'))
            sty_ok[lvl] = same and st.get(W + 'styleId') == 'TOC%d' % lvl
    ok('样式 toc 1～3 的行距、缩进、制表位与目录条一致，样式 ID 为 TOC1～TOC3（Word 里“更新整个目录”版式不变）',
       sty_ok == {1: True, 2: True, 3: True}, str(sty_ok))
    body_tbls = [t for t in root.iter(W + 'tbl') if t is not tbl]
    off = [i for i, t in enumerate(body_tbls) if t.find(W + 'tblPr/' + W + 'jc') is None
           or t.find(W + 'tblPr/' + W + 'jc').get(W + 'val') != 'center']
    ok('正文表格一律表级居中（左右边线对齐版心，不左移约 5 磅）', body_tbls and not off,
       '%d 张中 %d 张没居中' % (len(body_tbls), len(off)))
    fa = [re.sub(r'\d+$', '', x[1]) for x in compare.sequence(ORIG, 'front')]
    fb = [re.sub(r'\d+$', '', x[1]) for x in compare.sequence(out, 'front')]
    ok('封面、声明、人员表、目录、附件清单一致（不计页码）', fa == fb)
    rows = example_diffs()
    bad = []
    for f in sorted((EXAMPLE / '正文').glob('*.md')):
        got = (imp_root / '正文' / f.name).read_text(encoding='utf-8')
        for fn, a, b in rows:
            if fn == f.name:
                if got.count(a) != 1:
                    bad.append('%s：原稿句出现 %d 次' % (fn, got.count(a)))
                got = got.replace(a, b, 1)
        if got != f.read_text(encoding='utf-8'):
            bad.append(f.name)
    ok('示例项目 examples/马沟 与导入结果一致（按“与原稿的差异.md”替换 %d 处后）' % len(rows), rows and not bad,
       '; '.join(bad[:3]))
    ok('马沟示例检查无错误', not [1 for lv, _, _ in checks.run(Project(EXAMPLE).resolve()) if lv == '错误'])
    # 模板改动要做进 make_template.py：直接改 docx 的修复，换机构重建模板时会丢（封面居中、页码域、横向制表位都丢过）
    sys.path.insert(0, str(SKILL / 'scripts' / 'dev'))
    import make_template
    regen = tmp / '模板重建.docx'
    make_template.main(str(ORIG), str(regen))
    with zipfile.ZipFile(docxgen.TEMPLATE) as za, zipfile.ZipFile(regen) as zb:
        drift = [n for n in za.namelist() if n not in zb.namelist() or za.read(n) != zb.read(n)] + \
                [n for n in zb.namelist() if n not in za.namelist()]
    ok('make_template.py 由马沟原稿重建的模板与 templates/ 里的逐字节一致', not drift, '不同的部件：%s' % drift[:5])
    print('    生成用时 %.1f 秒' % (time.time() - t))
    if quick:
        return
    total, body, toc = orig_pages()
    pdf1, note = render.to_pdf(out, imp_root / '输出' / 'r1')
    finalize.apply(out, pdf1, r['headings'])
    pdf2, _ = render.to_pdf(out, imp_root / '输出')
    res = finalize.apply(out, pdf2, r['headings'], check=True)
    ok('目录页码与页脚逐页核对', not res['错误'], '; '.join(res['错误'][:3]))
    pk = wps_pack_problems(out)
    ok('回填页码后打包方式仍与 WPS 另存的一致', not pk, '；'.join(pk))
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    xmin = {}
    for pg in list(fitz.open(pdf2))[:12]:
        for blk in pg.get_text('dict')['blocks']:
            for ln in blk.get('lines', []):
                txt = ''.join(sp['text'] for sp in ln['spans']).strip()
                m = re.match(r'^(\d+(?:\.\d+){0,2})\s*[\u4e00-\u9fff（(]', txt)
                if m and re.search(r'\.{4,}|…{2,}', txt):
                    lv = m.group(1).count('.') + 1
                    xmin[lv] = min(xmin.get(lv, 1e9), ln['bbox'][0])
    ind = {k: round(v - xmin.get(1, 0), 1) for k, v in xmin.items()}
    ok('渲染后目录三级比一级缩进约 2 字（24 磅）', len(xmin) == 3 and 20 <= ind[3] <= 28 and 0 < ind[2] < ind[3],
       '二级 %s 磅、三级 %s 磅' % (ind.get(2), ind.get(3)))
    ok('总页数与原稿一致', res['总页数'] == total, '%d / 原稿 %d' % (res['总页数'], total))
    ok('正文页数与原稿一致', res['正文页数'] == body, '%d / 原稿 %d' % (res['正文页数'], body))
    diffs = {k: (v, toc.get(k)) for k, v in res['标题页码'].items() if toc.get(k) is not None and v != toc[k]}
    ok('各章起始页与原稿相差不超过 1 页', all(abs(a - b) <= 1 for a, b in diffs.values()),
       '完全一致 %d/%d 章%s' % (len(res['标题页码']) - len(diffs), len(res['标题页码']),
                          ('；差 1 页：' + '、'.join(k.split()[0] for k in diffs)) if diffs else ''))
    print('    渲染：%s；用时 %.0f 秒' % (note, time.time() - t))


def test_wps(tmp):
    """WPS 命令行出稿（PL-GEN-0059）：嵌字体、字体和插图检查、过期 Word 拒绝导出；不调用 WPS。"""
    print('【6】WPS 出稿')
    import uuid
    from unittest.mock import patch
    from pe import wps, embed_fonts
    ok('字体检查：缺仿宋、宋体时报错', len(wps.font_problems({'PingFangSC-Regular'}, [])) == 1)
    ok('字体检查：有仿宋、宋体、正文没有苹方时通过',
       wps.font_problems({'FangSongCN-Regular', 'SimSunCN-Regular', 'PingFangSC-Regular'}, []) == [])
    probs = wps.font_problems({'FangSongCN-Regular', 'SimSunCN-Regular'}, [(5, 'PingFangSC-Regular', '标题')])
    ok('字体检查：正文有文字用了苹方时报错，并写出第几页', len(probs) == 1 and '第5页“标题”' in probs[0], str(probs))
    ok('目录引导点不算正文文字', bool(wps.LEADER.match('..........')) and not wps.LEADER.match('1.6.1'))
    tnr = Path('/System/Library/Fonts/Supplemental/Times New Roman.ttf')
    if tnr.is_file():
        w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        parts = {
            '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                                   '<Default Extension="xml" ContentType="application/xml"/></Types>',
            'word/document.xml': '<w:document xmlns:w="%s"><w:body><w:p><w:r><w:t>永和 Abc</w:t></w:r>'
                                 '</w:p></w:body></w:document>' % w,
            'word/settings.xml': '<w:settings xmlns:w="%s"><w:zoom w:percent="100"/></w:settings>' % w,
            'word/fontTable.xml': '<w:fonts xmlns:w="%s"><w:font w:name="仿宋"><w:altName w:val="仿宋-简"/>'
                                  '<w:charset w:val="86"/></w:font><w:font w:name="仿宋"><w:charset w:val="00"/>'
                                  '</w:font></w:fonts>' % w,
        }
        src, out = tmp / 'wps_in.docx', tmp / 'wps_r' / 'wps_in.docx'
        with zipfile.ZipFile(src, 'w') as z:
            for n, d in parts.items():
                z.writestr(n, d)
        with patch.dict(embed_fonts.FONTS, {'仿宋': (tnr, None)}, clear=True):
            rep = embed_fonts.render_copy(src, out)
        with zipfile.ZipFile(out) as z:
            table = z.read('word/fontTable.xml').decode('utf-8')
            font = z.read('word/fonts/font1.odttf')
            settings = z.read('word/settings.xml').decode('utf-8')
        key = re.search(r'w:fontKey="\{([0-9A-F-]+)\}"', table).group(1)
        plain = embed_fonts.obfuscate(font, uuid.UUID(key))
        ok('嵌字体：裁剪后按 Word 的混淆写法嵌入，同名字体只留一条，异或还原后是 TrueType',
           [(n, k) for n, k, _ in rep] == [('仿宋', 'Regular')] and table.count('w:name="仿宋"') == 1
           and 'altName' not in table and 'embedTrueTypeFonts' in settings and plain[:4] == b'\x00\x01\x00\x00',
           '%d 字节' % len(font))
    else:
        print('  跳过  嵌字体：本机没有 Times New Roman')
    root = tmp / 'wps项目'
    py = [sys.executable, str(SKILL / 'scripts' / 'report.py')]
    import subprocess
    subprocess.run(py + ['new', str(root), '--名称', 'WPS测试'], check=True, capture_output=True)
    subprocess.run(py + ['build', str(root), '--不出PDF'], capture_output=True, text=True)
    docx = next((root / '输出').glob('*.docx'))
    pj = Project(root).resolve()
    heads = [(b['level'], b['text'].replace('**', '')) for b in pj.blocks if b['t'] == 'h']
    ok('读 Word 的标题与源文件一致', wps.docx_headings(docx) == heads, '%d 条' % len(heads))
    later = docx.stat().st_mtime + 60
    os.utime(root / '正文' / '11_结论与建议.md', (later, later))
    try:
        wps.run(root, docx, heads, out=tmp / 'wps核对')
        stale = ''
    except wps.WpsError as e:
        stale = str(e)
    ok('源文件比 Word 新时不导出，要求先 build', '比 输出/ 里的 Word 新' in stale and '11_结论与建议.md' in stale, stale[:80])
    ok('插图张数从 Word 里数（PDF 少了就报 WPS 丢图）', wps.docx_images(docx) == 1, str(wps.docx_images(docx)))


def main(quick=False):
    t = time.time()
    with tempfile.TemporaryDirectory(prefix='pe_test_') as d:
        tmp = Path(d)
        test_checks(tmp)
        test_parse(tmp)
        test_coverage(tmp)
        test_skeleton(tmp, quick)
        if ORIG.exists():
            test_magou(tmp, quick)
        else:
            print('【5】马沟回归：缺原稿 %s，跳过' % ORIG)
        test_wps(tmp)
    bad = [n for n, c in RESULTS if not c]
    print('\n合计 %d 项，通过 %d 项，失败 %d 项（用时 %.0f 秒）' % (len(RESULTS), len(RESULTS) - len(bad), len(bad), time.time() - t))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(quick='--快速' in sys.argv))
