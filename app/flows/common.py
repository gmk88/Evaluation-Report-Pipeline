"""各流程共用：提示词片段、材料拼装、按“原文→新文”改文件、机器检查清零。"""
import re
import time
from pathlib import Path

from .. import config, llm, tools
from ..tools import doctype as DT

REF = config.ROOT / 'references'
ROLE = '''你是晋城市安泰职业卫生健康技术咨询有限公司的职业病危害预评价报告编写助手。报告给评审专家和建设单位看。
基本规矩（“用户口径”有更细的规定，以它为准）：
- 事实只取给你的资料：数字、名称照资料写，不编、不推算。
- 可研没写的，写“可研没有写……”并提补充措施；不写【待核】【待填】、待确认、资料未提供、核实后。
- 只评职业病危害，不写消防、安全生产、防寒防冻、放射性内容。
- 不写编者口吻、方法取舍理由、资料边界说明，正文不堆砌条号。中文书面语，短句。'''
# 设计专篇（主报告、附件册）：缺口规矩和预评价反过来。专篇本身是设计文件，设计文件没写的设施由专篇按标准写成设计要求
ROLE_DESIGN = '''你是晋城市安泰职业卫生健康技术咨询有限公司的职业病防护设施设计专篇编写助手。专篇按 WS/T 882—2013 编写，安泰署名为设计单位，
给评审专家、建设单位和施工、采购单位看。
基本规矩（“用户口径”“设计专篇口径”有更细的规定，设计专篇口径优先）：
- 事实（项目概况、工艺、设备、定员、布置、尺寸）只取给你的资料、事实库和承接的预评价报告；数字、名称照资料写，不编。
- 设计文件（可研、初设）没写的职业病防护设施，专篇按标准写成设计要求：名称、规格型号、数量、设置位置、技术参数及控制性能、依据条款
  （WS/T 882—2013 第7.4.2条）。参数按标准和舱体、房间尺寸算出最低值，计算过程写进附件册计算书；设备固有参数要有出处（同类设备资料、类比实测、标准条款），
  没有出处的写“采购时要求厂家提供××保证值和测试报告”，需要时写“采购技术协议应满足……”并给出处；接触限值（85 dB(A)、WBGT 限值、OEL）不作设备或作业位置的采购指标。
- 指标要切实际、验收时能核：设计值先对照源强，做不到的不写达标值，改写措施、接触时间控制和个体防护（口径 D17）；验收核不了的不进设施表（口径 D18）。
- 读者要一页看懂：每章先给结论，一段只说一件事，并列要求用（1）（2）条目，不用行首“- ”，表格窄而短（口径 D19）。
- 不写“初设没有写”“可研没有写”“由设计单位确认”“待取得”这类说法；不写【待核】【待填】、待确认、资料未提供、核实后。
- 只写职业病防护，不写消防、安全生产内容；防寒、电离辐射等不设置的防护设施类别，在第4.3.5节逐类写不设置及理由；
  第1.3节、第3.1.5节按骨架各写一句，理由和第4.3.5节一致；别的章节不写。
- 评价单元、工作场所、工种、危害因素名称照岗位因素表，和预评价报告一字不差；预评价措施与本专篇设施的逐条对应只写在附件册附件6，主报告设施表不写预评价条目号。
- 施工过程的职业病危害和防护措施要写（WS/T 882—2013 第7.3.1条、第7.4.3.1条）。
- 不写编者口吻、方法取舍理由、资料边界说明，正文不堆砌条号。中文书面语，短句。'''
EDIT_RULES = '''改动的写法：
- “原文”照抄文件里的原样文字（不带行号，一个字都不改），要长到在文件里只出现一次，一般取整行或整句。
- “新文”是替换后的文字。删一整行时，原文写这一行连同它的上一行，新文只写上一行。
- 只改要改的地方，前后文要读得通；表格行的格数不能变。'''
EDITS = {
    'type': 'object', 'additionalProperties': False, 'required': ['改动', '跳过'],
    'properties': {
        '改动': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['原文', '新文', '说明'],
            'properties': {'原文': {'type': 'string'}, '新文': {'type': 'string'}, '说明': {'type': 'string'}}}},
        '跳过': {'type': 'array', 'items': {'type': 'string'}},
    },
}
FORMAT_HELP = ('表格每行的格数要和表头一样，合并格用 ^ 或 < 占位，格内竖线写成 \\|；'
               '注释要有结束符 -->，结束符后面不接正文；表格要有分隔行（如 |---|---|）。')


