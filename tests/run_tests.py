#!/usr/bin/env python3
"""自测。用法：python3 scripts/report.py test [--快速]（或直接运行本文件，加 --快速 不渲染 PDF）
1 检查规则：故意写错的小项目，每类错误都要查出来；
2 解析与计数：表格合并格、指令、图题、计数句、项目信息读写；
3 骨架出稿：new → build，能出 Word（和 PDF），错误 0 条，列出待办；
4 马沟回归：导入马沟最终版 → 重新生成，正文逐段与原稿一致；compare 按章比对能定位改动；示例项目与导入结果一致；
  渲染后总页数、正文页数与原稿一致，各章起始页与原稿相差不超过 1 页，目录页码和页脚逐页核对通过。"""
import json
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
from pe import mdparse, docxgen, checks, render, finalize, compare, windrose  # noqa: E402
from pe.common import W, RE_SCRIPT, media_problems, wps_pack_problems  # noqa: E402
from lxml import etree as E  # noqa: E402
from pe.model import Project, count_clause, lead_tally  # noqa: E402
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
    lead_md = ('表2.2-1  选址检查表\n| 序号 | 检查内容 | 判断及补充要求 |\n|---|---|---|\n'
               '| 1 | 甲 | 符合。周边无污染源 |\n| 2 | 乙 | 基本符合，应补充 |\n| 3 | 丙 | 符合，无需另设 |\n')
    root = tmp / 'lead'
    _mini(root, {'02_工程分析.md': '# 2 工程分析\n\n' + lead_md, '11_结论与建议.md': '# 11 结论与建议\n\n- 选址：{{计数:表2.2-1}}；\n'})
    pj = Project(root).resolve()
    ok('没有符合性列、档位写在“判断及补充要求”开头的表，{{计数}} 照样生成',
       not pj.unresolved and [v for _, _, v in pj.resolved] == ['表2.2-1共评价3项，其中符合2项、基本符合1项'],
       str(pj.unresolved or pj.resolved))
    lead = mdparse.parse_text(lead_md)[1]
    lead['rows'][3][2] = '未见相关资料'
    ok('“判断”格有一格不是档位开头就不计数（照旧报没有符合性列）', lead_tally(lead) is None)
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


WIND_MD = ('建设单位提供的晋城市近三年风向频率见表2.1-3。\n\n表2.1-3  晋城市近三年全年和夏季风向频率统计\n'
           '| 风向 | 全年（%） | 夏季（%） | 风向 | 全年（%） | 夏季（%） |\n|---|---|---|---|---|---|\n'
           '| N | 3.51 | 2.00 | S | 2.21 | 4.00 |\n| NNE | 4.72 | 2.10 | SSW | 1.89 | 3.00 |\n'
           '| NE | 5.86 | 3.50 | SW | 4.20 | 1.90 |\n| ENE | 2.03 | 3.00 | WSW | 6.14 | 6.00 |\n'
           '| E | 2.83 | 5.00 | W | 2.01 | 8.00 |\n| ESE | 8.83 | 6.00 | WNW | 21.94 | 5.00 |\n'
           '| SE | 3.28 | 15.50 | NW | 15.49 | 3.00 |\n| SSE | 2.10 | 8.00 | NNW | 1.71 | 2.00 |\n'
           '| C（静风） | 11.25 | 22.00 | 合计 | 100.00 | 100.00 |\n\n注：建设单位提供。\n\n（5）下一段。\n')


def test_figures(tmp):
    print('【2b】第2章的图：风频玫瑰图、缺图待办')
    tb = windrose.find_table(WIND_MD)
    fr, calm = windrose.read_table(tb['行'])
    ok('认出风频表（不认死表号），表注一起算进表里', tb and tb['表号'] == '表2.1-3' and tb['注'] == '注：建设单位提供。'
       and WIND_MD.split('\n')[tb['end']].strip() == '', str(tb and {k: tb[k] for k in ('表号', 'end', '注')}))
    ok('读出 16 个方位和静风', len(fr['全年']) == 16 and fr['全年']['WNW'] == 21.94 and calm == {'全年': 11.25, '夏季': 22.0})
    ok('标题、数据来源行、图题照表题写（同200MW图2.1-2）',
       windrose.place_name(tb['表题']) == '晋城市' and windrose.source_line(tb) == '数据来源：建设单位提供的晋城市近三年风向频率统计'
       and windrose.fig_title(tb) == '晋城市近三年全年和夏季风频玫瑰图', windrose.source_line(tb))
    bad = WIND_MD.replace('| NNW | 1.71 |', '| NNW | 2.71 |')
    try:
        windrose.read_table(windrose.find_table(bad)['行'])
        ok('风频合计不是 100% 不画', False)
    except ValueError as e:
        ok('风频合计不是 100% 不画', '合计' in str(e), str(e))
    if windrose.browser():
        png = tmp / 'rose' / '图.png'
        windrose.draw(tb, png)
        from PIL import Image
        ok('画出风频玫瑰图（2 倍像素 3000×2080），旁边记了指纹', Image.open(png).size == (3000, 2080)
           and json.loads(png.with_suffix('.json').read_text(encoding='utf-8'))['表号'] == '表2.1-3')
        ok('表里的数改了要重画，没改不重画', not windrose.stale(png, tb) and windrose.stale(png, windrose.find_table(
            WIND_MD.replace('| N | 3.51 | 2.00 | S | 2.21 |', '| N | 3.50 | 2.00 | S | 2.22 |'))))
        png.with_suffix('.json').unlink()
        ok('手画的图（没有指纹）不重画', not windrose.stale(png, windrose.find_table(bad)))
    else:
        print('  跳过  画风频玫瑰图：这台电脑没有 Chrome 或 Edge')
    root = tmp / 'figs_none'
    _mini(root, {'02_1_工程概况.md': '# 2 建设项目工程分析\n\n## 2.1 工程概况分析\n\n正文。\n'})
    todo = [m for lv, s, m in checks.run(Project(root).resolve()) if lv == '待办' and '第2章没有' in m]
    ok('第2章缺的图列进待办（区域位置图、风频玫瑰图、总平面布置图、工艺流程图）', len(todo) == 4, str(todo))
    root = tmp / 'figs_all'
    img = '![图2.1-%d  %s](图/a.png)\n图2.1-%d  %s\n\n'
    body = ''.join(img % (i, t, i, t) for i, t in enumerate(['项目区域位置图', '风频玫瑰图', '升压站平面布置图', '工艺流程图'], 1))
    _mini(root, {'02_1_工程概况.md': '# 2 建设项目工程分析\n\n## 2.1 工程概况分析\n\n' + body + '注：引自初设图1.3-1。\n'})
    res = checks.run(Project(root).resolve())
    ok('图都在就不报；“引自初设图1.3-1”是资料里的图，不报找不到图题',
       not [m for lv, s, m in res if '第2章没有' in m or '找不到图题' in m], str([m for lv, s, m in res][:5]))


