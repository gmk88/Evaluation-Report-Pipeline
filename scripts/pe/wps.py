"""用 WPS 命令行（wpscli word2pdf）出 PDF：两轮导出，回填目录页码和“共N页”，逐页核对（判例 PL-GEN-0059）。

- WPS 命令行只认嵌在 DOCX 里的字体：每轮从核对版另存一份嵌入裁剪字体的渲染副本去导出（embed_fonts.py）；
  核对版和交稿版 DOCX 不嵌字体。
- 第一轮按 PDF 回填核对版的目录页码和“共N页”，第二轮用回填后的副本重新导出，逐页核对。
- 导出后查字体（要有仿宋、宋体，正文不能有苹方等替换字体）和插图张数；任何一项不过就停，不交付。
- 每次运行的文件都放在一个核对文件夹里（默认 项目/WPS核对/时间戳/）；加“发布”时，核对通过后把核对版
  DOCX 和 PDF 复制到 输出/，原来的文件先存到 核对文件夹/发布前的输出/。
做法取自晋钢烧结项目 工作区/工具/WPS导出/（2026-10-30），路径改为按项目文件夹取。
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from lxml import etree as E

from . import embed_fonts, finalize
from .common import W, text_of

WPS_CANDIDATES = ('/Applications/wpsoffice.app/Contents/MacOS/wpscli',)
IS_WIN = sys.platform == 'win32'
OFFICE = 'office'           # Windows 上的导出方式：WPS 或 Word 程序（COM）
WPS_EXIT = {3: '导出超时', 100: '没有登录 WPS 账号', 101: 'WPS 账号没有会员权限', 201: '缺少参数',
            203: '找不到要导出的文件', 207: '文件超过 200 MB', 211: '输出文件夹不存在',
            212: '没有输出文件夹的写权限'}
REQUIRED_FONTS = {'仿宋': re.compile(r'FangSong|FangS-', re.I),
                  '宋体': re.compile(r'SimSun', re.I)}
# 设计专篇封面、封二用的字体（WS/T 882 附录D 附件1、附件2）：Word 里用到才要求 PDF 里有
OPTIONAL_FONTS = {'华文中宋': re.compile(r'STZhongsong|STZhongS', re.I),
                  '黑体': re.compile(r'SimHei', re.I)}
# WPS 命令行认不出字体时换上的字体；正文出现即说明有文字没用上嵌入字体
SUBSTITUTES = re.compile(r'PingFang|STSongti|Songti|STHeiti|Heiti', re.I)
LEADER = re.compile(r'^[.·…]+$')
RID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed'
BLIP = '{http://schemas.openxmlformats.org/drawingml/2006/main}blip'
SOURCE_GLOBS = ('正文/*.md', '项目信息.md', '检查规则.md', '图/*')


class WpsError(Exception):
    pass


def find_wps():
    for c in (os.environ.get('PE_WPSCLI'), shutil.which('wpscli'), *WPS_CANDIDATES):
        if c and Path(c).is_file():
            return Path(c)
    raise WpsError('找不到 WPS 命令行（wpscli）。macOS 装 WPS 后在 %s；装在别处的设环境变量 PE_WPSCLI。'
                   '没有 WPS 的电脑用 build（LibreOffice）或 SKILL.md 第3节的 Word 做法' % WPS_CANDIDATES[0])


def find_exporter():
    """Mac：WPS 命令行（wpscli）；Windows：本机 WPS 或 Word（office_win.py，经 COM 导出）。"""
    return OFFICE if IS_WIN else find_wps()


def _render_copy(copy, render, cache):
    """Mac 的 WPS 命令行只认嵌在 DOCX 里的字体，导出前另存一份嵌入裁剪字体的副本；
    Windows 用 WPS／Word 程序导出，系统里有仿宋、宋体，原样复制一份。返回嵌入了哪些字体。"""
    if IS_WIN:
        render.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(copy, render)
        return []
    return embed_fonts.render_copy(copy, render, cache)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def export(wps, docx, pdf):
    if wps == OFFICE:
        from . import office_win
        try:
            office_win.export_pdf(docx, pdf)
        except office_win.OfficeError as e:
            raise WpsError(str(e))
        return
    command = [str(wps), 'word2pdf', str(docx), '--output', str(pdf), '--timeout', '600', '--json']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=630)
    except subprocess.TimeoutExpired:
        raise WpsError('WPS 导出超时（10 分钟）：%s' % docx)
    if result.returncode or not pdf.is_file() or pdf.stat().st_size < 1000:
        why = WPS_EXIT.get(result.returncode, '')
        raise WpsError('WPS 导出失败（退出码 %s%s）：%s %s' % (result.returncode, '，' + why if why else '',
                                                        result.stdout[-600:], result.stderr[-300:]))
    if not any('"type":"completed"' in line and '"status":"success"' in line for line in result.stdout.splitlines()):
        raise WpsError('WPS 没有确认导出成功：' + result.stdout[-600:])


def _fitz():
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    return fitz


def pdf_fonts(pdf):
    """PDF 用到的字体名（去掉子集前缀），和用了替换字体的文字段 [(页, 字体, 文字)]（纯引导点不算）。"""
    fitz = _fitz()
    names, spans = set(), []
    with fitz.open(pdf) as doc:
        for number, page in enumerate(doc, 1):
            names.update(f[3].split('+')[-1] for f in page.get_fonts(full=True))
            for block in page.get_text('dict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        text = span['text'].strip()
                        if text and SUBSTITUTES.search(span['font']) and not LEADER.match(text):
                            spans.append((number, span['font'].split('+')[-1], text[:20]))
    return names, spans


def font_problems(names, spans, extra=()):
    """按字体名和替换字体文字段给出问题（空表＝通过）。extra：Word 里用到的 OPTIONAL_FONTS 字体名，也要求有。"""
    problems = []
    need = dict(REQUIRED_FONTS, **{k: OPTIONAL_FONTS[k] for k in extra if k in OPTIONAL_FONTS})
    missing = [label for label, pattern in need.items() if not any(pattern.search(n) for n in names)]
    if missing:
        problems.append('PDF 没有用上%s（实际字体：%s）：%s' % ('、'.join(missing), '、'.join(sorted(names)),
                                                       '这台电脑可能没装这些字体' if IS_WIN else 'WPS 没用嵌入字体'))
    if spans:
        page, font, text = spans[0]
        problems.append('PDF 里有 %d 处文字用了替换字体（如第%d页“%s”，%s），没用上嵌入字体' % (len(spans), page, text, font))
    return problems


def docx_images(docx):
    """正文插图张数（document.xml 里不同的图片引用）。"""
    with zipfile.ZipFile(docx) as z:
        root = E.fromstring(z.read('word/document.xml'))
    return len({b.get(RID) for b in root.iter(BLIP) if b.get(RID)})


def pdf_images(pdf):
    fitz = _fitz()
    with fitz.open(pdf) as doc:
        return len({im[0] for page in doc for im in page.get_images(full=True)})


def docx_fonts(docx):
    """Word 里用到的 OPTIONAL_FONTS 字体。"""
    with zipfile.ZipFile(docx) as z:
        parts = {n: z.read(n) for n in z.namelist() if re.match(r'word/(document|header\d+|footer\d+)\.xml$', n)}
    used = embed_fonts.used_fonts(parts)
    return [k for k in OPTIONAL_FONTS if k in used]


def check_pdf(pdf, images, extra=()):
    problems = font_problems(*pdf_fonts(pdf), extra=extra)
    got = pdf_images(pdf)
    if got < images:
        problems.append('PDF 里只有 %d 张图，Word 里有 %d 张：WPS 丢了图（大 PNG 常见），查 图/ 里的图片格式' % (got, images))
    if problems:
        raise WpsError('；'.join(problems) + '。这一版不能交付')


def docx_headings(docx):
    with zipfile.ZipFile(docx) as archive:
        styles = finalize._styles(archive)
        root = E.fromstring(archive.read('word/document.xml'))
    headings = []
    for paragraph in root.iter(W + 'p'):
        style = paragraph.find(W + 'pPr/' + W + 'pStyle')
        name = styles.get(style.get(W + 'val'), '') if style is not None else ''
        if name.startswith('heading ') and name[8:].isdigit():
            headings.append((int(name[8:]), text_of(paragraph)))
    return headings


def stale_sources(root, docx):
    """比 docx 新的源文件（改了源文件还没重新 build）。"""
    t = Path(docx).stat().st_mtime
    return sorted(str(p.relative_to(root)) for g in SOURCE_GLOBS for p in Path(root).glob(g)
                  if p.is_file() and not p.name.startswith('.') and p.stat().st_mtime > t + 1)


def run(root, docx, headings, out=None, publish=False, log=print):
    """root 项目文件夹，docx 输出/ 里的报告，headings 源文件的标题 [(级别, 文字)]。返回结果字典；不通过抛 WpsError。"""
    root, docx = Path(root).resolve(), Path(docx).resolve()
    source_dir = root / '输出'
    if not docx.is_file():
        raise WpsError('找不到 Word：%s（先 build --不出PDF）' % docx)
    if docx.parent != source_dir:
        raise WpsError('Word 必须在本项目的 输出/ 里：%s' % docx)
    if docx_headings(docx) != headings:
        raise WpsError('输出/ 里的 Word 与源文件标题不一致：先 build --不出PDF 再导出')
    newer = stale_sources(root, docx)
    if newer:
        raise WpsError('这些源文件比 输出/ 里的 Word 新：%s 等；先 build --不出PDF 再导出' % '、'.join(newer[:3]))
    wps = find_exporter()
    dest = Path(out).resolve() if out else root / 'WPS核对' / datetime.now().strftime('%Y%m%d_%H%M%S')
    if dest == source_dir or (dest.exists() and any(dest.iterdir())):
        raise WpsError('核对文件夹必须是空的，且不能是项目的 输出/：%s' % dest)
    if str(dest).startswith(('/tmp/', '/private/tmp/')):
        raise WpsError('核对文件夹不要放在 /tmp（WPS 命令行读不了那里的文件）：%s' % dest)
    dest.mkdir(parents=True, exist_ok=True)
    source_hash = digest(docx)
    copy = dest / docx.name
    shutil.copy2(docx, copy)
    images = docx_images(copy)
    extra = docx_fonts(copy)
    render = dest / '渲染用' / copy.name
    cache = {}
    log('第一轮：嵌入字体、WPS 导出、回填目录页码……')
    embedded = _render_copy(copy, render, cache)
    with tempfile.TemporaryDirectory(prefix='wps-first-', dir=dest) as first_dir:
        first = Path(first_dir) / 'first.pdf'
        export(wps, render, first)
        check_pdf(first, images, extra)
        initial = finalize.apply(copy, first, headings)
        if initial['找不到的标题'] or initial['目录条数'] != len([h for h in headings if h[0] <= 3]):
            raise WpsError('第一轮 PDF 缺标题或目录条数不符：%s' % initial['找不到的标题'][:5])
    log('第二轮：用回填后的 Word 重新导出、逐页核对……')
    _render_copy(copy, render, cache)
    final = copy.with_suffix('.pdf')
    export(wps, render, final)
    check_pdf(final, images, extra)
    checked = finalize.apply(copy, final, headings, check=True)
    if checked['错误'] or checked['找不到的标题']:
        raise WpsError('第二轮核对没通过：' + '；'.join(checked['错误'][:5] + checked['找不到的标题'][:5]))
    if digest(docx) != source_hash:
        raise WpsError('输出/ 里的 Word 在导出期间被改过；这次的核对版只对应开始时的 Word，重新导出')
    names, _ = pdf_fonts(final)
    res = dict(checked, 核对文件夹=dest, 核对版Word=copy, 核对版PDF=final, 插图=images,
               字体='、'.join(sorted(names)),
               嵌入字体='；'.join('%s %s %d KB' % (n, k, b // 1024) for n, k, b in embedded))
    if publish:
        backup = dest / '发布前的输出'
        backup.mkdir()
        pdf_out = docx.with_suffix('.pdf')
        for old in (docx, pdf_out):
            if old.exists():
                shutil.copy2(old, backup / old.name)
        shutil.copy2(copy, docx)
        shutil.copy2(final, pdf_out)
        res['发布'] = backup
    return res
