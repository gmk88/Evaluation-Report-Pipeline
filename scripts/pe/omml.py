"""LaTeX 子集 → Word 公式（OMML）。纯 Python，不依赖 pandoc。

设计专篇计算书的公式块（mdparse 的 formula 块）用它出 Word 原生可编辑公式。结构参照 pandoc 的 OMML 输出
（m:f、m:nary、m:sSub、m:sSup、m:sSubSup、m:d、m:rad、m:acc、m:bar），不认识的写法一律抛 LatexError，
由 formula.check 报“错误”，不静默降级成文字。

支持（规格 2.3）：变量、数字、运算符（+ − = × ÷ · ± ≤ ≥ < > ≈ ≠ ∝ % ′）、\\frac{}{}、_{} ^{}（含同时上下标）、
\\sum \\prod（带上下限）、\\sqrt{} \\sqrt[n]{}、\\left( \\right) \\left[ \\right] \\left\\{ \\right\\} 和普通括号、
\\lg \\ln \\log \\exp \\max \\min（函数名正体）、\\mathrm{} \\text{}（正体）、
\\cdot \\times \\div \\pm \\le \\ge \\leq \\geq \\approx \\neq \\infty \\partial \\Delta \\ldots \\cdots、常用希腊字母（大小写）、
空格命令 \\, \\; \\  ~ \\quad、\\%、\\bar{} \\overline{}。

用法：
    om = to_omml(r'Q = n \\cdot V')                 # m:oMath 元素；run 已带字体、字号
    para = math_para(om)                           # m:oMathPara（居中）
    symbols_of(r'L_{\\mathrm{EX,8h}}')             # 公式里需要“式中”说明的符号集合（规范化后的 LaTeX 写法）
"""
import re

from lxml import etree as E

M_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XML_NS = 'http://www.w3.org/XML/1998/namespace'
M = '{%s}' % M_NS
W = '{%s}' % W_NS

MATH_FONT = 'Cambria Math'


class LatexError(ValueError):
    """公式写法不在支持的子集内。args[0] 是原因，.latex 是公式原文。"""

    def __init__(self, msg, latex=''):
        super().__init__(msg)
        self.msg = msg
        self.latex = latex

    def __str__(self):
        return '%s（公式：%s）' % (self.msg, self.latex) if self.latex else self.msg


GREEK_LOWER = {n: c for n, c in zip(
    'alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho '
    'varrho sigma varsigma tau upsilon phi varphi chi psi omega'.split(),
    'αβγδεϵζηθϑικλμνξπϖρϱσςτυφϕχψω')}
GREEK_LOWER['ell'] = 'ℓ'
GREEK_UPPER = {n: c for n, c in zip(
    'Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega'.split(), 'ΓΔΘΛΞΠΣΥΦΨΩ')}
# 命令 → (显示字符, 类别)：op 运算符/关系符（正体）；sym 正体符号
SYMBOLS = {
    'cdot': '⋅', 'times': '×', 'div': '÷', 'pm': '±', 'mp': '∓', 'le': '≤', 'leq': '≤', 'ge': '≥', 'geq': '≥',
    'approx': '≈', 'neq': '≠', 'ne': '≠', 'propto': '∝', 'infty': '∞', 'partial': '∂', 'ldots': '…', 'dots': '…',
    'cdots': '⋯', 'to': '→', 'rightarrow': '→', 'leftarrow': '←', 'sim': '∼', 'in': '∈', 'll': '≪', 'gg': '≫',
    'cdots': '⋯', 'circ': '∘', 'degree': '°', 'prime': '′', '%': '%', '{': '{', '}': '}', '|': '|',
}
# 模型常写的同义写法
CMD_ALIASES = {'dfrac': 'frac', 'tfrac': 'frac', 'leqslant': 'le', 'geqslant': 'ge', 'textrm': 'mathrm',
               'operatorname': 'mathrm', 'mathit': 'mathit'}
SPACES = {',': '\u2009', ':': '\u205f', ';': '\u2004', ' ': '\u00a0', 'quad': '\u2003', 'qquad': '\u2003\u2003'}
FUNCS = ('lg', 'ln', 'log', 'exp', 'max', 'min', 'sin', 'cos', 'tan')
NARY = {'sum': '∑', 'prod': '∏'}
# 直接写在 LaTeX 里的字符
OP_CHARS = {'+': '+', '-': '−', '=': '=', '<': '<', '>': '>', ',': ',', ';': ';', ':': ':', '.': '.', '!': '!',
            '|': '|', '/': '/', '%': '%', "'": '′'}
