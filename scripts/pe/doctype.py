"""报告类型：预评价、设计专篇（主报告）、设计专篇附件册。出稿程序（scripts/）和各流程（app/）都从这里取，
不再各自判断章号、模板、骨架。

项目信息.md 写“- 报告类型：设计专篇”的是设计专篇，没写的按预评价（老项目不用改）。
设计专篇分两卷（WS/T 882—2013 第8.1.3条：技术性过程编入附件、单独成册）：
    专篇项目文件夹/             主报告：项目信息.md、检查规则.md、正文/01～05、资料/（事实库等，两卷共用）、图/、输出/
    专篇项目文件夹/附件册/      附件册：项目信息.md（报告类型：设计专篇附件册，其余项目信息从主报告继承）、
                                检查规则.md、正文/、图/、输出/。没有自己的 资料/，用主报告的。
附件册对各程序来说就是一个普通项目文件夹（检查、出稿、审稿、改稿照常），只是资料和项目信息取主报告的。
设计专篇的“预评价项目”（承接的预评价报告文件夹）记在主报告的 流水线设置.json：{"预评价": "文件夹完整路径"}。
"""
import json
import re
from pathlib import Path

PRE = '预评价'
DESIGN = '设计专篇'
ANNEX = '设计专篇附件册'
TYPES = (PRE, DESIGN, ANNEX)
ANNEX_DIR = '附件册'
SETTINGS_FILE = '流水线设置.json'
_PIPE = Path(__file__).resolve().parents[2]          # 流水线文件夹

RE_TYPE = re.compile(r'^\s*[-*]?\s*报告类型\s*[：:]\s*(\S+)', re.M)


def type_of(root):
    """项目文件夹的报告类型：项目信息.md 的“报告类型”；没写或写错的按预评价。"""
    p = Path(root) / '项目信息.md'
    try:
        text = re.sub(r'<!--.*?-->', '', p.read_text(encoding='utf-8'), flags=re.S)
    except OSError:
        return PRE
    m = RE_TYPE.search(text)
    return m.group(1) if m and m.group(1) in TYPES else PRE


def type_of_info(info):
    t = (info or {}).get('报告类型', '')
    return t if t in TYPES else PRE


def main_root(root):
    """附件册的主报告文件夹（资料、项目信息、流水线设置取这里）；别的类型就是自己。"""
    root = Path(root)
    return root.parent if type_of(root) == ANNEX else root


def annex_root(root):
    """设计专篇主报告的附件册文件夹（存在才返回）；别的类型返回 None。"""
    root = Path(root)
    if type_of(root) != DESIGN:
        return None
    a = root / ANNEX_DIR
    return a if (a / '项目信息.md').exists() else None


def volumes(root):
    """一份报告的各卷：设计专篇是 [主报告, 附件册]（附件册存在时），别的是 [自己]。"""
    root = Path(root)
    a = annex_root(root)
    return [root, a] if a else [root]


def settings(root):
    p = Path(main_root(root)) / SETTINGS_FILE
    try:
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    except ValueError:
        return {}


def pre_root(root):
    """设计专篇承接的预评价项目文件夹（流水线设置.json 的“预评价”，有 正文/ 才算）；没有返回 None。"""
    v = settings(root).get('预评价')
    if not v:
        return None
    p = Path(v).expanduser()
    return p if (p / '正文').is_dir() else None


