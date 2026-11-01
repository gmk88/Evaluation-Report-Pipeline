#!/usr/bin/env python3
"""维护用：由样板报告（马沟最终版）DOCX 生成通用模板 templates/预评价报告模板.docx。
保留样式、编号、主题、设置；封面、声明、人员表、目录、附件清单改成 {{占位符}}；正文换成 {{正文}}；
页眉页脚整理成 前置／纵向正文／横向正文 三套。换评价机构的版式时，用本机构的样板报告重跑本脚本。
用法：python3 scripts/dev/make_template.py 样板报告.docx templates/预评价报告模板.docx"""
import copy
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree as E

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pe.common import W, R, NS, text_of, el, sub, SECT_ORDER, PPR_ORDER, order_children, write_docx  # noqa: E402

CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
PR = '{http://schemas.openxmlformats.org/package/2006/relationships}'
HDR_T = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/header'
FTR_T = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer'
IMG_T = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'
TOC_IND = {1: 0, 2: 212, 3: 480}   # 目录各级左缩进，twip


def set_text(p, s):
    ts = p.findall('.//' + W + 't')
    ts[0].text = s
    if s != s.strip() or '  ' in s:
        ts[0].set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
    for t in ts[1:]:
        t.getparent().remove(t)


def run_xml(text, size=18, font='宋体'):
    r = el('r')
    rp = sub(r, 'rPr')
    sub(rp, 'rFonts', ascii='Times New Roman', hAnsi='Times New Roman', eastAsia=font, cs=font, hint='eastAsia')
    sub(rp, 'color', val='000000')
    sub(rp, 'sz', val=size)
    sub(rp, 'szCs', val=size)
    sub(rp, 'lang', val='en-US', eastAsia='zh-CN')
    sub(r, 't').text = text
    return r


def header_xml(tabpos):
    root = E.Element(W + 'hdr', nsmap={'w': NS['w'], 'r': NS['r']})
    p1 = sub(root, 'p')
    pp = sub(p1, 'pPr')
    sub(pp, 'spacing', before=0, after=0, line=240, lineRule='exact')
    sub(pp, 'ind', left=0, right=0, firstLine=0)
    sub(pp, 'jc', val='left')
    p1.append(run_xml('{{页眉第一行}}'))
    p2 = sub(root, 'p')
    pp = sub(p2, 'pPr')
    sub(sub(pp, 'pBdr'), 'bottom', val='single', color='000000', sz=4, space=4)
    sub(sub(pp, 'tabs'), 'tab', val='right', pos=tabpos)
    sub(pp, 'spacing', before=0, after=0, line=240, lineRule='exact')
    sub(pp, 'ind', left=0, right=0, firstLine=0)
    sub(pp, 'jc', val='left')
    p2.append(run_xml('{{页眉第二行}}'))
    sub(sub(p2, 'r'), 'tab')
    p2.append(run_xml('报告编号：{{报告编号}}'))
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def bare_run():
    """只带页脚字体格式、没有文字的 run，放制表符和域标记。"""
    r = run_xml('')
    r.remove(r.find(W + 't'))
    return r


def field_runs(p, instr, shown):
    """页码写成复杂域：begin、指令、separate、结果、end 每段都带页脚字体格式，指令加 \\* MERGEFORMAT。
    原来的 fldSimple 在 Word 更新域后按正文样式显示成 14 磅，比页脚其他字（9 磅）大（2026-10-27 用户意见）。"""
    for kind in ('begin', 'instr', 'separate', 'result', 'end'):
        if kind == 'result':
            p.append(run_xml(shown))
            continue
        r = bare_run()
        if kind == 'instr':
            x = sub(r, 'instrText')
            x.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            x.text = ' %s \\* MERGEFORMAT ' % instr
        else:
            sub(r, 'fldChar', fldCharType=kind)
        p.append(r)


def footer_xml(tabpos):
    root = E.Element(W + 'ftr', nsmap={'w': NS['w'], 'r': NS['r']})
    p = sub(root, 'p')
    pp = sub(p, 'pPr')
    sub(sub(pp, 'pBdr'), 'top', val='single', sz=6, space=4, color='000000')
    sub(sub(pp, 'tabs'), 'tab', val='right', pos=tabpos)
    sub(pp, 'spacing', before=80, after=0, line=232, lineRule='exact')
    sub(pp, 'ind', firstLine=0, left=0, right=0)
    p.append(run_xml('{{评价单位}}'))
    tab = bare_run()
    sub(tab, 'tab')
    p.append(tab)
    p.append(run_xml('第'))
    field_runs(p, 'PAGE', '1')
    p.append(run_xml('页 共'))
    field_runs(p, 'PAGEREF BodyEnd \\h', '0')
    p.append(run_xml('页'))
    return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def std_prefix(data):
    """部件若用的是 ns0: 之类自动前缀（马沟稿的前置页脚就是），改回标准 w:/r: 前缀——WPS 只认标准前缀。"""
    old = E.fromstring(data)
    new = E.Element(old.tag, nsmap={'w': NS['w'], 'r': NS['r']})
    for k, v in old.attrib.items():
        new.set(k, v)
    new.text = old.text
    for c in list(old):
        new.append(c)
    return E.tostring(new, xml_declaration=True, encoding='UTF-8', standalone=True)