def std(name):
    p = config.STD / name
    return tools.read(p) if p.exists() else ''


def std_path(t, key):
    """报告类型配置里的标准文件（doctype.conf 的“卡目录”“审稿清单”“类型口径”）：“标准/”开头的跟着 config.STD 走。"""
    v = DT.conf(t).get(key)
    if not v:
        return None
    return config.STD / v[len('标准/'):] if v.startswith('标准/') else config.ROOT / v


def type_std(t, key):
    p = std_path(t, key)
    return tools.read(p) if p and p.is_file() else ''


def card(t, method, fname=None, root=None):
    """本章要点卡。card(类型, 方法, 文件名)；老的写法 card(方法, 文件名) 照旧是预评价的卡。
    预评价第3章按方法分卡；设计专篇主报告按章号 01～05，附件册各文件用 附件.md。
    设计专篇给了 root 的：卡末尾加上承接的预评价用什么方法（第3章预期接触水平、第5章表5.2-1、附件册附件3/4 的写法跟着变）。"""
    if fname is None:
        t, method, fname = DT.PRE, t, method
    ch = fname[:2]
    if t == DT.PRE:
        p = config.STD / '各章要点' / (('03_%s.md' % method) if ch == '03' else ch + '.md')
    else:
        p = std_path(t, '卡目录') / ('附件.md' if t == DT.ANNEX else ch + '.md')
    text = tools.read(p) if p.exists() else ''
    if t != DT.PRE and root is not None and (t == DT.ANNEX or ch in DESIGN_METHOD_CH):
        note = design_method_note(root)
        if note:
            text = text.rstrip('\n') + '\n\n' + note + '\n'
    return text


# 设计专篇哪几章的写法跟预评价方法变（第3章预期接触水平、第5章预期效果；附件册附件3、附件4 照全册给）
DESIGN_METHOD_CH = ('03', '05')
DESIGN_METHOD_NOTE = {
    '风险评估法': '''## 本项目的方法（承接的预评价用风险评估法）
- 预评价没有类比项目，第3章是 GBZ/T 298—2017 半定量风险评估（相似接触组、HR、ER、R，可研方案状态和落实补充措施后两种状态）。
- 表3.3-1 按“工种（岗位）×因素”归并，列措施前预期水平，“推算依据”写“预评价报告表3.x-x 风险评估（R＝×）”或物料平衡、计算（附件册附件4）；
  预期值写数值范围，预评价只给了风险等级、没给浓度的，按物料平衡、设备参数或 GBZ/T 211、同类设备经验数据推算；逐地点的推算明细只在附件册表4.1-1（措施前、措施后两列）。
- 表5.2-1 同样按“工种（岗位）×因素”归并，列采取设计措施后的预期水平、职业接触限值、主要措施（只引第4章表号项号）和预期结论，不复述表3.3-1 的行，也没有“类比值”列
  （本项目没有类比检测值）；措施后的水平按第4章设施控制性能推算，不能直接写成限值本身。
- 风险等级（R）只作“采取设计措施前”的定性参考，预期结论按 GBZ 2.1、GBZ 2.2 限值判（口径 D12：写“符合”；推算超限的先看工程措施，再写“限制接触时间后符合”；噪声仍超限的写“超限，须个体防护”，个体防护不计入限值比较，化学因素和高温不得用）；不写“风险可接受”“风险可控”。
- 附件册附件3 第3.3节写“预评价风险评估结果摘要”（相似接触组、因素、两种状态的风险等级，取预评价表3.5-1），不写类比项目检测摘要；
  表题改成“预评价风险评估结果摘要”。不重做风险评估过程。''',
    '类比法': '''## 本项目的方法（承接的预评价用类比法）
- 类比值取预评价第3章类比项目的检测结果（写类比项目名称和报告编号）；类比项目没测的因素按计算或经验数据推算。''',
}


