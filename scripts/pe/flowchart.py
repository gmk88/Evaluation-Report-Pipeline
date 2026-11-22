"""工艺流程及职业病危害因素分布图（第2.5节）：按一份 JSON 说明画。画法照晋钢200MW 收尾工作区/绘制v3流程图.py
（用户认可的图2.5-1～2.5-8），画图部分不改；这里只把“框放哪、线怎么走、字放哪”改成按说明自动排：
- 框放在几行四列的格子里，框内三行＝设施名称／参数或防护设施／危害因素编号；依托的、已有的设施画虚线框；
- 连线走横平竖直的折线，自动找路：少拐弯，先走框间空隙；同一处走几条线时错开；
- 线上的物料字放在线旁空处，放不下时缩小字号、加宽行间空隙，还放不下就报错；
- 图例从 1 编号；框里的编号都要在图例里，图例里的都要有框用到。图题不画进图里，由正文图下题注给出。
压线检查照 流程图压线检查.py：线不压别的框、不重合；字不压框、不压线、不出外框、不互相重叠。
画之前按估算字宽查，画时再用浏览器量实际字宽查一遍；有问题报错，不出图。
说明的格式：
  {"图号": "图2.5-1",
   "框": [{"号": "A1", "行": 1, "列": 1, "名称": "红线接点及煤气母管", "说明": "电动蝶阀、眼镜阀",
           "因素": [1, 2], "虚线": false}],
   "连线": [{"从": "A1", "到": "A2", "物料": "混合煤气", "虚线": false, "出": "右", "进": "左"}],
   "因素": ["噪声", "一氧化碳"],
   "注": ["注：框内第二行为……；", "……。"]}
行从 1 起，列 1～4；“出”“进”指从框的哪一边出、进（上、下、左、右），可不写，不写时自动选。
用法：python3 scripts/pe/flowchart.py 说明.json 输出.png"""
import hashlib
import heapq
import html
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from . import windrose as WR
except ImportError:                                    # 直接运行本文件
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pe import windrose as WR

FONT = "'Songti SC','STSong','SimSun','NSimSun',serif"
W, BW, BH = 1600, 300, 118                     # 图宽，方框宽、高（同200MW）
XS = [50, 440, 830, 1220]                      # 四列左边
FRAME = 6                                      # 外虚线框离图边
GAP, TRACK = 92, 34                            # 行间空隙；空隙里每多走一条横线、或放字不够时加宽
FS = {'name': 23, 'sub': 17, 'code': 21, 'legend': 21, 'note': 19}
LABEL = (18, 16)                               # 物料字字号，放不下时依次缩小（16 px 是下限，见 MIN）
MIN = {'name': 16, 'sub': 16, 'code': 16, 'legend': 16}   # 框内字缩到这么小还放不下就报错。
# 图宽 1600 px 插成横向页 23 cm 时，16 px 约 6.5 磅，是打印后能看清的下限（K020）；再小的不画
CLEAR, LINE_CLEAR = 4, 1.5                     # 字离框线、离箭头线的最小间距（同压线检查）
MAX_ROWS, MAX_BENDS = 8, 4
VERSION = 1                                    # 画法改了就加 1
SIDES = {'上': 0, '下': 1, '左': 2, '右': 3}
MOVES = [(-1, 0), (1, 0), (0, -1), (0, 1)]     # 粗格子里的一步：上、下、左、右
INTO = {'上': 1, '下': 0, '左': 3, '右': 2}     # 从框的哪边进＝最后一步往哪走
KEYS = {'': {'图号', '框', '连线', '因素', '注'}, '框': {'号', '行', '列', '名称', '说明', '因素', '虚线'},
        '连线': {'从', '到', '物料', '虚线', '出', '进'}}
FRACS = (0.5, 0.3, 0.7, 0.15, 0.85)            # 物料字沿线放的位置（从线的中点起试）
SCRIPT = '⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉ₓ'
SCRIPT_PLAIN = str.maketrans(SCRIPT, '01234567890123456789x')


def units(t):
    """估算文字宽度（以字号为单位，宁宽勿窄）：按 Chrome 里宋体实量，汉字和全角符号 1，数字 0.5，
    小写 0.55（m、w 0.8），大写 0.75（M、W 0.9），上下标 0.4，其余 0.6。"""
    w = 0
    for ch in t:
        if ch in SCRIPT:
            w += 0.4
        elif ord(ch) > 0x2E80 or ch in '℃×→←≤≥°·～％':
            w += 1.0
        elif ch.isdigit():
            w += 0.5
        elif ch in 'mwMW':
            w += 0.8 if ch.islower() else 0.9
        elif ch.islower():
            w += 0.55
        elif ch.isupper():
            w += 0.75
        elif ch == ' ':
            w += 0.35
        else:
            w += 0.6
    return w


