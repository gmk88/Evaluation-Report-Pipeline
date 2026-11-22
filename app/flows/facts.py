"""事实库：从资料原文摘事实，汇成 资料/事实库.md 和 资料/口径.md，再对照原文补漏一遍（摘两遍互相对照）。

摘事实、汇总的结果和核对进度先存进 资料/事实片段/接着做/（转资料时不当资料），任务没做完就停了的，下次接着做：
11-01 田林那次汇总完了、核对时网络那头连着断了两次，摘事实和汇总（约 7 美元）白做了。"""
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config, llm, tools
from ..tools import doctype as DT
from . import common as C

HEADS = ['项目概况（名称、性质、规模、建设地点、总投资、进度）', '选址与周边（周边企业和设施）', '总平面布置', '产品、原辅材料及用量',
         '生产工艺', '主要设备（与危害有关的：数量、密闭、噪声、温度）', '建筑卫生学（采暖、通风、空调、采光照明）', '辅助用室',
         '劳动定员与班制（各岗位人数、班制、作业方式）', '职业病防护设施（防尘、防毒、防噪、防暑等）', '个人使用的职业病防护用品',
         '应急救援（报警、喷淋洗眼、急救用品、救援依托）', '职业卫生管理（机构、制度、经费）', '检修、化验等非正常作业']
# 设计专篇另要的几节（WS/T 882—2013 附录C、第7.3.1条、第7.4.7条）：事实库从预评价拷来，只对缺的节补摘
HEADS_DESIGN = ['竖向布置（各建构筑物标高、楼层、放散热量或有害气体设备的布置、含挥发性介质管道走向）',
                '建设施工组织（施工工艺、施工期、施工人数、施工机械、作业场所）',
                '预制舱及房间尺寸（长宽高、容积、门窗、设备布置）',
                '设计概算中职业卫生相关费用（通风、空调、报警、个体防护、警示标识、辅助用室等的建筑工程费、设备及工器具费、安装工程费、其他费用）',
                '暖通设计（各房间、舱体的通风方式、换气次数、风机型号和风量、事故通风、空调）',
                '照明设计（各场所照度、应急照明）',
                '给排水及地下池体（化粪池、隔油池、事故油池的容积、尺寸、人孔、通气管）',
                '检测报警配置（检测对象、设置位置、数量、报警设定值、联锁）',
                '主要技术经济指标和同行业水平']


def heads_of(t):
    """事实库按哪些节摘：预评价是 HEADS；设计专篇再加 HEADS_DESIGN。"""
    return HEADS + (HEADS_DESIGN if t != DT.PRE else [])


ONE_CALL = 320000      # 资料总字数不超过这个数，一次摘完；超过就分批（一份资料太长的切开，每批不超过 BATCH 字）
BATCH = 150000
# 设计专篇事实库有 23 节，同样多的资料摘出来的字多：11-06 新材料 7 万字一次摘、11-07 200MW 22.6 万字一次摘都写不完（截断、断流）。
# 设计专篇一次给的资料按 DESIGN_BATCH 算；某一批还是被截断的，切成两半重摘（最多切 SPLIT_DEPTH 层）
DESIGN_ONE_CALL = DESIGN_BATCH = 60000
SPLIT_DEPTH = 2
CHECK_BATCH = 100000   # 核对时一次给多少字资料：田林一次给了 35.6 万字（整份可研），两次都没回完就断了；7 万字的那次正常
DRAFT = '事实片段/接着做'  # 资料/事实片段/接着做/：摘事实、汇总、核对的中间结果（转资料时不当资料），事实库写好后删掉
# 核对重来几次还没成的批，事实库开头记一行（写作时模型看得到，交稿说明从这里读）
SKIPPED = '<!-- 没核对：第 %s 批（共 %d 批；网络那头断了），这几批资料里的事实只摘了一遍 -->'
SKIPPED_RE = r'<!-- 没核对：第 ([\d、]+) 批'