def design_method_note(root):
    """设计专篇承接的预评价用的方法（项目登记的“方法”或预评价第3章标题），给要点卡加的说明；判不出返回空。"""
    pre = DT.pre_root(DT.main_root(root))
    if pre is None:
        return ''
    return DESIGN_METHOD_NOTE.get(tools.method_of(pre), '')


def doc(title, text):
    return '<资料 名称="%s">\n%s\n</资料>' % (title, (text or '').strip())


def join(parts):
    return '\n\n'.join(x for x in parts if x)


def role(t):
    return ROLE if t == DT.PRE else ROLE_DESIGN


def system(*parts):
    """系统提示：角色＋用户口径＋其他标准文件（空的不放）。第一个参数可以是报告类型（system(类型, (题, 文)…)）：
    设计专篇换角色，用户口径后面再叠 标准/设计专篇/口径.md（K 号在专篇里怎么变，以它为准）。"""
    t = DT.PRE
    if parts and isinstance(parts[0], str):
        t, parts = parts[0], parts[1:]
    out = [role(t)]
    k = std('用户口径.md')
    if k:
        out.append(doc('用户口径（最高优先）' if t == DT.PRE else '用户口径（在设计专篇里有变化的，以下面“设计专篇口径”为准）', k))
    if t != DT.PRE:
        kt = type_std(t, '类型口径')
        if kt:
            out.append(doc('设计专篇口径（用户口径在设计专篇里的变化，与用户口径不一致时以它为准）', kt))
    for title, text in parts:
        if text:
            out.append(doc(title, text))
    return '\n\n'.join(out)


def write_format():
    return ('正文写法格式', tools.read(REF / '正文写法格式.md'))


def fact_docs(root, with_source=False, limit=450000):
    """事实材料：事实库、口径表、岗位因素表；有类比资料的加上类比事实；没有事实库或要求带原文时附可研等资料原文（总字数不超过 limit）。"""
    fx = tools.facts(root)
    # 设计专篇：事实库、岗位因素表是从预评价拷来的，里面“可研没有写……”是预评价阶段的说法，不照搬进专篇
    note = '（承接预评价阶段的说法：“可研没有写……”类句子不进专篇正文，按标准写成设计要求）' if tools.is_design(root) else ''
    out = [doc(n + (note if n in ('事实库.md', '岗位因素表.md') else ''), t) for n, t in fx]
    analog = tools.analog_facts(root)
    if analog:
        # 类比事实是别的项目的：只给第3章和引用类比结果的地方用，不当本项目的事实
        # （设计专篇：从预评价拷来，第3.3节预期接触水平、第5章预期效果和附件册类比检测资料摘要用）
        out.append(doc('类比事实.md（类比项目的，不是本项目的事实）', analog))
    if with_source or not any(n == '事实库.md' for n, _ in fx):
        src = tools.source_texts(root)
        if sum(len(t) for _, t in src) <= limit:
            out += [doc('资料原文 ' + n, t) for n, t in src]
    return out


def names_table(root):
    """名称以此为准：岗位因素表（有的话）或第1章表1.8-1、第4章因素识别表。
    设计专篇用从预评价拷来的岗位因素表（附件册用主报告的）；没有时用第1章表1.3-1、第3章表3.1-1/3.1-2。"""
    main = Path(DT.main_root(root))
    p = main / '资料' / '岗位因素表.md'
    if p.exists():
        return doc('岗位因素表（名称以此为准）', tools.read(p))
    found = []
    if tools.is_design(root):
        for f in sorted((main / '正文').glob('0[13]*.md')):
            found += tools.tables_by_caption(tools.read(f), r'^表(1\.3-1|3\.1-[12])\s')
        return doc('单元、场所和因素表（名称以此为准）', '\n\n'.join(found)) if found else ''
    # 表4.1-2 是工种表：第二次试审时没给它，第7章工种行的因素名被照表4.1-1 改了
    for f in sorted((Path(root) / '正文').glob('0[14]*.md')):
        found += tools.tables_by_caption(tools.read(f), r'^表(1\.8-1|4\.1-[12])\s')
    return doc('评价单元和因素表（名称以此为准）', '\n\n'.join(found)) if found else ''