FLOW = {'图号': '图2.5-1', '因素': ['噪声', '氨', '高温'], '注': ['注：虚线框为依托设施。'], '框': [
    {'号': 'A', '行': 1, '列': 1, '名称': '原料卸车', '说明': '槽车 20 t', '因素': [2]},
    {'号': 'B', '行': 1, '列': 2, '名称': '储罐', '说明': '2×50 m³', '因素': [2]},
    {'号': 'C', '行': 1, '列': 3, '名称': '反应器', '说明': '320 ℃', '因素': [1, 2, 3]},
    {'号': 'D', '行': 2, '列': 3, '名称': '尾气吸收塔', '因素': [2]},
    {'号': 'E', '行': 2, '列': 2, '名称': '产品冷却', '说明': '依托现有', '因素': [3], '虚线': True},
    {'号': 'F', '行': 2, '列': 4, '名称': '排气筒', '因素': []}], '连线': [
    {'从': 'A', '到': 'B', '物料': '氨水'}, {'从': 'B', '到': 'C', '物料': '泵送'},
    {'从': 'C', '到': 'D', '物料': '尾气'}, {'从': 'C', '到': 'E', '物料': '产品', '虚线': True},
    {'从': 'D', '到': 'F'}]}


def test_flowchart(tmp):
    print('【2c】工艺流程图（说明 JSON → PNG）')
    import copy
    from unittest.mock import patch
    from pe import flowchart
    ok('有分岔、跨行连线的说明查不出问题', flowchart.validate(FLOW) == [], str(flowchart.validate(FLOW)))
    g, _, _ = flowchart._plan(FLOW)
    cross = g['lines'][3]                                        # C→E：从第1行第3列到第2行第2列
    ok('跨行连线走横平竖直的折线', len(cross) >= 3 and all(p[0] == q[0] or p[1] == q[1] for p, q in zip(cross, cross[1:])),
       str(cross))

    def bad(change, want, name):
        s = copy.deepcopy(FLOW)
        change(s)
        p = flowchart.validate(s)
        ok('查出' + name, any(want in m for m in p), str(p))
    bad(lambda s: s['框'][1].update({'号': 'A'}), '框号“A”重复', '重复的框号')
    bad(lambda s: s['框'][4].update({'行': 1, '列': 1}), '占同一格', '两个框占同一格')
    bad(lambda s: s['连线'].append({'从': 'A', '到': 'Z'}), '“Z”没有这个框', '连线指向不存在的框')
    bad(lambda s: s['框'][0].update({'因素': [2, 5]}), '编号 5 在图例里没有', '图例里没有的因素编号')
    bad(lambda s: s['连线'][0].update({'物料': '氨水及少量其他物料'}), '放不下', '两框间的物料字放不下')
    try:
        flowchart.draw(dict(FLOW, 框=FLOW['框'] + [dict(FLOW['框'][0])]), tmp / 'flow' / 'bad.png')
        ok('说明有错时 draw 报 ValueError、不出图', False)
    except ValueError:
        ok('说明有错时 draw 报 ValueError、不出图', not (tmp / 'flow' / 'bad.png').exists())
    with patch.object(flowchart.WR, 'browser', return_value=None):
        try:
            flowchart.draw(FLOW, tmp / 'flow' / 'none.png')
            ok('没有浏览器时 draw 报 RuntimeError', False)
        except RuntimeError:
            ok('没有浏览器时 draw 报 RuntimeError', not (tmp / 'flow' / 'none.png').exists())
    if flowchart.WR.browser():
        png = tmp / 'flow' / '图2.5-1.png'
        res = flowchart.draw(FLOW, png)
        from PIL import Image
        im = Image.open(png)
        meta = json.loads(png.with_suffix('.json').read_text(encoding='utf-8'))
        ok('画出流程图（2 倍像素、RGB），旁边有 .svg 和记了指纹的 .json',
           im.size == (2 * res['尺寸'][0], 2 * res['尺寸'][1]) and im.mode == 'RGB' and png.with_suffix('.svg').is_file()
           and meta['指纹'] == flowchart.fingerprint(FLOW) and meta['危害编号'] == {'1': '噪声', '2': '氨', '3': '高温'},
           '%s %s' % (im.size, im.mode))
    else:
        print('  跳过  画工艺流程图：这台电脑没有 Chrome 或 Edge')


