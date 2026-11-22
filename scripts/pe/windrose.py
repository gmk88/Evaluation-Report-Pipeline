"""风频玫瑰图：全年、夏季各画一幅，左右并排（K019）。画法照晋钢200MW 收尾工作区/绘制v3风玫瑰图.py
（2026-10-28 改成马沟格式，烧结、新材料两份报告复制用过），画图部分不改；这里只把“读哪张表、
画到哪、图上标题和数据来源行”改成按项目来：
- 表：正文里表头为“风向|全年（%）|夏季（%）|风向|全年（%）|夏季（%）”的表（find_table），不认死表号；
- 标题：“××风频玫瑰图（全年）”，××取表题开头的地名（“晋城市近三年……”→晋城市）；
- 数据来源行：照表题写“数据来源：建设单位提供的晋城市近三年风向频率统计”（表前段落或表注写了“建设单位提供”才带）。
画法：16 方位，北在上、顺时针排列，风向指来向；各方位按风向频率在方位线上取点，连成闭合多边形（规范画法）；
中心圆内注静风频率，各方位频率自中心圆边缘起量；同心圆为频率刻度（每 4%），刻度值沿 ENE 方位线标注，圈距小时隔圈标；
频率不小于 5% 的方位在端点外侧（外角平分线方向）标数；图下为最多、最少风向统计表和资料来源；图题由正文图下题注给出。
资料只有风向频率，没有分风速等级的频率，所以不画冷轧图的风速分级图例。
渲染用本机 Chrome 或 Edge 无界面截图（2 倍像素）；都没有时报错，不出图。
用法：python3 scripts/pe/windrose.py 正文/02_1_工程概况.md 图2.1-2 [输出文件夹]"""
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html import escape
from pathlib import Path

FONT = "'Songti SC','STSong','SimSun','NSimSun',serif"                 # 马沟风玫瑰图用宋体；Windows 用宋体 SimSun
DIRS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
CN = {'N': 'N(北)', 'NE': 'NE(东北)', 'E': 'E(东)', 'SE': 'SE(东南)',
      'S': 'S(南)', 'SW': 'SW(西南)', 'W': 'W(西)', 'NW': 'NW(西北)'}
CN16 = dict(zip(DIRS, ['北', '北东北', '东北', '东东北', '东', '东东南', '东南', '南东南',
                       '南', '南西南', '西南', '西西南', '西', '西西北', '西北', '北西北']))   # 统计表风向后注中文（同马沟图）
W, H = 1500, 1040                    # 纵向页插入宽14.1 cm（同马沟图2.1-1），1 px≈0.27 pt
R, R0, CY = 272, 44, 432              # 外圈半径、中心圆半径、圆心纵坐标
FS = {'title': 36, 'sub': 22, 'dir': 25, 'val': 23, 'ring': 20, 'calm': 21, 'table': 24, 'note': 21}
LABEL_MIN = 5                         # 端点标数的最低频率（%）
WIDTH_CM = '14.10cm'                  # 正文插图宽（同马沟、200MW）
VERSION = 1                           # 画法改了就加 1，已画的图出稿时重画
BROWSERS = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe']
RE_HEAD = re.compile(r'^\|\s*风向\s*\|\s*全年[^|]*\|\s*夏季[^|]*\|\s*风向\s*\|\s*全年[^|]*\|\s*夏季[^|]*\|\s*$')
RE_TCAP = re.compile(r'^(表\s*[0-9A-Z]+(?:\.\d+)*-\d+)\s+(.+)$')


def browser():
    """Chrome 或 Edge 的路径（环境变量 PE_BROWSER 优先）；都没有返回 None。"""
    for p in [os.environ.get('PE_BROWSER', '')] + BROWSERS:
        if p and Path(p).is_file():
            return p
    for name in ('google-chrome', 'chromium', 'chrome', 'msedge'):
        if shutil.which(name):
            return shutil.which(name)
    return None