# ---------------- 各类型的配置 ----------------
# 名称后缀：出稿文件名、Word 文档标题；页眉第二行：页眉右下；章：一级标题依据（检查只报提示）
CONF = {
    PRE: {
        '名称后缀': '职业病危害预评价报告',
        '页眉第二行': '职业病危害预评价报告',
        '关键词': '职业病危害预评价;职业病防护设施三同时',
        '模板': 'templates/预评价报告模板.docx',
        '骨架': {'类比法': 'templates/项目骨架', '风险评估法': 'templates/项目骨架_风险评估法'},
        '依据': 'GBZ/T 196—2025',
        '章': [('总论',), ('建设项目工程分析', '现有企业概况及建设项目工程分析'),
              ('类比企业（项目）调查与分析', '职业健康风险评估分析与评价', '类比企业（项目）调查与分析或职业健康风险评估分析与评价'),
              ('职业病危害因素及危害程度分析',), ('职业病防护设施分析与评价',), ('应急救援措施的分析与评价',),
              ('个人使用的职业病防护用品的分析与评价',), ('职业卫生管理的分析与评价',), ('职业病危害关键控制点分析',),
              ('职业病防治措施的补充建议',), ('结论与建议',)],
        '卡目录': '标准/各章要点',
        '审稿清单': '标准/审稿清单.md',
        '类型口径': '',
        '类型检查规则': '',
    },
    DESIGN: {
        '名称后缀': '职业病防护设施设计专篇',
        '页眉第二行': '职业病防护设施设计专篇',
        '关键词': '职业病防护设施设计专篇;职业病防护设施三同时',
        '模板': 'templates/设计专篇模板.docx',
        '骨架': {'': 'templates/项目骨架_设计专篇'},
        '依据': 'WS/T 882—2013 附录C',
        '章': [('概述',), ('建设项目概况及工程分析',), ('职业病危害因素分析及危害程度预测',),
              ('职业病防护设施设计',), ('预期效果评价',)],
        '卡目录': '标准/设计专篇/各章要点',
        '审稿清单': '标准/设计专篇/审稿清单.md',
        '类型口径': '标准/设计专篇/口径.md',
        '类型检查规则': '标准/设计专篇/检查规则.md',
    },
    ANNEX: {
        '名称后缀': '职业病防护设施设计专篇资料性附件册',
        '页眉第二行': '职业病防护设施设计专篇资料性附件册',
        '关键词': '职业病防护设施设计专篇;资料性附件册',
        '模板': 'templates/设计专篇附件册模板.docx',
        '骨架': {'': 'templates/项目骨架_设计专篇/附件册'},
        '依据': 'WS/T 882—2013 第8.1.3条',
        '章': None,                     # 附件册一级标题是“附件N ……”，不对章名
        '卡目录': '标准/设计专篇/各章要点',
        '审稿清单': '标准/设计专篇/审稿清单.md',
        '类型口径': '标准/设计专篇/口径.md',
        '类型检查规则': '标准/设计专篇/检查规则.md',
    },
}


def conf(t):
    return CONF.get(t, CONF[PRE])


def path_of(t, key):
    """配置里的相对路径换成流水线文件夹下的绝对路径（没配的返回 None）。"""
    v = conf(t).get(key)
    return _PIPE / v if v else None


def skeleton(t, method=''):
    sk = conf(t)['骨架']
    v = sk.get(method) or sk.get('') or sk.get('类比法')
    return _PIPE / v


# ---------------- 设计专篇的写作先后、承接的预评价章 ----------------
# 键是正文文件名前缀（取最长匹配），值是要先写好的文件前缀。附件册各文件在主报告全部写好后再写。
DEPS = {
    PRE: {'02': [], '03': ['02'], '04': ['02', '03'], '08': ['02'], '05': ['02', '04'], '06': ['02', '04'], '07': ['02', '04'],
          '09': ['03', '04', '05', '06', '07'], '10': ['03', '04', '05', '06', '07', '08', '09'],
          '11': ['03', '04', '05', '06', '07', '08', '09', '10'], '01': ['03', '04', '05', '06', '07', '08', '09', '10']},
    DESIGN: {'02': [], '03': ['02'],
             # 04_1 表4.2-2 换气次数引 04_2 的事故通风；表4.1-1“详见”列要引 04_3、04_4 的表号项号，所以排在它们后面
             '04_1': ['02', '03', '04_2', '04_3', '04_4'],
             '04_2': ['02', '03'], '04_3': ['02', '03'],
             '04_4': ['02', '03', '04_2'], '04_5': ['02', '03', '04_1', '04_2', '04_3', '04_4'],
             '05': ['03', '04'], '01': ['02', '03', '04', '05']},
    ANNEX: {'04': ['02']},   # 附件4 的舱室净容积取附件2 表2.2-1，要一字不差
}

