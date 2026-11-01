"""各流程共用：提示词片段、材料拼装、按“原文→新文”改文件、机器检查清零。"""
import re
import time
from pathlib import Path

from .. import config, llm, tools

REF = config.ROOT / 'references'
ROLE = '''你是晋城市安泰职业卫生健康技术咨询有限公司的职业病危害预评价报告编写助手。报告给评审专家和建设单位看。
基本规矩（“用户口径”有更细的规定，以它为准）：
- 事实只取给你的资料：数字、名称照资料写，不编、不推算。
- 可研没写的，写“可研没有写……”并提补充措施；不写【待核】【待填】、待确认、资料未提供、核实后。
- 只评职业病危害，不写消防、安全生产、防寒防冻、放射性内容。
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


def card(method, fname):
    ch = fname[:2]
    p = config.STD / '各章要点' / (('03_%s.md' % method) if ch == '03' else ch + '.md')
    return tools.read(p) if p.exists() else ''


def doc(title, text):
    return '<资料 名称="%s">\n%s\n</资料>' % (title, (text or '').strip())


def join(parts):
    return '\n\n'.join(x for x in parts if x)


def system(*parts):
    """系统提示：角色＋用户口径＋其他标准文件（空的不放）。"""
    out = [ROLE]
    k = std('用户口径.md')
    if k:
        out.append(doc('用户口径（最高优先）', k))
    for title, text in parts:
        if text:
            out.append(doc(title, text))
    return '\n\n'.join(out)


def write_format():
    return ('正文写法格式', tools.read(REF / '正文写法格式.md'))


def fact_docs(root, with_source=False, limit=450000):
    """事实材料：事实库、口径表、岗位因素表；没有事实库或要求带原文时附可研等资料原文（总字数不超过 limit）。"""
    fx = tools.facts(root)
    out = [doc(n, t) for n, t in fx]
    if with_source or not any(n == '事实库.md' for n, _ in fx):
        src = tools.source_texts(root)
        if sum(len(t) for _, t in src) <= limit:
            out += [doc('资料原文 ' + n, t) for n, t in src]
    return out


def names_table(root):
    """名称以此为准：岗位因素表（有的话）或第1章表1.8-1、第4章因素识别表。"""
    p = Path(root) / '资料' / '岗位因素表.md'
    if p.exists():
        return doc('岗位因素表（名称以此为准）', tools.read(p))
    found = []
    # 表4.1-2 是工种表：第二次试审时没给它，第7章工种行的因素名被照表4.1-1 改了
    for f in sorted((Path(root) / '正文').glob('0[14]*.md')):
        found += tools.tables_by_caption(tools.read(f), r'^表(1\.8-1|4\.1-[12])\s')
    return doc('评价单元和因素表（名称以此为准）', '\n\n'.join(found)) if found else ''


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


def fix_format(job, fname, text, rounds=4):
    """写进文件之前先把 Markdown 格式改对（表格格数、注释结束符），免得整份正文读不了、别的章没法检查。"""
    for r in range(rounds):
        err = tools.parse_error(text, fname)
        if not err:
            return text
        job.check_cancel()
        res = llm.call_retry(job, '改格式 %s' % fname, system(write_format()),
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

    for r in range(1, rounds + 1):
        es = errs()
        if not es:
            return 0
        job.check_cancel()
        text = tools.read(Path(root) / '正文' / fname)
        content = join([doc('本章要点卡', card(method, fname)), doc('正文/' + fname + '（带行号）', tools.numbered(text)),
                        '机器检查报的错误：\n' + '\n'.join('- ' + e for e in es),
                        '任务：把这些错误改掉。“通用规则禁用”“项目规则禁用”的，照用户口径改写说法，不换近义词躲检查；'
                        '引用了不存在的表，查是表号写错还是表漏了，改对；计数句、表格格式照“正文写法格式”（' + FORMAT_HELP + '）。'
                        '改不了的写进“跳过”。\n' + EDIT_RULES])
        res = llm.call_retry(job, '清零 %s 第%d轮' % (fname, r), system(write_format()), content, effort='medium', schema=EDITS)
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
