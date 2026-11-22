"""公共工具：命名空间、XML 构造、文字提取、排版常量。"""
import copy
import re
from lxml import etree as E

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'pic': 'http://schemas.openxmlformats.org/drawingml/2006/picture',
}
W = '{%s}' % NS['w']
R = '{%s}' % NS['r']
XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'

# ---- 排版常量（与马沟最终版一致；改版式只改这里或模板）----
BODY_SIZE = 28        # 正文四号（半磅）
CAPTION_SIZE = 24     # 表题、图题小四加粗
TABLE_SIZE = 21       # 表格默认五号
LINE_BODY = 490       # 正文固定行距（twip）
INDENT_BODY = 560     # 首行缩进两字
PORTRAIT_TEXT_WIDTH = 8300    # 纵向版心宽（twip）
LANDSCAPE_TEXT_WIDTH = 13230  # 横向版心宽（twip）
FONT_EA = '仿宋'
FONT_LATIN = 'Times New Roman'

# 仿宋、宋体没有 ² ³ ₂ ₆ 这类上下标字形，Word、WPS 各自换别的字体显示，打印效果因机器而异；
# 生成时拆成普通字符加 w:vertAlign 上下标格式（与 WPS 的上标/下标按钮存的一样），md 里照写 m³、SO₂
SUP, SUP_PLAIN = '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ', '0123456789+-=()ni'
SUB, SUB_PLAIN = '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢ', '0123456789+-=()aeoxhklmnpsti'
TO_PLAIN = str.maketrans(SUP + SUB, SUP_PLAIN + SUB_PLAIN)
TO_SCRIPT = {'superscript': dict(zip(SUP_PLAIN, SUP)), 'subscript': dict(zip(SUB_PLAIN, SUB))}
RE_SCRIPT = re.compile('([%s]+)|([%s]+)' % (SUP, SUB))

# 评价表“符合性”列允许的档位（第7章用“无配备必要”）
LEVELS = ['符合', '基本符合', '按补充措施完善后符合', '无设置必要', '无配备必要']


def el(tag, **attrs):
    e = E.Element(W + tag)
    for k, v in attrs.items():
        e.set(W + k, str(v))
    return e


def sub(parent, tag, **attrs):
    e = el(tag, **attrs)
    parent.append(e)
    return e


def text_of(node):
    return ''.join(node.xpath('.//w:t/text()', namespaces=NS))


def text_md(node):
    """同 text_of，但上下标格式的字符还原成 m³、SO₂ 这样的字符，与正文 md 的写法一致。"""
    out = []
    for t in node.iter(W + 't'):
        s = t.text or ''
        va = t.getparent().find(W + 'rPr/' + W + 'vertAlign')
        m = TO_SCRIPT.get(va.get(W + 'val')) if va is not None else None
        if m and s and all(ch in m for ch in s):
            s = ''.join(m[ch] for ch in s)
        out.append(s)
    return ''.join(out)


def compact(s):
    return re.sub(r'\s+', '', s or '')


def run(s, size=BODY_SIZE, bold=False, font=FONT_EA, spacing=0):
    """spacing：字距（twip），设计专篇正文段落用 26（每行 30 字，WS/T 882 附录D）；0 不写。"""
    r = el('r')
    rp = sub(r, 'rPr')
    sub(rp, 'rFonts', ascii=FONT_LATIN, hAnsi=FONT_LATIN, eastAsia=font, cs=font, hint='eastAsia')
    if bold:
        sub(rp, 'b')
    sub(rp, 'color', val='000000')
    if spacing:
        sub(rp, 'spacing', val=spacing)
    sub(rp, 'sz', val=size)
    sub(rp, 'szCs', val=size)
    sub(rp, 'lang', val='en-US', eastAsia='zh-CN')
    t = sub(r, 't')
    t.text = s
    if s[:1].isspace() or s[-1:].isspace() or '  ' in s:
        t.set(XML_SPACE, 'preserve')
    return r


PPR_ORDER = ['pStyle', 'keepNext', 'keepLines', 'pageBreakBefore', 'framePr', 'widowControl', 'numPr',
             'suppressLineNumbers', 'pBdr', 'shd', 'tabs', 'suppressAutoHyphens', 'kinsoku', 'wordWrap',
             'overflowPunct', 'topLinePunct', 'autoSpaceDE', 'autoSpaceDN', 'bidi', 'adjustRightInd',
             'snapToGrid', 'spacing', 'ind', 'contextualSpacing', 'mirrorIndents', 'suppressOverlap', 'jc',
             'textDirection', 'textAlignment', 'textboxTightWrap', 'outlineLvl', 'divId', 'cnfStyle', 'rPr',
             'sectPr', 'pPrChange']
TCPR_ORDER = ['cnfStyle', 'tcW', 'gridSpan', 'hMerge', 'vMerge', 'tcBorders', 'shd', 'noWrap', 'tcMar',
              'textDirection', 'tcFitText', 'vAlign', 'hideMark']