# 写设计专篇某个文件时，整章给模型看的预评价正文（文件名前缀）。名称、因素、措施从预评价承接。
PRE_INPUT = {
    DESIGN: {'01': ['01'], '02_1': ['02_1'], '02_2': ['02_2', '02_3', '02_4'], '03': ['02_3', '03', '04', '06', '09'],
             '04_1': ['02_2', '02_4', '05', '10'], '04_2': ['03', '05', '10'], '04_3': ['06', '10'],
             '04_4': ['02_4', '07', '10'], '04_5': ['08', '09', '10', '11'], '05': ['03', '04', '11']},
    ANNEX: {'01': ['01', '02_1'], '02': ['02_1', '02_2', '02_3'], '03': ['03', '04'], '04': ['02_4', '03', '04'],
            '05': ['05', '06', '07', '10'], '06': ['09', '10'], '07': ['08', '10'], '08': ['05', '06', '10']},
}


# 再给的预评价单节（文件名前缀, 节号）：整章太大、只用其中一节的。如写第3章要承接预评价第1.4节（施工期、放射性的范围说明）
# 和第10.7节（施工期措施），写第1章、表2.1-1 要预评价结论里的风险类别（第11.1节）。
PRE_SECTIONS = {
    DESIGN: {'01': [('11', '11.1')], '02_1': [('11', '11.1')], '03': [('01', '1.4'), ('10', '10.7')],
             '04_2': [('04', '4.3')]},
    ANNEX: {'04': [('10', '10.7')]},
}


# 附件册是主报告第4章的逐条展开：写这几个文件时给主报告对应文件的全文（别的主报告文件只给摘要，摘要每格截到 40 字会丢参数）
ANNEX_MAIN_FULL = {'04': ['03', '04_2', '04_3', '05'], '05': ['04'], '06': ['04_5'], '07': ['04_2', '04_3', '04_4', '04_5'], '08': ['04_2', '04_3']}


def annex_main_full(fname, main_files):
    """写附件册 fname 时要整篇给的主报告文件。"""
    k = match_key(ANNEX_MAIN_FULL, fname)
    want = ANNEX_MAIN_FULL.get(k, []) if k else []
    return [f for f in main_files if any(f.startswith(w) for w in want)]


def pre_sections(t, fname):
    """写 fname 时另给的预评价单节 [(文件名前缀, 节号)]；已整章给了的那一章不再给。"""
    table = PRE_SECTIONS.get(t, {})
    k = match_key(table, fname)
    whole = PRE_INPUT.get(t, {}).get(match_key(PRE_INPUT.get(t, {}), fname) or '', [])
    return [(f, sec) for f, sec in (table.get(k, []) if k else []) if not any(f.startswith(w) for w in whole)]


def match_key(table, fname):
    """fname 在 table 里最长匹配的键（'04_2_xx.md' → '04_2'，没有再 '04'）。"""
    keys = sorted((k for k in table if fname.startswith(k)), key=len, reverse=True)
    return keys[0] if keys else None


def deps_of(t, fname, files):
    """fname 要等哪些文件先写好（files 是本卷全部正文文件名）。"""
    table = DEPS.get(t, DEPS[PRE])
    k = match_key(table, fname)
    pre = table.get(k, []) if k else []
    return [f for f in files if f != fname and any(f.startswith(p) for p in pre)]


def pre_files(t, fname, pre_files_all, titles=None):
    """写 fname 时给模型的预评价正文文件。第2章按节名找（PRE_TOPICS）：各预评价第2章分文件的办法不一样
    （田林 02_4＝建筑卫生学；200MW 02_4＝生产设备、02_5＝建筑卫生学；烧结 02_1 前面多一节现有企业概况）。
    titles＝{文件名: 各级标题连成的文字}，给了就按节名对第2章；没给或一个也没对上，照 PRE_INPUT 的文件名前缀。"""
    table = PRE_INPUT.get(t, {})
    k = match_key(table, fname)
    want = table.get(k, []) if k else []
    topics = PRE_TOPICS.get(t, {}).get(match_key(PRE_TOPICS.get(t, {}), fname) or '', [])
    ch2 = [f for f in pre_files_all if f.startswith('02')]
    hit = [f for f in ch2 if titles and any(re.search(p, titles.get(f, '')) for p in topics)]
    if topics and hit:
        want = [w for w in want if not w.startswith('02')]
        return [f for f in pre_files_all if f in hit or (not f.startswith('02') and any(f.startswith(p) for p in want))]
    return [f for f in pre_files_all if any(f.startswith(p) for p in want)]


