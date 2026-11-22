"""项目（项目信息＋正文块）→ DOCX。样式、封面、声明、人员表、目录、页眉页脚来自模板。"""
import copy
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree as E

from .common import (update_app_stats, NS, W, R, XML_SPACE, el, sub, run, text_of, order_children, PPR_ORDER, TCPR_ORDER,
                     SECT_ORDER, BODY_SIZE, CAPTION_SIZE, TABLE_SIZE, LINE_BODY, INDENT_BODY,
                     PORTRAIT_TEXT_WIDTH, LANDSCAPE_TEXT_WIDTH, write_docx, split_scripts)
from .common import FONT_EA
from .model import RE_PH, column_kind
from . import doctype, omml
from .mdparse import split_inline

SKILL_DIR = Path(__file__).resolve().parents[2]
TEMPLATE = SKILL_DIR / 'templates' / '预评价报告模板.docx'
CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
PR = '{http://schemas.openxmlformats.org/package/2006/relationships}'
IMG_T = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'
WP = '{%s}' % NS['wp']
A = '{%s}' % NS['a']
PIC = '{%s}' % NS['pic']
SIZE_NAMES = {'四号': 28, '小四': 24, '五号': 21, '小五': 18, '六号': 15}
ALIGN = {'中': 'center', '居中': 'center', '左': 'left', '右': 'right', '两端': 'both'}


class BuildError(Exception):
    pass


class Package:
    def __init__(self, path):
        with zipfile.ZipFile(path) as z:
            self.order = [i.filename for i in z.infolist()]
            self.parts = {n: z.read(n) for n in self.order}
        self._xml = {}

    def xml(self, name):
        if name not in self._xml:
            self._xml[name] = E.fromstring(self.parts[name])
        return self._xml[name]

    def add_media(self, data, ext):
        """图片存成 word/media/imageN.扩展名（N 不分扩展名、不与已有图片重名），关系 ID 用数字 rIdN。
        与 WPS、Word 自己保存的写法一致；rIdPe1 这类非数字 ID、pe_image1.png 与 pe_image1.jpeg 同名，
        有的 WPS 版本会显示不出图。"""
        ext = ext.lower().lstrip('.')
        ext = 'jpeg' if ext == 'jpg' else ext
        used = {n.rsplit('.', 1)[0] for n in self.parts if n.startswith('word/media/')}
        k = 1
        while 'word/media/image%d' % k in used:
            k += 1
        name = 'media/image%d.%s' % (k, ext)
        self.parts['word/' + name] = data
        self.order.append('word/' + name)
        rels = self.xml('word/_rels/document.xml.rels')
        nums = [int(m.group(1)) for m in (re.match(r'rId(\d+)$', r.get('Id', '')) for r in rels) if m]
        rid = 'rId%d' % (max(nums + [0]) + 1)
        r = E.SubElement(rels, PR + 'Relationship')
        r.set('Id', rid)
        r.set('Type', IMG_T)
        r.set('Target', name)
        ct = self.xml('[Content_Types].xml')
        if not any(d.get('Extension', '').lower() == ext for d in ct.findall(CT + 'Default')):
            d = E.Element(CT + 'Default')
            d.set('Extension', ext)
            d.set('ContentType', {'tif': 'image/tiff', 'svg': 'image/svg+xml'}.get(ext, 'image/' + ext))
            ct.insert(0, d)
        return rid

    def save(self, path, app_stats=False):
        for name, root in self._xml.items():
            self.parts[name] = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
        if app_stats:
            update_app_stats(self.parts, pages=1)   # 页数在 finalize 回填时按 PDF 改写
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        write_docx(path, {n: self.parts[n] for n in self.order if n in self.parts})   # 打包方式与 WPS 另存一致


def replace_placeholders(root, value_of):
    """逐段替换 {{…}}（占位符被拆在几个 run 里也能换）。value_of 返回 None 表示不换。"""
    for p in root.iter(W + 'p'):
        ts = p.findall('.//' + W + 't')
        if not ts:
            continue
        texts = [t.text or '' for t in ts]
        full = ''.join(texts)
        if '{{' not in full:
            continue
        starts, pos = [], 0
        for s in texts:
            starts.append(pos)
            pos += len(s)
        for m in reversed(list(RE_PH.finditer(full))):
            val = value_of(m.group(1).strip())
            if val is None:
                continue
            a, b = m.start(), m.end()
            if val.endswith('项目') and full.startswith('项目', b):
                b += 2  # 名称本身以“项目”结尾时去掉模板里紧跟的“项目”，免得出现“项目项目”
            ia = max(k for k in range(len(ts)) if texts[k] and starts[k] <= a < starts[k] + len(texts[k]))
            ib = max(k for k in range(len(ts)) if texts[k] and starts[k] < b <= starts[k] + len(texts[k]))
            if ia == ib:
                o = starts[ia]
                texts[ia] = texts[ia][:a - o] + val + texts[ia][b - o:]
            else:
                texts[ia] = texts[ia][:a - starts[ia]] + val
                for k in range(ia + 1, ib):
                    texts[k] = ''
                texts[ib] = texts[ib][b - starts[ib]:]
        for t, s in zip(ts, texts):
            t.text = s
            if s != s.strip() or '  ' in s:
                t.set(XML_SPACE, 'preserve')


def inline_runs(text, size=BODY_SIZE, bold=False, spacing=0):
    out, b = [], False
    for part in re.split(r'(\*\*)', text):
        if part == '**':
            b = not b
            continue
        if part:
            out.append(run(part.replace('\\*', '*'), size=size, bold=bold or b, spacing=spacing))
    return out


# 正文段落、列表项里的半角空格（数值与单位、标准代号与编号、①与条文、标准名称各部分之间）换成不换行空格，
# 只有两个英文单词之间的照常可断行。中文本来字字可断，在这些空格处断开只会把数值和单位分在两行；
# 只在空格处断行的预览器（VS Code Office Viewer）还会把空格前的短行拉满整行、字距拉散（2026-10-27 用户批注）
RE_GLUE_SP = re.compile(r'(?<![A-Za-z]) | (?![A-Za-z])')


def nobreak_spaces(text):
    return RE_GLUE_SP.sub('\u00a0', text)


def all_bold(text):
    t = text.strip()
    return t.startswith('**') and t.endswith('**') and t.count('**') == 2 and len(t) > 4