def find_table(text):
    """正文里的风频表：{'表号', '表题', '行', 'caption', 'end', '注', '前文'}；没有返回 None。
    caption 是表题所在行号；end 是表（连同紧跟的表注）后面一行的行号，插图从这里插。"""
    lines = text.split('\n')
    for i, ln in enumerate(lines):
        if not RE_HEAD.match(ln.strip()):
            continue
        j = i - 1
        while j >= 0 and (not lines[j].strip() or lines[j].strip().startswith('<!--')):
            j -= 1
        m = RE_TCAP.match(lines[j].strip()) if j >= 0 else None
        if not m:
            continue
        k = i
        while k < len(lines) and lines[k].strip().startswith('|'):
            k += 1
        end, note, n = k, '', k
        while n < len(lines) and (not lines[n].strip() or lines[n].strip().startswith('<!--')):
            n += 1
        if n < len(lines) and lines[n].strip().startswith('注'):
            end, note = n + 1, lines[n].strip()
        p = j - 1
        while p >= 0 and not lines[p].strip():
            p -= 1
        return {'表号': re.sub(r'\s+', '', m.group(1)), '表题': m.group(2).strip(), '行': lines[i:k],
                'caption': j, 'end': end, '注': note, '前文': lines[p].strip() if p >= 0 else ''}
    return None


def _num(x, d):
    try:
        return float(x)
    except ValueError:
        raise ValueError('风频表 %s 那一格不是数：“%s”' % (d, x)) from None


def read_table(rows):
    """从风频表的行读全年、夏季风频和静风；表为“风向|全年|夏季|风向|全年|夏季”两栏并排。"""
    year, summer, calm = {}, {}, {}
    for line in rows:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) != 6 or not re.match(r'^[\d.]+$', cells[1]):
            continue
        for d, a, s in (cells[0:3], cells[3:6]):
            key = re.sub(r'[^A-Z].*$', '', d)
            if key == 'C' or d.startswith('静风'):
                calm = {'全年': _num(a, d), '夏季': _num(s, d)}
            elif key in DIRS:
                year[key], summer[key] = _num(a, d), _num(s, d)
    if len(year) != 16 or not calm:
        raise ValueError('风频表读不全：要 16 个方位加静风（C），读到 %d 个方位%s' % (len(year), '' if calm else '、没有静风'))
    for k, v in (('全年', year), ('夏季', summer)):
        total = sum(v.values()) + calm[k]
        if abs(total - 100) >= 0.05:
            raise ValueError('风频表%s合计 %.2f%%，不是 100%%' % (k, total))
    return {'全年': year, '夏季': summer}, calm


def place_name(title):
    """表题开头的地名：“晋城市近三年全年和夏季风向频率统计”→“晋城市”；“田林县气象站……”→“田林县”。"""
    m = re.match(r'^(.{2,12}?)(?:气象站|气象台|国家基本气象站)?(?=近|多年|\d{4}|全年|夏季|风向|累年)', title)
    return m.group(1) if m else ''


def source_line(tb):
    """图上每幅小标题下的数据来源行（同200MW图）：“数据来源：建设单位提供的晋城市近三年风向频率统计”。"""
    body = re.sub(r'全年(?:和|及|、)?夏季|（%）', '', tb['表题'])
    body = re.sub(r'(?:统计|资料|表)+$', '', body) + '统计'
    who = '建设单位提供的' if '建设单位提供' in tb['前文'] + tb['注'] else ''
    return '数据来源：' + who + body


def fig_title(tb):
    """图题：“晋城市近三年全年和夏季风频玫瑰图”（同200MW、新材料的图2.1-2）。"""
    body = re.sub(r'风向频率(?:统计)?(?:资料|表)?$', '', tb['表题'])
    return (body or '全年和夏季') + '风频玫瑰图'


def fingerprint(tb):
    """表里的数、表题和画法版本都算进来：哪样变了，出稿时重画。"""
    raw = '\n'.join([tb['表题'], tb['注'], tb['前文']] + tb['行'] + [str(VERSION)])
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]


def stale(png, tb):
    """流水线画的图（旁边有同名 .json）表或画法变了就要重画；手画的图（没有 .json）不管。"""
    meta = Path(png).with_suffix('.json')
    if not meta.is_file():
        return False
    try:
        return json.loads(meta.read_text(encoding='utf-8')).get('指纹') != fingerprint(tb) or not Path(png).is_file()
    except (OSError, ValueError):
        return True


