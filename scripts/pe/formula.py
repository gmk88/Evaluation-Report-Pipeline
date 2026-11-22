"""设计专篇公式的机器检查（规格 2.5）。checks.py 的设计专篇分支调用 check(blocks, add, annex)。

add(级别, 源位置, 文字)：级别 '错误' 或 '提示'，与 checks.py 里的 add 相同。
错误：公式指令后不是单行 $$；指令写法不对（缺“编号=”）；段落、表格里写了 $…$；LaTeX 不在支持的子集内（含括号不配对）；LaTeX 里有 Unicode 上下标、全角、中文标点；
      编号格式不是“数字.数字-数字”；编号重复；编号的公式没有式中块。
提示：编号前缀与所在二级节号不一致；同节编号不连续；编号公式没被“式（x）”引用；引用的式不存在；
      式中符号与公式里的符号对不上（双向）；式中某行没有单位；附件册附件4 里公式写成了文字。
"""
import re

from . import mdparse, omml

RE_NO = re.compile(r'^(\d+)\.(\d+)-(\d+)$')
RE_CITE = re.compile(r'式\s*[（(]\s*(\d+\.\d+-\d+)\s*[）)]')
RE_BRACKET_NO = re.compile(r'[（(]\s*(\d+\.\d+-\d+)\s*[）)]')
RE_H2 = re.compile(r'^(\d+\.\d+)(?:\s|$)')
RE_ANNEX_H1 = re.compile(r'^附件\s*(\d+)')
RE_TEXT_FORMULA = re.compile(r'lg|ln|Σ|\^|√|[·⋅]')


def _plain(s):
    return (s or '').replace('**', '')


def _text_units(blocks):
    """(位置, 文字)：段落、列表、题注、表格单元格。公式块自己的式中不算。"""
    for b in blocks:
        t = b['t']
        if t in ('p', 'li', 'caption'):
            yield b['src'], _plain(b.get('text'))
        elif t == 'table':
            fname = b['src'].split(':')[0]
            for r, row in enumerate(b['rows']):
                src = '%s:%d' % (fname, b['lines'][r]) if r < len(b.get('lines', [])) else b['src']
                for cell in row:
                    if cell not in ('^', '<'):
                        yield src, _plain(cell)


def _strip_sup(s):
    return re.sub(r'\^(\{[^{}]*\}|\w)', '', s)