def para(text='', align='both', indent=INDENT_BODY, size=BODY_SIZE, bold=False, keep_next=False,
         keep_lines=False, line=LINE_BODY, rule='exact', before=0, after=0, style=None, spacing=0):
    p = el('p')
    pp = sub(p, 'pPr')
    if style:
        sub(pp, 'pStyle', val=style)
    if keep_next:
        sub(pp, 'keepNext')
    if keep_lines:
        sub(pp, 'keepLines')
    sub(pp, 'spacing', before=before, after=after, line=line, lineRule=rule)
    if indent is not None:
        sub(pp, 'ind', firstLine=indent)
    sub(pp, 'jc', val=align)
    for r in inline_runs(text, size=size, bold=bold, spacing=spacing):
        p.append(r)
    return p


def set_page_break(p):
    pp = p.find(W + 'pPr')
    for x in pp.findall(W + 'pageBreakBefore'):
        pp.remove(x)
    sub(pp, 'pageBreakBefore')
    order_children(pp, PPR_ORDER)


def parse_size(v, default):
    if v in (None, True, ''):
        return default
    v = str(v).strip()
    if v in SIZE_NAMES:
        return SIZE_NAMES[v]
    try:
        return int(round(float(v)))
    except ValueError:
        raise BuildError('字号“%s”看不懂（写半磅数如 21，或“五号”“小五”）' % v)


def auto_widths(rows, ncol, total):
    import unicodedata
    w = [2.0] * ncol
    for row in rows:
        for c, cell in enumerate(row):
            if cell in ('^', '<'):
                continue
            longest = max((sum(2 if unicodedata.east_asian_width(ch) in 'WF' else 1 for ch in s.replace('**', ''))
                           for s in re.split(r'<br\s*/?>', cell)), default=0)
            w[c] = max(w[c], min(longest, 60))
    s = sum(w)
    out = [max(500, int(total * x / s)) for x in w]
    out[-1] += total - sum(out)
    return out


# 表格版式（用户在马沟控评、马沟预评价和本项目提过的要求，2026-10-27 做进生成器）：
# 各表一律排满版心宽、左右对齐；正文表格不小于五号，行距固定 14 磅；外框 1.5 磅、内线 0.5 磅；
# 单元格留白统一，行高不小于 0.8 cm；短文字列不出现末行只剩一个字。
TABLE_FULL_WIDTH = {'portrait': PORTRAIT_TEXT_WIDTH, 'landscape': 16838 - 2 * 1440}  # 横向页边距各 1440
PRE_LAYOUT = doctype.layout(doctype.PRE)   # 版式参数按报告类型取（doctype.LAYOUT）；不传的按预评价
TABLE_MIN_SIZE = 21
TABLE_MAR = [40, 40, 100, 100]   # 上、下、左、右（twip）
TABLE_ROW_MIN = 454
TABLE_BORDER_OUT, TABLE_BORDER_IN = 12, 4   # 外框 1.5 磅、内线 0.5 磅（单位 1/8 磅），黑色


def table_borders():
    """正文表格边线：外框 1.5 磅黑、内线 0.5 磅黑（声明页人员签字表不用，照模板）。"""
    bd = el('tblBorders')
    for side in ('top', 'left', 'bottom', 'right'):
        sub(bd, side, val='single', sz=TABLE_BORDER_OUT, color='000000')
    for side in ('insideH', 'insideV'):
        sub(bd, side, val='single', sz=TABLE_BORDER_IN, color='000000')
    return bd


def em_len(s):
    """按全角 1、半角 0.5 估算文字宽度（字数）。"""
    import unicodedata
    s = re.sub(r'\*\*|\^|~', '', s)
    return sum(1 if unicodedata.east_asian_width(ch) in 'WFA' else 0.5 for ch in s)


def orphan(n, cap):
    """n 个字排在每行 cap 个字的格子里，末行只剩不到 1.5 个字。"""
    if cap <= 0 or n <= cap:
        return False
    tail = n - int(n / cap) * cap
    return 0 < tail < 1.5


def ease_orphans(rows, header, widths, size, mar):
    """短文字列（最长不超过 14 个字）有格子末行只剩一个字时，从平均字数最多的列借宽度，最多借到原宽的 40%。"""
    ncol, unit = len(widths), size * 10
    cols = [[] for _ in range(ncol)]
    for row in rows[header:]:
        if ncol > 1 and all(x == '<' for x in row[1:]):
            continue
        for c, cell in enumerate(row):
            if cell in ('^', '<', '') or (c + 1 < ncol and row[c + 1] == '<'):
                continue
            cols[c] += [em_len(x.strip()) for x in re.split(r'<br\s*/?>', cell) if x.strip()]
    avg = [sum(v) / len(v) if v else 0 for v in cols]
    donor = max(range(ncol), key=lambda c: avg[c])
    if avg[donor] < 12:
        return widths
    cap = lambda w: (w - mar[2] - mar[3]) / unit
    for c in range(ncol):
        if c == donor or not cols[c] or max(cols[c]) > 14 or not any(orphan(n, cap(widths[c])) for n in cols[c]):
            continue
        for extra in range(20, int(widths[c] * 0.4) + 1, 20):
            if not any(orphan(n, cap(widths[c] + extra)) for n in cols[c]) and cap(widths[donor] - extra) >= 12:
                widths[c] += extra
                widths[donor] -= extra
                break
    return widths


RE_NOTE = re.compile(r'^(备)?注[：:]')


def is_note(b):
    """表、图后的“注：…”段。"""
    return b is not None and b['t'] == 'p' and RE_NOTE.match(b['text'].replace('**', '')) is not None


def is_note_row(row):
    """表格里整行合并的“注：…”行。"""
    return len(row) > 1 and all(x == '<' for x in row[1:]) and RE_NOTE.match(row[0].replace('**', '').strip()) is not None


def note_paras(cell):
    """注文分段：<br> 处和句末的②③…处另起一段（第一段带“注：”）。"""
    out = []
    for seg in re.split(r'<br\s*/?>', cell):
        out += [x.strip() for x in re.split(r'(?<=[。；])(?=[②③④⑤⑥⑦⑧⑨⑩])', seg) if x.strip()]
    return out or ['']