RULES = '''摘录要求：
- 按下面这些标题分节，每节用“## 标题”开头（资料里没有的标题照样列出，下面写“- 资料没有写”）：
%s
- 每条一行：“- 事实（出处：资料名 节号或节标题）”。数字、名称、单位照原文，不改写、不推算、不合并成你的概括。
- 摘全：凡是影响危害因素识别、接触人员、接触时间、防护设施和管理判定的都要摘，宁多勿漏。设备表、定员表、原辅材料表逐行摘。
- 不摘：财务收益分析、电气、土建结构计算、环保排放指标、消防、安全生产内容。总投资、职业卫生投资照摘。
- 几份资料内容重复的（同一份可研的不同提取版本），只摘一次，出处写较完整的那份。
- 同一事实前后说法不一的，两种都摘，各注出处。''' % '\n'.join('  ## ' + h for h in HEADS)

KOUJING = '''口径表写法（另起一节输出）：
# 口径
## 说法不一
｜项目｜说法1（出处）｜说法2（出处）｜采用｜理由｜ 的表格。更具体、更新的资料优先（初设优于可研，设备表、布置图优于概述文字）；不影响危害识别的矛盾只写一次，按可研原文写。
## 可研没有写的
影响危害识别和评价、资料里确实没有的事项，每项一行：“- 事项：影响到第几章”。章号照“报告各章”写（应急救援、个体防护、职业卫生管理各有一章，
采暖、通风属于第2章建筑卫生学），拿不准的写影响哪方面，不猜章号。写作时按“可研没有写……”写进评价并提补充措施。'''

KOUJING_DESIGN = '''口径表写法（另起一节输出）：
# 口径
## 说法不一
｜项目｜说法1（出处）｜说法2（出处）｜采用｜理由｜ 的表格。更具体、更新的资料优先（施工图优于初设，初设优于可研，设备表、布置图优于概述文字）。
## 设计文件没有写的
影响职业病防护设施设计、设计文件（可研、初设）里确实没有的设施参数，每项一行：“- 事项：影响到第几章”。
写作时由专篇按标准写成设计要求（参数算出最低值，厂家才能定的写“采购技术协议应满足……”），不写“初设没有写”。'''


def rules_of(t):
    """摘事实的要求（节标题按类型）。"""
    return RULES if t == DT.PRE else RULES.replace('\n'.join('  ## ' + h for h in HEADS), '\n'.join('  ## ' + h for h in heads_of(t)), 1)


def koujing_of(t):
    return KOUJING if t == DT.PRE else KOUJING_DESIGN


FIX = {
    'type': 'object', 'additionalProperties': False, 'required': ['补充', '更正'],
    'properties': {
        '补充': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['标题', '条目'],
                                          'properties': {'标题': {'type': 'string'}, '条目': {'type': 'string'}}}},
        '更正': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False, 'required': ['原文', '新文', '说明'],
                                          'properties': {'原文': {'type': 'string'}, '新文': {'type': 'string'}, '说明': {'type': 'string'}}}},
    },
}


def _batches(src, size=BATCH):
    out, cur, n = [], [], 0
    for name, text in src:
        if n + len(text) > size and cur:
            out.append(cur)
            cur, n = [], 0
        cur.append((name, text))
        n += len(text)
    if cur:
        out.append(cur)
    return out


