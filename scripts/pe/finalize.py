"""按 PDF 的实际分页回填目录页码和页脚“共N页”，并核对；先把 PDF 文字层里的康熙部首改回统一汉字。"""
import re
import unicodedata
import zipfile
from pathlib import Path

from lxml import etree as E

from .common import W, TO_PLAIN, text_of, write_docx, update_app_stats

TOC_NAMES = ('toc 1', 'toc 2', 'toc 3')


def _c(s):
    return re.sub(r'\s+', '', (s or '').translate(TO_PLAIN))


def page_texts(pdf):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(pdf)
    pages = []
    for p in doc:
        lines = [_c(x) for x in p.get_text().split('\n')]
        pages.append([x for x in lines if x])
    return pages


# ---- PDF 文字层：康熙部首改回统一汉字（2026-10-28）----
# Mac 版 Word 导出 PDF 时，宋体（SimSunCN）里与部首同形的字，文字层的 ToUnicode 映射记成了部首字符：
# 页眉“山、气、目”记成 ⼭⽓⽬（U+2F2D、U+2F53、U+2F6C），页脚“生”记成 ⽣（U+2F63）。显示和打印用的是字形，
# 不受影响；但在 PDF 里搜索、复制，或用 WPS 把 PDF 转成 Word，得到的是部首字符。仿宋没有这个问题。
# 只改 ToUnicode 映射，页面内容、字体和排版都不动。
RADICAL_RANGES = ((0x2E80, 0x2EFF), (0x2F00, 0x2FDF), (0x3190, 0x319F), (0x31C0, 0x31EF))  # 部首补充、康熙部首、汉文训读、笔画
RE_RADICAL = re.compile('[%s]' % ''.join('%s-%s' % (chr(a), chr(b)) for a, b in RADICAL_RANGES))
RE_CMAP_SECT = re.compile(r'begin(bfchar|bfrange)(.*?)end\1', re.S)
RE_CMAP_TOK = re.compile(r'<([0-9A-Fa-f]*)>|\[([^\]]*)\]')
RE_HEX = re.compile(r'<([0-9A-Fa-f]*)>')


def _is_han(c):
    o = ord(c)
    return 0x3400 <= o <= 0x4DBF or 0x4E00 <= o <= 0x9FFF or 0x20000 <= o <= 0x3FFFF


def _unify(s):
    """部首字符换成 NFKC 对应的统一汉字；换不成单个汉字的保留原样（由 apply 报错）。"""
    out = []
    for ch in s:
        if RE_RADICAL.match(ch):
            u = unicodedata.normalize('NFKC', ch)
            if len(u) == 1 and _is_han(u):
                ch = u
        out.append(ch)
    return ''.join(out)


def _utf16(h):
    b = bytes.fromhex(h)
    return b.decode('utf-16-be') if len(b) % 2 == 0 else None


def _hex(s, like):
    h = s.encode('utf-16-be').hex()
    return h.upper() if like.isupper() else h


def _fix_cmap(text):
    """改 ToUnicode CMap 里映射到部首字符的条目，返回 (新文本, 改了几个代码)。
    <lo><hi><dst> 区间里有要改的，改写成 <lo><hi>[<d1> <d2> …]，条目数不变。看不懂的 CMap 原样返回。"""
    try:
        out, pos, n = [], 0, 0
        for m in RE_CMAP_SECT.finditer(text):
            kind, body = m.group(1), m.group(2)
            toks = list(RE_CMAP_TOK.finditer(body))
            step = 2 if kind == 'bfchar' else 3
            if RE_CMAP_TOK.sub('', body).strip() or len(toks) % step:
                return text, 0
            edits = []
            for i in range(0, len(toks), step):
                *src, dst = toks[i:i + step]
                if any(t.group(1) is None for t in src):
                    return text, 0
                if dst.group(1) is not None:
                    s = _utf16(dst.group(1))
                    if not s:
                        return text, 0
                    lo = hi = 0
                    if kind == 'bfrange':
                        lo, hi = int(src[0].group(1), 16), int(src[1].group(1), 16)
                    if hi < lo or hi - lo > 255:
                        return text, 0
                    old = [s[:-1] + chr(ord(s[-1]) + k) for k in range(hi - lo + 1)]
                    new = [_unify(x) for x in old]
                    if new != old:
                        rep = ('<%s>' % _hex(new[0], dst.group(1)) if len(new) == 1 else
                               '[%s]' % ' '.join('<%s>' % _hex(x, dst.group(1)) for x in new))
                        edits.append((dst.start(), dst.end(), rep))
                        n += sum(a != b for a, b in zip(old, new))
                elif kind == 'bfrange':
                    inner = list(RE_HEX.finditer(dst.group(2)))
                    old = [_utf16(t.group(1)) for t in inner]
                    if not old or any(x is None for x in old):
                        return text, 0
                    new = [_unify(x) for x in old]
                    if new != old:
                        edits.append((dst.start(), dst.end(),
                                      '[%s]' % ' '.join('<%s>' % _hex(x, t.group(1)) for x, t in zip(new, inner))))
                        n += sum(a != b for a, b in zip(old, new))
                else:
                    return text, 0
            if edits:
                for a, b, r in reversed(edits):
                    body = body[:a] + r + body[b:]
                out += [text[pos:m.start(2)], body]
                pos = m.end(2)
        return ''.join(out) + text[pos:], n
    except (ValueError, UnicodeError):
        return text, 0