def make_table(b, orient, notes=(), keep_last=False, lay=PRE_LAYOUT):
    """notes：表后紧跟的“注：…”段，排成表格最后一行（整行合并、两端对齐、悬挂缩进“注：”两字），与上一行同页。
    表注放进表格（2026-10-27 用户批注）：注不会和表分到两页，也不再需要“表末行—间隔段—注”的同页链。
    keep_last：项目信息写“表注：表后”时注仍是表后段落，末行与下段同页，注不单独落到下一页。"""
    rows, header, attrs = b['rows'], b['header'], b.get('attrs', {})
    ncol = len(rows[0])
    rows = rows + [[n] + ['<'] * (ncol - 1) for n in notes]
    nb = len(rows)  # 表末注行之前的行数
    while nb > header and is_note_row(rows[nb - 1]):
        nb -= 1
    page_w = lay['横向表格宽'] if orient == 'landscape' else lay['纵向版心宽']
    total = int(attrs['宽度']) if str(attrs.get('宽度', '')).isdigit() else page_w
    if attrs.get('列宽'):
        try:
            widths = [float(x) for x in re.split(r'[,，、]', str(attrs['列宽'])) if x.strip()]
        except ValueError:
            raise BuildError('%s 列宽要写数字，用逗号分隔' % b['src'])
        if len(widths) != ncol:
            raise BuildError('%s 列宽给了 %d 个数，表格有 %d 列' % (b['src'], len(widths), ncol))
        if sum(widths) <= 100 or '宽度' in attrs:
            s = sum(widths)
            widths = [int(round(total * x / s)) for x in widths]
            widths[-1] += total - sum(widths)
        else:
            # 列宽按比例排满版心宽：各表左右边线对齐（原来横向表多为 13230，比版心窄约 1.3 cm）
            s = sum(widths)
            widths = [int(round(page_w * x / s)) for x in widths]
            widths[-1] += page_w - sum(widths)
            total = page_w
    else:
        widths = auto_widths(rows[:nb], ncol, total)
    size = max(parse_size(attrs.get('字号'), TABLE_SIZE), TABLE_MIN_SIZE)
    mar = list(TABLE_MAR)
    if attrs.get('边距'):  # 只能比统一留白大
        mar = [max(int(x), m) for x, m in zip(re.split(r'[,，]', str(attrs['边距'])), TABLE_MAR)]
    widths = ease_orphans(rows, header, widths, size, mar)
    head = [re.sub(r'\s+', '', c.replace('**', '')) for c in rows[header - 1]] if header else [''] * ncol
    if attrs.get('对齐'):
        al = [ALIGN.get(x.strip()) for x in re.split(r'[,，、]', str(attrs['对齐']))]
        if len(al) != ncol or None in al:
            raise BuildError('%s 对齐要逐列写“中/左/右”，共 %d 列' % (b['src'], ncol))
    else:
        al = [column_kind(h, [row[c] for row in rows[header:nb]]) or 'left' for c, h in enumerate(head)]
    tbl = el('tbl')
    tp = sub(tbl, 'tblPr')
    sub(tp, 'tblW', w=total, type='dxa')
    # 一律表级居中：兼容模式 14 下 tblInd=0 对齐的是首格文字，满宽表左右边线会整体左移一个单元格留白（约 5 pt）
    sub(tp, 'jc', val='center')
    sub(tp, 'tblInd', w=0, type='dxa')
    tp.append(table_borders())
    sub(tp, 'tblLayout', type='fixed')
    cm = sub(tp, 'tblCellMar')
    for side, v in zip(('top', 'bottom', 'left', 'right'), mar):
        sub(cm, side, w=v, type='dxa')
    grid = sub(tbl, 'tblGrid')
    for w_ in widths:
        sub(grid, 'gridCol', w=w_)
    keep = set(range(header))
    for r, row in enumerate(rows):
        if r >= header and ncol > 1 and all(x == '<' for x in row[1:]):
            keep.add(r)  # 分组行（整行合并）与下一行同页
    if attrs.get('同页行'):
        for x in re.split(r'[,，、]', str(attrs['同页行'])):
            if x.strip().isdigit():
                keep.add(int(x) - 1)
    if attrs.get('不跨页'):
        keep |= set(range(len(rows) - 1))
    if nb < len(rows):
        keep.add(nb - 1)  # 表体最后一行与注行同页
    if keep_last:
        keep.add(len(rows) - 1)
    else:
        keep.discard(len(rows) - 1)
    for r, row in enumerate(rows):
        tr = sub(tbl, 'tr')
        trp = sub(tr, 'trPr')
        sub(trp, 'cantSplit')
        sub(trp, 'trHeight', val=TABLE_ROW_MIN, hRule='atLeast')
        if r < header:
            sub(trp, 'tblHeader')
        c = 0
        while c < ncol:
            cell = row[c]
            span = 1
            while c + span < ncol and row[c + span] == '<':
                span += 1
            tc = sub(tr, 'tc')
            tcp = sub(tc, 'tcPr')
            sub(tcp, 'tcW', w=sum(widths[c:c + span]), type='dxa')
            if span > 1:
                sub(tcp, 'gridSpan', val=span)
            if cell == '^' and r > 0:
                sub(tcp, 'vMerge')
            elif r + 1 < len(rows) and rows[r + 1][c] == '^':
                sub(tcp, 'vMerge', val='restart')
            sub(tcp, 'vAlign', val='center')
            if cell in ('\\^', '\\<'):
                cell = cell[1:]
            if cell in ('^', '<'):
                texts = ['']
            elif r >= nb:
                texts = note_paras(cell)
            else:
                texts = re.split(r'<br\s*/?>', cell)
            for k, t in enumerate(texts):
                p = sub(tc, 'p')
                pp = sub(p, 'pPr')
                if r in keep:
                    sub(pp, 'keepNext')
                sub(pp, 'spacing', before=0, after=0, line=280, lineRule='exact')
                if r >= nb:
                    two = size * 20  # 两字宽（twip）：首段“注：”悬挂，②③…各段与①对齐
                    # firstLine=0 要写明：不写会继承正文样式的首行缩进两字（四号 560），②③…比①缩进一截
                    sub(pp, 'ind', **({'left': two, 'hanging': two, 'right': 0} if k == 0
                                      else {'left': two, 'right': 0, 'firstLine': 0}))
                    sub(pp, 'jc', val='both')
                    t = nobreak_spaces(t)
                else:
                    sub(pp, 'ind', firstLine=0, left=0, right=0)
                    sub(pp, 'jc', val='center' if (r < header or span == ncol) else al[c])
                for x in inline_runs(t.strip(), size=size, bold=r < header):
                    p.append(x)
            c += span
    return tbl


def image_size(path):
    from PIL import Image
    with Image.open(path) as im:
        w, h = im.size
        dpi = im.info.get('dpi', (96, 96))[0] or 96
    return w / dpi * 2.54, w, h


IMG_MAX_PPI = 220   # 按版面尺寸的最高分辨率（Word 默认的“打印”质量）