def test_windstats(tmp):
    print('【2d】最近的国家气象站风向频率')
    from pe import windstats
    t = ('场址中心距14161#（东经 105°59’15.78”，北纬 24°23’38.62”）。场址中心坐\n标为东经 105°59’54.81”，北纬 24°21’35.10”。'
         '田林气象站地理坐标为：东经106°14′，北纬24°17′。')
    xy = windstats.site_coords(t)
    ok('认出场址坐标（不认测风塔、气象站的）', xy and abs(xy[0] - 24.3598) < 1e-3 and abs(xy[1] - 105.9986) < 1e-3, str(xy))
    yr = {'全年': [400] * 16 + [200], '夏季': [100] * 16 + [50]}      # 一年 8100 条，够“整年”（80%）
    yr['全年'][0] += 1500
    data = {'站': {'1': {'名称': '近站', '坐标': [24.6, 106.0, 100], '年': {y: yr for y in ('2021', '2022', '2023', '2024')}},
                  '2': {'名称': '更近但缺年', '坐标': [24.4, 106.0, 100], '年': {'2024': yr}},
                  '3': {'名称': '远站', '坐标': [26.5, 106.0, 100], '年': {y: yr for y in ('2022', '2023', '2024')}}}}
    sid, st, d, years = windstats.nearest(xy[0], xy[1], data)
    ok('找最近、三个整年都有的站，用最近三年', sid == '1' and years == ['2022', '2023', '2024'] and 20 < d < 35, str((sid, d, years)))
    ok('100 公里内没有就不找', windstats.nearest(xy[0], xy[1], {'站': {'3': data['站']['3']}}) is None)
    fr = windstats.freq(st, years)
    tb = windrose.find_table('\n'.join(windstats.table_md('表2.1-3', '近站近三年全年和夏季风向频率统计', fr, '注：……')))
    f2, calm = windrose.read_table(tb['行'])
    ok('风频合计正好 100%，写成的表画图程序读得回来', abs(sum(fr['全年']) - 100) < 1e-9 and f2['全年']['N'] == fr['全年'][0]
       and calm['全年'] == fr['全年'][16] and windstats.top(fr, '全年')[0] == 'N', str(windstats.top(fr, '全年')))
    if windstats.DATA.is_file():
        near = windstats.nearest(24.35975, 105.99856)
        ok('田林场址最近的国家站是百色站（约 80 公里）', near and near[0] == '592110' and 75 < near[2] < 85,
           str(near and (near[0], near[1].get('名称'), round(near[2]))))
        fr = windstats.freq(near[1], near[3])
        ok('百色站静风按风速算（风向记成缺测的静风钟点不丢）', 2 < fr['全年'][16] < 4, str(fr['全年'][16]))
        if windrose.browser():
            tb = windrose.find_table('\n'.join(windstats.table_md('表2.1-3', '百色站近三年全年和夏季风向频率统计', fr, '注：……')))
            try:
                windrose.draw(tb, tmp / 'rose' / '百色.png')
                ok('相邻方位的数值挨得近也画得出（后放的往外挪）', (tmp / 'rose' / '百色.png').is_file())
            except ValueError as e:
                ok('相邻方位的数值挨得近也画得出（后放的往外挪）', False, str(e))


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


MAGOU_DESIGN = Path('/Users/a1234/Desktop/中石化马沟加油站控制效果评价/马沟加气站职业病防护设施设计专篇_AT-2026-Z013.docx')
DESIGN_STATEMENT = ('晋城市安泰职业卫生健康技术咨询有限公司遵守国家有关法律、法规，在测试风电场职业病防护设施设计中优先采用'
                    '有利于职业病防治的新工艺、新方法和新材料，确保所出具的《测试风电场职业病防护设施设计专篇》的真实性、'
                    '合法性和实用性，并对其承担法律责任。')   # WS/T 882—2013 附录D 附件2 原文


def _docx_parts(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n).decode('utf-8') for n in z.namelist() if n.endswith('.xml')}


def _paras(xml):
    """[(段落文字, 段落元素)]"""
    root = E.fromstring(xml.encode('utf-8'))
    return [(''.join(t.text or '' for t in p.iter(W + 't')), p) for p in root.iter(W + 'p')]


