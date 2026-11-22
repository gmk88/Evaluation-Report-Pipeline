"""给 WPS 命令行导出用的 DOCX 副本嵌入字体（判例 PL-GEN-0059）。

本机 WPS 命令行（wpscli word2pdf）不按字体名找本机字体：仿宋、宋体被换成苹方，其他名字一律换成
Times New Roman 和 Songti SC。只有嵌进 DOCX 的字体它会照用。这里把报告用到的字体裁成报告里实际
出现的字，按 Word 的做法（混淆后的 .odttf）嵌进一份渲染专用副本；交稿的 DOCX 不嵌字体。
整份字体（如 20.8 MB、带 morx 表的 SimSunCN）嵌进去 WPS 不用，裁剪后才用。
算法取自晋钢烧结项目 工作区/工具/WPS导出/embed_fonts.py（2026-10-30 实测 294 页报告字体与 Word 版相同）。
"""

import io
import re
import sys
import uuid
import zipfile
from pathlib import Path

from lxml import etree as E


def _find(filename, *dirs):
    """在各字体文件夹里找同名文件；都没有时返回第一个位置（报错信息里显示它）。"""
    for d in dirs:
        if (d / filename).is_file():
            return d / filename
    return dirs[0] / filename


USER_FONTS = Path.home() / 'Library/Fonts'
MAC_FONTS = Path('/Library/Fonts')
SUPPLEMENTAL = Path('/System/Library/Fonts/Supplemental')
WORD_FONTS = Path('/Applications/Microsoft Word.app/Contents/Resources/DFonts')   # Word 自带，fsType=8 允许嵌入
# 字体名（与 DOCX 中 w:rFonts 一致）→ 常规、粗体字体文件。与 Word 导出的 PDF 用字一致。
FONTS = {
    '仿宋': (_find('FangSongCN-Regular.ttf', USER_FONTS, MAC_FONTS), None),
    '宋体': (_find('SimSunCN-Regular.ttf', USER_FONTS, MAC_FONTS), None),
    'Times New Roman': (_find('Times New Roman.ttf', SUPPLEMENTAL, MAC_FONTS),
                        _find('Times New Roman Bold.ttf', SUPPLEMENTAL, MAC_FONTS)),
    # 设计专篇封面、封二（WS/T 882—2013 附录D 附件1、附件2）：标题华文中宋、报告编号黑体
    '华文中宋': (_find('STZHONGS.ttf', USER_FONTS, MAC_FONTS, WORD_FONTS), None),
    '黑体': (_find('SimHei.ttf', USER_FONTS, MAC_FONTS, WORD_FONTS), None),
    # 设计专篇的公式（OMML）用 Cambria Math；Word 的 Cambria.ttc 里第 2 号字体（fsType=8 允许嵌入）
    'Cambria Math': (_find('Cambria.ttc', USER_FONTS, MAC_FONTS, WORD_FONTS), None),
}
# 字体文件是 TTC 合集时，用第几号字体（从 0 数）
TTC_NUMBER = {'Cambria Math': 1}
# 只在文档里真用到时才嵌入、才要求的字体（预评价不用，不给它的渲染副本多嵌两款字）
OPTIONAL = ('华文中宋', '黑体', 'Cambria Math')
# 找不到字体文件也不报错、只是不嵌的字体（公式字体：没装 Word 的电脑上出稿仍可进行，公式字体由 WPS 自行替换）
SOFT = ('Cambria Math',)
# 渲染副本里要改名的字体：WPS 命令行见到“黑体”这个名字就换成别的字，不用嵌进去的黑体（2026-11-04 实测
# 报告编号出成 SimSunCN）；渲染副本里把 w:rFonts 和嵌入字体的名字都改成 SimHeiCN，它就照用。交稿 DOCX 不改名。
RENDER_ALIAS = {'黑体': 'SimHeiCN'}
RE_ALIAS = re.compile(r'word/(document|header\d+|footer\d+|styles|numbering|fontTable)\.xml$')
W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PR_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
W = '{%s}' % W_NS
FONT_REL = R_NS + '/font'
ODTTF = 'application/vnd.openxmlformats-officedocument.obfuscatedFont'
FONT_RELS = 'word/_rels/fontTable.xml.rels'
# 常用全角符号；报告正文里的字从 DOCX 各部件收集
EXTRA = set('　、。〃〈〉《》「」『』【】〔〕〖〗—…‘’“”·×÷±≤≥≠≈～℃％‰§№°′″')
SETTINGS_BEFORE = ('writeProtection', 'view', 'zoom', 'removePersonalInformation', 'removeDateAndTime',
                   'doNotDisplayPageBoundaries', 'displayBackgroundShape', 'printPostScriptOverText',
                   'printFractionalCharacterWidth', 'printFormsData')


