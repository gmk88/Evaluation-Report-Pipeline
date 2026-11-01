#!/usr/bin/env python3
"""维护用：由马沟示例项目生成通用项目骨架 templates/项目骨架/。
骨架 = 11 章一、二级标题（GBZ/T 196—2025）＋各章写法要点（注释）＋常用评价表表头＋计数占位符
      ＋第1章可照用的通用段落（依据、目的、方法、程序、质量控制，标【待核】）。
用法：python3 scripts/dev/make_skeleton.py"""
import re
import shutil
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SKILL / 'scripts'))
from pe.model import Project, judge_col, table_grid  # noqa: E402
from pe.info import write_info  # noqa: E402

SRC = SKILL / 'examples' / '马沟'
DST = SKILL / 'templates' / '项目骨架'
TIPS = SKILL / 'references' / '各章写法' / '总览_报告骨架与各章要点.md'
COPY_SECTIONS = ('1.2', '1.3', '1.6', '1.7', '1.9')   # 第1章照用马沟段落（标【待核】）
PH = re.compile(r'\{\{(?:计数|行数):表([0-9.]+-\d+)[^}]*\}\}')
SECTION_TIPS = {   # 个别小节的写法提示（写进骨架，写完可删）
    '1.4': '<!-- 写法：写清评价范围（工程内容、运行期）。只评价粉尘、化学、物理（含非电离辐射）和生物因素；'
           '项目有放射源或射线装置的，加一句“放射性职业病危害因素由建设单位另行委托开展放射性职业病危害评价，'
           '不在本报告评价范围内”，其他各章不写放射性内容（判例 PL-PRE-0044）。 -->',
}


def attrs_text(a):
    return ' '.join(k if v is True else '%s=%s' % (k, v) for k, v in a.items())


def chapter_tips():
    out, cur = {}, None
    if not TIPS.exists():
        return out
    for line in TIPS.read_text(encoding='utf-8').split('\n'):
        m = re.match(r'^###\s+(\d+)\s', line)
        if m:
            cur = int(m.group(1))
            out[cur] = []
            continue
        if line.startswith('## '):
            cur = None
        elif cur and line.strip():
            out[cur].append(line.replace('-->', '→'))
    return out


def table_md(b, rows):
    L = [b['caption'], '<!-- 表格 %s -->' % attrs_text(b['attrs'])] if b.get('attrs') else [b['caption']]
    h = b['header'] or 1
    L += ['| ' + ' | '.join(r) + ' |' for r in b['rows'][:h]]
    L.append('|' + '---|' * len(b['rows'][0]))
    L += ['| ' + ' | '.join(r) + ' |' for r in rows]
    return L