def _design_style_checks(tmp):
    """设计专篇的篇幅、段落和单元格长度、列数、行首“- ”、标签过多、采购与限值同处、预期值等于限值、预期结论新写法、分档、body_rows 不数分组行。"""
    from pe import doctype
    from pe.model import body_rows
    root = tmp / '专篇风格'
    para = '设施按标准设置并满足要求。' * 15     # 约 150 汉字
    body = '# 4 职业病防护设施设计\n\n' + '<!-- ' + '注释里的字不算篇幅。' * 800 + ' -->\n\n'
    body += '\n\n'.join(para for _ in range(30)) + '\n\n'               # 约 4500 汉字
    body += '- 行首横杠条目。\n\n<!-- - 注释里的横杠不算 -->\n\n' + '很长的段落' * 60 + '\n\n'
    body += '表4.1-1  宽表\n| 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |\n|---|---|---|---|---|---|---|---|\n' \
            '| ' + ' | '.join(['甲'] * 7) + ' | ' + '长' * 101 + ' |\n\n'
    body += '表4.1-2  标签\n| 设施 | 指标 |\n|---|---|\n' + ''.join('| 设施%d | 85（采购指标） |\n' % i for i in range(4)) + '\n'
    body += '表4.1-3  采购\n| 设施 | 指标 |\n|---|---|\n| 风机 | 机舱作业位置噪声≤85 dB(A)，写入采购技术协议 |\n| 风机 | 采购时要求厂家提供声功率级保证值 |\n\n'
    body += ('表4.1-4  预期\n| 工种 | 职业病危害因素 | 采取设计措施后预期接触水平 | 职业接触限值 | 预期结论 |\n|---|---|---|---|---|\n'
             '| 甲 | 噪声 | 作业位置≤85 dB(A)；LEX,8h≤80 dB(A) | 85 dB(A) | 符合 |\n'
             '| 乙 | 噪声 | LEX,8h 78～82 dB(A)（类比实测） | 85 dB(A) | 超限，须个体防护 |\n'
             '| 丙 | 高温 | WBGT 28～30 ℃（计算） | WBGT 33℃ | 限制接触时间后符合 |\n'
             '| 丁 | 粉尘 | 1.2 mg/m³ | 8 mg/m³ | 采取防护后符合 |\n'
             '| 戊 | 粉尘 | 1.2 mg/m³ | 8 mg/m³ | 超限，须个体防护 |\n\n')
    _mini(root, {'04_2_防护设施设计.md': body})
    (root / '项目信息.md').write_text('- 报告类型：设计专篇\n- 项目名称：测试\n- 报告编号：AT-1\n- 报告日期：二〇二六年八月\n'
                                     '- 评价单位：某公司\n', encoding='utf-8')
    (root / '流水线设置.json').write_text('{"篇幅档": "A"}', encoding='utf-8')
    iss = checks.run(Project(root).resolve())
    msgs = [(lv, m) for lv, _, m in iss]
    ok('专篇：篇幅档 A 的 04_2 预算 3800 字，超过 1.2 倍给提示（注释不算字数）',
       any(lv == '提示' and '篇幅超预算' in m and 'A档预算约 3800' in m for lv, m in msgs), str([m for _, m in msgs if '篇幅' in m]))
    ok('专篇：行首“- ”报错（注释里的不算），只有一处', sum(lv == '错误' and '行首写了“- ”' in m for lv, m in msgs) == 1,
       str([m for _, m in msgs if '行首' in m]))
    ok('专篇：超 250 字的段落、超 100 字的单元格、超 7 列的表给提示',
       any(lv == '提示' and '段落超过 250' in m for lv, m in msgs) and any(lv == '提示' and '单元格超过 100' in m for lv, m in msgs)
       and any(lv == '提示' and '超过 7 列' in m and '表4.1-1 8 列' in m for lv, m in msgs))
    ok('专篇：“（采购指标）”标签超过 3 格给提示', any(lv == '提示' and '4 个单元格写了' in m for lv, m in msgs))
    ok('专篇：“采购”与“≤85 dB(A)”同行给提示，只提厂家保证值的那行不提',
       any(lv == '提示' and '同一行（段）（共 1 处）' in m for lv, m in msgs), str([m for _, m in msgs if '采购”与' in m]))
    ok('专篇：预期值写成限值本身（≤85 与 85 dB(A)）给提示，类比值、计算值范围不提',
       any(lv == '提示' and '预期接触水平与同行职业接触限值相同（共 1 处）' in m for lv, m in msgs), str([m for _, m in msgs if '相同' in m]))
    ok('专篇：预期结论规定写法不报，“采取防护后符合”报错，非噪声写“超限，须个体防护”报错',
       [re.split('不是|只准', m)[0] for lv, m in msgs if lv == '错误' and '预期结论' in m] == ['表4.1-4 预期结论“采取防护后符合”', '表4.1-4 预期结论“超限，须个体防护”'],
       str([m for lv, m in msgs if '预期结论' in m]))
    ok('doctype.EXPECT_LEVELS 三项', len(doctype.EXPECT_LEVELS) == 3 and doctype.EXPECT_LEVELS[0] == '符合')
    # 分档和预算
    ok('篇幅档设置优先；预算 A＝C×0.75、B＝C×0.88 取整到百',
       doctype.design_tier(root) == 'A' and doctype.budget_for(root, '正文/04_2_防护设施设计.md') == 3800
       and doctype.budget_for(root, '正文/03_x.md') == 4100 and doctype.budget_for(root, '附件册/正文/04_计算书.md') == 4500)
    (root / '流水线设置.json').write_text('{}', encoding='utf-8')
    (root / '资料').mkdir()
    def post(rows):
        (root / '资料' / '岗位因素表.md').write_text('# 岗位因素表\n| 评价单元 | 工作场所 | 职业病危害因素 |\n|---|---|---|\n' + ''.join(
            '| %s | %s | %s |\n' % r for r in rows), encoding='utf-8')
    post([('甲', '场所%d' % i, '噪声、高温' if i % 2 else '噪声；粉尘') for i in range(3)])
    ok('没设篇幅档时按岗位因素表分档：3 个场所、3 种因素为 A', doctype.design_tier(root) == 'A')
    post([('甲', '场所%d' % i, '噪声、高温、粉尘、振动、氨') for i in range(5)] + [('甲', '^', '一氧化碳')])
    ok('场所 5、因素 6 为 B（“^”接上一行场所）', doctype.design_tier(root) == 'B' and doctype.budget_for(root, '正文/05_x.md') == 1900)
    post([('甲', '场所%d' % i, '噪声') for i in range(3)])
    ok('A 档预算四舍五入取整到百（2200×0.75＝1650→1700，3000×0.75＝2250→2300；不用银行家舍入）',
       doctype.budget_for(root, '正文/02_1_x.md') == 1700 and doctype.budget_for(root, '正文/04_3_x.md') == 2300)
    post([('甲', '场所%d' % i, '噪声') for i in range(25)])
    ok('场所 25 为 C', doctype.design_tier(root) == 'C' and doctype.budget_for(root, '正文/05_x.md') == 2200)
    (root / '资料' / '岗位因素表.md').unlink()
    ok('读不到岗位因素表按 C；预评价项目没有预算', doctype.design_tier(root) == 'C' and doctype.budget_for(tmp, '正文/04_2_x.md') is None)
    # body_rows 不数分组行
    tb = {'rows': [['序号', '设施', '数量'], ['一、通风', '<', '<'], ['1', '甲', '2'], ['二、噪声', '^', '^'], ['2', '乙', '1'], ['合计', '', '3']],
          'header': 1}
    ok('body_rows 不数分组行（一、…且其余格为合并标记）和合计行', body_rows(tb) == 2, str(body_rows(tb)))