# 第2章各文件要的预评价节（节名的正则）：节号各项目不同，按节名找所在文件
PRE_TOPICS = {
    DESIGN: {'02_1': [r'工程概况'], '02_2': [r'选址', r'总体布局', r'原辅材料', r'生产工艺', r'生产设备', r'建筑卫生', r'辅助用室'],
             '03': [r'生产工艺'], '04_1': [r'选址', r'总体布局', r'建筑卫生', r'辅助用室'], '04_4': [r'辅助用室']},
    ANNEX: {'01': [r'工程概况'], '02': [r'工程概况', r'选址', r'总体布局', r'原辅材料', r'生产工艺', r'生产设备'],
            '04': [r'建筑卫生', r'辅助用室']},
}


# ---------------- 设计专篇的判定写法（机器检查用） ----------------
# 采纳情况表“处理方式／采纳情况”列只能写这三档；“不采纳”的理由栏不能空（WS/T 882 第7.4.6条）
ADOPT_HEADS = ('处理方式', '采纳情况', '采纳结论')
ADOPT_LEVELS = ['采纳', '调整采纳', '不采纳']
# 预期效果表“预期结论”列：设计必须保证达标（WS/T 882 第4.2.4条），不写“不符合”；工程措施做不到的按 D17 写条件结论（口径 D12）
EXPECT_HEADS = ('预期结论',)
# “超限，须个体防护”只准用于噪声（GB 23466—2025 有佩戴后有效声级的定量方法）；个体防护的衰减不计入 GBZ 2.1、GBZ 2.2 的限值比较
EXPECT_LEVELS = ['符合', '限制接触时间后符合', '超限，须个体防护']


# ---------------- 设计专篇的篇幅预算（口径 D19；机器检查和写作提示共用） ----------------
# 按“汉字数”计（CJK 汉字，含表格，不含 HTML 注释和指令行）。键是“卷/文件名前缀”；数是 C 档基数，
# A 档＝C×0.75，B 档＝C×0.88（取整到百）。超过预算 1.2 倍机器检查给提示；写作提示写“目标约 N 字，最多不超过 1.2N”。
DESIGN_BUDGET = {
    '正文/01': 2000, '正文/02_1': 2200, '正文/02_2': 2300, '正文/03': 5500,
    '正文/04_1': 1800, '正文/04_2': 5000, '正文/04_3': 3000, '正文/04_4': 3000, '正文/04_5': 1800, '正文/05': 2200,
    '附件册/01': 2500, '附件册/02': 4000, '附件册/03': 4000, '附件册/04': 6000,
    '附件册/05': 2500, '附件册/06': 4000, '附件册/07': 1500, '附件册/08': 1500,
}
TIER_FACTOR = {'A': 0.75, 'B': 0.88, 'C': 1.0}
OVER_RATIO = 1.2


def _split_factors(s):
    return [x.strip() for x in re.split(r'[、，,；;]', s or '') if x.strip() and x.strip() not in ('—', '-', '/', '／')]


def _post_table(root):
    """资料/岗位因素表.md 的 (工作场所数 P, 危害因素数 F)；读不到返回 None。"""
    p = Path(main_root(root)) / '资料' / '岗位因素表.md'
    try:
        text = re.sub(r'<!--.*?-->', '', p.read_text(encoding='utf-8'), flags=re.S)
    except OSError:
        return None
    places, factors, ip, jf, head = set(), set(), None, None, False
    last = ''
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if re.fullmatch(r'[\s:\-|]+', line):
            continue
        if not head:
            if '工作场所' in cells and '职业病危害因素' in cells:
                ip, jf, head = cells.index('工作场所'), cells.index('职业病危害因素'), True
            continue
        if len(cells) <= max(ip, jf):
            continue
        pl = cells[ip]
        if pl in ('^', '<', ''):
            pl = last
        last = pl
        if pl:
            places.add(pl)
        for f in _split_factors(cells[jf]):
            if '无主要' not in f:
                factors.add(f)
    return (len(places), len(factors)) if places else None


