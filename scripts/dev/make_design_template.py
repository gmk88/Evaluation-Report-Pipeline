#!/usr/bin/env python3
"""维护用：由预评价模板 templates/预评价报告模板.docx 生成设计专篇的两份模板（WS/T 882—2013 附录D）：
  templates/设计专篇模板.docx         主报告：封面（附件1）、封二声明（附件2 原文）、封三资质证书页、封四编写人员签字页、封五目录
  templates/设计专篇附件册模板.docx   资料性附件册：封面＋目录（WS/T 882 第8.1.3条，附件单独成册）
样式、编号、目录域、页码域、打包方式都沿用预评价模板（make_template.py 做的那些修复跟着带过来）；改的是前置页、
页面设置（A4 纵向，左28 右20 上25 下20 mm）、页眉页脚（宋体小五；页脚加日期）、目录制表位（版心宽 9184）。
正文每页 28 行、每行 30 字由生成器按 doctype.LAYOUT 排（固定行距 510、正文字距 26），模板里不设字符网格。
用法：python3 scripts/dev/make_design_template.py [预评价模板.docx] [输出文件夹]（默认 templates/ 下）"""
import copy
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree as E

DEV = Path(__file__).resolve().parent
sys.path.insert(0, str(DEV.parent))
sys.path.insert(0, str(DEV))
from pe.common import W, R, text_of, el, sub, SECT_ORDER, PPR_ORDER, order_children, write_docx  # noqa: E402
import make_template as MT  # noqa: E402

PIPE = DEV.parents[1]
SRC = PIPE / 'templates' / '预评价报告模板.docx'
MAIN, ANNEX = '设计专篇模板.docx', '设计专篇附件册模板.docx'
# 页面（twip）：附录D.2 左28 右20 上25 下20 mm。页眉、页脚距边 720、680：两行页眉、一行页脚都落在页边距里，
# 不把正文挤小（正文高 16838−1418−1134＝14286，28 行×510 正好排下）
PORTRAIT_MAR = dict(top=1418, right=1134, bottom=1134, left=1588, header=720, footer=680, gutter=0)
# 横向页（宽表用，附录D.2 写纵向排版，横向只给宽表）：纵向页转 90°，装订边在上；版心宽 16838−1418−1134＝14286
LANDSCAPE_MAR = dict(top=1588, right=1134, bottom=1134, left=1418, header=720, footer=680, gutter=0)
TEXT_W, LAND_W = 9184, 14286
TOC_TAB = TEXT_W - 100          # 目录页码右对齐制表位（预评价 8300 版心用 8200，同样留 100）
FONT_TITLE, FONT_NO, FONT_BODY = '华文中宋', '黑体', '仿宋'
STATEMENT = ('{{评价单位}}遵守国家有关法律、法规，在{{项目名称}}职业病防护设施设计中优先采用有利于职业病防治的新工艺、'
             '新方法和新材料，确保所出具的《{{项目名称}}职业病防护设施设计专篇》的真实性、合法性和实用性，并对其承担法律责任。')
SIGNS = ['设计单位：（加盖公章）', '设计单位法定代表人：（签名）', '项目负责人：（签名）', '专篇审核人：（签名）']


def run(text, size, font=FONT_BODY, bold=False, latin='Times New Roman'):
    r = el('r')
    rp = sub(r, 'rPr')
    sub(rp, 'rFonts', ascii=latin, hAnsi=latin, eastAsia=font, cs=font, hint='eastAsia')
    if bold:
        sub(rp, 'b')
        sub(rp, 'bCs')
    sub(rp, 'color', val='000000')
    sub(rp, 'sz', val=size)
    sub(rp, 'szCs', val=size)
    sub(rp, 'lang', val='en-US', eastAsia='zh-CN')
    t = sub(r, 't')
    t.text = text
    return r


def para(text, size, font=FONT_BODY, bold=False, jc='center', before=0, after=0, line=360, rule='auto',
         left=0, first=0, right=0, page_break=False, latin='Times New Roman'):
    """前置页段落：写明缩进（正文默认样式带首行缩进 2 字，不写时居中段整体右偏）、段前段后和行距。"""
    p = el('p')
    pp = sub(p, 'pPr')
    if page_break:
        sub(pp, 'pageBreakBefore')
    sub(pp, 'spacing', before=before, after=after, line=line, lineRule=rule)
    ind = sub(pp, 'ind', left=left, firstLine=first, firstLineChars=0)
    if right:
        ind.set(W + 'right', str(right))
    sub(pp, 'jc', val=jc)
    order_children(pp, PPR_ORDER)
    if text:
        p.append(run(text, size, font, bold, latin))
    return p


def number_line():
    """封面、封二顶部的“报告编号：”（附件1、附件2：黑体四号）。"""
    return para('报告编号：{{报告编号}}', 28, FONT_NO, jc='left', latin=FONT_NO)