UNI_OPS = set('−×÷⋅·±∓≤≥≈≠∝∞∂…⋯∘°′%')
STOP_OPS = set('+−±∓=<>≤≥≈≠∝,;:')       # 求和号的被加项到这些符号为止
BRACKETS = {'(': ')', '[': ']'}
LEFT_DELIMS = {'(': '(', '[': '[', '{': '{', '|': '|', '.': ''}
RIGHT_DELIMS = {')': ')', ']': ']', '}': '}', '|': '|', '.': ''}

# 上下标、全角、中文标点：LaTeX 里一律不许写（\text{} 里的汉字除外）
RE_BAD_CHAR = re.compile('[\u2070-\u209f\u00b2\u00b3\u00b9\u1d00-\u1dbf\u3000-\u303f\uff00-\uffef'
                         '\u2018\u2019\u201c\u201d\u2014\u2013\u4e00-\u9fff]')


def unicode_problems(latex):
    """公式里（\\text{} 外）的 Unicode 上下标、全角字符、中文标点、汉字，返回去重后的字符列表。"""
    s = re.sub(r'\\text\{[^{}]*\}', '', latex)
    out = []
    for ch in RE_BAD_CHAR.findall(s):
        if ch not in out:
            out.append(ch)
    return out


# ---------------------------------------------------------------- 词法
def lex(s):
    """→ [(类型, 值, 起, 止)]。类型：num ch cmd { } ^ _ text。\\text{…} 整体作为一个 text 记号。"""
    toks, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
        elif c == '\\':
            j = i + 1
            if j < n and s[j].isalpha():
                while j < n and s[j].isalpha():
                    j += 1
                name = s[i + 1:j]
            elif j < n:
                j += 1
                name = s[i + 1:j]
            else:
                raise LatexError('末尾多了一个反斜杠', s)
            if name == 'text':
                k = j
                while k < n and s[k].isspace():
                    k += 1
                if k >= n or s[k] != '{':
                    raise LatexError('\\text 后面要跟 {…}', s)
                depth, e = 0, k
                while e < n:
                    if s[e] == '{':
                        depth += 1
                    elif s[e] == '}':
                        depth -= 1
                        if depth == 0:
                            break
                    e += 1
                if e >= n:
                    raise LatexError('\\text{ 没有配对的 }', s)
                toks.append(('text', s[k + 1:e], i, e + 1))
                i = e + 1
                continue
            name = CMD_ALIASES.get(name, name)
            if name == '!':                       # \! 负间距：当零宽，丢掉
                i = j
                continue
            toks.append(('cmd', name, i, j))
            i = j
        elif c.isdigit():
            j = i
            while j < n and s[j].isdigit():
                j += 1
            if j + 1 < n and s[j] == '.' and s[j + 1].isdigit():
                j += 1
                while j < n and s[j].isdigit():
                    j += 1
            toks.append(('num', s[i:j], i, j))
            i = j
        elif c in '{}^_':
            toks.append((c, c, i, i + 1))
            i += 1
        else:
            toks.append(('ch', c, i, i + 1))
            i += 1
    return toks


# ---------------------------------------------------------------- 元素
def _m(tag, **attrs):
    e = E.Element(M + tag)
    for k, v in attrs.items():
        e.set(M + k, str(v))
    return e


def _mv(parent, tag, val):
    e = E.SubElement(parent, M + tag)
    e.set(M + 'val', str(val))
    return e