def ink(x, y, t, size, anchor='middle', w=None):
    """文字墨迹框 (x0, y0, x1, y1)：y 为基线；按 getBBox 去掉上下各 15% 留白（同压线检查）。"""
    w = units(t) * size if w is None else w
    x0 = x - w / 2 if anchor == 'middle' else (x if anchor == 'start' else x - w)
    return (x0, y - 0.84 * size, x0 + w, y + 0.14 * size)


def scripts(t, size):
    """m³、SO₂ 的上下标字符画成缩小、升降的普通字符（宋体没有 ₂ ₆ 等字形）。"""
    out = []
    for part in re.split(r'([⁰¹²³⁴⁵⁶⁷⁸⁹]+|[₀₁₂₃₄₅₆₇₈₉ₓ]+)', t):
        if not part:
            continue
        if part.translate(SCRIPT_PLAIN) == part:
            out.append(html.escape(part))
        else:
            shift = round(size * (0.36 if part[0] in '⁰¹²³⁴⁵⁶⁷⁸⁹' else -0.14), 1)
            out.append(f'<tspan font-size="{round(size * 0.65, 1)}" baseline-shift="{shift}">'
                       f'{html.escape(part.translate(SCRIPT_PLAIN))}</tspan>')
    return ''.join(out)


def text_svg(k, x, y, t, size, anchor='middle'):
    return (f'<text data-k="{k}" x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" '
            f'font-weight="normal" font-family="{FONT}">{scripts(t, size)}</text>')


def fit(t, size, width, least):
    """字号：放得下用 size，放不下缩小；缩到 least 还放不下返回 None。"""
    s = min(size, round(width / max(units(t), 0.5), 1))
    return s if s >= least else None