SECT_ORDER = ['headerReference', 'footerReference', 'footnotePr', 'endnotePr', 'type', 'pgSz', 'pgMar',
              'paperSrc', 'pgBorders', 'lnNumType', 'pgNumType', 'cols', 'formProt', 'vAlign', 'noEndnote',
              'titlePg', 'textDirection', 'bidi', 'rtlGutter', 'docGrid', 'printerSettings', 'sectPrChange']


def order_children(node, order):
    idx = {t: i for i, t in enumerate(order)}
    for child in sorted(list(node), key=lambda e: idx.get(E.QName(e).localname, 999)):
        node.append(child)


RPR_ORDER = ['rStyle', 'rFonts', 'b', 'bCs', 'i', 'iCs', 'caps', 'smallCaps', 'strike', 'dstrike', 'outline',
             'shadow', 'emboss', 'imprint', 'noProof', 'snapToGrid', 'vanish', 'webHidden', 'color', 'spacing', 'w',
             'kern', 'position', 'sz', 'szCs', 'highlight', 'u', 'effect', 'bdr', 'shd', 'fitText', 'vertAlign', 'rtl',
             'cs', 'em', 'lang', 'eastAsianLayout', 'specVanish', 'oMath', 'rPrChange']


def split_scripts(root):
    """把含 m³、SO₂ 这类上下标字符的文字段拆开：上下标字符换成普通字符，那一段加 w:vertAlign。"""
    for r in list(root.iter(W + 'r')):
        kids = [k for k in r if k.tag != W + 'rPr']
        if len(kids) != 1 or kids[0].tag != W + 't' or not RE_SCRIPT.search(kids[0].text or ''):
            continue
        rp = r.find(W + 'rPr')
        parts = RE_SCRIPT.split(kids[0].text)
        for k in range(0, len(parts), 3):
            segs = [(parts[k], None)]
            if k + 1 < len(parts):
                segs.append((parts[k + 1], 'superscript') if parts[k + 1] else (parts[k + 2], 'subscript'))
            for s, va in segs:
                if not s:
                    continue
                q = E.Element(W + 'r', dict(r.attrib))
                p = copy.deepcopy(rp) if rp is not None else E.Element(W + 'rPr')
                if va:
                    for x in p.findall(W + 'vertAlign'):
                        p.remove(x)
                    sub(p, 'vertAlign', val=va)
                    order_children(p, RPR_ORDER)
                    s = s.translate(TO_PLAIN)
                if len(p):
                    q.append(p)
                t = sub(q, 't')
                t.text = s
                if s[:1].isspace() or s[-1:].isspace() or '  ' in s:
                    t.set(XML_SPACE, 'preserve')
                r.addprevious(q)
        r.getparent().remove(r)


# ---- DOCX 打包：与 WPS 自己另存的 DOCX 完全一致（2026-10-17，插图在 Mac 版 WPS 不显示的第三次反馈后）----
WPS_ZIP_TIME = (2012, 7, 2, 9, 52, 14)   # WPS 给压缩包里每一项写的固定时间戳
XML_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'


def wps_xml(data):
    """XML 声明统一成 WPS/Word 的写法：双引号＋CRLF（lxml 默认写单引号、LF）。"""
    if data.startswith(b'\xef\xbb\xbf'):
        data = data[3:]
    s = data.lstrip()
    if s.startswith(b'<?xml'):
        s = s[s.index(b'?>') + 2:].lstrip(b'\r\n')
    return XML_DECL + s


def write_docx(path, parts):
    """按 WPS 另存 DOCX 的打包方式写文件。parts：{部件名: bytes}，以 / 结尾的目录项忽略、重新生成。
    [Content_Types].xml 在最前，其余按路径排序，每个文件夹先写目录项；文件 deflate、属性 0x20，
    目录项 stored、属性 0x10；create_system=0（DOS）、固定时间戳；.xml/.rels 的声明用双引号＋CRLF。"""
    import zipfile
    names = sorted(n for n in parts if not n.endswith('/') and n != '[Content_Types].xml')
    seq, dirs = ['[Content_Types].xml'], set()
    for n in names:
        segs = n.split('/')[:-1]
        for i in range(1, len(segs) + 1):
            d = '/'.join(segs[:i]) + '/'
            if d not in dirs:
                dirs.add(d)
                seq.append(d)
        seq.append(n)
    with zipfile.ZipFile(path, 'w') as z:
        for n in seq:
            zi = zipfile.ZipInfo(n, date_time=WPS_ZIP_TIME)
            zi.create_system, zi.create_version = 0, 20
            if n.endswith('/'):
                zi.compress_type, zi.extract_version, zi.external_attr = zipfile.ZIP_STORED, 10, 0x10
                z.writestr(zi, b'')
            else:
                zi.compress_type, zi.extract_version, zi.external_attr = zipfile.ZIP_DEFLATED, 20, 0x20
                data = parts[n]
                z.writestr(zi, wps_xml(data) if n.endswith(('.xml', '.rels')) else data)