def pre_docs(root, fname, whole=True):
    """设计专篇写、审某个文件时承接的预评价正文（doctype.pre_files 定哪几个文件）。whole＝假时只给摘要。
    预评价不是设计专篇，或流水线设置.json 里没记承接的预评价，返回空列表。"""
    t = tools.report_type(root)
    pre = DT.pre_root(root)
    if t == DT.PRE or not pre:
        return []
    out = []
    info = tools.read(pre / '项目信息.md') if (pre / '项目信息.md').is_file() else ''
    keys = [ln for ln in info.split('\n') if re.match(r'^- (项目名称|报告编号|报告日期|评价单位|建设单位)：', ln)]
    if keys:   # 第1.1节、第1.2.3节、第2.1.7节要写预评价报告的编号、日期和评价单位
        out.append(doc('承接的预评价报告 项目信息', '\n'.join(keys)))
    files = tools.body_files(pre)
    texts = {f: tools.read(pre / '正文' / f) for f in files}
    titles = {f: '\n'.join(ln for ln in x.split('\n') if ln.startswith('#')) for f, x in texts.items()}
    for f in DT.pre_files(t, fname, files, titles):       # 第2章按节名找文件（各项目第2章分文件的办法不同）
        text = texts[f]
        out.append(doc('承接的预评价报告 正文/%s（%s）' % (f, '全文' if whole else '摘要'), text if whole else tools.digest(text)))
    for key, sec in DT.pre_sections(t, fname):
        for f in [x for x in files if x.startswith(key)]:      # 一章分几个文件的（04_1、04_2），节在哪个文件取哪个
            m = re.search(r'^## %s\s.*?(?=^## |^# |\Z)' % re.escape(sec), tools.read(pre / '正文' / f), re.M | re.S)
            if m:
                out.append(doc('承接的预评价报告 正文/%s 第%s节' % (f, sec), m.group(0) if whole else tools.digest(m.group(0))))
                break
    return out


def vol_files(root):
    """设计专篇两卷的正文文件 [(标签, 卷文件夹, 文件名)]：主报告的标签就是文件名，附件册的是“附件册/文件名”。
    预评价只有一卷。"""
    out = []
    for i, vol in enumerate(DT.volumes(root)):
        out += [(('附件册/' if i else '') + f, Path(vol), f) for f in tools.body_files(vol)]
    return out


def outline(root, level=3, tables=True):
    """全文的各级标题和表题，几千字。审稿、摘事实时核对“第几章”“第几节”“表几”用：
    只给一章时模型看不到别的章，曾把对的“同第2.2节”“按第5章”改成了错的。"""
    out = []
    for f in tools.body_files(root):
        for ln in tools.read(Path(root) / '正文' / f).split('\n'):
            s = ln.strip().replace('**', '')
            if re.match(r'^#{1,%d} ' % level, s) or (tables and re.match(r'^[表图]\s*\d+(?:\.\d+)*-\d+', s)):
                out.append(s[:80])
    return doc('报告各章标题和表题（核对章节号、表号用）', '\n'.join(out)) if out else ''


def guard(root, edits, hints=False):
    """改后新带出机器检查词的改动不改（原文里本来就有的不算）。hints＝要人工确认的“提示”词也算：
    审稿的一条“建议”曾往小结里加了“防寒”。返回 (能改的, [(改动, 原因)])。"""
    words = tools.rule_words(root, hints=hints)
    ok, bad = [], []
    for e in edits:
        old, new = e.get('原文') or '', e.get('新文') or ''
        hit = [label for label, test in words if test(new) and not test(old)]
        if hit:
            bad.append((e, '改后带出检查词%s，没改' % '、'.join(hit[:3])))
        else:
            ok.append(e)
    return ok, bad


