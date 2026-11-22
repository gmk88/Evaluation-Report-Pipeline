"""资料欠缺清单：报告还缺哪些资料、补来能用在哪、找谁要（工作台显示，也写成 输出/资料欠缺清单.md 好转给建设单位）。
不进报告（用户 10-30：待补资料清单进报告显得不完善）。不调模型，只从现成的东西里汇总：
- 要补：正文里的【待填】、项目信息没填的、第2章缺的图要的资料、区域位置图没有审图号；
- 补了更准：岗位因素表里“按不利情况定的”（现在报告按不利情况写的）；
- 可以不补：口径表里“可研没有写的”（报告里已写补充措施）。"""
import json
import re
import time
from pathlib import Path

from . import tools
from pe import doctype as DT  # noqa: E402
from pe import windrose as WR  # noqa: E402  （tools 已把 scripts/ 放进 sys.path）

FILE = '资料欠缺清单.md'
FIG_ASK = {   # 检查里“第2章没有××”对应要的资料；工艺流程图照可研画，不算缺资料
    '区域位置图': ('带审图号的区域位置图（或项目中心坐标，写明坐标系）', '第2.1节', '建设单位或设计单位'),
    '风频玫瑰图': ('当地气象站近三年全年和夏季风向频率统计（16 个方位和静风）', '第2.1节风向、风频玫瑰图', '建设单位或当地气象部门'),
    '总平面布置图': ('总平面布置图（初设或可研附图）', '第2.3节', '设计单位'),
}
ANALOG = ('类比项目资料：规模、工艺相近，已投运项目的控制效果评价报告或定期检测报告',
          '第3章类比调查、第4章预测接触水平', '建设单位，或从本公司做过的同类项目里选')
# 设计专篇：图在附件册第2章（区域位置图、风频玫瑰图、工艺流程图）和主报告第2.2.1节（总平面布置图）；
# 口径表那一节叫“设计文件没有写的”（专篇已按标准写成设计要求）
FIG_ASK_DESIGN = {
    '区域位置图': ('带审图号的区域位置图（或项目中心坐标，写明坐标系）', '附件册第2.1节', '建设单位或设计单位'),
    '风频玫瑰图': ('当地气象站近三年全年和夏季风向频率统计（16 个方位和静风）', '附件册第2.1节', '建设单位或当地气象部门'),
    '总平面布置图': ('总平面布置图（初设或施工图）', '主报告第2.2.1节', '设计单位'),
}
# 设计专篇项目信息里安泰自己填的（设计单位资质、编写人员、专篇编号和日期）：不找建设单位要
OWN_KEYS = ('资质证书编号', '评价人员', '报告编号', '报告日期')
GAP = {DT.PRE: ('可研没有写的', '可研没写，报告里已写补充措施'), DT.DESIGN: ('设计文件没有写的', '设计文件没写，专篇已按标准写成设计要求')}


def _bullets(text, head):
    """“## head”下面各条“- ……”的文字。"""
    m = re.search(r'^## %s.*?$(.*?)(?=^## |\Z)' % re.escape(head), text, re.M | re.S)
    return [x[2:].strip() for x in (m.group(1) if m else '').split('\n') if x.startswith('- ')]


def _read(p):
    return tools.read(p) if Path(p).exists() else ''


def _still_proxy(root, name):
    """正文风频表的表题还写着代用站名；改稿、补资料换成当地气象站的数据后，就不再列这一条。"""
    for vol in DT.volumes(root):                   # 设计专篇的风频表在附件册
        for f in tools.body_files(vol):
            tb = WR.find_table(_read(Path(vol) / '正文' / f))
            if tb:
                return bool(name) and name in tb['表题']
    return False


