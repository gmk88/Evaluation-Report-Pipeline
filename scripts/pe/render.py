"""DOCX → PDF（LibreOffice 无界面转换）。
字体：本机装有仿宋（FangSong）就直接用；没有时用 NotoSansSC 改名生成“仿宋替身”（只影响本机渲染，
不改 DOCX），保证分页与装有仿宋的电脑基本一致。"""
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from lxml import etree as E

CACHE = Path(os.environ.get('PE_CACHE', Path.home() / '.cache' / 'pe-report'))
OOR = 'http://openoffice.org/2001/registry'
NOTO = ['/usr/share/fonts/google-noto-cjk/NotoSansSC-Regular.otf',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
        '/System/Library/Fonts/Supplemental/Songti.ttc']


class RenderError(Exception):
    pass


def find_soffice():
    cands = [os.environ.get('PE_SOFFICE'), shutil.which('soffice'), shutil.which('libreoffice'),
             '/usr/local/bin/soffice', '/usr/bin/soffice',
             '/Applications/LibreOffice.app/Contents/MacOS/soffice',
             r'C:\Program Files\LibreOffice\program\soffice.exe',
             r'C:\Program Files (x86)\LibreOffice\program\soffice.exe']
    for c in cands:
        if c and Path(c).exists():
            return c
    raise RenderError('没找到 LibreOffice（soffice）。请先安装 LibreOffice，或设置环境变量 PE_SOFFICE 指向 soffice。')


def has_fangsong():
    fc = shutil.which('fc-list')
    if not fc:
        return platform.system() == 'Windows'
    out = subprocess.run([fc, ':', 'family'], capture_output=True, text=True).stdout
    return any(x in out for x in ('FangSong', '仿宋', 'STFangsong', '华文仿宋'))


def make_substitute_font():
    """NotoSansSC 改名 FangSong，纵向度量 859/-141/0（与马沟渲染链一致，否则会多出约 10 页）。"""
    dst = CACHE / 'fonts' / 'FangSong-NotoSansSC-substitute.otf'
    if dst.exists():
        return dst
    src = next((p for p in NOTO if Path(p).exists() and p.endswith('.otf')), None)
    if not src:
        return None
    from fontTools.ttLib import TTFont
    f = TTFont(src)
    name = f['name']
    for r in name.names:
        if r.nameID in (1, 4, 6, 16, 17):
            r.string = 'FangSong'
        if r.nameID == 2:
            r.string = 'Regular'
    for nid, val in [(1, 'FangSong'), (2, 'Regular'), (4, 'FangSong')]:
        name.setName(val, nid, 3, 1, 2052)
    name.names = [r for r in name.names if r.nameID not in (16, 17)]
    h, o = f['hhea'], f['OS/2']
    h.ascent, h.descent, h.lineGap = 859, -141, 0
    o.sTypoAscender, o.sTypoDescender, o.sTypoLineGap = 859, -141, 0
    o.usWinAscent, o.usWinDescent = 859, 141
    o.fsSelection = 64
    dst.parent.mkdir(parents=True, exist_ok=True)
    f.save(dst)
    return dst


def _profile(path):
    user = path / 'user'
    user.mkdir(parents=True)
    root = E.Element('{%s}items' % OOR, nsmap={'oor': OOR})
    for index, (source, target) in enumerate([('仿宋', 'FangSong'), ('宋体', 'SimSun')]):
        item = E.SubElement(root, 'item', {'{%s}path' % OOR: '/org.openoffice.Office.Common/Font/Substitution/FontPairs'})
        node = E.SubElement(item, 'node', {'{%s}name' % OOR: '_%d' % index, '{%s}op' % OOR: 'replace'})
        for key, value in [('Always', 'true'), ('ReplaceFont', source), ('OnScreenOnly', 'false'),
                           ('SubstituteFont', target)]:
            prop = E.SubElement(node, 'prop', {'{%s}name' % OOR: key, '{%s}op' % OOR: 'fuse'})
            E.SubElement(prop, 'value').text = value
    item = E.SubElement(root, 'item', {'{%s}path' % OOR: '/org.openoffice.Office.Common/Font/Substitution'})
    prop = E.SubElement(item, 'prop', {'{%s}name' % OOR: 'Replacement', '{%s}op' % OOR: 'fuse'})
    E.SubElement(prop, 'value').text = 'true'
    E.ElementTree(root).write(str(user / 'registrymodifications.xcu'), encoding='UTF-8', xml_declaration=True)


def to_pdf(docx, out_dir):
    """返回 (pdf路径, 说明)。"""
    docx, out_dir = Path(docx).resolve(), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    soffice = find_soffice()
    env = os.environ.copy()
    note = '使用本机仿宋字体'
    substitute = not has_fangsong()
    if substitute:
        font = make_substitute_font()
        if font:
            conf = CACHE / 'fonts.conf'
            conf.write_text('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n<fontconfig>\n'
                            '  <include ignore_missing="yes">/etc/fonts/fonts.conf</include>\n'
                            '  <dir>%s</dir>\n  <cachedir>%s</cachedir>\n</fontconfig>\n'
                            % (font.parent, CACHE / 'fontcache'), encoding='utf-8')
            env['FONTCONFIG_FILE'] = str(conf)
            note = '本机没有仿宋，用 NotoSansSC 替身字体排版（页码与装仿宋的电脑基本一致）'
        else:
            note = '本机没有仿宋，也没找到替身字体，页码可能与 Word 不同'
    with tempfile.TemporaryDirectory(prefix='pe_lo_') as prof:
        # On macOS an installed 仿宋 resolves to FangSongCN; forcing an alias
        # can make LibreOffice fall back to Kai instead of using that font.
        if substitute:
            _profile(Path(prof))
        try:
            subprocess.run([soffice, '-env:UserInstallation=' + Path(prof).as_uri(), '--headless',
                            '--convert-to', 'pdf', '--outdir', str(out_dir), str(docx)],
                           env=env, check=True, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            raise RenderError('LibreOffice 转换失败：%s' % e)
    pdf = out_dir / (docx.stem + '.pdf')
    if not pdf.exists():
        raise RenderError('LibreOffice 没有生成 PDF：%s' % pdf)
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    fonts = {f[3] for page in fitz.open(pdf) for f in page.get_fonts()}
    if not any('FangSong' in f or 'Fangsong' in f for f in fonts):
        note += '；注意：PDF 未用仿宋（实际字体：%s）' % '、'.join(sorted(fonts)[:5])
    return pdf, note
