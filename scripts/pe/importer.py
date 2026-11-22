"""已有报告 DOCX → 项目文件夹（项目信息.md＋正文/*.md＋图/）。
用途：把旧报告、样板报告转成可编辑、可重新生成的项目；也用来做回归测试。"""
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from lxml import etree as E

from .common import W, R, NS, TO_SCRIPT, text_of, text_md
from . import mdparse
from .model import judge_col, count_clause, body_rows
from .info import write_info
from . import doctype

WP = '{%s}' % NS['wp']
A = '{%s}' % NS['a']
RE_NO = re.compile(r'^[A-Z]{1,6}-\d{4}-[A-Z0-9]+$')
RE_DATE = re.compile(r'^\d{4}年\d{1,2}月\d{1,2}日$')
# 设计专篇封面（WS/T 882 附件1）：“报告编号：AT-2026-Z013”一行；日期常写“二〇二六年八月”
RE_NO_LABEL = re.compile(r'^报告编号[：:]\s*([A-Z]{1,6}-\d{4}-[A-Z0-9]+)$')
RE_DATE_CN = re.compile(r'^[〇零一二三四五六七八九]{4}年[一二三四五六七八九十]{1,3}月(?:[一二三四五六七八九十]{1,3}日)?$')
RE_LICENCE = re.compile(r'^(?:设计单位)?资质证书编号[：:]\s*(.+)$')
RE_COUNT = re.compile(r'(?P<subj>表\d+(?:\.\d+)*-\d+|[\u4e00-\u9fa5]{2,10})共评价(?P<n>\d+)项，'
                      r'(?P<rest>其中(?:[\u4e00-\u9fa5]+\d+项、?)+|均判为[\u4e00-\u9fa5]+?(?=[。；，]))')
RE_KCP = re.compile(r'(\d+)(个职业病危害关键控制点)')


def is_on(e):
    return e is not None and e.get(W + 'val') not in ('0', 'false', 'off')


def get(node, path, attr='val'):
    e = node.find(path)
    return e.get(W + attr) if e is not None else None


def runs_md(p, plain=False):
    """段落 → 文字；plain=False 时加粗部分写成 **…**。"""
    segs = []
    for r in p.iter(W + 'r'):
        if r.getparent().tag not in (W + 'p', W + 'hyperlink', W + 'smartTag', W + 'ins'):
            continue
        b = is_on(r.find(W + 'rPr/' + W + 'b'))
        s = ''
        for ch in r:
            if ch.tag == W + 't':
                s += ch.text or ''
            elif ch.tag in (W + 'tab', W + 'br', W + 'cr'):
                s += ' '
        m = TO_SCRIPT.get(get(r, W + 'rPr/' + W + 'vertAlign'))
        if m and s and all(c in m for c in s):
            s = ''.join(m[c] for c in s)
        if not s:
            continue
        if segs and segs[-1][1] == b:
            segs[-1][0] += s
        else:
            segs.append([s, b])
    out = ''
    for s, b in segs:
        if b and not plain and s.strip():
            lead, tail = s[:len(s) - len(s.lstrip())], s[len(s.rstrip()):]
            out += lead + '**' + s.strip() + '**' + tail
        else:
            out += s
    return out


def escape_line(s):
    if re.match(r'^(#|\||!\[|<!--|\d{1,3}[.．]\s)', s):
        return '\\' + s
    return s