def main(src, dst):
    zin = zipfile.ZipFile(src)
    order = [i.filename for i in zin.infolist()]
    parts = {n: zin.read(n) for n in order}
    doc = E.fromstring(parts['word/document.xml'])
    body = doc.find(W + 'body')
    kids = list(body)
    texts = [text_of(k) for k in kids]
    i_toc = next(i for i, t in enumerate(texts) if re.sub(r'\s+', '', t) == '目录')
    i_body = next(i for i, k in enumerate(kids) if i > i_toc and k.tag == W + 'p'
                  and k.find(W + 'pPr/' + W + 'pStyle') is not None
                  and k.find(W + 'pPr/' + W + 'pStyle').get(W + 'val') == '3')
    i_att = next(i for i in range(i_toc, i_body) if texts[i].strip() == '附件')
    cover_end = next(i for i, k in enumerate(kids) if k.find('.//' + W + 'sectPr') is not None)
    title_i = next(i for i in range(cover_end) if texts[i].strip() == '职业病危害预评价报告')
    unit = next(texts[i].strip() for i in range(title_i, cover_end + 1)
                if texts[i].strip().endswith(('公司', '中心', '研究院', '研究所')))
    name_is = [i for i in range(title_i) if texts[i].strip()]
    set_text(kids[name_is[0]], '{{封面名称}}')
    for i in name_is[1:]:
        body.remove(kids[i])
    for i in range(title_i, cover_end + 1):
        t = texts[i].strip()
        if re.match(r'^[A-Z]{1,6}-\d{4}-[A-Z0-9]+$', t):
            set_text(kids[i], '{{报告编号}}')
        elif re.match(r'^\d{4}年\d{1,2}月\d{1,2}日$', t):
            set_text(kids[i], '{{报告日期}}')
        elif t == unit:
            set_text(kids[i], '{{评价单位}}')
    for i in range(cover_end + 1, i_toc):
        k, t = kids[i], texts[i]
        if k.tag == W + 'p' and '遵守国家有关法律' in t:
            set_text(k, '{{评价单位}}遵守国家有关法律、法规、规章和标准规范，在为{{项目名称}}项目提供职业卫生技术服务过程中，'
                        '坚持客观、真实、公正的原则，并对所出具的《{{项目名称}}职业病危害预评价报告》承担法律责任。')
        elif k.tag == W + 'p' and '法定代表人' in t:
            set_text(k, re.sub(r'法定代表人：.*', '法定代表人：{{法定代表人}}', t))
        elif k.tag == W + 'p' and t.strip() == unit:
            set_text(k, t[:len(t) - len(t.lstrip())] + '{{评价单位}}')
        elif k.tag == W + 'tbl':
            rows = k.findall(W + 'tr')
            for jc in rows[0].iter(W + 'jc'):   # 表头文字水平居中（格子本来就垂直居中）
                jc.set(W + 'val', 'center')
            proto = rows[1]
            for r in rows[2:]:
                k.remove(r)
            vals = ['{{类别}}', '{{姓名}}', '{{职称}}', '{{专业}}', '{{证书号}}', '']
            for tc, v in zip(proto.findall(W + 'tc'), vals):
                for vm in list(tc.iter(W + 'vMerge')):
                    vm.getparent().remove(vm)
                ps = tc.findall(W + 'p')
                for p in ps[1:]:
                    tc.remove(p)
                if ps[0].findall('.//' + W + 't'):
                    set_text(ps[0], v)
    # 封面、声明、“目录”标题的居中段写明首行缩进 0。正文默认样式带首行缩进 2 字（firstLine=560），
    # 不写时居中段在 PDF 里整体右偏约 14 磅（2026-10-28 晋钢200MW 排版审查）
    for k in kids[:i_toc + 1]:
        pp = k.find(W + 'pPr') if k.tag == W + 'p' and k.getparent() is body else None
        jc = pp.find(W + 'jc') if pp is not None else None
        if jc is not None and jc.get(W + 'val') == 'center' and text_of(k).strip() and pp.find(W + 'ind') is None:
            jc.addprevious(el('ind', firstLine=0, firstLineChars=0))
    seen = {}
    for i in range(i_toc + 1, i_att):
        k = kids[i]
        ps = k.find(W + 'pPr/' + W + 'pStyle')
        lvl = {'27': 1, '30': 2, '24': 3}.get(ps.get(W + 'val') if ps is not None else None)
        if lvl and lvl not in seen:
            seen[lvl] = k
            ts = k.findall('.//' + W + 't')
            ts[0].text = '{{目录%d}}' % lvl
            ts[-1].text = '{{页码}}'
        else:
            body.remove(k)
    assert len(seen) == 3, seen
    # 目录缩进：一级 0、二级 212、三级 480 twip（小四 1 字＝240）。只写绝对值，去掉 leftChars——
    # 样式 toc 3 自带 leftChars=400（4 字），会盖过段落里的 left，三级就缩进 4 字。
    for lvl, k in seen.items():
        ind = k.find(W + 'pPr/' + W + 'ind')
        if ind is None:
            ind = sub(k.find(W + 'pPr'), 'ind')
        for a in ('leftChars', 'startChars', 'hangingChars', 'firstLineChars'):
            ind.attrib.pop(W + a, None)
        ind.set(W + 'left', str(TOC_IND[lvl]))
        ind.set(W + 'firstLine', '0')
    sty = E.fromstring(parts['word/styles.xml'])
    for st in sty.findall(W + 'style'):
        nm = st.find(W + 'name')
        m = re.match(r'toc ([123])$', nm.get(W + 'val', '')) if nm is not None else None
        ind = st.find(W + 'pPr/' + W + 'ind') if m else None
        if ind is not None:
            for a in ('leftChars', 'startChars'):
                ind.attrib.pop(W + a, None)
            ind.set(W + 'left', str(TOC_IND[int(m.group(1))]))
    # 样式 toc 1～3 与目录条的直接格式一致（2026-10-30）：生成器把目录做成 Word 目录域，在 Word／WPS 里“更新整个目录”时
    # 条目按样式重排。马沟样式是行距 28 磅、制表位 9185、华文仿宋，三级还继承正文首行缩进 2 字，与目录条不一样。
    # 样式 ID 改成 Word 自己用的 TOC1～TOC3：VS Code 的 Office Viewer 只认这种 ID，别的 ID 会给目录超链接加下划线。
    rename = {}
    for st in sty.findall(W + 'style'):
        nm = st.find(W + 'name')
        m = re.match(r'toc ([123])$', nm.get(W + 'val', '')) if nm is not None else None
        if not m:
            continue
        lvl = int(m.group(1))
        proto = seen[lvl]
        rename[st.get(W + 'styleId')] = 'TOC%d' % lvl
        st.set(W + 'styleId', 'TOC%d' % lvl)
        pp = st.find(W + 'pPr')
        if pp is None:
            pp = el('pPr')
            rp_ = st.find(W + 'rPr')
            if rp_ is not None:
                rp_.addprevious(pp)
            else:
                st.append(pp)
        for tag in ('tabs', 'spacing', 'ind', 'jc'):
            for x in pp.findall(W + tag):
                pp.remove(x)
            src_ = proto.find(W + 'pPr/' + W + tag)
            if src_ is not None:
                pp.append(copy.deepcopy(src_))
        order_children(pp, PPR_ORDER)
        rf_src = proto.find('.//' + W + 'r/' + W + 'rPr/' + W + 'rFonts')
        rf = st.find(W + 'rPr/' + W + 'rFonts')
        if rf is not None and rf_src is not None and rf_src.get(W + 'eastAsia'):
            rf.set(W + 'eastAsia', rf_src.get(W + 'eastAsia'))
    for e in sty.iter(W + 'basedOn', W + 'next', W + 'link'):
        if e.get(W + 'val') in rename:
            e.set(W + 'val', rename[e.get(W + 'val')])
    for k in seen.values():
        ps = k.find(W + 'pPr/' + W + 'pStyle')
        ps.set(W + 'val', rename.get(ps.get(W + 'val'), ps.get(W + 'val')))
    parts['word/styles.xml'] = E.tostring(sty, xml_declaration=True, encoding='UTF-8', standalone=True)
    set_text(kids[i_att], '{{附件标题}}')
    set_text(kids[i_att + 1], '附件{{附件序号}}  {{附件名称}}')
    # 目录和附件清单与正文同为四号（马沟原稿小四；2026-10-28 用户：目录打印出来太小）
    for k in list(seen.values()) + [kids[i_att], kids[i_att + 1]]:
        for tag in ('sz', 'szCs'):
            for e in k.iter(W + tag):
                e.set(W + 'val', '28')
    for i in range(i_att + 2, i_body):
        if kids[i].find('.//' + W + 'sectPr') is None:
            body.remove(kids[i])
    final_sect = body.find(W + 'sectPr')
    land = None
    for k in kids[i_body:]:
        if k is final_sect:
            continue
        sp = k.find('.//' + W + 'sectPr')
        if sp is not None and land is None and sp.find(W + 'pgSz').get(W + 'orient') == 'landscape':
            land = copy.deepcopy(sp)
        body.remove(k)
    for x in land.findall(W + 'type'):
        land.remove(x)
    p = el('p')
    pp = sub(p, 'pPr')
    sub(pp, 'spacing', before=0, after=0, line=490, lineRule='exact')
    sub(pp, 'ind', firstLine=560)
    sub(pp, 'jc', val='both')
    sub(sub(p, 'r'), 't').text = '{{正文}}'
    final_sect.addprevious(p)
    p2 = copy.deepcopy(p)
    p2.find('.//' + W + 't').text = '{{横向分节}}'
    p2.find(W + 'pPr').append(land)
    final_sect.addprevious(p2)
    rels = E.fromstring(parts['word/_rels/document.xml.rels'])
    keep = {'footer1.xml', 'footer2.xml'}
    for name in keep:
        parts['word/' + name] = std_prefix(parts['word/' + name])
    for r in list(rels):
        if r.get('Type') in (HDR_T, FTR_T) and r.get('Target') not in keep:
            rels.remove(r)
            parts.pop('word/' + r.get('Target'), None)
            parts.pop('word/_rels/' + r.get('Target') + '.rels', None)
        elif r.get('Type') == IMG_T:
            rels.remove(r)
            parts.pop('word/' + r.get('Target'), None)
    # 横向页眉、页脚右对齐制表位＝横向版心宽 13958（页边距各 1440；原 13238，右端短约 0.5 cm，2026-10-27）
    new = {'header1.xml': header_xml(8306), 'header2.xml': header_xml(8306), 'header3.xml': header_xml(13958),
           'footer3.xml': footer_xml(8300), 'footer4.xml': footer_xml(13958)}
    for name, data in new.items():
        parts['word/' + name] = data
        if 'word/' + name not in order:
            order.append('word/' + name)
        x = E.SubElement(rels, PR + 'Relationship')
        n = 1   # 关系 ID 用数字 rIdN（WPS、Word 都这么写），取最小的空号
        while any(r.get('Id') == 'rId%d' % n for r in rels):
            n += 1
        x.set('Id', 'rId%d' % n)
        x.set('Type', HDR_T if name.startswith('header') else FTR_T)
        x.set('Target', name)
    rid_of = {r.get('Target'): r.get('Id') for r in rels}

    def set_refs(sp, hdr, ftr):
        for x in sp.findall(W + 'headerReference') + sp.findall(W + 'footerReference'):
            sp.remove(x)
        if hdr:
            h = sub(sp, 'headerReference', type='default')
            h.set(R + 'id', rid_of[hdr])
        f = sub(sp, 'footerReference', type='default')
        f.set(R + 'id', rid_of[ftr])
        order_children(sp, SECT_ORDER)

    set_refs(final_sect, 'header2.xml', 'footer3.xml')
    pn = final_sect.find(W + 'pgNumType')
    if pn is not None and pn.get(W + 'start'):
        del pn.attrib[W + 'start']
    set_refs(land, 'header3.xml', 'footer4.xml')
    fronts = [k.find('.//' + W + 'sectPr') for k in body if k.tag == W + 'p' and k is not p2
              and k.find('.//' + W + 'sectPr') is not None]
    set_refs(fronts[0], None, 'footer1.xml')
    set_refs(fronts[1], 'header1.xml', 'footer2.xml')
    parts['word/_rels/document.xml.rels'] = E.tostring(rels, xml_declaration=True, encoding='UTF-8', standalone=True)
    ct = E.fromstring(parts['[Content_Types].xml'])
    for o in list(ct):
        if o.tag == CT + 'Override' and o.get('PartName').lstrip('/') not in parts:
            ct.remove(o)
    for name in new:
        if not any(o.get('PartName') == '/word/' + name for o in ct):
            o = E.SubElement(ct, CT + 'Override')
            o.set('PartName', '/word/' + name)
            o.set('ContentType', 'application/vnd.openxmlformats-officedocument.wordprocessingml.'
                  + ('header' if name.startswith('header') else 'footer') + '+xml')
    parts['[Content_Types].xml'] = E.tostring(ct, xml_declaration=True, encoding='UTF-8', standalone=True)
    parts['word/document.xml'] = E.tostring(doc, xml_declaration=True, encoding='UTF-8', standalone=True)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    write_docx(dst, {n: parts[n] for n in order if n in parts})   # 打包方式与 WPS 另存一致
    print('模板已生成：', dst)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