def check(blocks, add, annex=False):
    formulas = [b for b in blocks if b['t'] == 'formula']
    texts = list(_text_units(blocks))
    cited = set()
    for _, tx in texts:
        cited.update(RE_BRACKET_NO.findall(tx))
    for b in formulas:
        for v in b.get('vars', []):
            cited.update(RE_BRACKET_NO.findall(v['text']))
    for src, tx in texts:
        if re.search(r'\$[^$]+\$', tx):
            add('错误', src, '段落、列表或表格里写了 $…$（“%s…”）：这些位置不识别公式，Word 里会原样印出美元符和 LaTeX 源码；'
                             '改用公式块，或直接用 Unicode 上下标（如 tᵢ、m³）' % tx[:24])
    numbers = {b['no'] for b in formulas if b.get('no')}

    # ---- 逐个公式 ----
    seen, last_in_prefix = {}, {}
    h2 = ''
    for b in blocks:
        if b['t'] == 'h':
            if b['level'] == 2:
                m = RE_H2.match(_plain(b['text']).strip())
                h2 = m.group(1) if m else ''
            elif b['level'] == 1:
                h2 = ''
            continue
        if b['t'] != 'formula':
            continue
        src, no = b['src'], b.get('no')
        if b.get('bad_attr'):
            add('错误', src, b['bad_attr'])
        if b.get('latex') is None:
            add('错误', src, b.get('bad') or '公式指令后面应紧接单独一行 $$…$$')
            continue
        lx = b['latex']
        # 编号
        if no is not None:
            m = RE_NO.match(no)
            if not m:
                add('错误', src, '公式编号“%s”不是“数字.数字-数字”形式（如 4.2-1）' % no)
            else:
                if no in seen:
                    add('错误', src, '公式编号 %s 重复（第一次在 %s）' % (no, seen[no]))
                seen.setdefault(no, src)
                prefix = '%s.%s' % (m.group(1), m.group(2))
                if h2 and prefix != h2:
                    add('提示', src, '公式编号 %s 的前缀与所在二级节 %s 不一致（编号前缀＝所在二级节号）' % (no, h2))
                prev = last_in_prefix.get(prefix, 0)
                if int(m.group(3)) != prev + 1:
                    add('提示', src, '公式编号 %s 在本节里不连续（应为 %s-%d）' % (no, prefix, prev + 1))
                last_in_prefix[prefix] = int(m.group(3))
                if no not in cited:
                    add('提示', src, '式（%s）在正文里没有被引用，正文要写“按式（%s）计算”' % (no, no))
            if not b.get('where') or not b.get('vars'):
                add('错误', src, '编号的公式（式 %s）后面缺“式中：”块（每行“$符号$ —— 含义，单位”）' % no)
        # LaTeX
        bad = omml.unicode_problems(lx)
        syms = None
        if bad:
            add('错误', b.get('latex_src', src),
                'LaTeX 里有不能写的字符 %s：下标上标用 _ ^，等号用 =，标点用英文，汉字用 \\text{}' % ''.join(bad))
        else:
            try:
                omml.to_omml(lx)
                syms = omml.symbols_of(lx)
            except omml.LatexError as e:
                add('错误', b.get('latex_src', src), '公式转换失败：%s' % e)
        # 式中
        for v in b.get('vars', []):
            if not v['unit']:
                add('提示', v['src'], '式中 $%s$ 没写单位（最后一个逗号后写单位，无单位写“量纲一”）' % v['sym'])
            for is_math, seg in mdparse.split_inline(v['text']):
                if is_math:
                    try:
                        omml.to_omml(seg)
                    except omml.LatexError as e:
                        add('错误', v['src'], '式中行内公式转换失败：%s' % e)
            try:
                omml.to_omml(v['sym'])
            except omml.LatexError as e:
                add('错误', v['src'], '式中符号转换失败：%s' % e)
        if syms is not None and b.get('vars'):
            listed = {omml.norm_symbol(v['sym']): v for v in b['vars']}
            flat = omml.norm_symbol(lx)
            listed_bare = {_strip_sup(k) for k in listed}
            for s in sorted(syms):
                if s not in listed and _strip_sup(s) not in listed_bare:
                    add('提示', src, '公式里的符号 %s 没有在“式中”说明' % s)
            for k, v in listed.items():
                if k not in syms and k not in flat:
                    add('提示', v['src'], '式中符号 $%s$ 在公式里没有出现' % v['sym'])

    # ---- 引用的式是否存在 ----
    for src, tx in texts:
        for n in RE_CITE.findall(tx):
            if n not in numbers:
                add('提示', src, '引用的“式（%s）”不存在（本文件没有编号 %s 的公式）' % (n, n))

    # ---- 附件册附件4：公式写成了文字 ----
    if annex:
        h1 = ''
        for b in blocks:
            if b['t'] == 'h' and b['level'] == 1:
                m = RE_ANNEX_H1.match(_plain(b['text']).strip())
                h1 = m.group(1) if m else ''
            if h1 != '4' or b['t'] not in ('p', 'li', 'table'):
                continue
            for src, tx in _text_units([b]):
                if '＝' in tx and RE_TEXT_FORMULA.search(tx):
                    add('提示', src, '公式写成了文字（“%s…”）：改用公式块（<!-- 公式 编号=… -->＋$$…$$＋式中），'
                                     '表格和段落里写“按式（4.N-k）计算”' % tx[:24])