def wps_safe_image(path, w_cm):
    """返回 (图片字节, 'jpeg')。所有插图一律存成**普通 JPEG**：基线、RGB、白底、不写 dpi（JFIF 密度 1:1），
    按版面宽度 w_cm 不超过 IMG_MAX_PPI（超出就缩小，不放大）。已经符合的 JPEG 原样用。
    依据（2026-10-17，用户 Mac 版 WPS 实测）：图2.1-1 卫星图（1269×728 的这种普通 JPEG）每一版都能显示；
    600/300 dpi 的大 PNG（最大 3425×4708）时有时无——v4 里风频玫瑰图空白，v5 只把它从 RGBA 换成 RGB，
    结果用户说“所有图都显示不出来”。LibreOffice、Linux 版 WPS 每一版都正常，本机查不出来。"""
    import io
    from PIL import Image
    data = path.read_bytes()
    max_w = max(1, int(w_cm / 2.54 * IMG_MAX_PPI))
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        if (im.format == 'JPEG' and im.mode == 'RGB' and not im.info.get('progressive')
                and not im.info.get('progression') and not im.info.get('dpi')
                and im.info.get('jfif_unit', 0) == 0 and im.width <= max_w * 1.02):
            return data, 'jpeg'
        if im.mode.startswith('I'):                       # 16 位灰度
            import numpy as np
            a = np.asarray(im).astype('uint32')
            im = Image.fromarray((a >> 8 if a.max() > 255 else a).astype('uint8'))
        if im.mode in ('RGBA', 'LA', 'PA', 'P') or 'transparency' in im.info:
            rgba = im.convert('RGBA')
            out = Image.new('RGB', rgba.size, (255, 255, 255))
            out.paste(rgba, mask=rgba.getchannel('A'))
        else:
            out = im.convert('RGB')
    if out.width > max_w:
        out = out.resize((max_w, max(1, int(round(out.height * max_w / out.width)))), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, 'JPEG', quality=95)                     # 基线、4:2:0、JFIF 1:1，与那张卫星图同类
    return buf.getvalue(), 'jpeg'


def parse_len(v):
    m = re.match(r'^\s*([\d.]+)\s*(cm|mm|厘米|毫米)?\s*$', str(v))
    if not m:
        raise BuildError('图片尺寸“%s”要写成 15cm 或 150mm' % v)
    x = float(m.group(1))
    return x / 10 if m.group(2) in ('mm', '毫米') else x


def parse_pt(v):
    """段前/段后：写磅数（如 4、4磅、4pt），返回 twip。"""
    if v in (None, True, ''):
        return 0
    m = re.match(r'^\s*(-?[\d.]+)\s*(磅|pt)?\s*$', str(v))
    if not m:
        raise BuildError('段前/段后“%s”要写磅数，如 4磅' % v)
    return int(round(float(m.group(1)) * 20))


def make_image(pkg, b, root, orient, docpr_id, lay=PRE_LAYOUT):
    path = (root / b['path']).resolve()
    if not path.exists():
        raise BuildError('%s 图片文件不存在：%s' % (b['src'], b['path']))
    wcm, px_w, px_h = image_size(path)
    page_cm = (lay['横向图宽'] if orient == 'landscape' else lay['纵向版心宽']) / 567.0
    attrs = b.get('attrs', {})
    if '宽' in attrs:
        w2 = parse_len(attrs['宽'])
        h2 = parse_len(attrs['高']) if '高' in attrs else w2 * px_h / px_w
    elif '高' in attrs:
        h2 = parse_len(attrs['高'])
        w2 = h2 * px_w / px_h
    else:
        w2 = min(wcm, page_cm)
        h2 = w2 * px_h / px_w
    cx, cy = int(round(w2 * 360000)), int(round(h2 * 360000))
    rid = pkg.add_media(*wps_safe_image(path, w2))   # 一律转成普通 JPEG（≤220 ppi），见 wps_safe_image
    p = el('p')
    pp = sub(p, 'pPr')
    if b.get('caption'):
        sub(pp, 'keepNext')
    sub(pp, 'keepLines')
    sub(pp, 'spacing', before=parse_pt(attrs.get('段前')), after=parse_pt(attrs.get('段后')), line=240, lineRule='auto')
    sub(pp, 'ind', firstLine=0)
    sub(pp, 'jc', val='center')
    d = sub(sub(p, 'r'), 'drawing')
    # 逐字照 WPS 另存出来的写法：a:、pic: 分别在 graphicFrameLocks、graphic、pic:pic 上就地声明，
    # cNvPicPr 里带 picLocks。（lxml 自动生成的 ns2:/ns3: 前缀、或把声明集中放在 wp:inline 上，
    # 都是 WPS 自己不会写的形式，Mac 版 WPS 打开可能显示不出图。）
    inl = E.SubElement(d, WP + 'inline', distT='0', distB='0', distL='0', distR='0')
    E.SubElement(inl, WP + 'extent', cx=str(cx), cy=str(cy))
    E.SubElement(inl, WP + 'effectExtent', l='0', t='0', r='0', b='0')
    E.SubElement(inl, WP + 'docPr', id=str(docpr_id), name='图片%d' % docpr_id)
    g = E.SubElement(inl, WP + 'cNvGraphicFramePr')
    E.SubElement(g, A + 'graphicFrameLocks', nsmap={'a': NS['a']}, noChangeAspect='1')
    gr = E.SubElement(inl, A + 'graphic', nsmap={'a': NS['a']})
    gd = E.SubElement(gr, A + 'graphicData', uri='http://schemas.openxmlformats.org/drawingml/2006/picture')
    pic = E.SubElement(gd, PIC + 'pic', nsmap={'pic': NS['pic']})
    nv = E.SubElement(pic, PIC + 'nvPicPr')
    E.SubElement(nv, PIC + 'cNvPr', id=str(docpr_id), name='图片%d' % docpr_id)
    E.SubElement(E.SubElement(nv, PIC + 'cNvPicPr'), A + 'picLocks', noChangeAspect='1')
    bf = E.SubElement(pic, PIC + 'blipFill')
    E.SubElement(bf, A + 'blip').set(R + 'embed', rid)
    E.SubElement(E.SubElement(bf, A + 'stretch'), A + 'fillRect')
    sp = E.SubElement(pic, PIC + 'spPr')
    xf = E.SubElement(sp, A + 'xfrm')
    E.SubElement(xf, A + 'off', x='0', y='0')
    E.SubElement(xf, A + 'ext', cx=str(cx), cy=str(cy))
    E.SubElement(E.SubElement(sp, A + 'prstGeom', prst='rect'), A + 'avLst')
    return p


def style_ids(pkg):
    out = {}
    for s in pkg.xml('word/styles.xml').findall(W + 'style'):
        n = s.find(W + 'name')
        if n is not None:
            out[n.get(W + 'val').lower()] = s.get(W + 'styleId')
    return out