APP_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}'


def update_app_stats(parts, pages=None):
    """改写 docProps/app.xml 的统计值（模板带的是马沟稿的 57 页、14955 字）。
    字数按 WPS 的算法估：汉字和全角标点每个算 1 字，连续的西文、数字算 1 字；pages 为 None 时不改页数。"""
    from lxml import etree
    if 'docProps/app.xml' not in parts or 'word/document.xml' not in parts:
        return
    app = etree.fromstring(parts['docProps/app.xml'])
    doc = etree.fromstring(parts['word/document.xml'])
    paras = [''.join(t.text or '' for t in p.iter(W + 't')) for p in doc.iter(W + 'p')]
    text = ''.join(paras)
    bare = re.sub(r'\s', '', text)
    cjk = len(re.findall('[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]', bare))
    latin = len(re.findall(r"[A-Za-z0-9]+(?:[.\-'][A-Za-z0-9]+)*", text))
    values = {'Words': cjk + latin, 'Characters': len(bare), 'CharactersWithSpaces': len(text),
              'Paragraphs': sum(1 for p in paras if p.strip())}
    if pages is not None:
        values['Pages'] = pages
    for tag, value in values.items():
        node = app.find(APP_NS + tag)
        if node is not None:
            node.text = str(value)
    parts['docProps/app.xml'] = etree.tostring(app, xml_declaration=True, encoding='UTF-8', standalone=True)


def wps_pack_problems(path):
    """与 WPS 另存的打包方式逐项比，返回不一致项（空列表＝一致）。"""
    import zipfile
    probs = []
    with zipfile.ZipFile(path) as z:
        inf = z.infolist()
        files = [i for i in inf if not i.filename.endswith('/')]
        dirs = {i.filename for i in inf if i.filename.endswith('/')}
        if not inf or inf[0].filename != '[Content_Types].xml':
            probs.append('第一项不是 [Content_Types].xml')
        need = {'/'.join(i.filename.split('/')[:k]) + '/' for i in files for k in range(1, i.filename.count('/') + 1)}
        if need - dirs:
            probs.append('缺目录项：%s' % sorted(need - dirs)[:3])
        if any(i.create_system != 0 for i in inf):
            probs.append('create_system 不是 0')
        if any(i.external_attr != 0x20 for i in files) or any(i.external_attr != 0x10 for i in inf if i.filename.endswith('/')):
            probs.append('文件/目录属性不是 0x20/0x10')
        bad = [i.filename for i in files if i.filename.endswith(('.xml', '.rels')) and not z.read(i.filename).startswith(XML_DECL)]
        if bad:
            probs.append('XML 声明不是双引号＋CRLF：%s' % bad[:3])
    return probs


def media_problems(path, max_ppi=225):
    """DOCX 里的插图逐张查：须为普通 JPEG（基线、RGB、不写 dpi），且按版面尺寸不超过 max_ppi。
    返回不合格项（空列表＝都合格）。依据见 docxgen.wps_safe_image 与 references/版式规范.md。"""
    import io
    import re
    import zipfile
    from PIL import Image
    probs = []
    with zipfile.ZipFile(path) as z:
        doc = z.read('word/document.xml').decode('utf8')
        rels = z.read('word/_rels/document.xml.rels').decode('utf8')
        tgt = {}
        for m in re.finditer(r'<Relationship\b[^>]*>', rels):
            a = dict(re.findall(r'(\w+)="([^"]*)"', m.group(0)))
            tgt[a.get('Id')] = 'word/' + a.get('Target', '')
        for dr in re.findall(r'<w:drawing>.*?</w:drawing>', doc, re.S):
            cx = re.search(r'<wp:extent cx="(\d+)"', dr)
            rid = re.search(r'r:embed="([^"]+)"', dr)
            if not (cx and rid) or tgt.get(rid.group(1)) not in z.namelist():
                probs.append('有一张图找不到图片文件（%s）' % (rid.group(1) if rid else '无 r:embed'))
                continue
            n = tgt[rid.group(1)]
            d = z.read(n)
            try:
                im = Image.open(io.BytesIO(d))
            except Exception as e:
                probs.append('%s 读不出（%s）' % (n, e))
                continue
            if (im.format != 'JPEG' or im.mode != 'RGB' or im.info.get('progressive') or im.info.get('progression')
                    or im.info.get('dpi') or im.info.get('jfif_unit', 0) != 0):
                probs.append('%s 不是普通 JPEG（%s %s）' % (n, im.format, im.mode))
            ppi = im.width / (int(cx.group(1)) / 914400.0)
            if ppi > max_ppi:
                probs.append('%s 按版面尺寸 %d ppi，超过 %d' % (n, ppi, max_ppi))
    return probs