def collect(root, chk=None):
    """{'项目', '要补': [{缺什么, 用在, 找谁, 现在}], '补了更准': [...], '可以不补': [...], '时间'}。"""
    root = Path(root)
    chk = chk or tools.check(root)
    t = DT.PRE if tools.report_type(root) == DT.PRE else DT.DESIGN
    figs = FIG_ASK if t == DT.PRE else FIG_ASK_DESIGN
    need, better, skip, own = [], [], [], []
    items = list(chk['按文件'].items())
    annex = DT.annex_root(root)
    if annex:                                      # 设计专篇：附件册的待办（缺图、待填）一起列
        items += [('附件册/' + f, v) for f, v in tools.check(annex)['按文件'].items() if f != '项目信息.md']
    for f, v in items:
        todo = v.get('待办', [])
        if f == '项目信息.md':
            for x in todo:
                m = re.search(r'“(.+?)”还没填', x)
                key = m.group(1) if m else x
                if t == DT.DESIGN and key in OWN_KEYS:
                    if key not in own:
                        own.append(key)
                    continue
                need.append({'缺什么': key, '用在': '封面、第1章', '找谁': '建设单位', '现在': '空着'})
            continue
        for x in todo:
            m = re.match(r'^第2章没有(.+?)（', x)
            if m and m.group(1) in figs:
                a, used, who = figs[m.group(1)]
                need.append({'缺什么': a, '用在': used, '找谁': who, '现在': '报告里没有%s' % m.group(1)})
        blanks = [x for x in todo if '【待填' in x]
        if not blanks:
            continue
        base = f[len('附件册/'):] if f.startswith('附件册/') else f
        ch = (('附件册附件%d' if f != base else '第%d章') % int(base[:2])) if base[:2].isdigit() else f
        if '类比' in f and t == DT.PRE:
            need.append({'缺什么': ANALOG[0], '用在': ANALOG[1], '找谁': ANALOG[2], '现在': '%s留了 %d 处待填' % (ch, len(blanks))})
            continue
        labels = []
        for x in blanks:
            for lab in re.findall(r'【待填[：:]?([^】]*)】', x):
                if lab and lab not in labels:
                    labels.append(lab[:20])
        need.append({'缺什么': '、'.join(labels[:3]) or '%s待填的内容' % ch, '用在': ch, '找谁': '建设单位',
                     '现在': '留了 %d 处待填' % len(blanks)})
    rec = {}
    for vol in DT.volumes(root):                   # 配图记录：设计专篇两卷各有一份
        p = Path(vol) / '图' / '配图记录.json'
        try:
            rec.update(json.loads(_read(p)) if p.exists() else {})
        except ValueError:
            pass
    w = rec.get('风频表') or {}
    if w.get('代用站') and _still_proxy(root, w.get('站', '')):
        better.append({'缺什么': '当地气象站近三年全年和夏季风向频率统计', '现在': '用的是最近的国家气象站%s（离项目约 %s 公里）%s～%s年的数据。'
                       % (w.get('站', ''), w.get('距离', ''), (w.get('年份') or ['?'])[0], (w.get('年份') or ['?'])[-1])})
    for name, r in rec.items():
        if not isinstance(r, dict) or name == '风频表':
            continue
        if r.get('类别') == '区域位置图' and not r.get('审图号'):
            src = r.get('资料', '')
            src = '初设' if '初' in src else '可研' if '可研' in src or '可行性' in src else '资料'
            need.append({'缺什么': '带审图号的区域位置图（现在的图引自%s，图上没有审图号）' % src,
                         '用在': '第2.1节' if t == DT.PRE else '附件册第2.1节', '找谁': '建设单位或设计单位', '现在': '先用了%s第%s页的图' % (src, r.get('页', ''))})
    for x in _bullets(_read(root / '资料' / '岗位因素表.md'), '按不利情况定的'):
        k, _, rest = x.partition('：')
        better.append({'缺什么': k, '现在': (re.match(r'^.{0,80}?。', rest) or re.match(r'^.{0,80}', rest)).group(0) or k})
    for x in _bullets(_read(root / '资料' / '口径.md'), GAP[t][0]):
        k, _, used = x.partition('：')
        if not any(k in b['缺什么'] + b['现在'] for b in better):     # “补了更准”里已经说了的不再列
            skip.append({'缺什么': k, '用在': used})
    return {'项目': tools.project_name(root), '要补': need, '补了更准': better, '可以不补': skip,
            '时间': time.strftime('%Y-%m-%d %H:%M'), '类型': t, '本公司填': own}


def _cell(s):
    return str(s).replace('|', '\\|').replace('\n', ' ')


def write(root, chk=None):
    """写 输出/资料欠缺清单.md，返回 collect 的结果。"""
    d = collect(root, chk)
    out = ['# %s 资料欠缺清单' % d['项目'], '',
           '（%s 程序汇总。内部用，不进报告。资料补来后在工作台项目页点“补充资料”。）' % d['时间'], '',
           '## 要补（报告里现在空着或缺图）', '']
    if d['要补']:
        out += ['| 序号 | 缺什么 | 用在 | 找谁 | 现在报告里 |', '|---|---|---|---|---|']
        out += ['| %d | %s | %s | %s | %s |' % (i, _cell(x['缺什么']), _cell(x['用在']), _cell(x['找谁']), _cell(x['现在']))
                for i, x in enumerate(d['要补'], 1)]
    else:
        out.append('没有。')
    if d.get('本公司填'):
        out += ['', '## 交稿前本公司填（设计单位自己的，不找建设单位）', '', '- ' + '、'.join(d['本公司填'])]
    out += ['', '## 补了更准（报告里现在按不利情况写）', '']
    out += ['- %s：%s' % (x['缺什么'], x['现在']) for x in d['补了更准']] or ['没有。']
    out += ['', '## 可以不补（%s）' % GAP[d.get('类型', DT.PRE)][1], '']
    out += ['- %s%s' % (x['缺什么'], '（%s）' % x['用在'] if x['用在'] else '') for x in d['可以不补']] or ['没有。']
    tools.write(Path(root) / '输出' / FILE, '\n'.join(out) + '\n')
    return d