def list_abstract(pkg, list_style):
    s = next(x for x in pkg.xml('word/styles.xml').findall(W + 'style') if x.get(W + 'styleId') == list_style)
    num_id = s.find(W + 'pPr/' + W + 'numPr/' + W + 'numId').get(W + 'val')
    num = next(x for x in pkg.xml('word/numbering.xml').findall(W + 'num') if x.get(W + 'numId') == num_id)
    return num.find(W + 'abstractNumId').get(W + 'val')


def new_list(pkg, abstract, start=1):
    nb = pkg.xml('word/numbering.xml')
    nid = max([int(x.get(W + 'numId')) for x in nb.findall(W + 'num')] + [0]) + 1
    num = sub(nb, 'num', numId=nid)
    sub(num, 'abstractNumId', val=abstract)
    sub(sub(num, 'lvlOverride', ilvl=0), 'startOverride', val=start)
    return nid


def find_para(body, marker):
    for p in body.iter(W + 'p'):
        if marker in text_of(p):
            return p
    return None


PEOPLE_WIDTHS = [1300, 1100, 1600, 1500, 1500, 1300]   # 人员表各列宽（twip），合计＝纵向版心宽


def fit_people_table(tbl, width=PORTRAIT_TEXT_WIDTH):
    """人员签字表：模板里是浮动表、列宽合计 8513，比版心宽约 11 磅（左伸出约 4.5 磅、右约 6.4 磅），
    “职称”“化工新材料”末行只剩一个字（2026-10-28 晋钢200MW 排版审查）。改成嵌入式、排满版心宽、表级居中
    （与正文表格同一做法，Word、WPS 都按居中排）；列宽按“类别 5 字、姓名 4 字、职称和专业每行 6 字”定；
    表顶仍空出原浮动定位的距离，表在页上的高低不变。"""
    tp = tbl.find(W + 'tblPr')
    pos = tp.find(W + 'tblpPr')
    dy = int(pos.get(W + 'tblpY', '0')) if pos is not None else 0
    for tag in ('tblpPr', 'tblOverlap'):
        for x in tp.findall(W + tag):
            tp.remove(x)
    cols = tbl.findall(W + 'tblGrid/' + W + 'gridCol')
    ws = list(PEOPLE_WIDTHS)
    if len(cols) != len(ws):   # 别的机构模板列数不同：按原比例缩放到版心宽
        s = sum(int(c.get(W + 'w')) for c in cols)
        ws = [int(width * int(c.get(W + 'w')) / s) for c in cols]
        ws[-1] += width - sum(ws)
    elif width != sum(ws):     # 版心更宽（设计专篇 9184）：各列按比例放宽
        ws = [int(width * w_ / sum(PEOPLE_WIDTHS)) for w_ in PEOPLE_WIDTHS]
        ws[-1] += width - sum(ws)
    for c, w_ in zip(cols, ws):
        c.set(W + 'w', str(w_))
    tw = tp.find(W + 'tblW')
    if tw is None:
        tw = sub(tp, 'tblW')
    tw.set(W + 'w', str(width))
    tw.set(W + 'type', 'dxa')
    for x in tp.findall(W + 'jc'):
        tp.remove(x)
    tw.addnext(el('jc', val='center'))
    # 边线不动，照模板（四边 0.5 磅）：2026-10-30 用户定“声明页的签字表不用改”
    lay = tp.find(W + 'tblLayout')
    if lay is None:
        lay = sub(tp, 'tblLayout')
    lay.set(W + 'type', 'fixed')
    for tr in tbl.findall(W + 'tr'):
        c = 0
        for tc in tr.findall(W + 'tc'):
            gs = tc.find(W + 'tcPr/' + W + 'gridSpan')
            span = int(gs.get(W + 'val')) if gs is not None else 1
            tcw = tc.find(W + 'tcPr/' + W + 'tcW')
            if tcw is not None:
                tcw.set(W + 'w', str(sum(ws[c:c + span])))
                tcw.set(W + 'type', 'dxa')
            c += span
    prev = tbl.getprevious()
    if dy > 0 and prev is not None and prev.tag == W + 'p' and not text_of(prev).strip():
        pp = prev.find(W + 'pPr')
        if pp is None:
            pp = el('pPr')
            prev.insert(0, pp)
        sp = pp.find(W + 'spacing')
        if sp is None:
            sp = sub(pp, 'spacing')
        sp.set(W + 'after', str(dy))
        order_children(pp, PPR_ORDER)


TOC_INSTR = ' TOC \\o "1-3" \\h \\z \\u '
TOC_BM_ID0 = 10000   # 标题书签编号起点（正文末书签 BodyEnd 用 9001）


def toc_marks(headings):
    """一至三级标题的书签名（目录条的 PAGEREF 指向它们）；四级标题不进目录，给 None。"""
    out, k = [], 0
    for lvl, _ in headings:
        if lvl <= 3:
            k += 1
            out.append('_Toc%09d' % k)
        else:
            out.append(None)
    return out


def field_run(rpr, kind=None, instr=None):
    """域的一段：kind 为 begin／separate／end 时是域符，否则是域指令。格式照抄 rpr。"""
    r = el('r')
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    if kind:
        sub(r, 'fldChar', fldCharType=kind)
    else:
        t = sub(r, 'instrText')
        t.text = instr
        t.set(XML_SPACE, 'preserve')
    return r


def toc_entry(q, mark):
    """一条目录：标题、制表位、页码套进指向标题书签的超链接，页码写成 PAGEREF 域（结果先为 0，由 finalize 按 PDF 回填）。
    写法与 Word 自己生成的目录相同。超链接不能改用 HYPERLINK 域：VS Code 的 Office Viewer 不认嵌套域，会丢掉标题只剩页码。"""
    runs = [r for r in q if r.tag == W + 'r']
    page = runs[-1]
    rpr = page.find(W + 'rPr')
    link = el('hyperlink', anchor=mark, history=1)
    runs[0].addprevious(link)
    for r in runs[:-1]:
        link.append(r)
    link.append(field_run(rpr, 'begin'))
    link.append(field_run(rpr, instr=' PAGEREF %s \\h ' % mark))
    link.append(field_run(rpr, 'separate'))
    link.append(page)
    link.append(field_run(rpr, 'end'))


def toc_field(entries):
    """整份目录套一个 TOC 域：首条开头放 begin、指令、separate；末条后面另起一个 1 磅高的空段放 end（同 Word、WPS 的写法）。
    这样 Word／WPS 里右键“更新域／更新目录”能更新页码；“更新整个目录”按 toc 1～3 样式重排，模板里这三个样式与目录条格式一致。"""
    first, last = entries[0], entries[-1]
    link = first.find(W + 'hyperlink')
    rpr = link.find(W + 'r/' + W + 'rPr')
    for r in (field_run(rpr, 'begin'), field_run(rpr, instr=TOC_INSTR), field_run(rpr, 'separate')):
        link.addprevious(r)
    q = el('p')
    pp = sub(q, 'pPr')
    sub(pp, 'spacing', before=0, after=0, line=20, lineRule='exact')
    sub(pp, 'ind', left=0, firstLine=0)
    q.append(field_run(rpr, 'end'))
    last.addnext(q)