def _design_checks(tmp):
    """设计专篇的机器检查：采纳三档、不采纳理由、预期结论、附件册标题和引用、外部表引用、放射性否定说法、豁免通用。"""
    from pe import doctype
    root = tmp / '专篇检查'
    _mini(root, {
        '04_设计.md': '# 1 概述\n\n## 1.1 任务\n\n见预评价报告表10.8-1、附件册表1.1-1，详见附件册附件2；附件册附件9 不存在。'
                     '承接预评价报告第10.2节表10.2-1第1项，表10.3-1第2项；本卷表9.9-9不存在。'
                     '附件册第1.1节表1.1-1、表1.1-7 不存在。'
                     '本项目不设置防电离辐射设施，理由：无放射源。防寒类别不设置。\n\n'
                     '表1.1-1  采纳\n| 序号 | 措施 | 处理方式 | 理由 |\n|---|---|---|---|\n| 1 | 甲 | 采纳 | — |\n'
                     '| 2 | 乙 | 不采纳 |  |\n| 3 | 丙 | 部分采纳 | 某 |\n| 4 | 丁 | 不采纳 | 已由第4.3节设施替代 |\n\n'
                     '表1.1-2  预期\n| 工种 | 预期结论 |\n|---|---|\n| 甲 | 符合 |\n| 乙 | 不符合 |\n| 丙 | 基本符合 |\n\n'
                     '表1.1-3  分项\n| 序号 | 项 |\n|---|---|\n| 1 | 甲 |\n| 2.1 | 乙 |\n| 2.2 | 丙 |\n| 3 | 丁 |\n\n'
                     '表1.1-4  跳号\n| 序号 | 项 |\n|---|---|\n| 1 | 甲 |\n| 3.1 | 乙 |\n'})
    (root / '项目信息.md').write_text('- 报告类型：设计专篇\n- 项目名称：测试\n- 报告编号：AT-1\n- 报告日期：二〇二六年八月\n'
                                     '- 评价单位：某公司\n', encoding='utf-8')
    ann = root / '附件册'
    (ann / '正文').mkdir(parents=True)
    (ann / '项目信息.md').write_text('- 报告类型：设计专篇附件册\n', encoding='utf-8')
    (ann / '正文' / '01.md').write_text('# 附件1 甲\n\n## 1.1 乙\n\n表1.1-1  丙\n| 序号 | 项 |\n|---|---|\n| 1 | 甲 |\n\n'
                                       '# 附件2 丁\n\n## 2.1 戊\n\n正文。\n\n# 附件4 己\n\n## 4.1 庚\n\n正文。\n\n# 5 辛\n\n正文。\n',
                                       encoding='utf-8')
    pj = Project(root).resolve()
    iss = checks.run(pj)
    errs = [m for lv, _, m in iss if lv == '错误']
    ok('专篇：采纳情况“部分采纳”不是三档之一报错', any('“部分采纳”不是规定写法' in m for m in errs))
    ok('专篇：“不采纳”没写理由报错，写了理由的不报', sum('没写理由' in m for m in errs) == 1, str(errs))
    ok('专篇：预期结论写“不符合”“基本符合”报错', any('预期结论写了“不符合”' in m for m in errs)
       and any('预期结论“基本符合”' in m for m in errs))
    ok('专篇：“预评价报告第10.2节表10.2-1”“……表10.2-1第1项，表10.3-1”隔着节号、并列表号也算外部引用；'
       '本卷没有的表照报；“附件册第1.1节表1.1-7”按附件册核',
       sorted(m for m in errs if '引用的' in m and '表' in m)
       == ['引用的表9.9-9不存在（表题要紧挨在表格上一行）', '引用的附件册表1.1-7不存在（附件册里没有这张表）'],
       str([m for m in errs if '引用的' in m]))
    ok('序号“2.1”“2.2”算第2项的分项不报；分项跳号照报',
       [m for m in errs if '序号不连续' in m] == ['表1.1-4 序号不连续：这一行是 3.1，应为 2'], str([m for m in errs if '序号' in m]))
    ok('专篇：“预评价报告表”“附件册表”当外部引用放过；附件册附件N 按附件册标题个数核', True
       and [m for m in errs if '附件' in m and '引用了' in m] == ['引用了附件9，附件册只有 4 个附件（一级标题“附件N ……”）'],
       str([m for m in errs if '引用' in m]))
    ok('专篇：“不设置防电离辐射设施”不报放射性；“防寒”按类型规则豁免通用', not any('PL-PRE-0044' in m for m in errs)
       and not any('防寒' in m for _, _, m in iss), str([m for _, _, m in iss if '防寒' in m or '0044' in m]))
    ok('专篇：不按 GBZ/T 196 的 11 章查章名', not any('GBZ/T 196' in m for _, _, m in iss))
    ap = Project(ann).resolve()
    ai = checks.run(ap)
    ok('附件册：项目信息从主报告继承（名称、编号、单位）', ap.type == doctype.ANNEX and ap.info.get('报告编号') == 'AT-1'
       and ap.info.get('评价单位') == '某公司')
    grp = {'header': 1, 'rows': [['序号', '问题'], ['一、识别', '<'], ['1', '甲'], ['2', '乙'], ['二、设施', '<'], ['3', '丙']]}
    ok('专篇：预评价措施表的条数不算整行合并的分组行', checks.pre_items(grp) == 3, str(checks.pre_items(grp)))
    ok('附件册：一级标题“附件N 标题”按 N 查连续，写成“5 辛”的报错',
       any('标题编号不连续：4' in m for _, _, m in ai) and any('“附件N 标题”' in m for _, _, m in ai),
       str([m for _, _, m in ai if '标题' in m]))


