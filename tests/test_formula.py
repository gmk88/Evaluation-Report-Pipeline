"""公式块测试：解析、LaTeX→OMML、公式检查、Word 版式、预评价不受影响。
运行：~/.report-pipeline/venv/bin/python -m unittest tests/test_formula.py
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
sys.path.append(str(SKILL / 'scripts' / '_vendor'))
from lxml import etree as E  # noqa: E402
from pe import mdparse, omml, formula, docxgen, embed_fonts  # noqa: E402
from pe.model import Project  # noqa: E402

M = omml.M
W = omml.W
PANDOC = Path('/Users/a1234/homebrew/bin/pandoc')
SOFFICE = Path('/Applications/LibreOffice.app/Contents/MacOS/soffice')
WORK = Path('/tmp/claude-501/dcfix/work/fm')

SAMPLE = r'''## 4.2 通风设施计算

噪声接触水平按式（4.2-1）计算，事故通风风量按式（4.2-2）计算，降至限值所需时间按式（4.2-3）计算。

<!-- 公式 编号=4.2-1 -->
$$L_{\mathrm{EX,8h}}=10\lg\left[\frac{1}{8}\sum_{i}t_i\cdot 10^{0.1L_{Aeq,T_i}}\right]$$
式中：
$L_{\mathrm{EX,8h}}$ —— 每周工作5天、每天工作8 h 的等效连续A计权声级，dB(A)
$t_i$ —— 第 $i$ 个工作地点的日接触时间，h
$L_{Aeq,T_i}$ —— 第 $i$ 个工作地点的等效连续A计权声级，dB(A)
$i$ —— 工作地点序号，量纲一

<!-- 公式 编号=4.2-2 -->
$$Q=n\cdot V$$
式中：
$Q$ —— 事故排风量，m³/h
$n$ —— 事故通风换气次数，次/h
$V$ —— 舱室净容积，m³

<!-- 公式 编号=4.2-3 -->
$$t=\frac{k}{n}\cdot\ln\frac{C_0}{C_t}$$
式中：
$t$ —— 通风降至限值所需时间，h
$k$ —— 混合不均匀系数，量纲一
$n$ —— 换气次数，次/h
$C_0$ —— 初始浓度，mg/m³
$C_t$ —— 通风 $t$ 时刻浓度，mg/m³

<!-- 公式 -->
$$Q=12\times 100$$

计算结果见表4.2-1。
'''


def parse(text):
    return mdparse.parse_text(text, 'x.md')


def run_check(text, annex=False):
    out = []
    formula.check(parse(text), lambda lv, src, msg: out.append((lv, src, msg)), annex)
    return out


def msgs(out, level=None):
    return [m for lv, _, m in out if level is None or lv == level]


class ParseTests(unittest.TestCase):
    def test_numbered_formula(self):
        bl = [b for b in parse(SAMPLE) if b['t'] == 'formula']
        self.assertEqual(len(bl), 4)
        b = bl[1]
        self.assertEqual(b['no'], '4.2-2')
        self.assertEqual(b['latex'], r'Q=n\cdot V')
        self.assertTrue(b['where'])
        self.assertEqual([v['sym'] for v in b['vars']], ['Q', 'n', 'V'])
        self.assertEqual(b['vars'][0]['unit'], 'm³/h')
        self.assertEqual(b['vars'][0]['meaning'], '事故排风量')

    def test_unit_split_ignores_comma_inside_math(self):
        b = [x for x in parse(SAMPLE) if x['t'] == 'formula'][0]
        v = b['vars'][2]
        self.assertEqual(v['unit'], 'dB(A)')
        self.assertIn('$i$', v['text'])
        self.assertEqual(mdparse.split_unit('含量，量纲一'), ('含量', '量纲一'))
        self.assertEqual(mdparse.split_unit('$L_{A,eq}$ 声级'), ('$L_{A,eq}$ 声级', ''))

    def test_unnumbered(self):
        b = [x for x in parse(SAMPLE) if x['t'] == 'formula'][3]
        self.assertIsNone(b['no'])
        self.assertFalse(b['where'])

    def test_where_block_ends_at_nonmatching_line(self):
        bl = parse('<!-- 公式 编号=4.1-1 -->\n$$a=b$$\n式中：\n$a$ —— 甲，m\n普通段落\n$b$ —— 乙，m\n')
        f = bl[0]
        self.assertEqual(len(f['vars']), 1)
        self.assertEqual([b['t'] for b in bl], ['formula', 'p', 'p'])

    def test_bare_display_math_is_error(self):
        with self.assertRaises(mdparse.ParseError):
            parse('正文\n$$a=b$$\n')

    def test_directive_without_display_line_is_tolerated_but_flagged(self):
        bl = parse('<!-- 公式 编号=4.1-1 -->\n普通段落\n')
        self.assertIsNone(bl[0]['latex'])
        out = []
        formula.check(bl, lambda *a: out.append(a))
        self.assertTrue(any(lv == '错误' for lv, _, _ in out))

    def test_dollar_in_text_and_cells_not_math(self):
        bl = parse('价格 $5 到 $6\n\n| a | b |\n|---|---|\n| $x$ | 1 |\n')
        self.assertEqual([b['t'] for b in bl], ['p', 'table'])
        self.assertEqual(bl[1]['rows'][1][0], '$x$')

    def test_other_directives_unchanged(self):
        bl = parse('<!-- 分页 -->\n<!-- 注释 -->\n正文\n')
        self.assertEqual([b['t'] for b in bl], ['pagebreak', 'p'])


class OmmlTests(unittest.TestCase):
    def tags(self, latex):
        return [E.QName(e).localname for e in omml.to_omml(latex).iter() if e.tag.startswith(M)]

    def test_structures(self):
        self.assertIn('f', self.tags(r'\frac{a}{b}'))
        self.assertIn('sSub', self.tags('x_i'))
        self.assertIn('sSup', self.tags('x^2'))
        self.assertIn('sSubSup', self.tags('x_i^2'))
        self.assertIn('rad', self.tags(r'\sqrt{x}'))
        self.assertIn('deg', self.tags(r'\sqrt[3]{x}'))
        self.assertIn('func', self.tags(r'\ln x'))
        self.assertIn('acc', self.tags(r'\bar{x}'))
        self.assertIn('bar', self.tags(r'\overline{xy}'))

    def test_nary_with_limits(self):
        om = omml.to_omml(r'\sum_{i=1}^{n} t_i')
        nary = om.find(M + 'nary')
        self.assertEqual(nary.find(M + 'naryPr/' + M + 'chr').get(M + 'val'), '∑')
        self.assertEqual(nary.find(M + 'naryPr/' + M + 'supHide').get(M + 'val'), 'off')
        self.assertEqual(omml.text_of(nary.find(M + 'sub')), 'i=1')
        nary2 = omml.to_omml(r'\prod_i x_i').find(M + 'nary')
        self.assertEqual(nary2.find(M + 'naryPr/' + M + 'chr').get(M + 'val'), '∏')
        self.assertEqual(nary2.find(M + 'naryPr/' + M + 'supHide').get(M + 'val'), 'on')

    def test_delimiters(self):
        for l, b, e in ((r'\left( a \right)', '(', ')'), (r'\left[ a \right]', '[', ']'),
                        (r'\left\{ a \right\}', '{', '}'), ('(a+b)', '(', ')'), ('[a]', '[', ']')):
            d = omml.to_omml(l).find(M + 'd')
            self.assertIsNotNone(d, l)
            self.assertEqual(d.find(M + 'dPr/' + M + 'begChr').get(M + 'val'), b)
            self.assertEqual(d.find(M + 'dPr/' + M + 'endChr').get(M + 'val'), e)

    def test_functions_upright(self):
        for name in ('lg', 'ln', 'log', 'exp', 'max', 'min'):
            f = omml.to_omml('\\%s(x)' % name).find(M + 'func')
            r = f.find(M + 'fName/' + M + 'r')
            self.assertEqual(r.find(M + 't').text, name)
            self.assertEqual(r.find(M + 'rPr/' + M + 'sty').get(M + 'val'), 'p')
            self.assertIsNotNone(f.find(M + 'e/' + M + 'd'))

    def test_italic_and_upright(self):
        om = omml.to_omml(r'x + 2 \mathrm{ab} \text{中文}')
        runs = {r.find(M + 't').text: r for r in om.iter(M + 'r')}
        self.assertIsNone(runs['x'].find(M + 'rPr'))                       # 变量：默认斜体
        for t in ('+', '2', 'ab'):
            self.assertEqual(runs[t].find(M + 'rPr/' + M + 'sty').get(M + 'val'), 'p', t)
        txt = runs['中文']
        self.assertIsNotNone(txt.find(M + 'rPr/' + M + 'nor'))
        self.assertEqual(txt.find(W + 'rPr/' + W + 'rFonts').get(W + 'eastAsia'), '宋体')

    def test_fonts_on_every_run(self):
        om = omml.to_omml(r'L_{\mathrm{EX,8h}}=10\lg\left[\frac{1}{8}\sum_i t_i\right]', size=28)
        runs = list(om.iter(M + 'r'))
        self.assertTrue(runs)
        for r in runs:
            f = r.find(W + 'rPr/' + W + 'rFonts')
            self.assertEqual(f.get(W + 'ascii'), 'Cambria Math')
            self.assertEqual(r.find(W + 'rPr/' + W + 'sz').get(W + 'val'), '28')

    def test_symbol_commands(self):
        txt = omml.text_of(omml.to_omml(r'a \cdot b \times c \div d \pm e \le f \ge g \approx h \neq i \propto j \infty \partial \Delta \ldots \cdots 5\%'))
        for ch in '⋅×÷±≤≥≈≠∝∞∂Δ…⋯%':
            self.assertIn(ch, txt)
        self.assertEqual(omml.text_of(omml.to_omml(r'\alpha\beta\Omega\mu')), 'αβΩμ')
        self.assertEqual(omml.text_of(omml.to_omml(r'\leq \geq')), '≤≥')

    def test_spaces_and_prime(self):
        om = omml.to_omml(r'a\,b\;c\ d~e\quad f')
        self.assertGreaterEqual(omml.text_of(om).count(' '), 1)
        self.assertIn('′', omml.text_of(omml.to_omml("x'")))
        self.assertIn('−', omml.text_of(omml.to_omml('a-b')))

    def test_sample_formulas_convert(self):
        for b in parse(SAMPLE):
            if b['t'] == 'formula':
                omml.to_omml(b['latex'])

    def test_errors(self):
        for bad in (r'\foo', '(a', 'a)', r'\left( a', r'\frac{a}', 'x^2^3', 'a＝b', 'x₂', 'a*b', '', r'\int x', 'a&b'):
            with self.assertRaises(omml.LatexError, msg=bad):
                omml.to_omml(bad)
        try:
            omml.to_omml(r'\foo{x}')
        except omml.LatexError as e:
            self.assertIn(r'\foo{x}', str(e))

    def test_sum_index_needs_no_explanation(self):
        self.assertEqual(omml.symbols_of(r'V=\sum_{j=1}^{2}\pi r_j^{2}h_j'), {'V', 'r_j^2', 'h_j'})
        self.assertEqual(omml.symbols_of(r'L=10\lg\left[\frac{1}{8}\sum_{i} t_i\cdot 10^{0.1L_i}\right]'), {'L', 't_i', 'L_i'})

    def test_symbols_of(self):
        self.assertEqual(omml.symbols_of(r'Q=n\cdot V'), {'Q', 'n', 'V'})
        s = omml.symbols_of(r'L_{\mathrm{EX,8h}}=10\lg\left[\frac{1}{8}\sum_{i}t_i\cdot 10^{0.1L_{Aeq,T_i}}\right]')
        self.assertEqual(s, {r'L_{\mathrm{EX,8h}}', 't_i', 'L_{Aeq,T_i}'})
        self.assertEqual(omml.symbols_of(r'y=e^{-x}'), {'y', 'x'})
        # \mathit{SNR} 是一个多字母符号：整体要在“式中”说明，不再拆成 S、N、R（田林 11-10 附件4 误报）
        self.assertEqual(omml.symbols_of(r"L'_{\mathrm{Ax}}=L_{\mathrm{C}}-\mathit{SNR}"),
                         {r"L'_{\mathrm{Ax}}", r'L_{\mathrm{C}}', r'\mathit{SNR}'})
        self.assertEqual(omml.symbols_of(r'\mathit{SNR}_{i}+x'), {r'\mathit{SNR}_i', 'x'})

    @unittest.skipUnless(PANDOC.exists(), '本机没有 pandoc')
    def test_structure_matches_pandoc(self):
        cases = [r'\frac{a}{b}', 'x_i', 'x^2', 'x_i^2', r'\sum_{i=1}^{n} t_i', r'\sqrt[3]{x}', r'\sqrt{y}',
                 r'\left[ a \right]', r'\left\{ c \right\}', r'\frac{x_i^2}{\sqrt{y}}']
        skel = lambda om: [E.QName(e).localname for e in om.iter() if e.tag.startswith(M)
                           and E.QName(e).localname not in ('r', 'rPr', 't', 'sty', 'oMath', 'oMathPara', 'oMathParaPr', 'jc', 'nor')]
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / 'a.md'
            src.write_text('\n\n'.join('$$%s$$' % c for c in cases), encoding='utf-8')
            subprocess.run([str(PANDOC), str(src), '-o', str(Path(d) / 'a.docx')], check=True, capture_output=True)
            root = E.fromstring(zipfile.ZipFile(Path(d) / 'a.docx').read('word/document.xml'))
        theirs = list(root.iter(M + 'oMath'))
        self.assertEqual(len(theirs), len(cases))
        for c, t in zip(cases, theirs):
            self.assertEqual(skel(omml.to_omml(c)), skel(t), c)
            # 元素的 m:val 属性（括号字符、n-ary 符号、分式类型）也一致
            vals = lambda om: [(E.QName(e).localname, e.get(M + 'val')) for e in om.iter()
                               if e.get(M + 'val') is not None and E.QName(e).localname in ('begChr', 'endChr', 'chr', 'type', 'limLoc')]
            self.assertEqual(vals(omml.to_omml(c)), vals(t), c)


class CheckTests(unittest.TestCase):
    def test_sample_is_clean(self):
        self.assertEqual(run_check(SAMPLE), [])

    def err(self, text, level='错误', annex=False):
        return msgs(run_check(text, annex), level)

    def one(self, latex, no='4.1-1', where='式中：\n$a$ —— 甲，m', head='## 4.1 节\n\n按式（4.1-1）计算。\n\n'):
        return head + '<!-- 公式 编号=%s -->\n$$%s$$\n%s\n' % (no, latex, where)

    def test_good_minimal(self):
        self.assertEqual(run_check(self.one('a=1')), [])

    def test_bad_no_format(self):
        self.assertTrue(any('数字.数字-数字' in m for m in self.err(self.one('a=1', no='A-1'))))

    def test_duplicate_no(self):
        t = self.one('a=1') + self.one('a=2', head='')
        self.assertTrue(any('重复' in m for m in self.err(t)))

    def test_missing_where(self):
        t = self.one('a=1', where='')
        self.assertTrue(any('式中' in m for m in self.err(t)))

    def test_unsupported_command_and_brackets(self):
        self.assertTrue(any('转换失败' in m for m in self.err(self.one(r'a=\foo'))))
        self.assertTrue(any('转换失败' in m for m in self.err(self.one(r'a=(1+2'))))

    def test_unicode_in_latex(self):
        for bad in ('a＝1', 'aᵢ=1', 'a=1，b=2', 'a₂=1', 'a²=1'):
            self.assertTrue(any('不能写的字符' in m for m in self.err(self.one(bad))), bad)
        self.assertEqual(self.err(self.one(r'a=\text{中文}')), [])   # \text 里的汉字可以

    def test_directive_without_display(self):
        self.assertTrue(self.err('<!-- 公式 编号=4.1-1 -->\n普通段落\n'))

    def test_prefix_and_sequence_hints(self):
        h = self.err(self.one('a=1', no='4.2-1'), '提示')
        self.assertTrue(any('前缀' in m for m in h))
        h = self.err(self.one('a=1', no='4.1-3', head='## 4.1 节\n\n按式（4.1-3）计算。\n\n'), '提示')
        self.assertTrue(any('不连续' in m for m in h))

    def test_not_cited_and_unknown_cited(self):
        h = self.err(self.one('a=1', head='## 4.1 节\n\n'), '提示')
        self.assertTrue(any('没有被引用' in m for m in h))
        h = self.err(self.one('a=1', head='## 4.1 节\n\n按式（4.1-1）和式（4.1-9）计算。\n\n'), '提示')
        self.assertTrue(any('4.1-9' in m and '不存在' in m for m in h))

    def test_symbol_mismatch_both_directions(self):
        h = self.err(self.one('a=b', where='式中：\n$a$ —— 甲，m\n$z$ —— 丙，m'), '提示')
        self.assertTrue(any('符号 b 没有在' in m for m in h))
        self.assertTrue(any('$z$' in m and '没有出现' in m for m in h))

    def test_missing_unit(self):
        h = self.err(self.one('a=1', where='式中：\n$a$ —— 甲'), '提示')
        self.assertTrue(any('没写单位' in m for m in h))

    def test_inline_math_error_in_where(self):
        t = self.one('a=1', where=r'式中：' + '\n' + r'$a$ —— 甲 $\foo$，m')
        self.assertTrue(any('行内公式' in m for m in self.err(t)))

    def test_text_formula_in_annex4(self):
        t = '# 附件4 计算书\n\n## 4.1 噪声计算\n\n接触水平 L＝10lg(x)，见下。\n\n# 附件5 清单\n\n其他 L＝10lg(x)\n'
        h = self.err(t, '提示', annex=True)
        self.assertEqual(len([m for m in h if '写成了文字' in m]), 1)
        self.assertEqual([m for m in self.err(t, '提示', annex=False) if '写成了文字' in m], [])
        cell = '# 附件4 计算书\n\n| a | b |\n|---|---|\n| T＝8×10^(0.1(85−L)) | 1 |\n'
        self.assertTrue(any('写成了文字' in m for m in self.err(cell, '提示', annex=True)))


class DocxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        src = WORK / '华能田林老八渡风电设计专篇'
        if not (src / '项目信息.md').exists():
            raise unittest.SkipTest('没有田林专篇样例')
        cls.tmp = Path(tempfile.mkdtemp())
        cls.root = cls.tmp / 'p'
        (cls.root / '正文').mkdir(parents=True)
        shutil.copy(src / '项目信息.md', cls.root)
        if (src / '检查规则.md').exists():
            shutil.copy(src / '检查规则.md', cls.root)
        body = '# 4 防护设施设计\n\n' + SAMPLE.replace('## 4.2', '## 4.2', 1) + '\n<!-- 横向 -->\n\n## 4.3 横向节\n\n<!-- 公式 编号=4.3-1 -->\n$$a=b$$\n式中：\n$a$ —— 甲，m\n$b$ —— 乙，m\n'
        (cls.root / '正文' / '04.md').write_text(body, encoding='utf-8')
        cls.pj = Project(cls.root).resolve()
        cls.docx = cls.tmp / 'o.docx'
        docxgen.build(cls.pj, cls.docx)
        cls.xml = E.fromstring(zipfile.ZipFile(cls.docx).read('word/document.xml'))
        cls.lay = docxgen.doctype.layout(docxgen.doctype.DESIGN)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def formula_tables(self):
        return [t for t in self.xml.iter(W + 'tbl') if t.find('.//' + M + 'oMathPara') is not None]

    def test_counts(self):
        self.assertEqual(len(self.formula_tables()), 4)                          # 编号的 4 个
        self.assertEqual(len(list(self.xml.iter(M + 'oMathPara'))), 5)           # 加 1 个不编号的
        self.assertGreater(len(list(self.xml.iter(M + 'oMath'))), 15)

    def test_widths_sum_to_text_width(self):
        widths = []
        for t in self.formula_tables():
            cols = [int(g.get(W + 'w')) for g in t.find(W + 'tblGrid')]
            self.assertEqual(len(cols), 3)
            self.assertEqual(int(t.find(W + 'tblPr/' + W + 'tblW').get(W + 'w')), sum(cols))
            self.assertEqual([int(c.find(W + 'tcPr/' + W + 'tcW').get(W + 'w')) for c in t.iter(W + 'tc')], cols)
            self.assertEqual(t.find(W + 'tblPr/' + W + 'tblLayout').get(W + 'type'), 'fixed')
            widths.append(sum(cols))
        self.assertEqual(widths[:3], [self.lay['纵向版心宽']] * 3)
        self.assertEqual(widths[3], self.lay['横向表格宽'])                      # 横向节用横向宽
        self.assertEqual(docxgen.formula_widths(8300), [1500, 5300, 1500])

    def test_cell_layout(self):
        t = self.formula_tables()[0]
        cells = list(t.iter(W + 'tc'))
        self.assertEqual(len(cells[0].findall('.//' + W + 't')), 0)              # 左格空
        self.assertEqual(cells[1].find('.//' + W + 'jc').get(W + 'val'), 'center')
        self.assertIsNotNone(cells[1].find('.//' + M + 'oMathPara'))
        self.assertEqual(''.join(cells[2].xpath('.//w:t/text()', namespaces={'w': W[1:-1]})), '（4.2-1）')
        self.assertEqual(cells[2].find('.//' + W + 'jc').get(W + 'val'), 'right')
        self.assertIsNotNone(cells[2].find(W + 'tcPr/' + W + 'noWrap'))
        sp = cells[1].find('.//' + W + 'spacing')
        self.assertEqual(sp.get(W + 'lineRule'), 'auto')                         # 自动行距
        self.assertIsNotNone(cells[1].find('.//' + W + 'keepNext'))
        for side in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
            self.assertEqual(t.find(W + 'tblPr/' + W + 'tblBorders/' + W + side).get(W + 'val'), 'nil')

    def test_where_paragraphs_keep_next(self):
        t = self.formula_tables()[1]                                             # 式 4.2-2：式中 + 3 行
        paras = []
        e = t.getnext()
        while e is not None and e.tag == W + 'p' and len(paras) < 4:
            paras.append(e)
            e = e.getnext()
        self.assertEqual(''.join(paras[0].xpath('.//w:t/text()', namespaces={'w': W[1:-1]})), '式中：')
        for p in paras[:3]:
            self.assertIsNotNone(p.find(W + 'pPr/' + W + 'keepNext'))
        self.assertIsNone(paras[3].find(W + 'pPr/' + W + 'keepNext'))            # 最后一段不带
        self.assertIsNotNone(paras[1].find(M + 'oMath'))                         # 符号是行内公式
        self.assertIn('事故排风量', ''.join(paras[1].xpath('.//w:t/text()', namespaces={'w': W[1:-1]})))
        ind = paras[1].find(W + 'pPr/' + W + 'ind')
        self.assertEqual(ind.get(W + 'left'), ind.get(W + 'hanging'))            # 悬挂
        self.assertIsNone(ind.get(W + 'firstLine'))

    def test_formula_font(self):
        raw = zipfile.ZipFile(self.docx).read('word/document.xml').decode()
        self.assertIn('Cambria Math', raw)
        self.assertIn('Cambria Math', embed_fonts.used_fonts({'word/document.xml': raw.encode()}))

    def test_unnumbered_is_centered_paragraph(self):
        paras = [p for p in self.xml.iter(W + 'p') if p.find(M + 'oMathPara') is not None and p.getparent().tag != W + 'tc']
        self.assertEqual(len(paras), 1)
        self.assertEqual(paras[0].find(W + 'pPr/' + W + 'jc').get(W + 'val'), 'center')

    def test_bad_latex_raises_build_error(self):
        bl = parse('<!-- 公式 -->\n$$\\foo$$\n')

        class P:
            pass
        with self.assertRaises(docxgen.BuildError):
            docxgen.make_formula(bl[0], 'portrait', self.lay)

    def test_embed_cambria_math_optional(self):
        self.assertIn('Cambria Math', embed_fonts.OPTIONAL)
        self.assertNotIn('Cambria Math', embed_fonts.font_files({'仿宋'}))
        if embed_fonts.FONTS['Cambria Math'][0].is_file():
            self.assertIn('Cambria Math', embed_fonts.font_files({'Cambria Math'}))
            data = embed_fonts.subset_font(embed_fonts.FONTS['Cambria Math'][0], set('Qn=V'), embed_fonts.TTC_NUMBER['Cambria Math'])
            from fontTools.ttLib import TTFont
            import io
            self.assertIn('Math', TTFont(io.BytesIO(data))['name'].getDebugName(4))

    def test_render_copy_math_variables_italic_for_wps(self):
        """WPS 命令行排数学斜体字符（U+1D434…）会显示成方框：渲染副本把变量 run 改成 nor＋w:i，交稿 docx 不动。"""
        tmp = self.tmp / 'rc'
        tmp.mkdir()
        out = tmp / 'r.docx'
        embed_fonts.render_copy(self.docx, out)
        orig = zipfile.ZipFile(self.docx).read('word/document.xml')
        rend = zipfile.ZipFile(out).read('word/document.xml')
        self.assertNotIn(b'<m:r><w:rPr>', rend)
        self.assertIn(b'<m:r><m:rPr><m:nor/></m:rPr><w:rPr><w:i/><w:iCs/><w:rFonts', rend)
        self.assertIn(b'<m:r><w:rPr>', orig)                                   # 交稿版的变量仍是默认（数学斜体）
        self.assertEqual(rend.count(b'<m:r>'), orig.count(b'<m:r>'))
        self.assertIn(b'<m:sty m:val="p"/>', rend)                              # 正体 run 不受影响

    @unittest.skipUnless(SOFFICE.exists(), '本机没有 LibreOffice')
    def test_render_png(self):
        out = Path('/tmp/claude-501/dcfix/work/formula_png')
        out.mkdir(parents=True, exist_ok=True)
        small = out / 'formula_test.docx'
        sample = out / 'p'
        shutil.rmtree(sample, ignore_errors=True)
        (sample / '正文').mkdir(parents=True)
        shutil.copy(self.root / '项目信息.md', sample)
        (sample / '正文' / '04.md').write_text('# 4 防护设施设计\n\n' + SAMPLE, encoding='utf-8')
        docxgen.build(Project(sample).resolve(), small)
        emb = out / 'formula_test_embed.docx'
        embed_fonts.render_copy(small, emb)
        subprocess.run([str(SOFFICE), '--headless', '--convert-to', 'pdf', '--outdir', str(out), str(emb)],
                       capture_output=True, timeout=180)
        import pymupdf
        pdf = pymupdf.open(out / 'formula_test_embed.pdf')
        found = [i for i, pg in enumerate(pdf) if '式中' in pg.get_text()]
        self.assertTrue(found)
        for i in found[:2]:
            pdf[i].get_pixmap(dpi=110).save(out / ('formula_page_%d.png' % (i + 1)))


class DcfixTests(unittest.TestCase):
    """修复步骤新增：指令行写坏、多行 $$、指令缺等号、行内 $、LaTeX 同义写法。"""

    def issues(self, text, annex=False):
        out = []
        formula.check(parse(text), lambda lv, src, msg: out.append((lv, msg)), annex)
        return out

    def test_directive_with_space_before_gt_raises(self):
        with self.assertRaises(mdparse.ParseError) as cm:
            parse('<!-- 公式 编号=4.2-1 -- >\n$$Q = nV$$\n\n#### 结论\n\n正文。\n\n<!-- 写作提示 -->\n')
        self.assertIn('-->', str(cm.exception))

    def test_ordinary_multiline_comment_still_ok(self):
        bl = parse('<!-- 写作提示\n公式块见卡 -- > 这里不是指令\n-->\n\n正文。\n')
        self.assertEqual([b['t'] for b in bl], ['p'])

    def test_multiline_display_reports_bad_not_parse_error(self):
        for body in ('$$\nQ=1\n$$', '$$Q=1$$ （4.2-1）'):
            bl = parse('## 4.2 x\n\n<!-- 公式 编号=4.2-1 -->\n%s\n\n正文。\n' % body)
            f = [b for b in bl if b['t'] == 'formula'][0]
            self.assertIsNone(f['latex'])
            self.assertIn('单独一行', f['bad'])
            self.assertEqual(bl[-1]['t'], 'p')
            self.assertTrue(any(lv == '错误' for lv, _ in self.issues('## 4.2 x\n\n<!-- 公式 编号=4.2-1 -->\n%s\n' % body)))

    def test_directive_without_equals_is_error(self):
        iss = self.issues('## 4.2 x\n\n<!-- 公式 编号 4.2-1 -->\n$$Q=1$$\n')
        self.assertTrue(any(lv == '错误' and '公式指令写法' in m for lv, m in iss), iss)

    def test_inline_dollar_in_paragraph_and_cell(self):
        iss = self.issues('## 4.2 x\n\n代入，$Q = 12$ m³/h。\n\n| a | b |\n|---|---|\n| $n$ | 1 |\n')
        self.assertEqual(sum(1 for lv, m in iss if lv == '错误' and '不识别公式' in m), 2, iss)

    def test_latex_aliases(self):
        for lx in (r'\dfrac{a}{b}', r'a\!b', r'\mathit{x}', r'\ell', r'\operatorname{sin}x', r'a \leqslant b', r'a\to b', r'\textrm{m}'):
            omml.to_omml(lx)

    def test_text_run_has_no_sty_with_nor(self):
        om = omml.to_omml(r'N = 10\text{台}')
        for rp in om.iter(M + 'rPr'):
            self.assertFalse(rp.find(M + 'nor') is not None and rp.find(M + 'sty') is not None)


class PreUnaffectedTests(unittest.TestCase):
    def test_pre_docx_identical_to_baseline(self):
        base, after = WORK / 'out_base_预评价.docx', WORK / 'out_after_预评价.docx'
        if not (base.exists() and after.exists()):
            self.skipTest('没有基线文件')
        rd = lambda p: re.sub(r'w:rsid\w*="[^"]*"', '', zipfile.ZipFile(p).read('word/document.xml').decode())
        self.assertEqual(rd(base), rd(after))

    def test_pre_parse_ignores_formula_syntax_absence(self):
        bl = parse('# 1 章\n\n正文 L_{a} 与 $x$ 不是公式。\n')
        self.assertEqual([b['t'] for b in bl], ['h', 'p'])


if __name__ == '__main__':
    unittest.main()