def info_values(info, t=None):
    """模板占位符的值。t 是报告类型（不给按项目信息的“报告类型”）：页眉第二行取 doctype 配置。
    设计专篇封面的“资质证书编号”没填时写【待填】（WS/T 882 附件1 封面要有设计单位资质证书编号）。"""
    t = t or doctype.type_of_info(info)
    v = {k: str(x) for k, x in info.items() if not isinstance(x, (list, dict))}
    v.setdefault('页眉第一行', info.get('页眉名称') or info.get('项目名称', ''))
    v.setdefault('页眉第二行', doctype.conf(t)['页眉第二行'])
    if t != doctype.PRE:
        v.setdefault('资质证书编号', '【待填】')
    return v


def fill_front(body, info, headings, marks=None, lay=PRE_LAYOUT):
    p = find_para(body, '{{封面名称}}')
    names = info.get('封面名称') or [info.get('项目名称', '')]
    if isinstance(names, str):
        names = [names]
    for n in names:
        q = copy.deepcopy(p)
        replace_placeholders(q, lambda k, n=n: n if k == '封面名称' else None)
        p.addprevious(q)
    p.getparent().remove(p)
    # 人员表：同类别连续的人合并“人员类别”格
    proto = next((tr for tr in body.iter(W + 'tr') if '{{姓名}}' in text_of(tr)), None)
    people = info.get('评价人员', [])
    if proto is not None:
        cats = [x.get('类别', '') for x in people]
        for i, person in enumerate(people):
            q = copy.deepcopy(proto)
            cont = i > 0 and cats[i] == cats[i - 1]
            restart = not cont and i + 1 < len(cats) and cats[i + 1] == cats[i]
            pv = {'类别': '' if cont else cats[i], '姓名': person.get('姓名', ''), '职称': person.get('职称', ''),
                  '专业': person.get('专业', ''), '证书号': person.get('证书号', '')}
            replace_placeholders(q, lambda k, pv=pv: pv.get(k))
            tcp = q.find(W + 'tc/' + W + 'tcPr')
            if cont:
                sub(tcp, 'vMerge')
            elif restart:
                sub(tcp, 'vMerge', val='restart')
            order_children(tcp, TCPR_ORDER)
            proto.addprevious(q)
        ptbl = proto.getparent()
        ptbl.remove(proto)
        fit_people_table(ptbl, lay['纵向版心宽'])
    # 目录：Word 目录域（2026-10-30 用户：目录页码要能自动更新；原来是程序回填的纯文字，Word／WPS 里更新不了）
    protos = {k: find_para(body, '{{目录%d}}' % k) for k in (1, 2, 3)}
    marks = marks or toc_marks(headings)
    entries = []
    for (lvl, title), mark in zip(headings, marks):
        if lvl > 3:
            continue
        q = copy.deepcopy(protos[lvl])
        replace_placeholders(q, lambda k, t=title, l=lvl: t if k == '目录%d' % l else ('0' if k == '页码' else None))
        toc_entry(q, mark)
        protos[1].addprevious(q)
        entries.append(q)
    for q in protos.values():
        q.getparent().remove(q)
    if entries:
        toc_field(entries)
    # 附件清单（设计专篇模板没有：附件单独成册，WS/T 882 第8.1.3条）
    atts = info.get('附件', [])
    ph, ht = find_para(body, '{{附件名称}}'), find_para(body, '{{附件标题}}')
    if ph is None or ht is None:
        for q in (ph, ht):
            if q is not None:
                q.getparent().remove(q)
        return
    for k, a in enumerate(atts, 1):
        q = copy.deepcopy(ph)
        name = a.get('名称', '') if isinstance(a, dict) else str(a)
        replace_placeholders(q, lambda key, k=k, name=name: str(k) if key == '附件序号' else (name if key == '附件名称' else None))
        ph.addprevious(q)
    ph.getparent().remove(ph)
    if atts:
        replace_placeholders(ht, lambda k: '附件' if k == '附件标题' else None)
    else:
        ht.getparent().remove(ht)


# ---------------------------------------------------------------- 公式块（设计专篇计算书，规格 2.2）
FORMULA_COLS = (1500, 5300, 1500)     # 左空格｜公式｜编号，按版心宽等比切


def formula_widths(total):
    w = [int(round(total * x / sum(FORMULA_COLS))) for x in FORMULA_COLS]
    w[1] += total - sum(w)
    return w


def _omml(latex, src, what='公式'):
    try:
        return omml.to_omml(latex, size=BODY_SIZE, ea_font=FONT_EA)
    except omml.LatexError as e:
        raise BuildError('%s %s转换失败：%s' % (src, what, e))


def _math_cell_para(align, keep_next=True):
    p = el('p')
    pp = sub(p, 'pPr')
    if keep_next:
        sub(pp, 'keepNext')
    sub(pp, 'spacing', before=60, after=60, line=240, lineRule='auto')   # 自动行距：固定行距会削掉分式
    sub(pp, 'ind', firstLine=0)
    sub(pp, 'jc', val=align)
    order_children(pp, PPR_ORDER)
    return p


def formula_table(b, om, total):
    """编号的公式：三列无框固定布局表，中格居中放公式，右格右对齐放（编号），左格空。"""
    widths = formula_widths(total)
    t = el('tbl')
    tp = sub(t, 'tblPr')
    sub(tp, 'tblW', w=total, type='dxa')
    sub(tp, 'jc', val='center')
    bd = sub(tp, 'tblBorders')
    for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        sub(bd, side, val='nil')
    sub(tp, 'tblLayout', type='fixed')
    mar = sub(tp, 'tblCellMar')
    for side in ('top', 'left', 'bottom', 'right'):
        sub(mar, side, w=0, type='dxa')
    grid = sub(t, 'tblGrid')
    for w in widths:
        sub(grid, 'gridCol', w=w)
    tr = sub(t, 'tr')
    sub(sub(tr, 'trPr'), 'cantSplit')
    paras = []
    for w, align in zip(widths, ('left', 'center', 'right')):
        tc = sub(tr, 'tc')
        tcp = sub(tc, 'tcPr')
        sub(tcp, 'tcW', w=w, type='dxa')
        sub(tcp, 'noWrap')
        sub(tcp, 'vAlign', val='center')
        order_children(tcp, TCPR_ORDER)
        p = _math_cell_para(align)
        tc.append(p)
        paras.append(p)
    mp = omml.math_para(om)
    paras[1].append(mp)
    paras[2].append(run('（%s）' % b['no'], size=BODY_SIZE))
    return t