class Parser:
    def __init__(self, latex, size=28, font=MATH_FONT, ea_font='宋体'):
        self.src = latex
        self.toks = lex(latex)
        self.p = 0
        self.size, self.font, self.ea = size, font, ea_font
        self.symbols = []
        self.nosym = 0            # >0：正在读下标，里面的字母是标签，不当作要说明的符号

    # ---- 基础
    def err(self, msg):
        raise LatexError(msg, self.src)

    def peek(self, k=0):
        return self.toks[self.p + k] if self.p + k < len(self.toks) else None

    def next(self):
        t = self.peek()
        if t is None:
            self.err('公式在这里意外结束')
        self.p += 1
        return t

    def run(self, text, sty=None, nor=False):
        r = E.Element(M + 'r')
        if sty or nor:
            rp = E.SubElement(r, M + 'rPr')
            if nor:
                E.SubElement(rp, M + 'nor')
            if sty:
                _mv(rp, 'sty', sty)
        wp = E.SubElement(r, W + 'rPr')
        f = E.SubElement(wp, W + 'rFonts')
        f.set(W + 'ascii', self.font)
        f.set(W + 'hAnsi', self.font)
        f.set(W + 'eastAsia', self.ea if nor else self.font)
        f.set(W + 'cs', self.font)
        E.SubElement(wp, W + 'sz').set(W + 'val', str(self.size))
        E.SubElement(wp, W + 'szCs').set(W + 'val', str(self.size))
        t = E.SubElement(r, M + 't')
        t.text = text
        if text != text.strip() or text.isspace():
            t.set('{%s}space' % XML_NS, 'preserve')
        return r

    def box(self, tag, nodes):
        e = E.Element(M + tag)
        for n in nodes:
            e.append(n)
        return e

    # ---- 序列
    def parse_all(self):
        nodes = self.seq(None)
        if self.peek() is not None:
            self.closer_error(self.peek())
        return nodes

    def is_closer(self, t):
        return t is not None and (t[0] == '}' or (t[0] == 'ch' and t[1] in ')]') or (t[0] == 'cmd' and t[1] == 'right'))

    def closer_error(self, t):
        what = {'}': '多了一个 }', ')': '括号不配对：多了一个 )', ']': '括号不配对：多了一个 ]'}.get(t[0] if t[0] == '}' else t[1])
        self.err(what or '\\left 与 \\right 不配对：多了一个 \\right')

    def seq(self, closer, upright=False):
        """读到 closer（记号类型/字符）为止，不消耗它。closer=None 读到结尾。"""
        nodes = []
        while True:
            t = self.peek()
            if t is None:
                if closer is not None:
                    self.err({'}': '缺少 }', ')': '括号不配对：缺少 )', ']': '括号不配对：缺少 ]',
                              'right': '\\left 没有配对的 \\right'}[closer])
                return nodes
            if closer is not None and self.at_closer(t, closer):
                return nodes
            if self.is_closer(t):
                if closer is None:
                    self.closer_error(t)
                self.err('括号不配对：这里应该是%s' % {'}': '}', ')': ')', ']': ']', 'right': '\\right'}[closer])
            nodes.extend(self.term(upright))

    @staticmethod
    def at_closer(t, closer):
        if closer == '}':
            return t[0] == '}'
        if closer == 'right':
            return t[0] == 'cmd' and t[1] == 'right'
        return t[0] == 'ch' and t[1] == closer

    # ---- 项（原子加上下标）
    def term(self, upright=False):
        t = self.peek()
        k, v = t[0], t[1]
        start = t[2]
        if k == 'cmd' and v in FUNCS:
            return self.function(upright)
        if k == 'cmd' and v in NARY:
            return self.nary(upright)
        if upright and self.is_plain(t):
            # 正体块里连续的字母数字标点合并成一个 run
            text = ''
            while self.peek() is not None and self.is_plain(self.peek()):
                tk = self.next()
                text += OP_CHARS.get(tk[1], tk[1]) if tk[0] == 'ch' else tk[1]
            return self.scripts([self.run(text, sty='p')], start, False)
        base, symbol = self.atom(upright)
        base = self.scripts(base, start, symbol)
        return base

    @staticmethod
    def is_plain(t):
        return t[0] == 'num' or (t[0] == 'ch' and (t[1].isalnum() or t[1] in OP_CHARS or t[1] in '()[]'))

    def single(self, upright=False):
        """上下标、函数自变量里不带 {} 的单个记号。"""
        t = self.peek()
        if t is None:
            self.err('^ 或 _ 后面没有内容')
        if t[0] == 'num':
            self.next()
            if len(t[1]) > 1:                                   # x^23 → 只取第一位，和 TeX 一致
                self.toks.insert(self.p, ('num', t[1][1:], t[2] + 1, t[3]))
                return [self.run(t[1][0], sty='p')]
            return [self.run(t[1], sty='p')]
        if t[0] == '{':
            return self.group(upright)
        return self.term_noscript(upright)

    def term_noscript(self, upright):
        base, _ = self.atom(upright)
        return base

    def group(self, upright=False):
        t = self.next()
        if t[0] != '{':
            self.err('这里应该是 {')
        nodes = self.seq('}', upright)
        self.next()
        return nodes

    def arg(self, upright=False):
        """命令的参数：{…} 或单个记号。"""
        t = self.peek()
        if t is None:
            self.err('命令后面缺参数')
        if t[0] == '{':
            return self.group(upright)
        return self.single(upright)

    def scripts(self, base, start, symbol):
        sub = sup = None
        primes = ''
        end = self.toks[self.p - 1][3]
        while True:
            t = self.peek()
            if t is None:
                break
            if t[0] == 'ch' and t[1] == "'":
                self.next()
                primes += '′'
                end = t[3]
            elif t[0] == '^':
                if sup is not None:
                    self.err('同一个符号上有两个上标，请用 {} 括起来')
                self.next()
                sup = self.script_arg(False)
                end = self.toks[self.p - 1][3]
            elif t[0] == '_':
                if sub is not None:
                    self.err('同一个符号上有两个下标，请用 {} 括起来')
                self.next()
                sub = self.script_arg(True)
                end = self.toks[self.p - 1][3]
            else:
                break
        if primes:
            sup = [self.run(primes, sty='p')] + (sup or [])
        if sub is None and sup is None:
            if symbol:
                self.record(start, end, base)
            return base
        if not base:
            self.err('上标或下标前面没有符号')
        if symbol:
            self.record(start, end, base)
        return [self.sub_sup(base, sub, sup)]

    def script_arg(self, is_sub):
        if is_sub:
            self.nosym += 1
        try:
            nodes = self.arg()
        finally:
            if is_sub:
                self.nosym -= 1
        if not nodes:
            self.err('上标或下标是空的')
        return nodes

    def sub_sup(self, base, sub, sup):
        if sub is not None and sup is not None:
            e = E.Element(M + 'sSubSup')
            parts = (('e', base), ('sub', sub), ('sup', sup))
        elif sub is not None:
            e = E.Element(M + 'sSub')
            parts = (('e', base), ('sub', sub))
        else:
            e = E.Element(M + 'sSup')
            parts = (('e', base), ('sup', sup))
        for tag, nodes in parts:
            e.append(self.box(tag, nodes))
        return e

    def record(self, start, end, base):
        if self.nosym:
            return
        raw = self.src[start:end]
        if raw == 'e' or re.match(r'^e\s*\^', raw):               # 自然对数的底
            return
        if raw in ('\\pi', '\\infty', '\\partial', 'π'):
            return
        self.symbols.append(norm_symbol(raw))

    # ---- 原子
    def atom(self, upright=False):
        """→ (节点列表, 是否变量符号)"""
        t = self.next()
        k, v = t[0], t[1]
        if k == 'num':
            return [self.run(v, sty='p')], False
        if k == 'text':
            return [self.run(v, nor=True)], False
        if k == '{':
            self.p -= 1
            return self.group(upright), False
        if k in ('^', '_'):
            self.err('上标或下标前面没有符号')
        if k == 'ch':
            return self.char_atom(t, upright)
        # 命令
        if v in GREEK_LOWER:
            return [self.run(GREEK_LOWER[v], sty='p' if upright else None)], not upright
        if v in GREEK_UPPER:
            if v == 'Delta' and not upright:
                return self.delta(t)
            return [self.run(GREEK_UPPER[v], sty='p')], not upright and v != 'Delta'
        if v in SYMBOLS:
            return [self.run(SYMBOLS[v], sty='p')], False
        if v in SPACES:
            return [self.run(SPACES[v], sty='p')], False
        if v == 'frac':
            num, den = self.arg(upright), self.arg(upright)
            f = E.Element(M + 'f')
            _mv(E.SubElement(f, M + 'fPr'), 'type', 'bar')
            f.append(self.box('num', num))
            f.append(self.box('den', den))
            return [f], False
        if v == 'sqrt':
            deg = None
            nt = self.peek()
            if nt is not None and nt[0] == 'ch' and nt[1] == '[':
                self.next()
                deg = self.seq(']', upright)
                self.next()
                if not deg:
                    self.err('\\sqrt[ ] 里的根指数是空的')
            body = self.arg(upright)
            rad = E.Element(M + 'rad')
            if deg is None:
                _mv(E.SubElement(rad, M + 'radPr'), 'degHide', 'on')
                E.SubElement(rad, M + 'deg')
            else:
                rad.append(self.box('deg', deg))
            rad.append(self.box('e', body))
            return [rad], False
        if v in ('mathrm', 'rm', 'mathbf'):
            return self.arg(True), False
        if v == 'mathit':
            # \mathit{SNR}：多个字母组成的一个符号（单值降噪数等），整体记一个、要在“式中”说明；
            # 里面的字母不再各算一个符号（田林附件4 的 \mathit{SNR} 曾被报“N、R、S 没在式中说明”）
            saved, self.nosym = self.nosym, 1
            try:
                body = self.arg(False)
            finally:
                self.nosym = saved
            return body, True
        if v in ('bar', 'overline'):
            body = self.arg(upright)
            if v == 'bar':
                acc = E.Element(M + 'acc')
                _mv(E.SubElement(acc, M + 'accPr'), 'chr', '\u203e')
                acc.append(self.box('e', body))
                return [acc], False
            bar = E.Element(M + 'bar')
            _mv(E.SubElement(bar, M + 'barPr'), 'pos', 'top')
            bar.append(self.box('e', body))
            return [bar], False
        if v == 'left':
            return [self.left_right()], False
        if v == 'right':
            self.err('\\left 与 \\right 不配对：多了一个 \\right')
        self.err('不支持的命令 \\%s' % v)

    def char_atom(self, t, upright):
        v = t[1]
        if v.isascii() and v.isalpha():
            return [self.run(v, sty='p' if upright else None)], not upright
        if v in BRACKETS:
            body = self.seq(BRACKETS[v], upright)
            self.next()
            return [self.delim(v, BRACKETS[v], body)], False
        if v in ')]':
            self.closer_error(t)
        if v in OP_CHARS:
            return [self.run(OP_CHARS[v], sty='p')], False
        if v in UNI_OPS:
            return [self.run('⋅' if v == '·' else v, sty='p')], False
        if v == '~':
            return [self.run('\u00a0', sty='p')], False
        if 'α' <= v <= 'ω' and v.isalpha():
            return [self.run(v, sty='p' if upright else None)], not upright
        if v in 'ΓΔΘΛΞΠΣΥΦΨΩ':
            return [self.run(v, sty='p')], False
        if v == '&' or v == '#' or v == '$':
            self.err('不支持的字符 %s' % v)
        if v in '*':
            self.err('不支持的字符 *（乘号写 \\cdot 或 \\times）')
        self.err('不支持的字符 %s' % v)

    def delta(self, t):
        """\\Delta L 这类“变化量”：Δ 与紧跟的符号合成一个要说明的符号。"""
        delta = self.run('Δ', sty='p')
        nt = self.peek()
        if nt is not None and ((nt[0] == 'cmd' and nt[1] in GREEK_LOWER) or
                               (nt[0] == 'ch' and nt[1].isascii() and nt[1].isalpha())):
            nodes, _ = self.atom()
            return [delta] + nodes, True
        return [delta], False

    def delim(self, beg, end, body):
        d = E.Element(M + 'd')
        dp = E.SubElement(d, M + 'dPr')
        _mv(dp, 'begChr', beg)
        _mv(dp, 'sepChr', '')
        _mv(dp, 'endChr', end)
        E.SubElement(dp, M + 'grow')
        d.append(self.box('e', body))
        return d

    def left_right(self):
        t = self.next()
        if t[0] == 'ch' and t[1] in LEFT_DELIMS:
            beg = LEFT_DELIMS[t[1]]
        elif t[0] == 'cmd' and t[1] in ('{', '|'):
            beg = '{' if t[1] == '{' else '|'
        else:
            self.err('\\left 后面要跟 ( [ \\{ | 或 .')
        body = self.seq('right')
        self.next()
        t = self.next()
        if t[0] == 'ch' and t[1] in RIGHT_DELIMS:
            end = RIGHT_DELIMS[t[1]]
        elif t[0] == 'cmd' and t[1] in ('}', '|'):
            end = '}' if t[1] == '}' else '|'
        else:
            self.err('\\right 后面要跟 ) ] \\} | 或 .')
        return self.delim(beg, end, body)

    # ---- 函数、求和
    def operand(self, upright, stop_at_ops=True):
        """函数的自变量：下一项（带上下标）；碰到运算符、关系符、括号结束符时为空。"""
        t = self.peek()
        if t is None or self.is_closer(t) or t[0] in ('^', '_'):
            return []
        if t[0] == 'ch' and (OP_CHARS.get(t[1]) in STOP_OPS or t[1] in STOP_OPS):
            return []
        if t[0] == 'cmd' and t[1] in ('pm', 'mp', 'le', 'leq', 'ge', 'geq', 'approx', 'neq', 'ne', 'propto'):
            return []
        return self.term(upright)

    def function(self, upright):
        name = self.next()[1]
        start = self.toks[self.p - 1][2]
        fname = [self.run(name, sty='p')]
        sub = sup = None
        while self.peek() is not None and self.peek()[0] in ('^', '_'):
            c = self.next()[0]
            if c == '_':
                sub = self.script_arg(True)
            else:
                sup = self.script_arg(False)
        if sub is not None or sup is not None:
            fname = [self.sub_sup(fname, sub, sup)]
        arg = self.operand(upright)
        f = E.Element(M + 'func')
        f.append(self.box('fName', fname))
        f.append(self.box('e', arg))
        return [f]

    def nary(self, upright):
        name = self.next()[1]
        sub = sup = None
        while self.peek() is not None and self.peek()[0] in ('^', '_'):
            c = self.next()[0]
            self.nosym += 1                    # 求和号上下限里的指标（i、j=1、n）不要求在“式中”说明
            try:
                if c == '_':
                    sub = self.arg()
                else:
                    sup = self.arg()
            finally:
                self.nosym -= 1
        n = E.Element(M + 'nary')
        pr = E.SubElement(n, M + 'naryPr')
        _mv(pr, 'chr', NARY[name])
        _mv(pr, 'limLoc', 'undOvr')
        _mv(pr, 'subHide', 'on' if sub is None else 'off')
        _mv(pr, 'supHide', 'on' if sup is None else 'off')
        n.append(self.box('sub', sub or []))
        n.append(self.box('sup', sup or []))
        body = []
        while True:
            t = self.peek()
            if t is None or self.is_closer(t):
                break
            if (t[0] == 'ch' and (OP_CHARS.get(t[1], t[1]) in STOP_OPS)) or \
                    (t[0] == 'cmd' and t[1] in ('pm', 'mp', 'le', 'leq', 'ge', 'geq', 'approx', 'neq', 'ne', 'propto')):
                break
            body.extend(self.term(upright))
        n.append(self.box('e', body))
        return [n]