def design_tier(root):
    """设计专篇的篇幅档 A／B／C：流水线设置.json 的“篇幅档”优先；没设按岗位因素表：
    工作场所数 P≤8 且危害因素数 F≤4 → A；P≤20 且 F≤8 → B；其余 C（读不到按 C）。"""
    v = str(settings(root).get('篇幅档', '')).strip().upper()
    if v in TIER_FACTOR:
        return v
    pf = _post_table(root)
    if pf is None:
        return 'C'
    p, f = pf
    return 'A' if p <= 8 and f <= 4 else ('B' if p <= 20 and f <= 8 else 'C')


def budget_for(root, rel):
    """rel（正文文件相对路径，如“正文/04_2_防护设施设计.md”“附件册/正文/04_计算书.md”，附件册自己的文件夹里写“正文/04_计算书.md”也行）
    的汉字数预算（按档折算、取整到百）；不是设计专篇的文件返回 None。"""
    root = Path(root)
    parts = Path(str(rel).replace('\\', '/')).parts
    annex = '附件册' in parts or type_of(root) == ANNEX
    if not annex and type_of(root) != DESIGN:
        return None
    name = Path(str(rel)).name
    m = re.match(r'(\d+(?:_\d+)?)', name)
    if not m:
        return None
    table = {k.split('/', 1)[1]: v for k, v in DESIGN_BUDGET.items() if k.startswith('附件册/' if annex else '正文/')}
    k = match_key(table, m.group(1))
    if k is None:
        return None
    n = int(table[k] * TIER_FACTOR[design_tier(root)] / 100.0 + 0.5) * 100      # 四舍五入（round 是银行家舍入，16.5 会变 16）
    if annex and k == '06':        # 附件6 逐条明细，行数由预评价定：预算不低于 条数×每行约 45 字
        n = max(n, int(ANNEX6_PER_ITEM * _pre_items(root) / 100.0 + 0.5) * 100)
    return n


ANNEX6_PER_ITEM = 45


def _pre_items(root):
    """承接的预评价项目里附件6 逐条覆盖的三张表的措施条数之和；读不到返回 0。"""
    pr = pre_root(main_root(root))
    if pr is None:
        return 0
    try:
        from .model import Project
        from .checks import pre_items
        pp = Project(pr)
        return sum(pre_items(pp.tables[k]) for k in PRE_ADOPT_TABLES if k in pp.tables)
    except Exception:  # noqa: BLE001
        return 0


# ---------------- 各类型的版式（出 Word 用，docxgen 取） ----------------
# 预评价照马沟预评价最终版（common.py 的常量）；设计专篇按 WS/T 882 附录D：页边距左28 右20 上25 下20 mm，
# 版心宽 9184 twip，四号字（280）加字距 26，30×306＝9180，每行 30 字；正文高 14286，固定行距 510，每页 28 行。
# 横向页（宽表用）把纵向页转 90°：装订边在上，横向版心宽 16838−1134−1418＝14286。
LAYOUT = {
    PRE: {'纵向版心宽': 8300, '横向表格宽': 16838 - 2 * 1440, '横向图宽': 13230, '行距': 490, '缩进': 560, '字距': 0},
    DESIGN: {'纵向版心宽': 9184, '横向表格宽': 14286, '横向图宽': 14286, '行距': 510, '缩进': 612, '字距': 26},
}
LAYOUT[ANNEX] = LAYOUT[DESIGN]


def layout(t):
    return LAYOUT.get(t, LAYOUT[PRE])


# ---------------- 设计专篇的数字核对（机器检查用） ----------------
ADOPT_SUMMARY = '4.8-1'                        # 主报告采纳情况汇总表（“条数”列合计＝附件册逐条明细行数）
ANNEX_ADOPT_CHAPTER = '6'                      # 附件册逐条采纳明细在附件6（表6.x-y）
PRE_ADOPT_TABLES = ('10.8-1', '10.1-1', '9.2-1')   # 附件6 逐条覆盖的预评价表
RE_ANNEX_H1 = re.compile(r'^附件\s*(\d+)\s+(.+)$')  # 附件册一级标题“附件1 设计输入资料及差异处理”