def _subset_module():
    """本机装了 fontTools 就用装好的，没装的用 skill 自带的（scripts/_vendor/）。"""
    try:
        from fontTools import subset
    except ImportError:
        sys.path.append(str(Path(__file__).resolve().parents[1] / '_vendor'))
        from fontTools import subset
    return subset


def font_files(used=None):
    """要嵌入的字体。used 是文档 w:rFonts 里用到的字体名，OPTIONAL 里的字体只在用到时嵌入；不给就全部。"""
    fonts = {k: v for k, v in FONTS.items() if used is None or k not in OPTIONAL or k in used}
    fonts = {k: v for k, v in fonts.items() if k not in SOFT or all(p is None or p.is_file() for p in v)}
    missing = [str(p) for pair in fonts.values() for p in pair if p and not p.is_file()]
    if missing:
        raise RuntimeError('缺少要嵌入的字体文件：' + '、'.join(missing))
    return fonts


def used_fonts(parts):
    """DOCX 正文、页眉页脚的 w:rFonts 里写到的东亚字体名。"""
    names = set()
    for name, data in parts.items():
        if re.match(r'word/(document|header\d+|footer\d+)\.xml$', name):
            for el in E.fromstring(data).iter(W + 'rFonts'):
                names.update(v for k, v in el.attrib.items() if k in (W + 'eastAsia', W + 'ascii', W + 'hAnsi'))
    return names


def used_chars(parts):
    chars = {chr(c) for c in range(0x20, 0x7f)} | {chr(c) for c in range(0xff01, 0xff5f)} | EXTRA
    for name, data in parts.items():
        if name.startswith('word/') and name.endswith('.xml'):
            for el in E.fromstring(data).iter():
                for s in (el.text, el.tail, *el.attrib.values()):
                    if s:
                        chars.update(s)
    return {c for c in chars if ord(c) >= 0x20}


def _rename_font(data, alias):
    """裁好的字体改族名、全名、PostScript 名（alias、alias、alias-Regular），给 RENDER_ALIAS 用。"""
    _subset_module()                    # 保证 fontTools 能导入（本机没装就用 _vendor/）
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(data))
    for rec in font['name'].names:
        if rec.nameID in (1, 4, 16):
            rec.string = alias
        elif rec.nameID == 6:
            rec.string = alias + '-Regular'
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def _alias_parts(parts):
    """渲染副本里把 RENDER_ALIAS 的字体名换成别名（只改 rFonts 等属性值和 fontTable 的 w:name）。"""
    for name in [n for n in parts if RE_ALIAS.match(n)]:
        root = E.fromstring(parts[name])
        hit = False
        for el in root.iter(W + 'rFonts', W + 'font', W + 'altName'):
            for k, v in el.attrib.items():
                if k.startswith(W) and v in RENDER_ALIAS:
                    el.set(k, RENDER_ALIAS[v])
                    hit = True
        if hit:
            parts[name] = E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def subset_font(path, chars, number=0):
    """number：path 是 .ttc 合集时取第几号字体。"""
    subset = _subset_module()
    opt = subset.Options()
    opt.name_IDs = ['*']
    opt.name_languages = ['*']
    opt.name_legacy = True
    opt.layout_features = ['*']
    opt.notdef_outline = True
    opt.hinting = True
    opt.drop_tables += ['morx', 'feat', 'meta', 'mort', 'kerx', 'trak', 'DSIG']
    if number:   # TTC 合集里的第 number 号字体
        from fontTools.ttLib import TTFont
        font = TTFont(str(path), fontNumber=number, lazy=True)
    else:
        font = subset.load_font(str(path), opt)
    sub = subset.Subsetter(opt)
    sub.populate(unicodes=sorted(ord(c) for c in chars))
    sub.subset(font)
    out = io.BytesIO()
    subset.save_font(font, out, opt)
    return out.getvalue()


def obfuscate(data, guid):
    """Word 的字体混淆：前 32 字节与 GUID 的 16 个字节（倒序）异或。异或两次即还原。"""
    key = bytes.fromhex(guid.hex)[::-1]
    out = bytearray(data)
    for i in range(32):
        out[i] ^= key[i % 16]
    return bytes(out)