def formula_plain(om):
    """不编号的公式：单独一段居中。"""
    p = _math_cell_para('center', keep_next=False)
    p.append(omml.math_para(om))
    return p


def formula_where(b, lay):
    """“式中：”一段，各符号行一段（首行不缩进、悬挂对齐）；最后一段以外都与下段同页。"""
    out = []
    items = b.get('vars', [])
    sp = lay['字距']

    def where_para(runs, keep, hang):
        p = el('p')
        pp = sub(p, 'pPr')
        if keep:
            sub(pp, 'keepNext')
        sub(pp, 'spacing', before=0, after=0, line=lay['行距'], lineRule='exact')
        if hang:
            sub(pp, 'ind', left=hang, hanging=hang)
        else:
            sub(pp, 'ind', firstLine=0)
        sub(pp, 'jc', val='both')
        order_children(pp, PPR_ORDER)
        for r in runs:
            p.append(r)
        return p

    out.append(where_para([run('式中：', size=BODY_SIZE, spacing=sp)], bool(items), 0))
    for k, v in enumerate(items):
        end = '。' if k == len(items) - 1 else '；'
        runs = [_omml(v['sym'], v['src'], '式中符号'), run('——', size=BODY_SIZE, spacing=sp)]
        text = v['text'].rstrip('。；;') + end
        for is_math, seg in split_inline(text):
            if is_math:
                runs.append(_omml(seg, v['src'], '式中行内公式'))
            elif seg:
                runs.append(run(nobreak_spaces(seg), size=BODY_SIZE, spacing=sp))
        out.append(where_para(runs, k < len(items) - 1, lay['缩进']))
    return out


def make_formula(b, orient, lay):
    """公式块 → [表或段, 式中各段…]。"""
    if b.get('latex') is None:
        raise BuildError('%s %s' % (b['src'], b.get('bad') or '公式指令后面应紧接单独一行 $$…$$'))
    total = lay['横向表格宽'] if orient == 'landscape' else lay['纵向版心宽']
    om = _omml(b['latex'], b.get('latex_src', b['src']))
    out = [formula_table(b, om, total) if b.get('no') else formula_plain(om)]
    if b.get('where'):
        out += formula_where(b, lay)
    return out


def template_of(t):
    """报告类型的 Word 模板（doctype 配置）；预评价就是 TEMPLATE。"""
    return TEMPLATE if t == doctype.PRE else doctype.path_of(t, '模板')