def T(x, y, s, size, anchor='middle', color='#000', halo=False):
    """y 为文字竖向中线。"""
    h = ' paint-order="stroke" stroke="#fff" stroke-width="5" stroke-linejoin="round"' if halo else ''
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" text-anchor="{anchor}" dominant-baseline="central" '
            f'fill="{color}" font-family="{FONT}"{h}>{escape(s)}</text>')


def tw(s, size):
    """估算文字宽度，用于把方位名、端点数值放在圆外或端点外侧。"""
    w = 0
    for ch in s:
        if ord(ch) > 0x2E80:
            w += 1.0
        elif ch == 'W':
            w += 0.94
        elif ch.isupper():
            w += 0.7
        elif ch.isdigit():
            w += 0.6
        elif ch == '%':
            w += 0.9
        else:
            w += 0.34
    return w * size


def pt(cx, cy, r, deg):
    a = math.radians(deg)
    return cx + r * math.sin(a), cy - r * math.cos(a)


def outside(cx, cy, r, deg, w, h, gap):
    """宽 w、高 h 的文字框放在半径 r 外侧，框与圆的最近距离约为 gap，返回框中心。"""
    a = math.radians(deg)
    return pt(cx, cy, r + gap + abs(math.sin(a)) * w / 2 + abs(math.cos(a)) * h / 2, deg)