def cover(annex):
    """封面（附件1）：报告编号；建设单位名称、建设项目名称、职业病防护设施设计专篇（华文中宋一号加粗）；
    设计单位资质证书编号；设计单位；日期。附件册再加一行“资料性附件册”。"""
    out = [number_line()]
    titles = ['{{封面名称}}', '职业病防护设施设计专篇'] + (['资料性附件册'] if annex else [])
    for i, t in enumerate(titles):
        out.append(para(t, 52, FONT_TITLE, bold=True, before=2000 if i == 0 else 0))
    out.append(para('设计单位资质证书编号：{{资质证书编号}}', 32, bold=True, before=2400 if annex else 3000))
    out.append(para('{{评价单位}}', 32, bold=True, before=1200))
    out.append(para('{{报告日期}}', 32, bold=True, before=200))
    return out


def front_title(text, page_break=True):
    return para(text, 44, bold=True, before=0, after=600, page_break=page_break)


def statement():
    """封二（附件2）：报告编号；声明原文；四行签字；日期。仿宋三号。"""
    out = [number_line(), para('声　明', 44, bold=True, before=600, after=600)]
    out.append(para(STATEMENT, 32, jc='both', line=600, rule='exact', first=640))
    for i, s in enumerate(SIGNS):
        out.append(para(s, 32, jc='left', before=1200 if i == 0 else 0, line=720, rule='exact', left=1120))
    out.append(para('{{报告日期}}', 32, jc='right', before=960, line=600, rule='exact', right=640))
    return out


def licence():
    """封三：设计单位资质证书影印件（占位页，出稿后放影印件）。"""
    return [front_title('设计单位资质证书影印件'),
            para('（此页附设计单位资质证书影印件，并加盖设计单位公章）', 28, before=2400)]


def footer_xml(tabpos):
    """页脚（附录D.1）：设计单位名称、日期、第X页共XX页，宋体小五。“共N页”仍用 PAGEREF BodyEnd，finalize 回填。"""
    root = E.Element(W + 'ftr', nsmap={'w': MT.NS['w'], 'r': MT.NS['r']})
    p = sub(root, 'p')
    pp = sub(p, 'pPr')
    sub(sub(pp, 'pBdr'), 'top', val='single', sz=6, space=4, color='000000')
    sub(sub(pp, 'tabs'), 'tab', val='right', pos=tabpos)
    sub(pp, 'spacing', before=80, after=0, line=232, lineRule='exact')
    sub(pp, 'ind', firstLine=0, left=0, right=0)
    p.append(MT.run_xml('{{评价单位}}　{{报告日期}}'))
    tab = MT.bare_run()
    sub(tab, 'tab')
    p.append(tab)
    p.append(MT.run_xml('第'))
    MT.field_runs(p, 'PAGE', '1')
    p.append(MT.run_xml('页 共'))
    MT.field_runs(p, 'PAGEREF BodyEnd \\h', '0')
    p.append(MT.run_xml('页'))
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def set_page(sp, mar, w, h, landscape=False):
    pg = sp.find(W + 'pgSz')
    pg.attrib.clear()
    pg.set(W + 'w', str(w))
    pg.set(W + 'h', str(h))
    if landscape:
        pg.set(W + 'orient', 'landscape')
    pm = sp.find(W + 'pgMar')
    pm.attrib.clear()
    for k in ('top', 'right', 'bottom', 'left', 'header', 'footer', 'gutter'):
        pm.set(W + k, str(mar[k]))
    order_children(sp, SECT_ORDER)


def set_tabs(p, pos):
    for tab in p.iter(W + 'tab'):
        if tab.getparent().tag == W + 'tabs':
            tab.set(W + 'pos', str(pos))