def build(project, out_path, template=None):
    rtype = doctype.type_of_info(project.info)
    lay = doctype.layout(rtype)
    pkg = Package(template or template_of(rtype))
    body = pkg.xml('word/document.xml').find(W + 'body')
    sid = style_ids(pkg)
    hs = [sid.get('heading %d' % k) for k in (1, 2, 3, 4)]
    list_style = sid.get('list number')
    abstract = list_abstract(pkg, list_style) if list_style else None
    blocks = project.blocks
    headings = [(b['level'], b['text'].replace('**', '')) for b in blocks if b['t'] == 'h']
    vals = info_values(project.info, rtype)
    marks = toc_marks(headings)
    fill_front(body, project.info, headings, marks, lay)
    hk = iter(range(len(headings)))
    p_body, p_land = find_para(body, '{{正文}}'), find_para(body, '{{横向分节}}')
    proto = {'landscape': copy.deepcopy(p_land.find(W + 'pPr/' + W + 'sectPr')),
             'portrait': copy.deepcopy(body.find(W + 'sectPr'))}
    for sp in proto.values():
        pn = sp.find(W + 'pgNumType')
        if pn is not None and pn.get(W + 'start'):
            del pn.attrib[W + 'start']
    p_land.getparent().remove(p_land)
    out, spacers = [], set()
    st = {'orient': 'portrait', 'first': True, 'pb': False, 'list': None, 'last_list': None, 'docpr': 1000,
          'since': -1}            # since：上次分节后 out 的长度
    skip_empty = rtype != doctype.PRE
    # 表后紧跟的“注：…”段排成表格最后一行；项目信息.md 写“表注：表后”时仍排在表后（导入的旧稿按原样）
    notes_in_table = str(project.info.get('表注', '表内')).strip() != '表后'

    def sect_copy(orient):
        sp = copy.deepcopy(proto[orient])
        if st['first']:
            pn = sp.find(W + 'pgNumType')
            if pn is None:
                pn = sub(sp, 'pgNumType')
                order_children(sp, SECT_ORDER)
            pn.set(W + 'start', '1')
            st['first'] = False
        return sp

    def emit(e):
        if st['pb']:
            if e.tag == W + 'p':
                set_page_break(e)
            else:
                q = para('', indent=0, line=20, rule='atLeast')
                set_page_break(q)
                out.append(q)
            st['pb'] = False
        out.append(e)

    def close_section():
        sp = sect_copy(st['orient'])
        last = out[-1] if out else None
        # 表后的“注”段（带 keepLines）不兼作分节段：Word 对带分节符的段不执行“与上表同页”
        if last is not None and last.tag == W + 'p' and id(last) not in spacers \
                and last.find(W + 'pPr/' + W + 'sectPr') is None \
                and last.find(W + 'pPr/' + W + 'keepLines') is None \
                and last.find('.//{%s}oMathPara' % omml.M_NS) is None:   # 含公式的段挂分节符，LibreOffice 会整段不画
            pp = last.find(W + 'pPr')
            pp.append(sp)
            order_children(pp, PPR_ORDER)
        else:
            if last is not None and id(last) in spacers:
                out.pop()
            # 固定 1 磅：表格刚好排满一页时，按字号撑高的空分节段会单独挤到下一页
            q = para('', indent=0, line=20, rule='exact')
            q.find(W + 'pPr').append(sp)
            out.append(q)

    absorbed = set()  # 已排进表格末行的“注”段
    for i, b in enumerate(blocks):
        if i in absorbed:
            continue
        t = b['t']
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        if t != 'li' and st['list'] is not None:
            st['last_list'] = st['list']
            st['list'] = None
        if t == 'sect':
            if b['orient'] != st['orient']:
                # 设计专篇：上一节刚结束、这一节还没内容又换回来（“纵向”后紧跟“横向”，常在两个文件交界处），
                # 不出这个空节，免得多一张空白页。预评价照旧。
                if out and not (skip_empty and len(out) == st['since']):
                    close_section()
                    st['since'] = len(out)
                st['orient'] = b['orient']
            st['pb'] = False
            continue
        if t == 'pagebreak':
            st['pb'] = True
            continue
        if t == 'h':
            p = para(b['text'].replace('**', ''), align='left', indent=0, bold=True, keep_next=True,
                     keep_lines=True, style=hs[b['level'] - 1], line=lay['行距'])
            if b['level'] == 1:
                set_page_break(p)
            k = next(hk)
            if marks[k]:  # 目录条的 PAGEREF 指向这个书签
                p.find(W + 'pPr').addnext(el('bookmarkStart', id=TOC_BM_ID0 + k + 1, name=marks[k]))
                sub(p, 'bookmarkEnd', id=TOC_BM_ID0 + k + 1)
            emit(p)
        elif t == 'p':
            a = b.get('attrs', {})
            align = 'center' if a.get('居中') else ('right' if a.get('右对齐') else ('left' if a.get('左对齐') else 'both'))
            size = parse_size(a.get('字号'), BODY_SIZE)
            indent = 0 if (a.get('不缩进') or a.get('居中')) else lay['缩进'] * size // BODY_SIZE
            kn = (all_bold(b['text']) and len(b['text']) <= 60) or bool(a.get('与下段同页'))
            emit(para(nobreak_spaces(b['text']), align=align, indent=indent, keep_next=kn,
                      keep_lines=i > 0 and blocks[i - 1]['t'] == 'table' and is_note(b),
                      size=size, line=lay['行距'] * size // BODY_SIZE,
                      spacing=lay['字距'] if size == BODY_SIZE else 0))
        elif t == 'blank':
            emit(para('', line=lay['行距']))
        elif t == 'li':
            n = b['num']
            if st['list'] is not None and n == st['list'][1] + 1:
                nid = st['list'][0]
            elif n > 1 and st['last_list'] is not None and n == st['last_list'][1] + 1:
                nid = st['last_list'][0]
            else:
                nid = new_list(pkg, abstract, start=n)
            st['list'] = (nid, n)
            p = el('p')
            pp = sub(p, 'pPr')
            sub(pp, 'pStyle', val=list_style)
            np_ = sub(pp, 'numPr')
            sub(np_, 'ilvl', val=0)
            sub(np_, 'numId', val=nid)
            sub(pp, 'spacing', before=0, after=0, line=lay['行距'], lineRule='exact')
            sub(pp, 'jc', val='both')
            for r in inline_runs(nobreak_spaces(b['text']), spacing=lay['字距']):
                p.append(r)
            emit(p)
        elif t == 'caption':
            emit(para(b['text'].replace('**', ''), align='center', indent=0, size=CAPTION_SIZE, bold=True,
                      keep_next=b['kind'] == '表', keep_lines=True, line=360, before=60, after=60))
        elif t == 'img':
            st['docpr'] += 1
            emit(make_image(pkg, b, project.root, st['orient'], st['docpr'], lay))
        elif t == 'formula':
            for e in make_formula(b, st['orient'], lay):
                emit(e)
        elif t == 'table':
            notes, j = [], i + 1
            while notes_in_table and j < len(blocks) and is_note(blocks[j]):
                notes.append(blocks[j]['text'])
                absorbed.add(j)
                j += 1
            nxt = blocks[j] if j < len(blocks) else None
            note = is_note(nxt)  # 只在“表注：表后”时还会遇到表后的注段
            emit(make_table(b, st['orient'], notes=notes, keep_last=note, lay=lay))
            spacer = str(b.get('attrs', {}).get('表后空行', '是')) not in ('否', '0', '无')
            if (spacer or note) and nxt is not None and nxt['t'] not in ('sect', 'pagebreak'):
                # 表后接“注”时，间隔段带 keepNext：表末行→间隔段→注 连成一串（Word 对表末行只看下一段首行）；
                # 表后空行=否 的表也插一个 1 磅高的间隔段，只为接上这串
                q = el('p')
                qp = sub(q, 'pPr')
                if note:
                    sub(qp, 'keepNext')
                sub(qp, 'spacing', after=0, line=80 if spacer else 20, lineRule='exact')
                spacers.add(id(q))
                out.append(q)
    last = out[-1] if out else None
    if last is None or last.tag != W + 'p' or id(last) in spacers:
        if last is not None and id(last) in spacers:
            out.pop()
        last = para('', line=lay['行距'])
        out.append(last)
    sub(last, 'bookmarkStart', id=9001, name='BodyEnd')
    sub(last, 'bookmarkEnd', id=9001)
    for e in out:
        p_body.addprevious(e)
    p_body.getparent().remove(p_body)
    final = body.find(W + 'sectPr')
    final.addprevious(sect_copy(st['orient']))
    body.remove(final)
    left = []

    def val(k):
        if k in vals:
            return vals[k]
        left.append(k)
        return None

    for name in list(pkg.parts):
        if re.match(r'word/(document|header\d+|footer\d+)\.xml$', name):
            replace_placeholders(pkg.xml(name), val)
            split_scripts(pkg.xml(name))
    if 'docProps/core.xml' in pkg.parts:
        core = pkg.xml('docProps/core.xml')
        dc = '{http://purl.org/dc/elements/1.1/}'
        t_ = core.find(dc + 'title')
        if t_ is None:
            t_ = E.SubElement(core, dc + 'title')
        conf = doctype.conf(rtype)
        t_.text = vals.get('项目名称', '') + conf['名称后缀']
        c = core.find(dc + 'creator')
        if c is not None:
            c.text = vals.get('评价单位', '')
        # 模板里的关键词、最后修改人和日期是马沟加气站留下的，出稿时按本报告重写
        cp = '{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}'
        dcterms = '{http://purl.org/dc/terms/}'
        now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        props = [(cp + 'keywords', conf['关键词']), (cp + 'lastModifiedBy', vals.get('评价单位', '')),
                 (dcterms + 'created', now), (dcterms + 'modified', now)]
        if rtype != doctype.PRE:   # 预评价模板的 subject（“建设项目职业病危害预评价”）照旧；专篇写本卷名称和报告编号
            no = vals.get('报告编号', '')
            props.append((dc + 'subject', ('建设项目%s %s' % (conf['名称后缀'], '' if '待' in no else no)).strip()))
        for tag, text in props:
            node = core.find(tag)
            if node is None:
                node = E.SubElement(core, tag)
                if tag.startswith(dcterms):
                    node.set('{http://www.w3.org/2001/XMLSchema-instance}type', 'dcterms:W3CDTF')
            node.text = text
    pkg.save(out_path, app_stats=True)
    return {'missing': sorted(set(left)), 'headings': headings}