def inside(px, py, poly):
    """射线法判断点是否在多边形内。"""
    c = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > py) != (y2 > py) and px < x1 + (py - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


def hits(a, b, box):
    """线段 ab 是否穿过矩形 box=(x0, y0, x1, y1)（Liang-Barsky 裁剪）。"""
    x0, y0, x1, y1 = box
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def place(pts, i, w, h, gap=5, taken=()):
    """端点数值框放在多边形外侧：沿该端点外角平分线外移，直到不压多边形的边、也不压已放好的数值框（taken），
    返回框 (x0, y0, x1, y1)。不压的位置和原来一样，所以以前画得开的图一个像素都不变。"""
    P, A, B = pts[i], pts[i - 1], pts[(i + 1) % len(pts)]
    u = [(q[0] - P[0], q[1] - P[1]) for q in (A, B)]
    u = [(x / math.hypot(x, y), y / math.hypot(x, y)) for x, y in u]
    bx, by = u[0][0] + u[1][0], u[0][1] + u[1][1]
    if math.hypot(bx, by) < 1e-6:                                         # 两边共线：取任一法向，下面再判内外
        bx, by = -u[0][1], u[0][0]
    n = math.hypot(bx, by)
    bx, by = bx / n, by / n
    if inside(P[0] + 3 * bx, P[1] + 3 * by, pts):
        bx, by = -bx, -by
    d = gap + abs(bx) * w / 2 + abs(by) * h / 2
    for _ in range(30):
        x, y = P[0] + bx * d, P[1] + by * d
        box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        if (not any(hits(pts[j], pts[(j + 1) % len(pts)], box) for j in range(len(pts)))
                and not any(overlap(box, t) for t in taken)):
            return box
        d += 2
    raise ValueError(f'端点数值放不下：第{i}个方位')


def render(svg, W, H, out, exe):
    tmp = Path(tempfile.mkdtemp(prefix='pe_rose_'))
    try:
        (tmp / 'f.html').write_text('<html><head><meta charset="utf-8"><style>html,body{margin:0;padding:0;'
                                    f'background:#fff;overflow:hidden}}</style></head><body>{svg}</body></html>',
                                    encoding='utf-8')
        r = subprocess.run([exe, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
                            f'--user-data-dir={tmp / "p"}', '--force-device-scale-factor=2', f'--window-size={W},{H}',
                            f'--screenshot={tmp / "f.png"}', (tmp / 'f.html').as_uri()], capture_output=True, timeout=120)
        if not (tmp / 'f.png').is_file():
            raise RuntimeError('风频玫瑰图渲染失败：' + r.stderr.decode('utf-8', 'replace')[-800:])
        shutil.copyfile(tmp / 'f.png', out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def table(x0, y0, cw, rows, size=24, rh=38):
    """统计表，同马沟图：表头黑底白字，数据行隔行浅灰，灰色框线。"""
    s = f'<rect x="{x0}" y="{y0}" width="{sum(cw)}" height="{rh}" fill="#222"/>'
    for j in range(1, len(rows), 2):
        s += f'<rect x="{x0}" y="{y0 + j * rh}" width="{sum(cw)}" height="{rh}" fill="#f0f0f0"/>'
    for j, row in enumerate(rows):
        x = x0
        for c, w in zip(row, cw):
            s += T(x + w / 2, y0 + j * rh + rh / 2, c, size, color='#fff' if j == 0 else '#000')
            x += w
    for j in range(1, len(rows) + 1):
        s += f'<line x1="{x0}" y1="{y0 + j * rh}" x2="{x0 + sum(cw)}" y2="{y0 + j * rh}" stroke="#777" stroke-width="1.2"/>'
    x = x0
    for w in [0] + cw:
        x += w
        s += f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y0 + len(rows) * rh}" stroke="#777" stroke-width="1.2"/>'
    return s, y0 + len(rows) * rh


def rules(y, down):
    """上下双横线（马沟图）：外侧粗线、内侧细线。"""
    t = y + (6 if down else -6)
    return (f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#000" stroke-width="4"/>'
            f'<line x1="0" y1="{t}" x2="{W}" y2="{t}" stroke="#000" stroke-width="1.3"/>')


def panel(cx, title, v, calm, sub):
    """一幅风玫瑰。外圈刻度先取 4 的倍数加 2（同冷轧图），端点数值放不进外圈时每次加 2。sub 是数据来源行。"""
    lab = [i for i, d in enumerate(DIRS) if v[d] >= LABEL_MIN]
    far = lambda b: math.hypot(max(abs(b[0] - cx), abs(b[2] - cx)), max(abs(b[1] - CY), abs(b[3] - CY)))
    rr = lambda f: R0 + f * k
    rmax = math.ceil(max(v.values()) / 4) * 4 + 2
    while True:
        k = (R - R0) / rmax
        pts = [pt(cx, CY, rr(v[d]), i * 22.5) for i, d in enumerate(DIRS)]
        boxes = []                                   # 相邻两个方位的数值挨得近时，后放的那个再往外挪，不叠在一起
        for i in lab:
            t = f'{v[DIRS[i]]:.1f}%'
            boxes.append((place(pts, i, tw(t, FS['val']) + 4, FS['val'] + 3, taken=[b for b, _ in boxes]), t))
        if max(far(b) for b, _ in boxes) <= R - 4:
            break
        rmax += 2
    for (a, ta), (b, tb) in itertools.combinations(boxes, 2):
        if overlap(a, b):
            raise ValueError(f'端点数值 {ta} 与 {tb} 重叠')
    rings = []                                       # 刻度值：圈距小于 38 时隔圈标注；出外圈、压数值或端点的不标
    step = 4 if 4 * k >= 42 else 8
    for f in range(step, rmax, step):
        x, y = pt(cx, CY, rr(f), 67.5)
        t = f'{f}%'
        w, h = tw(t, FS['ring']) + 4, FS['ring'] + 3
        b = (x + 14 - w / 2, y - 4 - h / 2, x + 14 + w / 2, y - 4 + h / 2)
        if (far(b) > R - 6 or any(overlap(b, o) for o, _ in boxes)
                or any(b[0] - 4 < px < b[2] + 4 and b[1] - 4 < py < b[3] + 4 for px, py in pts)):
            continue
        rings.append((b, t))
    s = ''
    for f in range(4, rmax, 4):                                           # 刻度圈
        s += f'<circle cx="{cx}" cy="{CY}" r="{rr(f):.1f}" fill="none" stroke="#bdbdbd" stroke-width="1"/>'
    for i in range(16):                                                   # 方位线
        x1, y1 = pt(cx, CY, R0, i * 22.5)
        x2, y2 = pt(cx, CY, R, i * 22.5)
        s += f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#bdbdbd" stroke-width="1"/>'
    s += f'<circle cx="{cx}" cy="{CY}" r="{R}" fill="none" stroke="#000" stroke-width="2.2"/>'
    s += ('<polygon points="' + ' '.join(f'{x:.1f},{y:.1f}' for x, y in pts) +
          '" fill="#c8c8c8" fill-opacity="0.55" stroke="#000" stroke-width="2.6" stroke-linejoin="round"/>')
    for x, y in pts:
        s += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.6" fill="#000"/>'
    for b, t in rings:                                                    # 刻度值，沿 ENE 方位线（同冷轧图）
        s += T((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, t, FS['ring'], color='#555', halo=True)
    for b, t in boxes:                                                    # 端点数值
        s += T((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, t, FS['val'], halo=True)
    for i, d in enumerate(DIRS):                                          # 方位名
        t = CN.get(d, d)
        x, y = outside(cx, CY, R, i * 22.5, tw(t, FS['dir']), FS['dir'] + 4, 8)
        s += T(x, y, t, FS['dir'])
    s += (f'<circle cx="{cx}" cy="{CY}" r="{R0}" fill="#fff" stroke="#333" stroke-width="1.4"/>'
          + T(cx, CY - 12, '静风', FS['calm']) + T(cx, CY + 13, f'{calm:.2f}%', FS['calm'] - 2))
    s += T(cx, 44, title, FS['title'])
    s += T(cx, 84, sub, FS['sub'], color='#444')
    return s, rmax


def draw(tb, out_png):
    """按风频表 tb（find_table 的结果）画 out_png，旁边写同名 .svg 和 .json（指纹，出稿时据此判断要不要重画）。"""
    fr, calm = read_table(tb['行'])
    exe = browser()
    if not exe:
        raise RuntimeError('这台电脑上没找到 Chrome 或 Edge，画不了风频玫瑰图')
    out_png = Path(out_png)
    sub, name = source_line(tb), place_name(tb['表题'])
    if tw(sub, FS['sub']) > W / 2 - 40:
        sub = '数据来源：见%s' % tb['表号']
    s, rm = '', {}
    for cx, k in ((375, '全年'), (1125, '夏季')):
        title = f'{name}风频玫瑰图（{k}）'
        if tw(title, FS['title']) > W / 2 - 40:
            title = f'风频玫瑰图（{k}）'
        p, rm[k] = panel(cx, title, fr[k], calm[k], sub)
        s += p
    rows = [('统计项目', '风向（来向）', '频率')]                              # 统计表，项目名同冷轧图
    for k in ('全年', '夏季'):
        v = fr[k]
        for word, f in (('最多', max), ('最少', min)):
            x = f(v.values())
            rows.append((f'{k}{word}风向', '、'.join(f'{d}（{CN16[d]}）' for d in DIRS if v[d] == x), f'{x:.2f}%'))
    cw = [270, 250, 210]
    tbl, yb = table((W - sum(cw)) / 2, 766, cw, rows, FS['table'])
    s += tbl
    note = f'注：风向指风的来向；中心圆内为静风频率，各方位频率自中心圆周起算；统计数据见{tb["表号"]}'
    if tw(note, FS['note']) >= W - 40:
        raise ValueError('风频玫瑰图图下注一行放不下')
    s += T(W / 2, yb + 34, note, FS['note'], color='#444')
    if yb + 34 + 30 >= H - 14:
        raise ValueError('风频玫瑰图图高不够')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
           f'<rect width="{W}" height="{H}" fill="#fff"/>{rules(4, True)}{rules(H - 4, False)}{s}</svg>')
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.with_suffix('.svg').write_text(svg, encoding='utf-8')
    render(svg, W, H, out_png, exe)
    out_png.with_suffix('.json').write_text(json.dumps(
        {'表号': tb['表号'], '指纹': fingerprint(tb), '画法版本': VERSION, '统计': [list(r) for r in rows[1:]],
         '静风': calm}, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'外圈刻度': rm, '统计': rows[1:], '静风': calm}


def main(argv=None):
    a = (argv if argv is not None else sys.argv[1:])
    if len(a) < 2:
        print(__doc__.strip().split('\n')[-1])
        return 2
    md, fig = Path(a[0]), a[1]
    tb = find_table(md.read_text(encoding='utf-8'))
    if not tb:
        print('%s 里没有风频表（表头：风向｜全年（%%）｜夏季（%%）｜风向｜全年（%%）｜夏季（%%））' % md.name)
        return 1
    out = Path(a[2]) if len(a) > 2 else md.resolve().parent.parent / '图'
    res = draw(tb, out / ('%s.png' % fig))
    print('已画', fig, '（按%s）' % tb['表号'], res['统计'], '静风', res['静风'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