def test_design(tmp, quick):
    """设计专篇（WS/T 882—2013）：骨架两卷零错误、两卷出 Word、附录D 的页面和前置页、马沟专篇导入。"""
    print('【7】设计专篇')
    import subprocess
    from pe import doctype
    _design_checks(tmp)
    _design_style_checks(tmp)
    # 模板改动要做进 make_design_template.py（同预评价模板的做法）
    sys.path.insert(0, str(SKILL / 'scripts' / 'dev'))
    import make_design_template as MDT
    regen = MDT.main(MDT.SRC, tmp / '专篇模板重建')
    drift = []
    for new in regen:
        with zipfile.ZipFile(SKILL / 'templates' / new.name) as za, zipfile.ZipFile(new) as zb:
            drift += ['%s:%s' % (new.name, n) for n in za.namelist() if n not in zb.namelist() or za.read(n) != zb.read(n)]
    ok('make_design_template.py 由预评价模板重建的两份专篇模板与 templates/ 里的逐字节一致', not drift, str(drift[:5]))
    root = tmp / '专篇项目'
    py = [sys.executable, str(SKILL / 'scripts' / 'report.py')]
    r = subprocess.run(py + ['new', str(root), '--名称', '测试风电场', '--类型', '设计专篇'], capture_output=True, text=True)
    ok('new --类型 设计专篇：复制专篇骨架（含 附件册/）', r.returncode == 0 and doctype.type_of(root) == doctype.DESIGN
       and doctype.annex_root(root) is not None, r.stdout[-200:])
    vols = doctype.volumes(root)
    for v in vols:
        name = '附件册' if v != root else '主报告'
        iss = checks.run(Project(v).resolve())
        c = checks.summary(iss)
        ok('专篇骨架%s检查零错误' % name, c['错误'] == 0, '; '.join(m for lv, _, m in iss if lv == '错误')[:300])
        if v != root:
            ok('附件册骨架第2章只要区域位置图、风频玫瑰图、工艺流程图（总平面图在主报告第2.2.1节）',
               not any('总平面' in m for _, _, m in iss) and any('区域位置图' in m for _, _, m in iss))
        else:
            ok('主报告骨架：附件册附件6 还是骨架时不拿骨架行数报条数错误，只提示',
               not any('附件册附件6' in m for lv, _, m in iss if lv == '错误') and any('没写处理方式' in m for _, _, m in iss))
    subprocess.run(py + ['build', str(root), '--不出PDF'], capture_output=True, text=True)
    docs = [next(iter((v / '输出').glob('*.docx')), None) for v in vols]
    ok('build 主报告文件夹时两卷都出 Word（文件名按类型）', len(docs) == 2 and all(docs)
       and docs[0].name == '测试风电场职业病防护设施设计专篇.docx'
       and docs[1].name == '测试风电场职业病防护设施设计专篇资料性附件册.docx', str([d and d.name for d in docs]))
    if not all(docs):
        return
    for d, annex in zip(docs, (False, True)):
        name = '附件册' if annex else '主报告'
        parts = _docx_parts(d)
        doc = parts['word/document.xml']
        paras = _paras(doc)
        first_h = next(i for i, (t, p) in enumerate(paras) if p.find(W + 'pPr/' + W + 'pStyle') is not None
                       and p.find(W + 'pPr/' + W + 'pStyle').get(W + 'val') == '3')
        i_toc = next(i for i, (t, _) in enumerate(paras) if t == '目  录')
        front = ''.join(t for t, _ in paras[:i_toc])   # 封面至签字页（目录条是正文标题，另算）
        hf = ''.join(v for n, v in parts.items() if re.match(r'word/(header|footer)\d+\.xml$', n))
        props = parts['docProps/core.xml'] + parts.get('docProps/app.xml', '')
        src = ''.join(f.read_text(encoding='utf-8') for f in sorted(((root / '附件册') if annex else root).glob('正文/*.md')))
        body_n = ''.join(t for t, _ in paras[first_h:]).count('预评价')
        src_n = re.sub(r'<!--.*?-->', '', src, flags=re.S).count('预评价')
        toc_bad = [t for t, _ in paras[i_toc:first_h] if '预评价' in t and not re.search(r'预评价报告', t)]
        ok('%s Word 属性、页眉页脚、前置页没有“预评价”；正文里的只来自正文源文件（引用预评价报告）' % name,
           '预评价' not in front + hf + props and body_n <= src_n and not toc_bad,
           '正文 %d 处／源文件 %d 处；%s' % (body_n, src_n, [x for x in ('预评价' in front, '预评价' in hf, '预评价' in props)]))
        core = parts['docProps/core.xml']
        want = '测试风电场' + doctype.conf(doctype.ANNEX if annex else doctype.DESIGN)['名称后缀']
        ok('%s Word 属性：标题、主题、关键词按类型' % name, '<dc:title>%s</dc:title>' % want in core
           and '设计专篇' in re.search(r'<dc:subject>([^<]*)', core).group(1)
           and '设计专篇' in re.search(r'<cp:keywords>([^<]*)', core).group(1), core[-400:])
        mars = re.findall(r'<w:pgSz w:w="(\d+)" w:h="(\d+)"[^>]*/><w:pgMar ([^>]*)/>', doc)
        port = [dict(re.findall(r'w:(\w+)="(\d+)"', m)) for w_, h_, m in mars if int(w_) < int(h_)]
        ok('%s 纵向页 A4、页边距左28 右20 上25 下20 mm（附录D.2）' % name, port and all(
            (m['left'], m['right'], m['top'], m['bottom']) == ('1588', '1134', '1418', '1134') for m in port),
            str(port[:1]))
        heads = [v for n, v in parts.items() if re.match(r'word/header\d+\.xml$', n)]
        foots = [v for n, v in parts.items() if re.match(r'word/footer[34]\.xml$', n)]
        second = doctype.conf(doctype.ANNEX if annex else doctype.DESIGN)['页眉第二行']
        ok('%s 页眉两行：项目名称／%s＋报告编号，宋体小五（附录D.1）' % (name, second), heads and all(
            '测试风电场' in h and second + '</w:t>' in h and '报告编号：' in h and 'w:eastAsia="宋体"' in h
            and 'w:sz w:val="18"' in h for h in heads))
        ok('%s 页脚：设计单位、日期、第PAGE页 共PAGEREF BodyEnd页，宋体小五（附录D.1）' % name, foots and all(
            '晋城市安泰职业卫生健康技术咨询有限公司' in f and '【待填】' in f and 'PAGEREF BodyEnd' in f and ' PAGE ' in f
            and 'w:sz w:val="18"' in f for f in foots))
        body = [p for t, p in paras[first_h:] if p.find(W + 'pPr/' + W + 'ind') is not None
                and p.find(W + 'pPr/' + W + 'ind').get(W + 'firstLine') == '612' and t.strip()]
        sp = {(p.find(W + 'pPr/' + W + 'spacing').get(W + 'line'),
               p.find('.//' + W + 'r/' + W + 'rPr/' + W + 'spacing').get(W + 'val')
               if p.find('.//' + W + 'r/' + W + 'rPr/' + W + 'spacing') is not None else None) for p in body}
        ok('%s 正文仿宋四号 28 行×30 字：固定行距 510、字距 26、首行缩进 2 字' % name, body and sp == {('510', '26')}, str(sp))
        h1 = [t for t, p in paras if p.find(W + 'pPr/' + W + 'pStyle') is not None
              and p.find(W + 'pPr/' + W + 'pStyle').get(W + 'val') == '3']
        if annex:
            ok('附件册一级标题在 Word 里是“附件1 ……”', h1 and all(re.match(r'^附件\d+ ', t) for t in h1), str(h1[:3]))
    # 封面、声明（附录D 附件1、附件2）
    mp = _paras(_docx_parts(docs[0])['word/document.xml'])
    texts = [t for t, _ in mp]

    def run_of(text):
        p = next(p for t, p in mp if t == text)
        rp = p.find('.//' + W + 'r/' + W + 'rPr')
        return (rp.find(W + 'rFonts').get(W + 'eastAsia'), rp.find(W + 'sz').get(W + 'val'), rp.find(W + 'b') is not None)

    ok('封面：报告编号黑体四号；项目名称和“职业病防护设施设计专篇”华文中宋一号加粗；资质证书编号；单位；日期',
       texts[0] == '报告编号：【待填】' and run_of('报告编号：【待填】') == ('黑体', '28', False)
       and run_of('职业病防护设施设计专篇') == ('华文中宋', '52', True) and run_of(texts[1]) == ('华文中宋', '52', True)
       and '设计单位资质证书编号：【待填】' in texts[:8] and '晋城市安泰职业卫生健康技术咨询有限公司' in texts[:8],
       str(texts[:8]))
    i = texts.index('声\u3000明')
    ok('封二：顶部报告编号；声明为 WS/T 882 附件2 原文，仿宋三号；四行签字', texts[i - 1] == '报告编号：【待填】'
       and texts[i + 1] == DESIGN_STATEMENT and run_of(DESIGN_STATEMENT)[:2] == ('仿宋', '32')
       and texts[i + 2:i + 6] == ['设计单位：（加盖公章）', '设计单位法定代表人：（签名）', '项目负责人：（签名）', '专篇审核人：（签名）'],
       str(texts[i - 1:i + 7]))
    ok('封三资质证书页、封四签字页、封五目录；主报告目录后没有附件清单',
       texts.index('设计单位资质证书影印件') < texts.index('参加专篇编写人员签字页') < texts.index('目  录')
       and '附件1  ' not in ''.join(texts[:texts.index('目  录') + 80]))
    doc0 = E.fromstring(_docx_parts(docs[0])['word/document.xml'].encode('utf-8'))
    tbl = next(t for t in doc0.iter(W + 'tbl') if '人员类别' in ''.join(x.text or '' for x in t.iter(W + 't')))
    ok('签字页人员表排满版心宽 9184', tbl.find(W + 'tblPr/' + W + 'tblW').get(W + 'w') == '9184'
       and sum(int(g.get(W + 'w')) for g in tbl.iter(W + 'gridCol')) == 9184)
    at = [t for t, _ in _paras(_docx_parts(docs[1])['word/document.xml'])]
    ok('附件册：封面加“资料性附件册”，没有声明、资质证书页和签字页', '资料性附件册' in at[:8]
       and not any(x in at for x in ('声\u3000明', '设计单位资质证书影印件', '参加专篇编写人员签字页')))
    from pe import wps
    ok('出稿字体：专篇 Word 用到华文中宋、黑体，WPS 核对时要求 PDF 里有', wps.docx_fonts(docs[0]) == ['华文中宋', '黑体']
       and len(wps.font_problems({'FangSongCN-Regular', 'SimSunCN-Regular'}, [], ['华文中宋', '黑体'])) == 1
       and not wps.font_problems({'FangSongCN-Regular', 'SimSunCN-Regular', 'STZhongsong', 'SimHei'}, [], ['华文中宋', '黑体']))
    pk = [x for d in docs for x in wps_pack_problems(d)]
    ok('两卷打包方式与 WPS 另存的一致', not pk, '；'.join(pk))
    # 空节：“纵向”后紧跟“横向”（文件交界处常见）不出空的纵向节，否则 PDF 多一张空白页
    empty = []
    for d in docs:
        bd = E.fromstring(_docx_parts(d)['word/document.xml'].encode('utf-8')).find(W + 'body')
        kids, last = list(bd), None
        for k, ch in enumerate(kids):
            if ch.find(W + 'pPr/' + W + 'sectPr') is not None or ch.tag == W + 'sectPr':
                if last is not None and k - last == 1 and not ''.join(ch.itertext()).strip():
                    empty.append('%s 第%d个子元素' % (d.name, k))
                last = k
    ok('两卷没有空节（纵向、横向交界不多出空白页）', not empty, '；'.join(empty[:3]))
    # 渲染副本：黑体改名 SimHeiCN 嵌入（WPS 命令行见“黑体”就换字）；交稿 Word 仍写黑体
    from pe import embed_fonts
    rc = tmp / '专篇渲染' / docs[0].name
    try:
        embed_fonts.render_copy(docs[0], rc)
    except RuntimeError as e:
        print('  跳过  渲染副本黑体改名：%s' % e)
    else:
        rp = _docx_parts(rc)
        ok('渲染副本：黑体改名 SimHeiCN 嵌入，交稿 Word 不改', 'w:eastAsia="SimHeiCN"' in rp['word/document.xml']
           and '"黑体"' not in rp['word/document.xml'] and 'w:name="SimHeiCN"' in rp['word/fontTable.xml']
           and 'w:eastAsia="黑体"' in _docx_parts(docs[0])['word/document.xml'])
    # 马沟专篇导入：编号、日期、名称、类型认得出；检查不报 GBZ/T 196 的 11 章
    if not MAGOU_DESIGN.exists():
        print('  跳过  马沟专篇导入：缺 %s' % MAGOU_DESIGN)
        return
    imp = tmp / '马沟专篇导入'
    info, files = Importer(MAGOU_DESIGN).run(imp)
    ok('导入马沟专篇：报告编号、日期（二〇二六年八月）、项目名称、设计单位、类型都认出来',
       info.get('报告编号') == 'AT-2026-Z013' and info.get('报告日期') == '二〇二六年八月'
       and info.get('项目名称') == '中国石化销售股份有限公司山西晋城阳城马沟加气站'
       and info.get('评价单位') == '晋城市安泰职业卫生健康技术咨询有限公司' and info.get('法定代表人') is None
       and doctype.type_of(imp) == doctype.DESIGN and len(files) == 5,
       str({k: info.get(k) for k in ('报告编号', '报告日期', '项目名称', '评价单位', '法定代表人', '报告类型')}))
    iss = checks.run(Project(imp).resolve())
    ok('马沟专篇导入后检查不报 GBZ/T 196 的 11 章、不把“预评价报告表”当缺表',
       not any('GBZ/T 196' in m or '11 章' in m for _, _, m in iss) and not any('引用的表' in m for _, _, m in iss),
       str([m for _, _, m in iss if 'GBZ/T 196' in m or '引用的表' in m][:3]))
    from pe import coverage
    ch = coverage.chapters(imp)
    ok('篇幅核对：专篇 5 章各归各章（第4章设计、第5章预期效果不合并）', sorted(ch) == [1, 2, 3, 4, 5]
       and ch[4]['title'] == '职业病防护设施设计' and ch[5]['title'] == '预期效果评价', str(sorted(ch)))


def main(quick=False):
    t = time.time()
    with tempfile.TemporaryDirectory(prefix='pe_test_') as d:
        tmp = Path(d)
        test_checks(tmp)
        test_parse(tmp)
        test_figures(tmp)
        test_windstats(tmp)
        test_flowchart(tmp)
        test_coverage(tmp)
        test_skeleton(tmp, quick)
        if ORIG.exists():
            test_magou(tmp, quick)
        else:
            print('【5】马沟回归：缺原稿 %s，跳过' % ORIG)
        test_wps(tmp)
        test_design(tmp, quick)
    bad = [n for n, c in RESULTS if not c]
    print('\n合计 %d 项，通过 %d 项，失败 %d 项（用时 %.0f 秒）' % (len(RESULTS), len(RESULTS) - len(bad), len(bad), time.time() - t))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(quick='--快速' in sys.argv))