def fix_text_layer(pdf):
    """把 PDF 文字层（ToUnicode）里的康熙部首改回统一汉字，增量保存；返回改了几个代码（0＝不用改，文件不动）。"""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(pdf)
    seen, n = set(), 0
    try:
        for x in range(1, doc.xref_length()):
            try:
                kind, val = doc.xref_get_key(x, 'ToUnicode')
            except Exception:  # noqa: BLE001
                continue
            if kind != 'xref':
                continue
            t = int(val.split()[0])
            if t in seen:
                continue
            seen.add(t)
            raw = doc.xref_stream(t)
            new, k = _fix_cmap(raw.decode('latin-1')) if raw else ('', 0)
            if k:
                doc.update_stream(t, new.encode('latin-1'))
                n += k
        if n:
            doc.saveIncr()
    finally:
        doc.close()
    return n


def locate(pages, headings):
    """正文第1页 = 页脚出现“第1页共”的第一页；各标题按顺序向后找（行首匹配优先）。"""
    first = next((i for i, ls in enumerate(pages) if any(re.search(r'第1页共\d*页', x) for x in ls)), None)
    if first is None:
        raise ValueError('PDF 里找不到“第1页 共…页”页脚，无法确定正文第1页')
    mapping, cur, missing = [], first, []
    for lvl, title in headings:
        t = _c(title)
        hit = None
        for i in range(cur, len(pages)):
            if any(x.startswith(t) for x in pages[i]):
                hit = i
                break
        if hit is None:
            for i in range(cur, len(pages)):
                if t in ''.join(pages[i]):
                    hit = i
                    break
        if hit is None:
            missing.append(title)
            mapping.append(None)
            continue
        mapping.append(hit - first + 1)
        cur = hit
    return first, mapping, missing


def pdf_toc_numbers(front, titles):
    """PDF 前置页（封面至目录）的目录上，各标题后面印的页码；找不到的给 None。
    按标题文字找，不按点线找：标题长、折成两行时点线只剩一两个点，标题也会被拆在两行（2026-10-30 新材料稿 6.1.5）。"""
    s = '\n'.join(x for ls in front for x in ls) + '\n'
    k = s.find('目录')
    pos, out = (k + 2 if k >= 0 else 0), []
    for t in titles:
        ct = _c(t)
        m = re.compile('\n?'.join(map(re.escape, ct)) + r'\n?[.…·]*(\d+)\n').search(s, pos) if ct else None
        out.append(int(m.group(1)) if m else None)
        if m:
            pos = m.end() - 1
    return out


def _styles(z):
    out = {}
    for s in E.fromstring(z.read('word/styles.xml')).findall(W + 'style'):
        n = s.find(W + 'name')
        if n is not None:
            out[s.get(W + 'styleId')] = n.get(W + 'val').lower()
    return out