def apply(job, root, fname, edits, label):
    """把改动写进 正文/fname（改坏表格格式的、新带出禁用词的那一处不改），记日志。返回 (改成几处, 没改成的说明)。"""
    p = Path(root) / '正文' / fname
    edits, held = guard(root, edits)
    text, done, failed = tools.apply_edits(tools.read(p), edits, fname)
    failed = held + failed
    if done:
        tools.write(p, text)
    bad = ['%s（%s）' % ((e.get('原文') or '')[:30], why) for e, why in failed]
    job.info('%s：改 %d 处%s' % (label, len(done), '，没改成 %d 处' % len(bad) if bad else ''))
    return len(done), bad


def fix_format(job, fname, text, rounds=4, t=DT.PRE):
    """写进文件之前先把 Markdown 格式改对（表格格数、注释结束符），免得整份正文读不了、别的章没法检查。
    t：报告类型（设计专篇换角色提示，不带预评价的“可研没有写”规矩）。"""
    text = re.sub(r'(<!--[ \t]*公式[^>\n]*?)--[ \t]+>', r'\1-->', text)   # 照骨架示范抄来的“-- >”，先自动并上
    for r in range(rounds):
        err = tools.parse_error(text, fname)
        if not err:
            return text
        job.check_cancel()
        res = llm.call_retry(job, '改格式 %s' % fname, system(t, write_format()),
                             join([doc(fname + '（带行号）', tools.numbered(text)), '格式错误：' + err,
                                   '任务：改掉这个格式错误。' + FORMAT_HELP + '\n' + EDIT_RULES]),
                             effort='medium', schema=EDITS)
        text, done, _ = tools.apply_edits(text, res['改动'])
        if not done:
            break
    return text


def file_errors(job, root, fname):
    """这个文件的机器检查错误。别的文件格式读不了时全文检查做不了，等它改好（最多 3 分钟）。"""
    c = tools.check(root)
    for _ in range(36):
        if not c.get('格式错误') or fname in c['按文件']:
            break
        job.check_cancel()
        time.sleep(5)
        c = tools.check(root)
    return tools.errors_of(c, fname)


def plain(msg):
    """错误信息去掉行号，改前改后好比较。"""
    return re.sub(r'^第\d+行 ', '', msg)


def fix_errors(job, root, fname, method, rounds=2, only=None):
    """机器检查报的错误交给模型改，直到这个文件错误为 0 或轮数用完。
    only＝这些错误（去掉行号的信息）不管：改稿时只清自己带出来的错误。"""
    def errs():
        return [e for e in file_errors(job, root, fname) if not only or plain(e) not in only]

    t = tools.report_type(root)

    for r in range(1, rounds + 1):
        es = errs()
        if not es:
            return 0
        job.check_cancel()
        text = tools.read(Path(root) / '正文' / fname)
        content = join([doc('本章要点卡', card(t, method, fname, root=root)), doc('正文/' + fname + '（带行号）', tools.numbered(text)),
                        '机器检查报的错误：\n' + '\n'.join('- ' + e for e in es),
                        '任务：把这些错误改掉。“通用规则禁用”“项目规则禁用”的，照用户口径改写说法，不换近义词躲检查；'
                        '引用了不存在的表，查是表号写错还是表漏了，改对；计数句、表格格式照“正文写法格式”（' + FORMAT_HELP + '）。'
                        '改不了的写进“跳过”。\n' + EDIT_RULES])
        res = llm.call_retry(job, '清零 %s 第%d轮' % (fname, r), system(t, write_format()), content, effort='medium', schema=EDITS)
        n, _ = apply(job, root, fname, res['改动'], '清零 ' + fname)
        if not n:
            break
    return len(errs())


def sample_chapter(sample, fname):
    """样板项目里同一章的文件：先按同名前缀（如 02_1），没有再按章号。"""
    if not sample:
        return []
    files = sorted((Path(sample) / '正文').glob('*.md'))
    key = re.match(r'^\d\d(_\d)?', fname).group(0)
    hit = [p for p in files if p.name.startswith(key + ('' if '_' in key else '_'))] or \
          [p for p in files if p.name[:2] == fname[:2]]
    return [(p.name, tools.read(p)) for p in hit]