class Importer:
    def __init__(self, docx):
        self.z = zipfile.ZipFile(docx)
        self.doc = E.fromstring(self.z.read('word/document.xml'))
        self.body = self.doc.find(W + 'body')
        self.rels = {r.get('Id'): r.get('Target') for r in
                     E.fromstring(self.z.read('word/_rels/document.xml.rels'))}
        self.sname = {}
        for s in E.fromstring(self.z.read('word/styles.xml')).findall(W + 'style'):
            n = s.find(W + 'name')
            if n is not None:
                self.sname[s.get(W + 'styleId')] = n.get(W + 'val').lower()
        self.kids = list(self.body)
        self.notes = []

    def sty(self, p):
        return self.sname.get(get(p, W + 'pPr/' + W + 'pStyle') or '', '')

    def level(self, p):
        m = re.match(r'heading (\d)$', self.sty(p))
        return int(m.group(1)) if m else 0

    def is_list(self, p):
        return self.sty(p) == 'list number' and get(p, W + 'pPr/' + W + 'numPr/' + W + 'numId') != '0'

    def body_start(self):
        for i, k in enumerate(self.kids):
            if k.tag == W + 'p' and self.level(k) == 1:
                return i
        raise ValueError('没找到一级标题（样式“标题 1”），无法确定正文从哪里开始')

    def front_info(self, start):
        kids = self.kids[:start]
        texts = [text_of(k) for k in kids]
        info = {}
        cover_end = next((i for i, k in enumerate(kids) if k.find('.//' + W + 'sectPr') is not None), len(kids) - 1)
        # 封面标题行：预评价“职业病危害预评价报告”，设计专篇“职业病防护设施设计专篇”
        design = any(texts[i].strip().endswith('设计专篇') for i in range(cover_end + 1))
        mark = '设计专篇' if design else '预评价报告'
        ti = next((i for i in range(cover_end + 1) if mark in texts[i]), None)
        if design:
            info['报告类型'] = doctype.DESIGN
        if ti is not None:
            info['封面名称'] = [t.strip() for t in texts[:ti] if t.strip() and not (design and RE_NO_LABEL.match(t.strip()))]
        for i in range(cover_end + 1):
            t = texts[i].strip()
            m = RE_NO_LABEL.match(t) if design else None     # 预评价照旧只认单独一行的编号
            if RE_NO.match(t) or m:
                info['报告编号'] = m.group(1) if m else t
            elif RE_DATE.match(t) or (design and RE_DATE_CN.match(t)):
                info['报告日期'] = t
            elif design and RE_LICENCE.match(t):
                info['资质证书编号'] = RE_LICENCE.match(t).group(1).strip()
            elif ti is not None and i > ti and t.endswith(('公司', '中心', '研究院', '研究所')):
                info['评价单位'] = t
        for t in texts:
            m = re.search(r'在(.+?)职业病防护设施设计中', t) if design else re.search(r'在为(.+?)项目提供', t)
            if m:
                info['项目名称'] = m.group(1)
            m = re.search(r'法定代表人[：:]\s*(\S+)', t)
            if m and not (design and m.group(1).startswith(('（', '('))):   # 专篇声明页是“设计单位法定代表人：（签名）”
                info['法定代表人'] = m.group(1)
        if '项目名称' not in info and info.get('封面名称'):
            info['项目名称'] = ''.join(info['封面名称'])
        if design and not info.get('评价单位'):   # 专篇声明开头是设计单位全称
            m = next((re.match(r'^(.+?(?:公司|中心|研究院|研究所))遵守', t.strip()) for t in texts
                      if '遵守国家有关法律' in t), None)
            if m:
                info['评价单位'] = m.group(1)
        people = []
        for k in kids:
            if k.tag == W + 'tbl':
                head = text_of(k.find(W + 'tr'))
                if '姓' in head and '证书' in head:
                    cat = ''
                    for tr in k.findall(W + 'tr')[1:]:
                        cells = [re.sub(r'\s+', '', text_of(tc)) for tc in tr.findall(W + 'tc')]
                        if not any(cells):
                            continue
                        cat = cells[0] or cat
                        people.append({'类别': cat, '姓名': cells[1], '职称': cells[2], '专业': cells[3],
                                       '证书号': cells[4] if len(cells) > 4 else ''})
                    break
        info['评价人员'] = people
        atts = []
        idx = [i for i, t in enumerate(texts) if t.strip() == '附件']
        if idx:
            for t in texts[idx[-1] + 1:]:
                m = re.match(r'^\s*附件\s*(\d+)\s*(.+?)\s*$', t)
                if m:
                    atts.append({'名称': m.group(2)})
        info['附件'] = atts
        return info

    def sections(self, start):
        secs, cur = [], []
        for k in self.kids[start:]:
            sp = k if k.tag == W + 'sectPr' else (k.find(W + 'pPr/' + W + 'sectPr') if k.tag == W + 'p' else None)
            cur.append(k)
            if sp is not None:
                pg = sp.find(W + 'pgSz')
                land = pg is not None and (pg.get(W + 'orient') == 'landscape'
                                           or int(pg.get(W + 'w', 0)) > int(pg.get(W + 'h', 0)))
                secs.append((cur, 'landscape' if land else 'portrait'))
                cur = []
        return secs

    def table_md(self, t):
        grid = [int(g.get(W + 'w')) for g in t.find(W + 'tblGrid')]
        ncol = len(grid)
        trs = t.findall(W + 'tr')
        header = 0
        for tr in trs:
            if tr.find(W + 'trPr/' + W + 'tblHeader') is not None:
                header += 1
            else:
                break
        rows, sizes, aligns = [], Counter(), defaultdict(Counter)
        for ri, tr in enumerate(trs):
            row = []
            for tc in tr.findall(W + 'tc'):
                span = int(get(tc, W + 'tcPr/' + W + 'gridSpan') or 1)
                vm = tc.find(W + 'tcPr/' + W + 'vMerge')
                if vm is not None and vm.get(W + 'val') != 'restart':
                    row.append('^')
                else:
                    paras = []
                    for p in tc.findall(W + 'p'):
                        paras.append(runs_md(p, plain=ri < max(header, 1)).strip())
                        if ri >= header and text_of(p).strip():
                            jc = get(p, W + 'pPr/' + W + 'jc') or 'left'
                            aligns[len(row)][{'center': '中', 'right': '右', 'end': '右'}.get(jc, '左')] += 1
                    while len(paras) > 1 and not paras[-1]:
                        paras.pop()
                    s = '<br>'.join(paras).replace('|', '\\|')
                    if s in ('^', '<'):
                        s = '\\' + s
                    row.append(s if s else ' ')
                for x in tc.iter(W + 'sz'):
                    if x.getparent().tag == W + 'rPr' and x.getparent().getparent().tag == W + 'r':
                        sizes[int(x.get(W + 'val'))] += 1
                row += ['<'] * (span - 1)
            if len(row) != ncol:
                self.notes.append('表格一行的格数(%d)与列数(%d)不符，已按列数截补：%s' % (len(row), ncol, text_of(tr)[:30]))
                row = (row + [' '] * ncol)[:ncol]
            rows.append(row)
        attrs = ['列宽=' + ','.join(map(str, grid))]
        if sizes:
            attrs.append('字号=%d' % sizes.most_common(1)[0][0])
        attrs.append('对齐=' + ','.join(aligns[c].most_common(1)[0][0] if aligns[c] else '左' for c in range(ncol)))
        tp = t.find(W + 'tblPr')
        ind = get(tp, W + 'tblInd', 'w')
        if ind and ind != '0' and get(tp, W + 'tblInd', 'type') == 'dxa':
            attrs.append('缩进=' + ind)
        # 单元格边距：表级 tblCellMar 为底，单元格自带 tcMar 时以其为准，取最常见的实际值
        side = {'start': 'left', 'end': 'right'}
        base = {'top': '0', 'bottom': '0', 'left': '108', 'right': '108'}
        mar = tp.find(W + 'tblCellMar')
        for x in (mar if mar is not None else []):
            base[side.get(E.QName(x).localname, E.QName(x).localname)] = x.get(W + 'w')
        eff = Counter()
        for tc in t.iter(W + 'tc'):
            m = dict(base)
            for x in (tc.find(W + 'tcPr/' + W + 'tcMar') if tc.find(W + 'tcPr/' + W + 'tcMar') is not None else []):
                m[side.get(E.QName(x).localname, E.QName(x).localname)] = x.get(W + 'w')
            eff[(m['top'], m['bottom'], m['left'], m['right'])] += 1
        v = list(eff.most_common(1)[0][0]) if eff else [base['top'], base['bottom'], base['left'], base['right']]
        if v != ['60', '60', '90', '90']:
            attrs.append('边距=' + ','.join(v))
        if get(tp, W + 'jc') == 'center':
            attrs.append('居中')
        kn = [all(is_on(p.find(W + 'pPr/' + W + 'keepNext')) for p in tr.iter(W + 'p')) for tr in trs]
        auto = set(range(header)) | {r for r, row in enumerate(rows)
                                     if r >= header and ncol > 1 and all(x == '<' for x in row[1:])}
        k_rows = {r for r, v in enumerate(kn) if v and r < len(rows) - 1}
        if len(rows) > 2 and k_rows >= set(range(header, len(rows) - 1)):
            attrs.append('不跨页')
        elif k_rows - auto:
            attrs.append('同页行=' + ','.join(str(r + 1) for r in sorted(k_rows - auto)))
        if header == 0:
            attrs.append('表头行=0')
        h = max(header, 1)
        lines = ['| ' + ' | '.join(r) + ' |' for r in rows[:h]]
        lines.append('|' + '---|' * ncol)
        lines += ['| ' + ' | '.join(r) + ' |' for r in rows[h:]]
        return attrs, lines, rows, header

    def run(self, out_dir, placeholders=True):
        out = Path(out_dir)
        (out / '正文').mkdir(parents=True, exist_ok=True)
        (out / '图').mkdir(exist_ok=True)
        start = self.body_start()
        info = self.front_info(start)
        info.setdefault('表注', '表后')  # 旧稿的表注是表后段落，按原样排；删掉这行就排进表格末行
        flat, orient_of, sec_first = [], {}, set()
        prev = 'portrait'
        for si, (els, orient) in enumerate(self.sections(start)):
            for j, k in enumerate(els):
                flat.append(k)
                orient_of[id(k)] = orient
                if j == 0 and (si > 0 or orient != 'portrait'):
                    sec_first.add(id(k))
        chapters, lines, seq = [], None, []
        list_count, n_img = Counter(), 0
        pending, cur_orient = None, 'portrait'
        for i, k in enumerate(flat):
            if id(k) in sec_first:
                o = orient_of[id(k)]
                pending = ('<!-- 横向 -->' if o == 'landscape' else '<!-- 纵向 -->') if o != cur_orient else '<!-- 分页 -->'
                cur_orient = o
            if k.tag == W + 'sectPr':
                continue
            nxt = flat[i + 1] if i + 1 < len(flat) else None
            prv = flat[i - 1] if i > 0 else None
            if k.tag == W + 'p':
                lvl, txt = self.level(k), text_md(k)
                has_img = k.find('.//' + W + 'drawing') is not None
                if lvl == 1:
                    lines = []
                    chapters.append([txt.strip(), lines])
                if pending and (txt.strip() or has_img):
                    if not (pending == '<!-- 分页 -->' and lvl == 1):
                        lines += ['', pending, '']
                    pending = None
                if lvl != 1 and is_on(k.find(W + 'pPr/' + W + 'pageBreakBefore')) and (txt.strip() or has_img):
                    lines.append('<!-- 分页 -->')
                if lvl:
                    lines += ['', '#' * lvl + ' ' + txt.strip(), '']
                elif has_img:
                    cap = text_of(nxt).strip() if nxt is not None and nxt.tag == W + 'p' else ''
                    m = mdparse.RE_FCAP.match(cap)
                    for blip in k.iter(A + 'blip'):
                        target = self.rels[blip.get(R + 'embed')]
                        data = self.z.read('word/' + target.replace('../', ''))
                        n_img += 1
                        name = ('图%s' % m.group(1) if m else '插图%02d' % n_img) + Path(target).suffix
                        (out / '图' / name).write_bytes(data)
                        ext = next(k.iter(WP + 'extent'), None)
                        size = ''
                        if ext is not None:
                            size = '宽=%.2fcm 高=%.2fcm' % (int(ext.get('cx')) / 360000, int(ext.get('cy')) / 360000)
                        for key, att in (('段前', 'before'), ('段后', 'after')):
                            v = get(k, W + 'pPr/' + W + 'spacing', att)
                            if v and v.lstrip('-').isdigit() and int(v):
                                size += ' %s=%g磅' % (key, int(v) / 20)
                        size = '{%s}' % size.strip() if size.strip() else ''
                        lines.append('![%s](图/%s)%s' % (cap if m else '图片', name, size))
                elif self.is_list(k):
                    nid = get(k, W + 'pPr/' + W + 'numPr/' + W + 'numId') or 'style'
                    list_count[nid] += 1
                    lines.append('%d. %s' % (list_count[nid], runs_md(k).strip()))
                    seq.append(('p', lines, len(lines) - 1))
                elif not txt.strip():
                    if k.find(W + 'pPr/' + W + 'sectPr') is None and not (prv is not None and prv.tag == W + 'tbl'):
                        lines.append('<!-- 空行 -->')
                else:
                    t0 = txt.strip()
                    is_tcap = nxt is not None and nxt.tag == W + 'tbl' and mdparse.RE_TCAP.match(t0)
                    is_fcap = prv is not None and prv.find('.//' + W + 'drawing') is not None and mdparse.RE_FCAP.match(t0)
                    if is_tcap or is_fcap:
                        lines.append(t0)
                    else:
                        jc = get(k, W + 'pPr/' + W + 'jc') or 'both'
                        fl = get(k, W + 'pPr/' + W + 'ind', 'firstLine')
                        if jc == 'center':
                            lines.append('<!-- 段落 居中 -->')
                        elif fl == '0' and get(k, W + 'pPr/' + W + 'ind', 'firstLineChars') in (None, '0'):
                            lines.append('<!-- 段落 不缩进 -->')
                        lines.append(escape_line(runs_md(k).strip()))
                        seq.append(('p', lines, len(lines) - 1))
            elif k.tag == W + 'tbl':
                if pending:
                    lines += ['', pending, '']
                    pending = None
                attrs, tlines, rows, header = self.table_md(k)
                spacer = nxt is not None and nxt.tag == W + 'p' and not text_of(nxt).strip() \
                    and nxt.find('.//' + W + 'drawing') is None and nxt.find(W + 'pPr/' + W + 'sectPr') is None
                if not spacer:
                    attrs.append('表后空行=否')
                cap = next((l for l in reversed(lines) if l.strip()), '')
                m = mdparse.RE_TCAP.match(cap)
                lines.append('<!-- 表格 %s -->' % ' '.join(attrs))
                lines += tlines
                lines.append('')
                seq.append(('table', {'rows': rows, 'header': max(header, 1), 'key': m.group(1) if m else None,
                                      'caption': cap if m else ''}))
        if not info.get('建设单位'):
            for item in seq:
                if item[0] != 'table':
                    continue
                for row in item[1]['rows']:
                    if len(row) >= 2 and re.sub(r'\s+', '', row[0]) == '建设单位' and row[1].strip() not in ('', '<', '^'):
                        info['建设单位'] = re.sub(r'<br>', '', row[1]).replace('**', '').strip()
                        break
                if info.get('建设单位'):
                    break
        if placeholders:
            self.add_placeholders(seq)
        files = []
        for idx, (title, ls) in enumerate(chapters, 1):
            name = re.sub(r'[\\/:*?"<>|\s]+', '', re.sub(r'^\d+\s*', '', title))[:20] or '章'
            fn = '%02d_%s.md' % (idx, name)
            txt = re.sub(r'\n{3,}', '\n\n', '\n'.join(ls).strip('\n') + '\n')
            (out / '正文' / fn).write_text(txt, encoding='utf-8')
            files.append(fn)
        write_info(out / '项目信息.md', info)
        return info, files

    def add_placeholders(self, seq):
        """计数句、关键控制点个数与表一致时改成占位符，以后改表会自动跟着变。"""
        tables = {x[1]['key']: x[1] for x in seq if x[0] == 'table' and x[1]['key']}
        kcp = [tb for tb in tables.values() if tb['caption'].rstrip().endswith('关键控制点')]
        kcp = kcp[-1] if kcp else None
        last_judged = None
        for item in seq:
            if item[0] == 'table':
                if judge_col(item[1]) is not None:
                    last_judged = item[1]
                continue
            _, ls, li = item

            def repl(m, last=last_judged):
                subj = m.group('subj')
                if subj.startswith('表'):
                    tb, ph = tables.get(subj[1:]), '{{计数:%s}}' % subj
                else:
                    tb = last
                    ph = '{{计数:表%s:%s}}' % (tb['key'], subj) if tb is not None and tb['key'] else None
                if tb is None or ph is None or judge_col(tb) is None:
                    return m.group(0)
                exp = count_clause(tb, subj)
                if exp == m.group(0):
                    return ph
                self.notes.append('计数句与表不一致，保留原文：%s（按表应为：%s）' % (m.group(0), exp))
                return m.group(0)

            s = RE_COUNT.sub(repl, ls[li])
            if kcp is not None:
                n = body_rows(kcp)
                s = RE_KCP.sub(lambda m: ('{{行数:表%s}}' % kcp['key'] + m.group(2)) if int(m.group(1)) == n
                               else m.group(0), s)
            ls[li] = s