def apply(docx, pdf, headings, check=False):
    """回填（check=False）或核对（check=True）。返回结果字典。
    开头先用 fix_text_layer 改 PDF 文字层里的康熙部首（只改文字映射，不动页面），再按 PDF 文字核对。"""
    errors = []
    try:
        fixed = fix_text_layer(pdf)
    except Exception as e:  # noqa: BLE001
        fixed = 0
        errors.append('PDF 文字层没能改：%s' % e)
    pages = page_texts(pdf)
    first, mapping, missing = locate(pages, headings)
    total = len(pages) - first
    z = zipfile.ZipFile(docx)
    parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    order = [i.filename for i in z.infolist()]
    names = _styles(z)
    z.close()
    doc = E.fromstring(parts['word/document.xml'])
    toc = [p for p in doc.iter(W + 'p') if p.find(W + 'pPr/' + W + 'pStyle') is not None
           and names.get(p.find(W + 'pPr/' + W + 'pStyle').get(W + 'val')) in TOC_NAMES]
    want = [(t, m) for (l, t), m in zip(headings, mapping) if l <= 3]
    bad = [i + 1 for i, ls in enumerate(pages) if any(RE_RADICAL.search(x) for x in ls)]
    if bad:
        errors.append('PDF 文字层第%s页有康熙部首等部首字符，在 PDF 里搜索、复制或转成 Word 会得到错字'
                      % '、'.join(map(str, bad[:5])) + ('等 %d 页' % len(bad) if len(bad) > 5 else ''))
    if len(toc) != len(want):
        errors.append('目录条数 %d 与标题数 %d 不一致' % (len(toc), len(want)))
    for p, (title, m) in zip(toc, want):
        ts = p.findall('.//' + W + 't')
        if m is None:
            errors.append('PDF 里找不到标题：' + title)
            continue
        if check:
            if ts[-1].text != str(m):
                errors.append('目录页码不符：%s 应为 %d，现为 %s' % (title, m, ts[-1].text))
        else:
            ts[-1].text = str(m)
    if check:
        # PDF 目录上印的页码也要核：Word 印了内存里的旧稿时，Word 文件里的页码对、PDF 上却全是 0
        got = pdf_toc_numbers(pages[:first], [t for t, _ in want])
        bad = [(t, m, g) for (t, m), g in zip(want, got) if m is not None and g != m]
        if bad:
            t, m, g = bad[0]
            errors.append('PDF 目录上印的页码有 %d 条与标题所在页不符（如“%s”应为 %d，印的是 %s）：'
                          'Word 印的可能是内存里的旧稿，关掉窗口重新打开再导出' % (len(bad), t, m, g if g is not None else '空'))
    for i in range(first, len(pages)):
        exp = '第%d页共%d页' % (i - first + 1, total)
        if not any(exp in x for x in pages[i]):
            errors.append('PDF 第%d页页脚应为“%s”' % (i + 1, exp))
            if len(errors) > 30:
                break
    if not check:
        parts['word/document.xml'] = E.tostring(doc, xml_declaration=True, encoding='UTF-8', standalone=True)
        for name in list(parts):
            if not re.match(r'word/footer\d+\.xml$', name):
                continue
            rt = E.fromstring(parts[name])
            for fld in rt.iter(W + 'fldSimple'):
                if 'PAGEREF BodyEnd' in fld.get(W + 'instr', ''):
                    for t in fld.iter(W + 't'):
                        t.text = str(total)
            # 复杂域（模板页码域每段都带格式，Word 更新域后字号不变）：separate 与 end 之间是缓存结果
            for p in rt.iter(W + 'p'):
                instr, state = '', None
                for r in p.iter(W + 'r'):
                    fc = r.find(W + 'fldChar')
                    kind = fc.get(W + 'fldCharType') if fc is not None else None
                    if kind == 'begin':
                        instr, state = '', 'instr'
                    elif kind == 'separate':
                        state = 'result'
                    elif kind == 'end':
                        state = None
                    elif state == 'instr':
                        instr += ''.join(x.text or '' for x in r.iter(W + 'instrText'))
                    elif state == 'result' and 'PAGEREF BodyEnd' in instr:
                        for t in r.iter(W + 't'):
                            t.text = str(total)
            parts[name] = E.tostring(rt, xml_declaration=True, encoding='UTF-8', standalone=True)
        update_app_stats(parts, pages=len(pages))
        write_docx(docx, {n: parts[n] for n in order})   # 打包方式与 WPS 另存一致
    return {'总页数': len(pages), '前置页数': first, '正文页数': total, '目录条数': len(toc),
            '标题页码': {t: m for (l, t), m in zip(headings, mapping) if l == 1},
            '错误': errors, '找不到的标题': missing, '文字层改字': fixed}