def _pieces(src, size):
    """超过 size 字的资料按行切成几段差不多长的，段名注明第几段（核对时一次给的资料别太长）。"""
    out = []
    for name, text in src:
        k = -(-len(text) // size)
        if k <= 1:
            out.append((name, text))
            continue
        cuts, start = [], 0
        for i in range(1, k):
            want = len(text) * i // k
            cut = text.rfind('\n', start + 1, want + 1)
            cuts.append(cut if cut > start else want)
            start = cuts[-1]
        bounds = [0] + cuts + [len(text)]
        out += [('%s（第%d段，共%d段）' % (name, i + 1, k), text[bounds[i]:bounds[i + 1]]) for i in range(k)]
    return out


def _too_long(e):
    """调用因为回答写不完没成（被截断）：llm.call 报“输出太长被截断”，不重来。"""
    return isinstance(e, llm.CallError) and '输出太长被截断' in str(e)


def _extract_batch(job, label, sysmsg, part, task, depth=0):
    """摘一批资料。回答写不完被截断的，把这批资料切成两半各摘一遍（最多切 SPLIT_DEPTH 层），摘录拼在一起，
    汇总时再去重合并。资料只有一份、切不开的，按字数对半切。"""
    docs = '\n\n'.join(C.doc(n, t) for n, t in part)
    try:
        return llm.call_retry(job, label, sysmsg, docs + '\n\n' + task, effort='high', phase='事实')
    except llm.CallError as e:
        if not _too_long(e) or depth >= SPLIT_DEPTH:
            raise
    total = sum(len(t) for _, t in part)
    halves = _batches(_pieces(part, -(-total // 2)), -(-total // 2))
    if len(halves) < 2:
        raise llm.CallError('%s：资料切不开还是写不完' % label, retry=False)
    job.info('%s 回答太长被截断，资料切成 %d 份重摘' % (label, len(halves)))
    return '\n\n'.join(_extract_batch(job, '%s-%d' % (label, k + 1), sysmsg, h, task, depth + 1) for k, h in enumerate(halves))


def _stamp(src):
    """资料的指纹（各份名称和字数）：中间结果只在资料没变时接着用。"""
    return '<!-- 资料：%s -->' % '；'.join('%s %d 字' % (n, len(t)) for n, t in src)


def _draft_get(root, name, stamp):
    p = Path(root) / '资料' / DRAFT / name
    if p.is_file():
        first, _, rest = tools.read(p).partition('\n')
        if first == stamp:
            return rest
    return None


def _draft_put(root, name, stamp, text):
    tools.write(Path(root) / '资料' / DRAFT / name, stamp + '\n' + text)


def _norm(line):
    """比两条事实是不是同一条：去掉开头的“- ”、末尾的出处和空白。"""
    s = re.sub(r'[（(]\s*出处[:：].*?[）)]\s*[。；]?\s*$', '', line.strip().lstrip('-').strip())
    return re.sub(r'\s+', '', s)


def _insert(fact_md, head, line):
    """把一条事实插到“## 标题”那一节的末尾；找不到这一节就在文末新开一节。"""
    lines = fact_md.split('\n')
    key = head.strip().lstrip('#').strip()[:6]
    start = next((i for i, ln in enumerate(lines) if ln.startswith('## ') and ln[3:].strip().startswith(key)), None)
    item = line if line.startswith('- ') else '- ' + line
    if start is None:
        return fact_md.rstrip('\n') + '\n\n## %s\n%s\n' % (head.strip().lstrip('#').strip(), item)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('#')), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, item)
    return '\n'.join(lines)


def _fix_ok(fact_md, edits):
    """核对时的“更正”只许把一整条事实换成另一整条：原文是事实库里以“- ”开头的整行，新文也以“- ”开头、不含节标题。
    试审时模型把节标题填进原文、旧行填进新文，三个节标题被换成了事实。返回 (能改的, [(不合格的, 原因)])。"""
    lines = set(fact_md.split('\n'))
    good, bad = [], []
    for e in edits:
        old, new = (e.get('原文') or '').strip('\n'), (e.get('新文') or '').strip('\n')
        olds, news = old.split('\n'), new.split('\n')
        if not all(x.startswith('- ') and x in lines for x in olds):
            bad.append((e, '原文不是事实库里的整条事实'))
        elif not all(x.startswith('- ') for x in news) or any(x.lstrip().startswith('#') for x in news):
            bad.append((e, '新文不是以“- ”开头的整条事实'))
        else:
            good.append(dict(e, 原文=old, 新文=new))
    return good, bad


def build(job, root):
    root = Path(DT.main_root(root))         # 设计专篇附件册：事实库是主报告的
    t = tools.report_type(root)
    RULES, KOUJING = rules_of(t), koujing_of(t)  # noqa: N806  按类型换节标题和口径表写法
    src = tools.source_texts(root)
    if not src:
        raise RuntimeError('没有资料文字：先把可研等资料放进项目的 资料/ 并转成文字')
    total = sum(len(t) for _, t in src)
    job.info('资料 %d 份，共 %d 字' % (len(src), total))
    stamp = _stamp(src)
    sysmsg = C.system(t)
    docs = lambda part: '\n\n'.join(C.doc(n, t) for n, t in part)  # noqa: E731
    chapters = C.outline(root, level=1, tables=False).replace('报告各章标题和表题（核对章节号、表号用）', '报告各章')
    out = _draft_get(root, '汇总.md', stamp)
    one_call, batch = (ONE_CALL, BATCH) if t == DT.PRE else (DESIGN_ONE_CALL, DESIGN_BATCH)
    whole = '先输出一行 <<<事实库>>>，接着写“# 事实库”和各节；再输出一行 <<<口径>>>，接着写口径表。\n' + KOUJING
    if out is not None:
        job.info('接着上次做：摘事实、汇总上次已做完，这次不重做')
    elif total <= one_call:
        try:
            out = llm.call_retry(job, '摘事实', sysmsg, docs(src) + '\n\n' + chapters + '\n\n任务：从上面的资料摘事实，写事实库。\n' + RULES +
                                 '\n\n' + whole, effort='high', phase='事实')
        except llm.CallError as e:
            if not _too_long(e):
                raise
            out = None                   # 一次写不完：下面按分批摘、再汇总
            job.info('摘事实一次写不完，改成分批摘、再汇总')
        if out is not None:
            _draft_put(root, '汇总.md', stamp, out)
    if out is None:
        def one(ip):
            # 每批的指纹按这一批里的各段算：分批的办法变了，旧的一批对不上，就重摘
            name, bstamp = '第%d批.md' % (ip[0] + 1), _stamp(ip[1])
            got = _draft_get(root, name, bstamp)
            if got is None:
                got = _extract_batch(job, '摘事实 第%d批' % (ip[0] + 1), sysmsg, ip[1], '任务：从上面这批资料摘事实'
                                     '（资料太长的分段给，出处的资料名不写“第几段”）。\n' + RULES)
                _draft_put(root, name, bstamp, got)
            else:
                job.info('接着上次做：摘事实 第%d批上次已做完' % (ip[0] + 1))
            return got

        parts = _batches(_pieces(src, batch), batch)   # 一份资料太长的也切开：一次给的太多，网络那头容易断
        if len(parts) < 2:
            parts = _batches(_pieces(src, -(-total // 2)), -(-total // 2))   # 一次写不完才走到这里：至少切两批
        with ThreadPoolExecutor(min(len(parts), 4)) as ex:
            frags = list(ex.map(one, enumerate(parts)))
        out = _merge(job, sysmsg, frags, chapters, t, root=root)
        _draft_put(root, '汇总.md', stamp, out)
    sec = llm.sections(out, ['事实库', '口径'])
    fact_md, kj = sec.get('事实库', ''), sec.get('口径', '')
    if fact_md.count('\n- ') < 20:
        raise RuntimeError('事实库只摘出 %d 条，资料可能没转好' % fact_md.count('\n- '))
    # 第二遍：对照原文补漏、改错。资料切成每批不超过 CHECK_BATCH 字；每核完一批存一次进度，断了从下一批接着做
    checks = _batches(_pieces(src, CHECK_BATCH), CHECK_BATCH)
    cstamp = _stamp([x for b in checks for x in b])      # 核对进度只在分批办法没变时接着用
    added = fixed = start = 0
    skipped = []          # 重来几次还没成的批：按第一遍的事实接着写，事实库开头和交稿说明里写明
    prog = _draft_get(root, '核对.md', cstamp)
    m = re.match(r'<!-- 已核对 (\d+)/(\d+) 批，补 (\d+) 条，改 (\d+) 条(?:，没核对 ([\d、]+))? -->\n', prog or '')
    if m and int(m.group(2)) == len(checks):
        start, added, fixed = int(m.group(1)), int(m.group(3)), int(m.group(4))
        skipped = [int(x) for x in (m.group(5) or '').split('、') if x]
        fact_md = prog[m.end():]
        job.info('接着上次做：核对已做完 %d 批（共 %d 批）' % (start, len(checks)))
    for i in range(start, len(checks)):
        job.check_cancel()
        try:
            res = _check_one(job, sysmsg, docs(checks[i]), fact_md, i, len(checks), RULES)
        except llm.CallError as e:
            if not e.retry:
                raise           # 没有密钥、拒答、请求本身不对：后面每一步都会这样，停下
            skipped.append(i + 1)
            job.info('核对事实库 第%d批没成，先按第一遍摘的事实接着做（交稿说明里会写明）：%s' % (i + 1, str(e)[:100]))
            res = {'补充': [], '更正': []}
        have = {_norm(ln) for ln in fact_md.split('\n') if ln.startswith('- ')}
        for x in res['补充']:
            if _norm(x['条目']) in have:          # 分批核对时，前一批已经补过的不再补
                continue
            have.add(_norm(x['条目']))
            fact_md = _insert(fact_md, x['标题'], x['条目'])
            added += 1
        good, bad = _fix_ok(fact_md, res['更正'])
        fact_md, done, failed = tools.apply_edits(fact_md, good)
        fixed += len(done)
        for e, why in bad + failed:
            job.info('事实库更正没改：%s（%s）' % ((e.get('原文') or '')[:40], why))
        if i + 1 < len(checks):
            _draft_put(root, '核对.md', cstamp, '<!-- 已核对 %d/%d 批，补 %d 条，改 %d 条%s -->\n%s' % (
                i + 1, len(checks), added, fixed, '，没核对 ' + '、'.join(map(str, skipped)) if skipped else '', fact_md))
    head = (SKIPPED % ('、'.join(map(str, skipped)), len(checks)) + '\n') if skipped else ''
    tools.write(root / '资料' / '事实库.md', head + fact_md.strip() + '\n')
    tools.write(root / '资料' / '口径.md', (kj.strip() or '# 口径') + '\n')
    _draft_clear(root)
    n = fact_md.count('\n- ')
    job.info('事实库 %d 条（核对时补 %d 条、改 %d 条%s）' % (n, added, fixed, '；第 %s 批没核对' % '、'.join(map(str, skipped)) if skipped else ''))
    return {'事实': n, '补充': added, '更正': fixed, '没核对': skipped}


MERGE_ONE = 60000      # 各批摘录合起来不超过这个字数，汇总一次写完；超过按节分组写
MERGE_GROUP = 4        # 分组汇总时每次写几节
MERGE_TOKENS = 100000  # 分组汇总每次回答最多多少 token（含思考；11-07 200MW：每组输出 8～39k token，40000 有两组被截）；口径表用默认上限（思考占 3 万 token，可见字只 9 千）
# 11-07 200MW：5 批摘录 11.7 万字一次汇总，模型把每批摘录各自当成一份“# 事实库”原样重写了 6 遍，口径表写到一半又从头来过，
# 60 分钟 23.6 万字后断流。输入里每批摘录开头的“# 事实库”标题要去掉，汇总只由一个标题统领
RE_TITLE = re.compile(r'(?m)^#\s*事实库\s*\n?')


def _dedupe_heads(text):
    """分组汇总的回答：去掉“# 事实库”标题，同名的“## 节”只留第一次出现的。"""
    text = RE_TITLE.sub('', text)
    parts = re.split(r'(?m)^(?=## )', text)
    seen, out = set(), []
    for x in parts:
        m = re.match(r'## (.*)', x)
        if m:
            k = re.sub(r'\s+', '', m.group(1))
            if k in seen:
                continue
            seen.add(k)
        out.append(x)
    return ''.join(out).strip('\n')


def _merge_group(job, sysmsg, body, group, label, depth=0):
    """汇总事实库里的几节。回答写不完（被截断）的，这几节分成两半各写一遍。"""
    task = (body + '这一次只写下面这几节（别的节另写，不写“# 事实库”标题，不写口径表）：\n' + '\n'.join('  ## ' + h for h in group) +
            '\n先输出一行 <<<事实库>>>，接着写这几节，每节只写一次。')
    try:
        r = llm.call_retry(job, '汇总事实库 ' + label, sysmsg, task, effort='high', phase='事实', max_tokens=MERGE_TOKENS)
    except llm.CallError as e:
        if not _too_long(e) or len(group) < 2 or depth >= 1:
            raise
        h = (len(group) + 1) // 2
        return '\n\n'.join(_merge_group(job, sysmsg, body, g, label + x, depth + 1) for g, x in ((group[:h], 'a'), (group[h:], 'b')))
    return _dedupe_heads(llm.sections(r, ['事实库']).get('事实库', ''))


def _merge(job, sysmsg, frags, chapters, t, root=None):
    """把各批摘录汇成事实库和口径表。摘录不多（MERGE_ONE 字以内）的一次写完；多的按节分组写（每组几节、并行），
    再单独写口径表（看全部摘录，比对各处的说法）。返回带 <<<事实库>>>、<<<口径>>> 标记的全文。"""
    frags = [RE_TITLE.sub('', f) for f in frags]
    body = ('\n\n'.join(C.doc('第%d批摘录' % (i + 1), f) for i, f in enumerate(frags)) + '\n\n' + chapters +
            '\n\n任务：把各批摘录汇成一份事实库：同样分节，去重合并，保留出处，不要漏条。\n')
    koujing = koujing_of(t)
    if sum(len(f) for f in frags) <= MERGE_ONE:
        try:
            return llm.call_retry(job, '汇总事实库', sysmsg, body + '先输出一行 <<<事实库>>>，接着写“# 事实库”和各节；'
                                  '再输出一行 <<<口径>>>，接着写口径表。\n' + koujing, effort='high', phase='事实')
        except llm.CallError as e:
            if not _too_long(e):
                raise
    heads = heads_of(t)
    groups = [heads[i:i + MERGE_GROUP] for i in range(0, len(heads), MERGE_GROUP)]
    job.info('汇总事实库：%d 节分 %d 组写，再单独写口径表' % (len(heads), len(groups)))
    base = hashlib.sha1('\n'.join(frags).encode('utf-8')).hexdigest()[:12]

    def cached(name, key, fn):
        """每组、口径表写好就存进 资料/事实片段/接着做/，断了接着做时只重写没写完的（11-07 200MW：口径那次被截，十组白写，约 10 美元）。"""
        stamp = '<!-- 摘录 %s %s -->' % (base, key)
        got = _draft_get(root, name, stamp) if root is not None else None
        if got is not None:
            job.info('接着上次做：%s 上次已写好' % name[:-3])
            return got
        got = fn()
        if root is not None:
            _draft_put(root, name, stamp, got)
        return got

    def group_of(i, g):
        return cached('汇总_组%d.md' % (i + 1), '|'.join(g), lambda: _merge_group(job, sysmsg, body, g, '%d/%d' % (i + 1, len(groups))))

    def kj_of():
        r = llm.call_retry(job, '汇总事实库 口径', sysmsg, body + '这一次只写口径表（不写事实库）：先输出一行 <<<口径>>>，'
                           '接着写口径表（比对全部摘录的说法）。\n' + koujing, effort='high', phase='事实')
        return llm.sections(r, ['口径']).get('口径', '').strip()

    with ThreadPoolExecutor(min(len(groups) + 1, 4)) as ex:
        futs = [ex.submit(group_of, i, g) for i, g in enumerate(groups)]
        kj = ex.submit(cached, '汇总_口径.md', '口径', kj_of)
        facts = [f.result() for f in futs]
        sk = kj.result()
    return '<<<事实库>>>\n# 事实库\n\n%s\n<<<口径>>>\n%s\n' % ('\n\n'.join(x for x in facts if x), sk)


def skipped_checks(root):
    """事实库开头记下的没核对的批号（交稿说明用）；都核对过的返回空。"""
    p = Path(root) / '资料' / '事实库.md'
    m = re.match(SKIPPED_RE, tools.read(p)) if p.exists() else None
    return m.group(1) if m else ''


def _check_one(job, sysmsg, part, fact_md, i, n, rules=RULES):
    """第二遍核对的一批：part＝这批资料原文（已拼好），fact_md＝当前的事实库。"""
    return llm.call_retry(job, '核对事实库%s' % ('' if n == 1 else ' 第%d批' % (i + 1)), sysmsg,
                          part + '\n\n' + C.doc('事实库（第一遍）', fact_md) +
                          '\n\n任务：逐节对照上面这批资料原文核对事实库（资料只给了一部分的，只核这部分写到的内容）。\n'
                          '- 漏摘的（尤其设备表、定员表、原辅材料表的行，防护设施、个体防护、应急设施的每一项）写进“补充”：'
                          '标题照抄事实库的节标题，条目写成“事实（出处：资料名 节号）”，资料名不写“第几段”；事实库里已有的不再补。\n'
                          '- 摘错的（数字、名称与原文不符）写进“更正”：原文照抄事实库里那一整行（以“- ”开头），'
                          '新文写改对的整行（也以“- ”开头），说明写改了什么。不改节标题。\n'
                          '没有就返回空列表。' + rules.split('\n', 1)[1], effort='high', schema=FIX, phase='事实')


def _draft_clear(root):
    """事实库写好以后，删掉摘事实、汇总、核对的中间结果（新报告的写作记录不动）。"""
    d = Path(root) / '资料' / DRAFT
    for p in [d / '汇总.md', d / '核对.md'] + (sorted(d.glob('第*批.md')) + sorted(d.glob('汇总_*.md')) if d.is_dir() else []):
        p.unlink(missing_ok=True)
    drop_empty(root)


def drop_empty(root):
    """中间结果文件夹空了就删（两层）。"""
    d = Path(root) / '资料' / DRAFT
    for x in (d, d.parent):
        try:
            x.rmdir()
        except OSError:
            break


EXTRA_TASK = '''任务：现有事实库（从承接的预评价项目拷来）缺下面这几节。只为这几节从上面的资料摘事实，别的节不摘、不改。
%s
- 每节用“## 标题”开头，标题照抄上面的写法（资料里没有的标题照样列出，下面写“- 资料没有写”）。
- 每条一行：“- 事实（出处：资料名 节号或节标题）”。数字、名称、单位照原文，不改写、不推算；表格（尺寸表、概算表、暖通表）逐行摘。
- 上面只给了事实库现有各节的标题和条数：这几节要的事实一律照资料摘（别的节可能也有，照样摘进这几节，程序会去掉完全相同的条目）。
输出：只输出这几节的 Markdown（从第一个“## ”开始），不要“# 事实库”标题，不要别的说明。'''


def missing_heads(fact_md, heads):
    """事实库里还没有的节（按节标题前 6 个字比，和 _insert 一样）。"""
    have = [ln[3:].strip() for ln in fact_md.split('\n') if ln.startswith('## ')]
    return [h for h in heads if not any(x.startswith(h[:6]) for x in have)]


def _head_counts(fact_md):
    """事实库各节的标题和条数。"""
    out = []
    for ln in fact_md.split('\n'):
        if ln.startswith('## '):
            out.append([ln[3:].strip(), 0])
        elif ln.startswith('- ') and out:
            out[-1][1] += 1
    return out


def build_extra(job, root, heads):
    """增量补摘：只对事实库里缺的节，从资料原文摘一遍，补进事实库末尾（设计专篇从预评价拷来事实库后用）。
    资料太长的分批摘，各批的同一节合到一起。返回 {'补了': [节], '条数': n}。"""
    root = Path(root)
    fp = root / '资料' / '事实库.md'
    fact_md = tools.read(fp) if fp.is_file() else '# 事实库\n'
    want = missing_heads(fact_md, heads)
    if not want:
        job.info('事实库里这几节都有了，不用补摘')
        return {'补了': [], '条数': 0}
    src = tools.source_texts(root)
    if not src:
        raise RuntimeError('没有资料文字：补摘事实库要先有资料/文本/')
    task = EXTRA_TASK % '\n'.join('  ## ' + h for h in want)
    parts = [[x for x in b] for b in _batches(_pieces(src, DESIGN_BATCH), DESIGN_BATCH)]
    stamp = _stamp(src) + '<!-- 补摘：%s -->' % '；'.join(want)
    got = _draft_get(root, '补摘.md', stamp)
    if got is None:
        # 只带事实库的节标题和条数（看有什么就够了）：11-05 田林每批都带整份事实库（11 万字），11 批花了 32 美元
        have_heads = '\n'.join('%s（%d 条）' % (h, n) for h, n in _head_counts(fact_md))
        outs = []
        for i, part in enumerate(parts):
            job.check_cancel()
            label = '补摘事实' + ('' if len(parts) == 1 else ' 第%d批' % (i + 1))
            outs.append(_extract_batch(job, label, C.system(tools.report_type(root)), part,
                                       C.doc('事实库现有的节（只看有什么，不改）', have_heads) + '\n\n' + task))
        got = '\n\n'.join(outs)
        _draft_put(root, '补摘.md', stamp, got)
    else:
        job.info('接着上次做：补摘事实上次已做完')
    n, cur = 0, None
    have = {_norm(ln) for ln in fact_md.split('\n') if ln.startswith('- ')}
    for ln in got.split('\n'):
        s = ln.strip()
        if s.startswith('## '):
            key = s[3:].strip()
            cur = next((h for h in want if key.startswith(h[:6]) or h.startswith(key[:6])), None)
            if cur and not any(x.startswith('## ' + cur[:6]) for x in fact_md.split('\n')):
                fact_md = fact_md.rstrip('\n') + '\n\n## %s\n' % cur
        elif cur and s.startswith('- ') and _norm(s) not in have:
            have.add(_norm(s))
            fact_md = _insert(fact_md, cur, s)
            n += 1
    for h in want:                                     # 模型漏了的节也列上，写作时看得到“资料没有写”
        if not any(x.startswith('## ' + h[:6]) for x in fact_md.split('\n')):
            fact_md = fact_md.rstrip('\n') + '\n\n## %s\n- 资料没有写\n' % h
    tools.write(fp, fact_md.rstrip('\n') + '\n')
    (root / '资料' / DRAFT / '补摘.md').unlink(missing_ok=True)
    drop_empty(root)
    job.info('事实库补摘 %d 节、%d 条' % (len(want), n))
    return {'补了': want, '条数': n}


def ensure(job, root):
    """项目还没有事实库时先建一份（审稿、改稿核对事实用）。没有资料文字的，跳过。附件册看主报告的事实库。"""
    root = DT.main_root(root)
    if (Path(root) / '资料' / '事实库.md').exists():
        return None
    if not tools.source_texts(root):
        job.info('项目没有资料文字（资料/文本/ 或 提取文本/），事实只能对照正文前后核对')
        return None
    job.info('项目还没有事实库，先从资料建一份（只做一次）')
    return build(job, root)


def job_facts(job):
    return build(job, job.project)


__all__ = ['build', 'build_extra', 'ensure', 'job_facts', 'HEADS', 'HEADS_DESIGN', 'heads_of', 'config']