def main():
    pj = Project(SRC)
    raw = {f.name: f.read_text(encoding='utf-8').split('\n') for f in pj.files}
    phs = {}
    for f, lines in raw.items():
        for ln in lines:
            for m in PH.finditer(ln):
                phs.setdefault(m.group(1), m.group(0))
    tips = chapter_tips()
    if DST.exists():
        shutil.rmtree(DST)
    (DST / '正文').mkdir(parents=True)
    (DST / '图').mkdir()
    (DST / '资料').mkdir()
    used_imgs = set()
    chapters, cur = [], None
    blocks = pj.blocks
    orient = 'portrait'
    for i, b in enumerate(blocks):
        if b['t'] == 'sect':
            orient = b['orient']
        if b['t'] == 'h' and b['level'] == 1:
            cur = {'title': b['text'], 'items': [], 'file': b['src'].split(':')[0]}
            chapters.append(cur)
        elif b['t'] == 'h' and b['level'] == 2:
            cur['items'].append(('h2', b))
        elif b['t'] == 'table' and b.get('caption'):
            key = re.match(r'表\s*([0-9.]+-\d+)', b['caption']).group(1)
            jc = judge_col(b)
            head = table_grid(b)[0][b['header'] - 1] if b['header'] else []
            is_eval = jc is not None and re.sub(r'\s+', '', head[jc]).startswith('符合性')
            if is_eval or key in phs or key.startswith('1.8'):
                cur['items'].append(('table', b, key, orient))
    for ci, ch in enumerate(chapters, 1):
        num = ch['title'].split()[0]
        L = []
        if tips.get(ci):
            L += ['<!-- 本章写法要点（详见 references/各章写法/，写完可删）：'] + tips[ci] + ['-->', '']
        L += ['# ' + ch['title'], '']
        items = ch['items']
        for k, it in enumerate(items):
            if it[0] != 'h2':
                continue
            b = it[1]
            sec = b['text'].split()[0]
            L += ['## ' + b['text'], '']
            if sec in COPY_SECTIONS:
                f, ln = b['src'].split(':')
                lines = raw[f][int(ln):]
                end = next((j for j, x in enumerate(lines) if re.match(r'^#{1,2}\s', x)), len(lines))
                body = '\n'.join(lines[:end]).strip('\n')
                body = re.sub(r'附件\s*\d+', '附件【待核】', body)
                body = re.sub(r'(?<![（(])表\s*(\d+(?:\.\d+)*-\d+)', lambda m: m.group(0) if m.group(1).startswith(sec) else '表【待核】', body)
                for m in re.finditer(r'\]\((图/[^)]+)\)', body):
                    used_imgs.add(m.group(1))
                L += ['<!-- 以下照搬马沟项目，逐条核对：现行有效、适用本项目、单位和项目名称已替换；核完删掉【待核】 -->',
                      '【待核】本节内容沿用样板，请逐条核对。', '', body, '']
                continue
            if sec in SECTION_TIPS:
                L += [SECTION_TIPS[sec], '']
            tabs = []
            for it2 in items[k + 1:]:
                if it2[0] == 'h2':
                    break
                tabs.append(it2)
            if not tabs:
                L += ['【待填】', '']
            for n_tab, (_, tb, key, ori) in enumerate(tabs, 1):
                new_key = '%s-%d' % (sec, n_tab)
                tb = dict(tb, caption=tb['caption'].replace('表' + key, '表' + new_key, 1))
                ncol = len(tb['rows'][0])
                head = [re.sub(r'\s+', '', x) for x in table_grid(tb)[0][tb['header'] - 1]]
                rows = []
                for r in (1, 2):
                    rows.append([str(r) if h in ('序号', '编号') else '【待填】' for h in head])
                L += ['【待填】评价说明（依据、方法、判定口径）。', '']
                if ori == 'landscape':
                    L += ['<!-- 横向 -->', '']
                L += table_md(tb, rows) + ['']
                if key in phs:
                    ph = phs[key].replace('表' + key, '表' + new_key)
                    if ph.startswith('{{行数'):
                        L += ['本项目共确定%s个职业病危害关键控制点。' % ph, '']
                    else:
                        L += ['评价结果：%s。' % ph, '']
                if ori == 'landscape':
                    L += ['<!-- 纵向 -->', '']
        (DST / '正文' / ch['file']).write_text('\n'.join(L).rstrip('\n') + '\n', encoding='utf-8')
    for rel in used_imgs:
        shutil.copy(SRC / rel, DST / rel)
    info = {'项目名称': '', '封面名称': [], '报告编号': '', '报告日期': '', '建设单位': '',
            '评价单位': pj.info.get('评价单位', ''), '法定代表人': pj.info.get('法定代表人', ''),
            '评价人员': [{'类别': '项目负责人', '姓名': '【待填】', '职称': '【待填】', '专业': '【待填】', '证书号': '【待填】'},
                     {'类别': '报告编写人', '姓名': '【待填】', '职称': '【待填】', '专业': '【待填】', '证书号': '【待填】'},
                     {'类别': '报告审核人', '姓名': '【待填】', '职称': '【待填】', '专业': '【待填】', '证书号': '【待填】'},
                     {'类别': '报告签发人', '姓名': '【待填】', '职称': '【待填】', '专业': '【待填】', '证书号': '【待填】'}],
            '附件': [{'名称': '职业病危害预评价委托书'}, {'名称': '项目备案（核准、批复）文件'}, {'名称': '项目地理位置图'},
                   {'名称': '总平面布置图'}, {'名称': '工艺流程图'}]}
    write_info(DST / '项目信息.md', info)
    (DST / '检查规则.md').write_text(
        '# 检查规则\n\n<!-- 本项目专用的检查（通用写法规矩程序已内置）。每行写“- 文字”，含空格或符号时用反引号 ` 包起来。\n'
        '     ## 禁用：出现即报错（如已纠正的旧数字、不适用的内容）  ## 必备：必须出现（关键数字、定稿表述）\n'
        '     ## 提示：出现时提醒人工确认  ## 正则禁用：按正则表达式查。例子见 examples/马沟/检查规则.md -->\n\n'
        '## 禁用\n\n## 必备\n\n## 提示\n\n- `设计专篇`\n- `控制效果评价`\n\n## 正则禁用\n', encoding='utf-8')
    (DST / '资料' / '说明.md').write_text(
        '# 资料\n\n把本项目收到的资料放在这里（可研报告、备案文件、总平面图、工艺流程图、设备清单、劳动定员、\n'
        '同类项目检测报告等）。需要哪些资料见 references/编制流程与资料清单.md。\n', encoding='utf-8')
    (DST / '图' / '说明.md').write_text(
        '# 图\n\n正文用到的图片放这里，正文里这样引用（另起一行）：\n\n'
        '    ![项目总平面布置图](图/总平面布置图.png){宽=15cm}\n    图2.3-1  项目总平面布置图\n', encoding='utf-8')
    print('骨架已生成：', DST)


if __name__ == '__main__':
    main()
