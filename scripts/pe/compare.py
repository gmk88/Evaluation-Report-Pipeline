"""两份 DOCX 正文逐段比对（回归测试；report.py compare 用它核对电脑上的稿子是不是上轮交付的版本）。"""
import re
import zipfile

from lxml import etree as E

from .common import W, text_md


def _sty_names(z):
    out = {}
    for s in E.fromstring(z.read('word/styles.xml')).findall(W + 'style'):
        n = s.find(W + 'name')
        if n is not None:
            out[s.get(W + 'styleId')] = n.get(W + 'val').lower()
    return out


def sequence(docx, part='body'):
    """part='body'：从第一个一级标题到正文末；'front'：之前的封面、声明、人员表、目录、附件清单。
    返回 [(类型, 文字)]：段落取原文（去首尾空白），表格逐格取文字（纵向合并续格跳过）。"""
    z = zipfile.ZipFile(docx)
    names = _sty_names(z)
    body = E.fromstring(z.read('word/document.xml')).find(W + 'body')
    kids = [k for k in body if k.tag in (W + 'p', W + 'tbl')]
    start = next(i for i, k in enumerate(kids) if k.tag == W + 'p' and k.find(W + 'pPr/' + W + 'pStyle') is not None
                 and names.get(k.find(W + 'pPr/' + W + 'pStyle').get(W + 'val')) == 'heading 1')
    sel = kids[start:] if part == 'body' else kids[:start]
    out = []
    for k in sel:
        if k.tag == W + 'p':
            t = text_md(k).replace(' ', ' ').strip()  # 不换行空格只是版式（docxgen.nobreak_spaces），按普通空格比
            if t:
                st = k.find(W + 'pPr/' + W + 'pStyle')
                kind = names.get(st.get(W + 'val'), '') if st is not None else ''
                out.append(('h' if kind.startswith('heading') else 'p', t))
        else:
            for tr in k.findall(W + 'tr'):
                for tc in tr.findall(W + 'tc'):
                    vm = tc.find(W + 'tcPr/' + W + 'vMerge')
                    if vm is not None and vm.get(W + 'val') != 'restart':
                        continue
                    out.append(('c', re.sub(r'\s+', '', text_md(tc))))
    return out


def diff(a, b, limit=20):
    """返回不同之处列表（最多 limit 条）。段落按原文比；表格单元格不计空白。"""
    import difflib
    sa = ['%s|%s' % x for x in a]
    sb = ['%s|%s' % x for x in b]
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        out.append((op, sa[i1:i2][:3], sb[j1:j2][:3]))
        if len(out) >= limit:
            break
    return out


def chapter_of(seq):
    """每一项所在的章（一级标题文字，如“3 类比企业（项目）调查与分析”）；第一个一级标题之前为空串。"""
    cur, out = '', []
    for kind, t in seq:
        if kind == 'h' and re.match(r'^\d+[ \u3000]', t):
            cur = t
        out.append(cur)
    return out


def by_chapter(a, b):
    """按章汇总不同之处：返回 [(章, 不同项数)]，按章的先后排列；两份一致时返回 []。"""
    import difflib
    sa = ['%s|%s' % x for x in a]
    sb = ['%s|%s' % x for x in b]
    ca, cb = chapter_of(a), chapter_of(b)
    cnt = {}
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sa, sb, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        chs = set(ca[i1:i2]) | set(cb[j1:j2])
        for c in chs:
            cnt[c] = cnt.get(c, 0) + max(i2 - i1, j2 - j1)
    order = list(dict.fromkeys(ca + cb))
    return [(c, cnt[c]) for c in order if c in cnt]