def build(parts, annex):
    parts = dict(parts)
    doc = E.fromstring(parts['word/document.xml'])
    body = doc.find(W + 'body')
    kids = list(body)
    texts = [text_of(k) for k in kids]
    cover_end = next(i for i, k in enumerate(kids) if k.find('.//' + W + 'sectPr') is not None)
    i_toc = next(i for i, t in enumerate(texts) if re.sub(r'\s+', '', t) == '目录')
    i_tbl = next(i for i, k in enumerate(kids) if k.tag == W + 'tbl' and '{{姓名}}' in texts[i])
    i_att = next(i for i, t in enumerate(texts) if '{{附件标题}}' in t)
    # 封面：换成附录D附件1 的版式；分节段留着
    for k in kids[:cover_end]:
        body.remove(k)
    anchor = kids[cover_end]
    for p in cover(annex):
        anchor.addprevious(p)
    # 封二～封四：声明、资质证书页、签字页（附件册没有）；目录标题前插
    people = kids[i_tbl]
    for k in kids[cover_end + 1:i_toc]:
        if k is not people:
            body.remove(k)
    toc_title = kids[i_toc]
    if annex:
        body.remove(people)
        pp = toc_title.find(W + 'pPr')
        for x in pp.findall(W + 'pageBreakBefore'):
            pp.remove(x)   # 目录就是分节后的第一页
    else:
        for p in statement() + licence() + [front_title('参加专篇编写人员签字页'), para('', 28)]:
            people.addprevious(p)
        people.addnext(para('', 28))
    # 目录：去掉附件清单（附件单独成册）；页码制表位按版心宽
    for k in kids[i_att:i_att + 2]:
        body.remove(k)
    for k in kids[i_toc + 1:i_att]:
        set_tabs(k, TOC_TAB)
    # 正文占位段、横向分节段的行距、缩进按专篇正文
    for k in body.iter(W + 'p'):
        if text_of(k) in ('{{正文}}', '{{横向分节}}'):
            k.find(W + 'pPr/' + W + 'spacing').set(W + 'line', '510')
            k.find(W + 'pPr/' + W + 'ind').set(W + 'firstLine', '612')
    # 页面设置：纵向各节、横向节
    for sp in body.iter(W + 'sectPr'):
        if sp.find(W + 'pgSz').get(W + 'orient') == 'landscape':
            set_page(sp, LANDSCAPE_MAR, 16838, 11906, landscape=True)
        else:
            set_page(sp, PORTRAIT_MAR, 11906, 16838)
    parts['word/document.xml'] = E.tostring(doc, xml_declaration=True, encoding='UTF-8', standalone=True)
    # 样式 toc 1～3 的制表位与目录条一致（Word／WPS 里“更新整个目录”按样式重排）
    sty = E.fromstring(parts['word/styles.xml'])
    for st in sty.findall(W + 'style'):
        if re.match(r'TOC[123]$', st.get(W + 'styleId', '')):
            set_tabs(st, TOC_TAB)
    # 样式 heading 4（计算书的“计算依据、计算公式……”四级标题，不进目录）：字体、加粗与 heading 3 一致，不斜体
    # （预评价模板里它用主题字体且带斜体，中文标题斜体不对）；outlineLvl 3、semiHidden 的设定不动
    by_id = {st.get(W + 'styleId'): st for st in sty.findall(W + 'style')}
    h3, h4 = by_id.get('5'), by_id.get('6')
    if h3 is not None and h4 is not None and h4.find(W + 'name').get(W + 'val') == 'heading 4':
        h4.replace(h4.find(W + 'rPr'), copy.deepcopy(h3.find(W + 'rPr')))
        c4 = by_id.get('153')       # Heading 4 Char：同样去掉斜体
        if c4 is not None:
            for tag in ('i', 'iCs'):
                for x in c4.findall(W + 'rPr/' + W + tag):
                    x.getparent().remove(x)
    parts['word/styles.xml'] = E.tostring(sty, xml_declaration=True, encoding='UTF-8', standalone=True)
    # 页眉两行（{{页眉第一行}}／{{页眉第二行}}＋报告编号）、页脚（单位、日期、页码），宋体小五
    parts['word/header1.xml'] = MT.header_xml(TEXT_W)
    parts['word/header2.xml'] = MT.header_xml(TEXT_W)
    parts['word/header3.xml'] = MT.header_xml(LAND_W)
    parts['word/footer3.xml'] = footer_xml(TEXT_W)
    parts['word/footer4.xml'] = footer_xml(LAND_W)
    # 文档属性：模板里不留马沟、预评价字样（生成器出稿时按本报告重写）
    core = E.fromstring(parts['docProps/core.xml'])
    name = '职业病防护设施设计专篇' + ('资料性附件册' if annex else '')
    for tag, text in (('{http://purl.org/dc/elements/1.1/}title', name),
                      ('{http://purl.org/dc/elements/1.1/}subject', '建设项目' + name),
                      ('{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}keywords',
                       '职业病防护设施设计专篇' + (';资料性附件册' if annex else ';职业病防护设施三同时')),
                      ('{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}lastModifiedBy', '')):
        node = core.find(tag)
        if node is not None:
            node.text = text
    parts['docProps/core.xml'] = E.tostring(core, xml_declaration=True, encoding='UTF-8', standalone=True)
    return parts


def fonts(parts):
    """字体表登记华文中宋（封面标题用；黑体模板里已有），Word／WPS 没装这款字时按字体表找替代。"""
    ft = E.fromstring(parts['word/fontTable.xml'])
    if not any(f.get(W + 'name') == FONT_TITLE for f in ft.findall(W + 'font')):
        f = sub(ft, 'font', name=FONT_TITLE)
        sub(f, 'altName', val='STZhongsong')
        sub(f, 'panose1', val='02010600040101010101')
        sub(f, 'charset', val='86')
        sub(f, 'family', val='auto')
        sub(f, 'pitch', val='variable')
    parts['word/fontTable.xml'] = E.tostring(ft, xml_declaration=True, encoding='UTF-8', standalone=True)
    return parts


def main(src=SRC, out_dir=PIPE / 'templates'):
    with zipfile.ZipFile(src) as z:
        order = [i.filename for i in z.infolist()]
        parts = {n: z.read(n) for n in order}
    parts = fonts(parts)
    out = []
    for annex, name in ((False, MAIN), (True, ANNEX)):
        p = build(parts, annex)
        dst = Path(out_dir) / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        write_docx(dst, {n: p[n] for n in order if n in p})   # 打包方式与 WPS 另存一致
        print('模板已生成：', dst)
        out.append(dst)
    return out


if __name__ == '__main__':
    a = sys.argv[1:]
    main(Path(a[0]) if a else SRC, Path(a[1]) if len(a) > 1 else PIPE / 'templates')