def norm_symbol(raw):
    """符号的规范写法：去空白；单字符的 {x} 去掉花括号（t_{i}→t_i）。"""
    s = re.sub(r'\s+', '', raw)
    s = re.sub(r'([_^])\{([A-Za-z0-9])\}', r'\1\2', s)
    return s


def _merge_letters(root):
    """相邻的、没有样式的 m:r（变量字母）合并成一个 run。"""
    for parent in root.iter():
        prev = None
        for ch in list(parent):
            if ch.tag == M + 'r' and ch.find(M + 'rPr') is None and prev is not None:
                prev.find(M + 't').text += ch.find(M + 't').text
                parent.remove(ch)
                continue
            prev = ch if (ch.tag == M + 'r' and ch.find(M + 'rPr') is None) else None


def to_omml(latex, size=28, font=MATH_FONT, ea_font='宋体'):
    """LaTeX 子集 → m:oMath。不支持的写法抛 LatexError。"""
    if not latex or not latex.strip():
        raise LatexError('公式是空的', latex)
    bad = unicode_problems(latex)
    if bad:
        raise LatexError('公式里有不能写的字符 %s（上下标用 _ ^，等号用 =，标点用英文）' % ''.join(bad), latex)
    p = Parser(latex, size, font, ea_font)
    nodes = p.parse_all()
    om = E.Element(M + 'oMath', nsmap={'m': M_NS, 'w': W_NS})
    for n in nodes:
        om.append(n)
    _merge_letters(om)
    return om


def math_para(om):
    """居中的独立公式：m:oMathPara 包着 m:oMath。"""
    mp = E.Element(M + 'oMathPara')
    _mv(E.SubElement(mp, M + 'oMathParaPr'), 'jc', 'center')
    mp.append(om)
    return mp


def symbols_of(latex):
    """公式里需要在“式中”说明的符号（规范写法的集合）。转换失败抛 LatexError。"""
    p = Parser(latex)
    p.parse_all()
    return set(p.symbols)


def text_of(om):
    """m:oMath 的纯文字（测试、检查用）。"""
    return ''.join(t.text or '' for t in om.iter(M + 't'))