def fingerprint(spec):
    """说明的内容和画法版本都算进来：哪样变了，出稿时重画。"""
    raw = json.dumps(spec, ensure_ascii=False, sort_keys=True) + '\n' + str(VERSION)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _check(spec):
    """说明本身的错：字段、重复、指向、编号。返回问题列表。"""
    if not isinstance(spec, dict):
        return ['说明应是一个 JSON 对象（{…}）']
    p = ['说明里有不认识的字段：%s（只认 %s）' % ('、'.join(sorted(set(spec) - KEYS[''])), '、'.join(sorted(KEYS[''])))
         ] if set(spec) - KEYS[''] else []
    fac = spec.get('因素')
    if not isinstance(fac, list) or not fac or not all(isinstance(f, str) and f.strip() for f in fac):
        p.append('“因素”应是危害因素名称的列表（按图例顺序，从 1 编号），不能为空')
        fac = []
    for f in sorted({f for f in fac if fac.count(f) > 1}):
        p.append('图例里“%s”写了两次' % f)
    boxes = spec.get('框')
    if not isinstance(boxes, list) or not boxes:
        return p + ['“框”应是方框的列表，不能为空']
    ids, cells, used = {}, {}, set()
    for i, b in enumerate(boxes, 1):
        if not isinstance(b, dict):
            p.append('第%d个框不是 {…}' % i)
            continue
        no = b.get('号')
        name = '框“%s”' % no if isinstance(no, str) and no else '第%d个框' % i
        if set(b) - KEYS['框']:
            p.append('%s有不认识的字段：%s' % (name, '、'.join(sorted(set(b) - KEYS['框']))))
        if not isinstance(no, str) or not no.strip():
            p.append('%s没有框号（“号”）' % name)
        elif no in ids:
            p.append('框号“%s”重复' % no)
        else:
            ids[no] = b
        r, c = b.get('行'), b.get('列')
        if not (_int(r) and 1 <= r <= MAX_ROWS and _int(c) and 1 <= c <= 4):
            p.append('%s的位置不对：行应为 1～%d、列应为 1～4 的整数' % (name, MAX_ROWS))
        elif (r, c) in cells:
            p.append('%s和框“%s”占同一格（第%d行第%d列）' % (name, cells[(r, c)], r, c))
        else:
            cells[(r, c)] = no
        if not isinstance(b.get('名称'), str) or not b['名称'].strip():
            p.append('%s没有名称' % name)
        if not isinstance(b.get('说明', ''), str):
            p.append('%s的“说明”应是一行字' % name)
        if not isinstance(b.get('虚线', False), bool):
            p.append('%s的“虚线”应是 true 或 false' % name)
        codes = b.get('因素', [])
        if not isinstance(codes, list) or not all(_int(k) for k in codes):
            p.append('%s的“因素”应是图例编号的列表，如 [1, 2]' % name)
            continue
        for k in sorted(set(codes)):
            if codes.count(k) > 1:
                p.append('%s的因素编号 %d 写了两次' % (name, k))
            if not 1 <= k <= len(fac):
                p.append('%s的因素编号 %d 在图例里没有（图例只有 1～%d）' % (name, k, len(fac)))
        used |= set(codes)
    for k in range(1, len(fac) + 1):
        if k not in used:
            p.append('图例第 %d 项“%s”没有框用到（图例只列图上标了的因素）' % (k, fac[k - 1]))
    links = spec.get('连线', [])
    if not isinstance(links, list):
        return p + ['“连线”应是连线的列表']
    for i, ln in enumerate(links, 1):
        if not isinstance(ln, dict):
            p.append('第%d条连线不是 {…}' % i)
            continue
        if set(ln) - KEYS['连线']:
            p.append('第%d条连线有不认识的字段：%s' % (i, '、'.join(sorted(set(ln) - KEYS['连线']))))
        a, b = ln.get('从'), ln.get('到')
        for k, v in (('从', a), ('到', b)):
            if v not in ids:
                p.append('第%d条连线“%s”的框“%s”没有这个框' % (i, k, v))
        if a == b and a in ids:
            p.append('第%d条连线从框“%s”连到它自己' % (i, a))
        if not isinstance(ln.get('物料', ''), str) or not isinstance(ln.get('虚线', False), bool):
            p.append('第%d条连线的“物料”应是一行字、“虚线”应是 true 或 false' % i)
        for k in ('出', '进'):
            if ln.get(k, '') not in ('', *SIDES):
                p.append('第%d条连线的“%s”应是 上、下、左、右 之一' % (i, k))
    notes = spec.get('注', [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        p.append('“注”应是若干行字的列表')
    elif any(units(n) * FS['note'] > W - 2 * FRAME - 40 for n in notes):
        p.append('注有一行太长（每行不超过 %d 个汉字宽），请分成两行' % ((W - 2 * FRAME - 40) // FS['note']))
    if not isinstance(spec.get('图号', ''), str):
        p.append('“图号”应是一行字，如“图2.5-1”')
    return p


def _route(n, occ, a, b, out, into, used):
    """在粗格子里找一条横平竖直的路（Dijkstra）：奇数行列是框所在的格，偶数行列是框间空隙和四周空白。
    不穿别的框；少拐弯；尽量不走竖向窄缝和四周空白；和已画的线少交叉、少并行。返回经过的格子。"""
    R, C = n
    start = (0, 0, a, None, 0, (a,))
    heap, best, seq = [start], {}, 1
    while heap:
        cost, _, cell, d, bends, path = heapq.heappop(heap)
        if cell == b:
            return list(path)
        if best.get((cell, d, bends), 1e9) < cost:
            continue
        for k, (dr, dc) in enumerate(MOVES):
            nxt = (cell[0] + dr, cell[1] + dc)
            if not (0 <= nxt[0] <= R and 0 <= nxt[1] <= C) or nxt == a or (nxt in occ and nxt != b):
                continue
            if d is None and out is not None and k != out:
                continue
            if d is not None and (k ^ 1) == d:                       # 不原地掉头
                continue
            if nxt == b and into is not None and k != into:
                continue
            nb = bends + (d is not None and k != d)
            if nb > MAX_BENDS:
                continue
            c = cost + 1 + 4 * (nb > bends)
            if k < 2 and nxt[1] % 2 == 0:                            # 竖着走框间窄缝或左右空白（200MW 图都不走）
                c += 1
            if nxt[0] in (0, R) or nxt[1] in (0, C):
                c += 2
            o = 'V' if k < 2 else 'H'
            if nxt != b and nxt in used:
                c += 1.5 if used[nxt] == {o} else 3
            if c < best.get((nxt, k, nb), 1e9):
                best[(nxt, k, nb)] = c
                heapq.heappush(heap, (c, seq, nxt, k, nb, path + (nxt,)))
                seq += 1
    return None


def _runs(path):
    """格子路径→直段：[(方向 'H'/'V', 所在行或列, 起, 止, 走向 ±1)]；起止是粗格子坐标，两端框的边按 ±0.4 计。"""
    out = []
    for p, q in zip(path, path[1:]):
        o, s = ('V', q[0] - p[0]) if q[1] == p[1] else ('H', q[1] - p[1])
        line, at, to = (p[1], p[0], q[0]) if o == 'V' else (p[0], p[1], q[1])
        if out and out[-1][0] == o and out[-1][4] == s:
            out[-1][3] = to
        else:
            out.append([o, line, at, to, s])
    out[0][2] += 0.4 * out[0][4]
    out[-1][3] -= 0.4 * out[-1][4]
    return out


def _lean(rs, si):
    """直段偏向：竖段接着往右走（或从左边来）为正，横段接着往下走为正；同组里偏负的排在左、上。"""
    return (rs[si + 1][4] if si + 1 < len(rs) else 0) - (rs[si - 1][4] if si > 0 else 0)


def _tracks(runs):
    """同一行（列）上区间相接或重叠的直段错开走：返回 {(连线, 段): (名次, 同组条数)}。"""
    groups, res = {}, {}
    for li, rs in enumerate(runs):
        for si, r in enumerate(rs):
            groups.setdefault((r[0], r[1]), []).append((li, si))
    for members in groups.values():
        span = {m: sorted(runs[m[0]][m[1]][2:4]) for m in members}
        members.sort(key=lambda m: span[m])
        comps, hi = [], None
        for m in members:
            if comps and span[m][0] <= hi + 1e-6:
                comps[-1].append(m)
                hi = max(hi, span[m][1])
            else:
                comps.append([m])
                hi = span[m][1]
        for comp in comps:
            comp.sort(key=lambda m: (_lean(runs[m[0]], m[1]), m[0]))
            for i, m in enumerate(comp):
                res[m] = (i, len(comp))
    return res


def _routes(spec):
    """各连线的直段（按两框远近，近的先找路）。返回 (直段列表, 问题)。"""
    nrows = max(b['行'] for b in spec['框'])
    cell = {b['号']: (2 * b['行'] - 1, 2 * b['列'] - 1) for b in spec['框']}
    occ, links = set(cell.values()), spec.get('连线', [])
    dist = lambda ln: abs(cell[ln['从']][0] - cell[ln['到']][0]) + abs(cell[ln['从']][1] - cell[ln['到']][1])
    used, runs, probs = {}, [None] * len(links), []
    for i in sorted(range(len(links)), key=lambda i: (dist(links[i]), i)):
        ln = links[i]
        path = _route((2 * nrows, 8), occ, cell[ln['从']], cell[ln['到']], SIDES.get(ln.get('出') or ''),
                      INTO.get(ln.get('进') or ''), used)
        if not path:
            probs.append('第%d条连线（%s→%s）找不到不压别的框的走法，请调整框的位置或“出”“进”' % (i + 1, ln['从'], ln['到']))
            continue
        for p, q, r in zip(path, path[1:], path[2:]):
            used.setdefault(q, set()).update({'V' if p[1] == q[1] else 'H', 'V' if q[1] == r[1] else 'H'})
        runs[i] = _runs(path)
    return runs, probs


def _legend_rows(fac):
    """图例按字宽折行（同200MW）：每行不超过 68 个字宽，首行加“职业病危害因素：”，末行只剩一项时从上一行挪一项下来。"""
    rows, cur = [], []
    for it in ['%d %s' % (i, f) for i, f in enumerate(fac, 1)]:
        if cur and units('；'.join(cur + [it])) + (0 if rows else 8) > 68:
            rows.append(cur)
            cur = [it]
        else:
            cur.append(it)
    rows.append(cur)
    if len(rows) > 1 and len(rows[-1]) == 1 and len(rows[-2]) > 2:
        rows[-1].insert(0, rows[-2].pop())
    rows = ['；'.join(r) for r in rows]
    rows[0] = '职业病危害因素：' + rows[0]
    return [r + ('；' if i < len(rows) - 1 else '') for i, r in enumerate(rows)]


def _geometry(spec, runs, extra):
    """粗格子→像素。行间空隙按里面走的横线条数加宽，extra 是放字不够时另加的。返回 (几何, 问题)。"""
    R = 2 * max(b['行'] for b in spec['框'])
    tr, m, probs = _tracks(runs), {}, []
    for (li, si), (k, cnt) in tr.items():
        o, line = runs[li][si][:2]
        cap = (5 if o == 'V' else 3) if line % 2 else (4 if o == 'H' else 2 if line in (0, 8) else 3)
        if cnt > cap:
            probs.append('第%d条连线和别的线挤在同一条空隙里（%d条），请调整框的位置' % (li + 1, cnt))
        if o == 'H':
            m[line] = max(m.get(line, 0), cnt)
    y, rows = FRAME, {}
    for r in range(R + 1):
        h = BH if r % 2 else (GAP + TRACK * (m[r] - 1)) if m.get(r) else (44 if r == 0 else 45 if r == R else GAP)
        h += 0 if r % 2 else TRACK * extra.get(r, 0)
        rows[r], y = (y, y + h), y + h
    cols = {c: (XS[c // 2], XS[c // 2] + BW) if c % 2 else (FRAME, XS[0]) if c == 0 else
            (XS[3] + BW, W - FRAME) if c == 8 else (XS[c // 2 - 1] + BW, XS[c // 2]) for c in range(9)}

    def coord(o, line, k, cnt):
        lo, hi = (cols if o == 'V' else rows)[line]
        step = min(120 if o == 'V' else 48, (hi - lo - 40) / max(cnt - 1, 1)) if line % 2 else (hi - lo) / (cnt + 1)
        return (lo + hi) / 2 + (k - (cnt - 1) / 2) * step

    rect = {b['号']: (cols[2 * b['列'] - 1][0], rows[2 * b['行'] - 1][0], cols[2 * b['列'] - 1][1],
                     rows[2 * b['行'] - 1][1]) for b in spec['框']}
    lines = []
    for li, (ln, rs) in enumerate(zip(spec.get('连线', []), runs)):
        cs = [coord(r[0], r[1], *tr[(li, si)]) for si, r in enumerate(rs)]
        A, B = rect[ln['从']], rect[ln['到']]
        o, s = rs[0][0], rs[0][4]
        pts = [(A[2] if s > 0 else A[0], cs[0]) if o == 'H' else (cs[0], A[3] if s > 0 else A[1])]
        pts += [(cs[i + 1], cs[i]) if rs[i][0] == 'H' else (cs[i], cs[i + 1]) for i in range(len(rs) - 1)]
        o, s = rs[-1][0], rs[-1][4]
        pts.append((B[0] - 2 if s > 0 else B[2] + 2, cs[-1]) if o == 'H' else (cs[-1], B[1] - 2 if s > 0 else B[3] + 2))
        lines.append(pts)
    leg = _legend_rows(spec['因素'])
    legend = (50, y, 1540, y + 40 + 34 * len(leg))
    H = legend[3] + 40 + 32 * len(spec.get('注', [])) + 10
    ends = [(ln['从'], ln['到']) for ln in spec.get('连线', [])]
    return {'rect': rect, 'lines': lines, 'ends': ends, 'legend': legend, 'legend_rows': leg, 'H': H}, probs


def _inflate(r, d):
    return (r[0] - d, r[1] - d, r[2] + d, r[3] + d)


def _hit(a, b):
    """两个矩形 (x0, y0, x1, y1) 是否相交。"""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _segs(lines):
    """连线各段和箭头占的矩形（同压线检查）。"""
    out = []
    for pts in lines:
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            out.append((min(x1, x2) - 1, min(y1, y2) - 1, max(x1, x2) + 1, max(y1, y2) + 1))
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        dx, dy = (x2 > x1) - (x2 < x1), (y2 > y1) - (y2 < y1)
        tip, base = (x2 + dx, y2 + dy), (x2 - 11 * dx, y2 - 11 * dy)
        out.append((min(tip[0], base[0]) - 5 * abs(dy), min(tip[1], base[1]) - 5 * abs(dx),
                    max(tip[0], base[0]) + 5 * abs(dy), max(tip[1], base[1]) + 5 * abs(dx)))
    return out


def _free(b, g, segs, placed):
    """物料字放在 b 处：不出外框、不进图例区，离框、离线、离已放的字都不太近。"""
    return not (b[0] < FRAME + CLEAR or b[2] > W - FRAME - CLEAR or b[1] < FRAME + CLEAR
                or b[3] > g['legend'][1] - CLEAR or any(_hit(b, _inflate(r, CLEAR)) for r in g['rect'].values())
                or any(_hit(_inflate(b, LINE_CLEAR), s) for s in segs) or any(_hit(b, o) for o in placed))


def _spots(pts):
    """物料字的候选位置，好的在前：字号从大到小；长段在前（横段加 150，同样长先放横段）；先放段中点再往两头挪；
    横段先上后下，竖段先右后左。"""
    order = sorted(range(len(pts) - 1), key=lambda i: -abs(pts[i + 1][0] - pts[i][0]) - abs(pts[i + 1][1] - pts[i][1])
                   - 150 * (pts[i][1] == pts[i + 1][1]))
    for size in LABEL:
        for i in order:
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            if i == len(pts) - 2:                 # 末段算到框边（箭头尖离框 2），段中点就在两框正中
                x2, y2 = x2 + 2 * ((x2 > x1) - (x2 < x1)), y2 + 2 * ((y2 > y1) - (y2 < y1))
            for f in FRACS:
                x, y = x1 + (x2 - x1) * f, y1 + (y2 - y1) * f
                if y1 == y2:
                    yield x, y - 10, size, 'middle'
                    yield x, y + 8 + 0.84 * size, size, 'middle'
                else:
                    yield x + 12, y + 0.35 * size, size, 'start'
                    yield x - 12, y + 0.35 * size, size, 'end'


def _labels(spec, g):
    """每条线的物料字找一处空地；找不到的连线序号放进 fail。"""
    segs, placed, out, fail = _segs(g['lines']), [], [], []
    for li, (ln, pts) in enumerate(zip(spec.get('连线', []), g['lines'])):
        t = ln.get('物料', '').strip()
        if not t:
            continue
        spot = next(((x, y, s, a) for x, y, s, a in _spots(pts) if _free(ink(x, y, t, s, a), g, segs, placed)), None)
        if spot is None:
            fail.append(li)
            continue
        placed.append(ink(*spot[:2], t, *spot[2:]))
        out.append({'x': spot[0], 'y': spot[1], 't': t, 'size': spot[2], 'anchor': spot[3], 'own': None})
    return out, fail


def _box_texts(spec, g):
    """框内三行（说明为空时两行）：上下居中，放不下时缩小字号（同200MW），缩到下限还放不下报错。"""
    out, probs = [], []
    for b in spec['框']:
        x0, y0, x1, y1 = g['rect'][b['号']]
        codes = '、'.join(map(str, sorted(b.get('因素', [])))) or '—'
        rows = [(b['名称'], 'name')] + ([(b['说明'], 'sub')] if b.get('说明', '').strip() else []) + [(codes, 'code')]
        cy = y0 + (y1 - y0 - sum(FS[k] * 1.28 for _, k in rows)) / 2
        for t, k in rows:
            cy += FS[k] * 1.28
            s = fit(t, FS[k], x1 - x0 - 16, MIN[k])
            if s is None:
                probs.append('框“%s”的%s太长：“%s”（一行约 %d 个汉字宽）' % (
                    b['号'], {'name': '名称', 'sub': '说明', 'code': '因素编号'}[k], t, (x1 - x0 - 16) // MIN[k]))
                s = MIN[k]
            out.append({'x': (x0 + x1) / 2, 'y': cy - FS[k] * 0.28, 't': t, 'size': s, 'anchor': 'middle', 'own': b['号']})
    return out, probs


def _foot_texts(spec, g):
    """图例框里的行和图下的注（注居中，每行 32）。"""
    x0, y0, x1, y1 = g['legend']
    out, probs = [], []
    cy = y0 + (y1 - y0 - FS['legend'] * 1.28 * len(g['legend_rows'])) / 2
    for t in g['legend_rows']:
        cy += FS['legend'] * 1.28
        s = fit(t, FS['legend'], x1 - x0 - 16, MIN['legend'])
        if s is None:
            probs.append('图例有一行放不下：“%s”' % t)
            s = MIN['legend']
        out.append({'x': (x0 + x1) / 2, 'y': cy - FS['legend'] * 0.28, 't': t, 'size': s, 'anchor': 'middle',
                    'own': '图例'})
    for i, t in enumerate(spec.get('注', [])):
        out.append({'x': W / 2, 'y': y1 + 40 + 32 * i, 't': t, 'size': FS['note'], 'anchor': 'middle', 'own': None})
    return out, probs


def _plan(spec):
    """找路、排位置、放字；字放不下时加宽它走过的行间空隙再排。返回 (几何, 全部文字, 问题)。"""
    runs, probs = _routes(spec)
    if probs:
        return None, [], probs
    extra = {}
    for _ in range(10):
        g, probs = _geometry(spec, runs, extra)
        if probs:
            return None, [], probs
        labels, fail = _labels(spec, g)
        grow = {r for li in fail for o, line, a, b, _ in runs[li]
                for r in ([line] if o == 'H' else range(int(min(a, b)) + 1, int(max(a, b)) + 1))
                if r % 2 == 0 and extra.get(r, 0) < 4}
        if not fail or not grow:
            break
        for r in grow:
            extra[r] = extra.get(r, 0) + 1
    links = spec.get('连线', [])
    probs = ['第%d条连线（%s→%s）的物料字“%s”放不下：线旁没有空地，请缩短（相邻两框间一行约 5 个汉字宽）或调整框的位置'
             % (li + 1, links[li]['从'], links[li]['到'], links[li]['物料']) for li in fail]
    bt, p1 = _box_texts(spec, g)
    ft, p2 = _foot_texts(spec, g)
    return g, bt + labels + ft, probs + p1 + p2


def _audit(g, texts, boxes=None):
    """压线检查（照 流程图压线检查.py）：线不压不是它两端的框、不和别的线重合；字不出外框、不压框线、
    不贴框、不压箭头线、不互相重叠。boxes 是浏览器量出的字框 {序号: (x, y, w, h)}，不给时按估算字宽查。"""
    probs = []
    for li, pts in enumerate(g['lines']):
        segs = list(zip(pts, pts[1:]))
        for si, (p, q) in enumerate(segs):
            s = (min(p[0], q[0]), min(p[1], q[1]), max(p[0], q[0]), max(p[1], q[1]))
            for no, r in g['rect'].items():
                own = no in g['ends'][li]
                if own and (si == 0 and no == g['ends'][li][0] or si == len(segs) - 1 and no == g['ends'][li][1]):
                    continue
                if _hit(_inflate(s, 0.5), _inflate(r, 0 if own else 8)):
                    probs.append('第%d条连线压到了框“%s”' % (li + 1, no))
    flat = [(li, p, q) for li, pts in enumerate(g['lines']) for p, q in zip(pts, pts[1:])]
    for i, (la, p, q) in enumerate(flat):
        for lb, u, v in flat[i + 1:]:
            if la == lb:
                continue
            for k in (0, 1):                                  # k=0 竖段比 x，k=1 横段比 y
                if p[k] == q[k] and u[k] == v[k] and abs(p[k] - u[k]) < 8 and \
                        min(max(p[1 - k], q[1 - k]), max(u[1 - k], v[1 - k])) - \
                        max(min(p[1 - k], q[1 - k]), min(u[1 - k], v[1 - k])) > 0:
                    probs.append('第%d条和第%d条连线重合' % (la + 1, lb + 1))
    T = []
    for k, t in enumerate(texts):
        if not t['t'].strip():
            continue
        if boxes and k in boxes:
            x, y, w, h = boxes[k]
            T.append((t, (x, y + h * 0.15, x + w, y + h * 0.85)))
        else:
            T.append((t, ink(t['x'], t['y'], t['t'], t['size'], t['anchor'])))
    own = dict(g['rect'], **{'图例': g['legend']})
    segs = _segs(g['lines'])
    for t, b in T:
        if b[0] < FRAME + CLEAR or b[2] > W - FRAME - CLEAR or b[1] < FRAME + CLEAR or b[3] > g['H'] - FRAME - CLEAR:
            probs.append('出外框：“%s”' % t['t'])
        for no, r in own.items():
            if no == t['own']:
                if b[0] < r[0] + CLEAR or b[2] > r[2] - CLEAR or b[1] < r[1] or b[3] > r[3]:
                    probs.append('贴框或出框：“%s”' % t['t'])
            elif _hit(b, _inflate(r, CLEAR)):
                probs.append('压框线或离框太近：“%s”' % t['t'])
        if t['own'] is None and any(_hit(_inflate(b, LINE_CLEAR), s) for s in segs):
            probs.append('压箭头线：“%s”' % t['t'])
    for i, (ta, a) in enumerate(T):
        for tb, b in T[i + 1:]:
            if _hit(a, b):
                probs.append('文字重叠：“%s”和“%s”' % (ta['t'], tb['t']))
    return list(dict.fromkeys(probs))


def _svg(spec, g, texts):
    """画法同200MW：外虚线框、实线或虚线方框、折线箭头、图例框；字最后画。"""
    H = round(g['H'])
    s = ''
    dashed = {b['号']: b.get('虚线', False) for b in spec['框']}
    for no, (x0, y0, x1, y1) in list(g['rect'].items()) + [('图例', g['legend'])]:
        s += (f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="#fff" stroke="#000" stroke-width="2"'
              + (' stroke-dasharray="8,5"' if dashed.get(no) else '') + '/>')
    for ln, pts in zip(spec.get('连线', []), g['lines']):
        s += ('<polyline points="' + ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts) + '" fill="none" stroke="#000" '
              'stroke-width="2"' + (' stroke-dasharray="7,5"' if ln.get('虚线') else '') + ' marker-end="url(#ah)"/>')
    s += ''.join(text_svg(k, t['x'], t['y'], t['t'], t['size'], t['anchor']) for k, t in enumerate(texts))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
            '<defs><marker id="ah" markerWidth="12" markerHeight="10" refX="11" refY="5" orient="auto" '
            'markerUnits="userSpaceOnUse"><path d="M0,0 L12,5 L0,10 z" fill="#000"/></marker></defs>'
            f'<rect width="{W}" height="{H}" fill="#fff"/>'
            f'<rect x="{FRAME}" y="{FRAME}" width="{W - 2 * FRAME}" height="{H - 2 * FRAME}" fill="none" stroke="#000" '
            f'stroke-width="1.5" stroke-dasharray="6,5"/>{s}</svg>'), H


JS = r"""<script>
window.addEventListener('load', () => {
  const out = {};
  document.querySelectorAll('text[data-k]').forEach(t => {
    const b = t.getBBox(); out[t.getAttribute('data-k')] = [b.x, b.y, b.width, b.height]; });
  const pre = document.createElement('pre'); pre.id = 'out'; pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
});
</script>"""


def measure(svg, exe):
    """用浏览器量出每段字的实际位置（getBBox），返回 {序号: (x, y, w, h)}（同压线检查）。"""
    tmp = Path(tempfile.mkdtemp(prefix='pe_flow_'))
    try:
        (tmp / 'a.html').write_text('<html><head><meta charset="utf-8"></head><body style="margin:0">' + svg + JS
                                    + '</body></html>', encoding='utf-8')
        r = subprocess.run([exe, '--headless=new', '--disable-gpu', '--no-first-run', f'--user-data-dir={tmp / "p"}',
                            '--virtual-time-budget=3000', '--dump-dom', (tmp / 'a.html').as_uri()],
                           capture_output=True, timeout=120)
        m = re.search(r'<pre id="out">(.*?)</pre>', r.stdout.decode('utf-8', 'replace'), re.S)
        if not m:
            raise RuntimeError('流程图量字宽失败：' + r.stderr.decode('utf-8', 'replace')[-800:])
        return {int(k): tuple(v) for k, v in json.loads(html.unescape(m.group(1))).items()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def validate(spec):
    """查说明：字段、框号重复、两框占一格、连线指向不存在的框、图例里没有的编号；再排一遍版，查找不到路、
    字放不下、压线压框。返回问题列表（空表示能画）。"""
    probs = _check(spec)
    if probs:
        return probs
    g, texts, probs = _plan(spec)
    return probs or _audit(g, texts)


def draw(spec, out_png):
    """按说明画 out_png，旁边写同名 .svg 和 .json（指纹，出稿时据此判断要不要重画）。
    说明有错、排不开、压线时报 ValueError；没有浏览器报 RuntimeError。都不写文件。"""
    probs = validate(spec)
    if probs:
        raise ValueError('流程图说明有问题：\n' + '\n'.join('- ' + p for p in probs))
    exe = WR.browser()
    if not exe:
        raise RuntimeError('这台电脑上没找到 Chrome 或 Edge，画不了流程图')
    g, texts, _ = _plan(spec)
    svg, H = _svg(spec, g, texts)
    probs = _audit(g, texts, measure(svg, exe))
    if probs:
        raise ValueError('流程图按实际字宽有压线：\n' + '\n'.join('- ' + p for p in probs))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.with_suffix('.svg').write_text(svg, encoding='utf-8')
    try:
        WR.render(svg, W, H, out_png, exe)
    except RuntimeError as e:
        raise RuntimeError(str(e).replace('风频玫瑰图', '流程图')) from None
    legend = {str(i): f for i, f in enumerate(spec['因素'], 1)}
    out_png.with_suffix('.json').write_text(json.dumps(
        {'图号': spec.get('图号', ''), '指纹': fingerprint(spec), '画法版本': VERSION, '尺寸': [W, H],
         '危害编号': legend}, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'尺寸': (W, H), '危害编号': legend, '连线': g['lines']}


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if len(a) != 2:
        print(__doc__.strip().split('\n')[-1])
        return 2
    try:
        spec = json.loads(Path(a[0]).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as e:
        print('读不了说明 %s：%s' % (a[0], e))
        return 1
    try:
        res = draw(spec, a[1])
    except (ValueError, RuntimeError) as e:
        print(e)
        return 1
    print('已画', a[1], '%d×%d' % res['尺寸'], '图例', '；'.join('%s %s' % kv for kv in res['危害编号'].items()))
    return 0


if __name__ == '__main__':
    sys.exit(main())