def _font_table(data, embeds):
    root = E.fromstring(data)
    for name, items in embeds.items():
        same = [f for f in root.findall(W + 'font') if f.get(W + 'name') == name]
        for extra in same[1:]:
            root.remove(extra)
        if same:
            font = same[0]
            for child in list(font):
                if child.tag in (W + 'altName', W + 'embedRegular', W + 'embedBold',
                                 W + 'embedItalic', W + 'embedBoldItalic'):
                    font.remove(child)
        else:
            font = E.SubElement(root, W + 'font', {W + 'name': name})
            E.SubElement(font, W + 'charset', {W + 'val': '86' if not name.isascii() else '00'})
            E.SubElement(font, W + 'family', {W + 'val': 'auto'})
            E.SubElement(font, W + 'pitch', {W + 'val': 'variable'})
        for kind, rid, guid in items:
            E.SubElement(font, W + 'embed' + kind,
                         {'{%s}id' % R_NS: rid, W + 'fontKey': '{%s}' % str(guid).upper()})
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _settings(data):
    root = E.fromstring(data)
    if root.find(W + 'embedTrueTypeFonts') is None:
        flag = E.Element(W + 'embedTrueTypeFonts')
        before = [root.find(W + t) for t in SETTINGS_BEFORE]
        before = [el for el in before if el is not None]
        if before:
            before[-1].addnext(flag)
        else:
            root.insert(0, flag)
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _rels(data, items):
    root = E.fromstring(data) if data else E.Element('{%s}Relationships' % PR_NS, nsmap={None: PR_NS})
    for rel in [r for r in root if r.get('Type') == FONT_REL]:
        root.remove(rel)
    for rid, target in items:
        E.SubElement(root, '{%s}Relationship' % PR_NS, {'Id': rid, 'Type': FONT_REL, 'Target': target})
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def _content_types(data):
    root = E.fromstring(data)
    if not any(d.get('Extension', '').lower() == 'odttf' for d in root.findall('{%s}Default' % CT_NS)):
        root.insert(0, E.Element('{%s}Default' % CT_NS, {'Extension': 'odttf', 'ContentType': ODTTF}))
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


RE_MATH_VAR_RUN = re.compile(rb'<m:r>(<w:rPr><w:rFonts)(.*?)(</w:rPr>)(<m:t)', re.S)


def _wps_math_italic(parts):
    """渲染副本专用：WPS 命令行排 OMML 变量时把字母换成数学斜体字符（U+1D434…，不在基本平面），
    嵌入的字体里有这些字形也显示成两个方框。把没有样式的变量 run 改成“普通文字＋斜体”（nor＋w:i），
    字母不变、仍是 Cambria Math 斜体。交稿的 Word 不动（Word 自己会排数学斜体）。"""
    for name in [n for n in parts if n.startswith('word/') and n.endswith('.xml')]:
        if b'<m:oMath' in parts[name]:
            parts[name] = RE_MATH_VAR_RUN.sub(
                lambda m: b'<m:r><m:rPr><m:nor/></m:rPr><w:rPr><w:i/><w:iCs/><w:rFonts' + m.group(2) + m.group(3) + m.group(4),
                parts[name])


def render_copy(docx, out, cache=None):
    """把 docx 另存为嵌入字体的渲染副本 out，返回 [(字体名, 常规/粗体, 嵌入字节数)]。
    cache 传同一个 dict 时，第二轮直接用第一轮裁好的字体。"""
    cache = {} if cache is None else cache
    with zipfile.ZipFile(docx) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    for name in [n for n in parts if n.startswith('word/fonts/')]:
        del parts[name]
    if 'chars' not in cache:
        cache['chars'] = used_chars(parts)
    _wps_math_italic(parts)
    used = used_fonts(parts)
    if used & set(RENDER_ALIAS):
        _alias_parts(parts)
    embeds, rels, report = {}, [], []
    n = 0
    for name, pair in font_files(used).items():
        alias = RENDER_ALIAS.get(name)
        for kind, path in zip(('Regular', 'Bold'), pair):
            if path is None:
                continue
            key = (path, alias)
            if key not in cache:
                data = subset_font(path, cache['chars'], TTC_NUMBER.get(name, 0))
                cache[key] = _rename_font(data, alias) if alias else data
            n += 1
            guid = uuid.uuid4()
            rid, target = 'rIdEmbFont%d' % n, 'fonts/font%d.odttf' % n
            parts['word/' + target] = obfuscate(cache[key], guid)
            embeds.setdefault(alias or name, []).append((kind, rid, guid))
            rels.append((rid, target))
            report.append((name, kind, len(cache[key])))
    parts['word/fontTable.xml'] = _font_table(parts['word/fontTable.xml'], embeds)
    parts[FONT_RELS] = _rels(parts.get(FONT_RELS), rels)
    parts['word/settings.xml'] = _settings(parts['word/settings.xml'])
    parts['[Content_Types].xml'] = _content_types(parts['[Content_Types].xml'])
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return report
