"""配图：写稿只写字，这一步把第2章该有的图放进正文。只有工艺流程图要调一次模型（排框，1 美元以内），别的不花钱。
- 工艺流程图：第2.5节没有的，请模型照第2.5节排框（scripts/pe/flowchart.py 的说明格式），程序查过、画好，
  插在第2.5节末的横向页；查出问题退回去改一次，还不行的那张不画，报待办。
- 风频玫瑰图：正文里有风频表（风向｜全年｜夏季 两栏并排）就照 K019 画（scripts/pe/windrose.py），插在表后；
  表改了，出稿前重画（publish 调 refresh_wind）。没有风频表的不画，检查里报待办，资料欠缺清单里列上。
- 区域位置图、总平面布置图（风电场另加机位布置图）：从资料里的可研、初设 PDF 按图题找到那一页，截下图来，
  区域位置图放 2.1.1 节末，平面布置图放总体布局一节末的横向页（K020）。找不到的报待办。
- 插图时按所在节编号（图2.1-1、图2.1-2……）；插在已有图前面的，后面的图号和正文里的“见图××”跟着顺延。
截了哪页、有没有审图号记在 图/配图记录.json（资料欠缺清单用）。"""
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from .. import llm, tools
from ..tools import doctype as DT
from . import common as C
from pe import flowchart as FC  # noqa: E402
from pe import windrose as WR  # noqa: E402  （tools 已把 scripts/ 放进 sys.path）
from pe import windstats as WS  # noqa: E402
from pe.model import table_grid  # noqa: E402

RECORD = '配图记录.json'
TEMP = 901                            # 新插的图先编成 X-901、X-902，插好后整节按正文先后重编
RE_H = re.compile(r'^(#{1,3})\s+(\d+(?:\.\d+)*)\s')
RE_IMG = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)')
RE_FIG = re.compile(r'^图\s*([0-9A-Z]+(?:\.\d+)*-\d+)')
EXT2 = ('可研', '初设', '附件', '原图', '原表')   # “可研图2.2-1”“初设表3.1-2”是资料里的图表，改号时不跟着改
# 截哪些图：(类别, 图题正则（资料 PDF 里的）, 正文里已有的算数的图题正则, 放哪)
CAPTURE = [
    ('区域位置图', r'区域位置|地理位置|工程位置|交通位置', r'区域位置|地理位置|位置图', '位置'),
    ('总平面布置图', r'总平面|厂区平面|升压站.{0,8}平面布置|平面布置图', r'总平面|平面布置', '布局'),
    ('机位布置图', r'机位布置|风机布置|风电机组布置', r'机位布置|风机布置|风电机组布置', '布局'),
]
RE_CAP = re.compile(r'^(?:附)?图\s*(\d+(?:\.\d+)*-\d+)\s*(.{2,40}?)\s*$')
PORTRAIT = (14.2, 19.0)               # 纵向页插图最宽、最高（cm），给图题和注留出位置
LANDSCAPE = (23.0, 13.0)              # 横向页（K020：尺寸取图、图题、注能在一页放下的最大值）
FLOW_ROWS = 3                         # 工艺流程图一张最多几行框（200MW 的 8 张都是 3 行）
FLOW_TALL = LANDSCAPE[1] / LANDSCAPE[0]   # 流程图高宽比超过这个，横向页只能缩小放，字就小于 6.5 磅


def _body(root):
    return [(f, Path(root) / '正文' / f) for f in tools.body_files(root)]


def _prefix(lines, upto, fname):
    """lines[:upto] 里最后一个二级标题的编号（图号以它开头），没有就用一级标题、再没有用文件名的章号。"""
    h1 = h2 = ''
    for ln in lines[:upto]:
        m = RE_H.match(ln)
        if m and len(m.group(1)) == 1:
            h1, h2 = m.group(2), ''
        elif m and len(m.group(1)) == 2:
            h2 = m.group(2)
    return h2 or h1 or str(int(fname[:2]))


def figures(root):
    """正文里的图：[(文件名, 图片行号, 图号, 图题文字)]，按正文先后。"""
    out = []
    for f, p in _body(root):
        lines = tools.read(p).split('\n')
        for i, ln in enumerate(lines):
            m = RE_IMG.match(ln.strip())
            if not m:
                continue
            cap = lines[i + 1].strip() if i + 1 < len(lines) else ''
            k = RE_FIG.match(cap) or RE_FIG.match(m.group(1))
            if k:
                out.append((f, i, k.group(1), cap if RE_FIG.match(cap) else m.group(1)))
    return out


def tables(root):
    """正文里的表：[(文件名, 表题行号, 表号)]，按正文先后（表题下面隔着指令、空行就是表格）。续表不算。"""
    out = []
    for f, p in _body(root):
        lines = tools.read(p).split('\n')
        for i, ln in enumerate(lines):
            m = re.match(r'^表\s*([0-9A-Z]+(?:\.\d+)*-\d+)\s', ln.strip())
            if not m:
                continue
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].strip().startswith('<!--')):
                j += 1
            if j < len(lines) and lines[j].strip().startswith('|'):
                out.append((f, i, m.group(1)))
    return out


def _renumber(root, prefix, kind='图'):
    """prefix 这一节的图（kind='表' 时是表）按正文先后重编 1、2、3……：图题表题、图片行的说明、
    正文里的“见图××”“{{计数:表××}}”一起改；图片文件名不动（文件按图名起名，不带图号）。返回 {旧号: 新号}。"""
    mp = {}
    for k in ([x[2] for x in figures(root)] if kind == '图' else [x[2] for x in tables(root)]):
        if k.rsplit('-', 1)[0] == prefix and k not in mp:
            mp[k] = '%s-%d' % (prefix, len(mp) + 1)
    if all(k == v for k, v in mp.items()):
        return mp
    pat = re.compile(r'(%s\s*)(%s-\d+)(?!\d)' % (kind, re.escape(prefix)))

    def sub(s):
        def one(m):
            if m.group(2) not in mp or s[max(0, m.start() - 2):m.start()] in EXT2:
                return m.group(0)
            return m.group(1) + mp[m.group(2)]
        return pat.sub(one, s)

    for f, p in _body(root):
        text = tools.read(p)
        lines = text.split('\n')
        for i, ln in enumerate(lines):
            m = RE_IMG.match(ln.strip())
            lines[i] = ln.replace('![%s]' % m.group(1), '![%s]' % sub(m.group(1)), 1) if m else sub(ln)
        if '\n'.join(lines) != text:
            tools.write(p, '\n'.join(lines))
    return mp


def _record(root):
    p = Path(root) / '图' / RECORD
    try:
        return json.loads(tools.read(p)) if p.exists() else {}
    except ValueError:
        return {}


def _save_record(root, rec):
    if rec or (Path(root) / '图' / RECORD).exists():
        tools.write(Path(root) / '图' / RECORD, json.dumps(rec, ensure_ascii=False, indent=1))


def _insert(p, line, block):
    """在 p 的第 line 行前插入 block（几行字），前后各空一行。"""
    lines = tools.read(p).split('\n')
    head, tail = lines[:line], lines[line:]
    while block and not block[-1].strip():
        block = block[:-1]
    while head and not head[-1].strip():
        head.pop()
    while tail and not tail[0].strip():
        tail.pop(0)
    tools.write(p, '\n'.join(head + [''] + block + [''] + tail))


def _free(path):
    """图/ 里同名文件已在（手放的、上次截的）就加 _2、_3，不压掉。"""
    path, n = Path(path), 1
    out = path
    while out.exists():
        n += 1
        out = path.with_name('%s_%d%s' % (path.stem, n, path.suffix))
    return out


def _find_rose(root):
    """(风频表所在文件, 表, 已有风玫瑰图的 (图号, 图片路径)/None)。没有风频表返回 None。"""
    for f, p in _body(root):
        lines = tools.read(p).split('\n')
        tb = WR.find_table('\n'.join(lines))
        if not tb:
            continue
        for g, i, k, cap in figures(root):
            if '玫瑰' in cap:
                img = RE_IMG.match(tools.read(Path(root) / '正文' / g).split('\n')[i].strip())
                return f, tb, (k, Path(root) / img.group(2))
        return f, tb, None
    return None


def _retitle(root, key, title):
    """流水线画的图重画后，图片行的说明和下一行图题改成 title；本来一样就不动。"""
    for g, i, k, _ in figures(root):
        if k != key:
            continue
        p = Path(root) / '正文' / g
        lines = tools.read(p).split('\n')
        ln = lines[i].strip()
        img = RE_IMG.match(ln)
        cap = '图%s  %s' % (key, title)
        new = ['![%s](%s)%s' % (cap, img.group(2), ln[img.end():])]
        if i + 1 < len(lines) and RE_FIG.match(lines[i + 1].strip()):
            new.append(cap)
        if lines[i:i + len(new)] != new:
            lines[i:i + len(new)] = new
            tools.write(p, '\n'.join(lines))
        return


def wind(root):
    """有风频表、没有风玫瑰图：画好插在表后；流水线画的图表改了就重画。返回做了什么（一句话）或 None。"""
    got = _find_rose(root)
    if not got:
        return None
    f, tb, have = got
    if have:
        key, png = have
        if WR.stale(png, tb):
            WR.draw(tb, png)
            _retitle(root, key, WR.fig_title(tb))      # 代用站换成当地气象站（表题变了），图题跟着换
            return '风频玫瑰图（图%s）按%s重画' % (key, tb['表号'])
        return None
    p = Path(root) / '正文' / f
    line = tb['end']
    prefix = _prefix(tools.read(p).split('\n'), line, f)
    with tempfile.TemporaryDirectory(prefix='pe_fig_') as d:
        WR.draw(tb, Path(d) / 'rose.png')                 # 先画：画不成就什么都不改
        png = _free(Path(root) / '图' / '风频玫瑰图.png')
        png.parent.mkdir(parents=True, exist_ok=True)
        for s in ('.png', '.svg', '.json'):
            shutil.copyfile(Path(d) / ('rose' + s), png.with_suffix(s))
    title, tmp = WR.fig_title(tb), '%s-%d' % (prefix, TEMP)
    _insert(p, line, ['![图%s  %s](图/%s){宽=%s}' % (tmp, title, png.name, WR.WIDTH_CM), '图%s  %s' % (tmp, title),
                      '', '<!-- 段落 字号=五号 -->', '注：按%s绘制，左为全年，右为夏季；风向为风的来向。' % tb['表号']])
    key = _renumber(root, prefix).get(tmp, tmp)
    return '画了风频玫瑰图（图%s，按%s）' % (key, tb['表号'])


# 风频表写在哪一节：预评价是自然环境概况；设计专篇附件册是 2.1.2 自然环境概况；设计专篇主报告不写（风频玫瑰图在附件册）
WIND_HEAD = {DT.PRE: r'^#{2,3}\s+\d+(?:\.\d+)*\s+自然环境', DT.ANNEX: r'^#{2,3}\s+\d+(?:\.\d+)*\s+自然环境', DT.DESIGN: None}


def _wind_para(root):
    """(文件名, 行号)：自然环境概况一节里写风向的那段；没有返回 None。"""
    head = WIND_HEAD.get(tools.report_type(root), WIND_HEAD[DT.PRE])
    if not head:
        return None
    for f, p in _body(root):
        lines = tools.read(p).split('\n')
        for i, ln in enumerate(lines):
            if not re.match(head, ln):
                continue
            lvl = len(ln) - len(ln.lstrip('#'))
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s.startswith('#') and len(s) - len(s.lstrip('#')) <= lvl:
                    break
                if '风向' in s and not s.startswith(('|', '!', '<!--', '#', '表', '图', '注')):
                    return f, j
    return None


def wind_table(root):
    """正文没有风频表的：按项目坐标找最近的国家气象站（scripts/pe/windstats.py），把近三年全年、夏季风向频率表
    写在“风向”那段后面，画上风频玫瑰图，那段末尾补一句结论。返回 (做了什么, 没做成的原因)，都可能是 None。"""
    if _find_rose(root):
        return None, None
    at = _wind_para(root)
    if not at:
        return None, '风频表（正文里没找到写风向的那段）'
    texts = [tools.read(p) for _, p in _body(root)] + [t for _, t in tools.source_texts(root)]
    xy = next((c for c in (WS.site_coords(t) for t in texts) if c), None)
    if not xy:
        return None, '风频表（资料里没找到场址坐标，找不了最近的气象站）'
    near = WS.nearest(*xy)
    rec = _record(root)
    if not near:
        rec['风频表'] = {'代用站': False, '原因': '%d 公里内没有国家气象站的逐时数据' % WS.RADIUS}
        _save_record(root, rec)
        return None, '风频表（%d 公里内没有国家气象站的逐时数据）' % WS.RADIUS
    sid, st, d, years = near
    if st.get('名称待核') or not st.get('名称'):             # 站名空着或重名：不把拿不准的站名写进报告
        rec['风频表'] = {'代用站': False, '原因': '最近的国家站（区站号%s，约 %.0f 公里）站名待核' % (sid, d)}
        _save_record(root, rec)
        return None, '风频表（最近的国家站区站号%s 站名待核，没写）' % sid
    name = st['名称']
    fr = WS.freq(st, years)
    f, line = at
    p = Path(root) / '正文' / f
    prefix = _prefix(tools.read(p).split('\n'), line, f)
    tmp = '表%s-%d' % (prefix, TEMP)
    note = '注：据国家气象科学数据中心%s近三年逐时观测数据统计；静风为风速不大于0.2 m/s。' % name
    _insert(p, line + 1, WS.table_md(tmp, '%s近三年全年和夏季风向频率统计' % name, fr, note))
    tkey = _renumber(root, prefix, '表').get(tmp[1:], tmp[1:])
    drew, why = None, None
    try:
        drew = wind(root)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        why = '风频玫瑰图（%s）' % e
    fkey = next((k for _, _, k, cap in figures(root) if '玫瑰' in cap), None) if drew else None
    (ya, pa), (ys, ps) = WS.top(fr, '全年'), WS.top(fr, '夏季')
    lead = '据距项目最近的国家气象观测站%s近三年逐时观测数据（见表%s%s），全年最多风向为%s（%.2f%%），夏季最多风向为%s（%.2f%%）。' % (
        name, tkey, '、图%s' % fkey if fkey else '', ya, pa, ys, ps)
    lines = tools.read(p).split('\n')
    lines[line] = lines[line].rstrip() + lead
    tools.write(p, '\n'.join(lines))
    rec = _record(root)
    rec['风频表'] = {'代用站': True, '站': name, '区站号': sid, '距离': round(d), '年份': years, '表号': '表' + tkey,
                   '时间': time.strftime('%Y-%m-%d %H:%M')}
    _save_record(root, rec)
    return ('用%s（离项目约 %.0f 公里）%s～%s年的逐时数据写了风频表（表%s）%s' % (
        name, d, years[0], years[-1], tkey, '、画了风频玫瑰图（图%s）' % fkey if fkey else '')), why


def refresh_wind(job, root):
    """出稿前：风频表有了、图还没有就画上插进去；流水线画的图表改了就重画。画不成只记一句，不拦出稿。"""
    try:
        done = wind(root)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        job.info('风频玫瑰图没画成：%s' % e)
        return None
    if done:
        job.info(done)
    return done


def sources(root):
    """资料里的 PDF：资料/文本/索引.json 记的来源，没有索引时取 资料/ 下的 PDF。初设排在可研前面（图更细）。"""
    main = Path(DT.main_root(root))                   # 设计专篇附件册：资料是主报告的
    idx = main / '资料' / '文本' / '索引.json'
    try:
        srcs = {b['来源'] for b in json.loads(tools.read(idx))['块']} if idx.exists() else set()
    except (ValueError, KeyError, TypeError):
        srcs = set()
    srcs = [Path(s) for s in srcs] or list((main / '资料').glob('*.pdf'))
    pdfs = [s for s in srcs if s.suffix.lower() == '.pdf' and s.is_file()]
    return sorted(pdfs, key=lambda s: (0 if '初' in s.name else 1 if '可研' in s.name or '可行性' in s.name else 2, s.name))


def _doc(src):
    return '初设' if '初' in src.name else '可研' if '可研' in src.name or '可行性' in src.name else src.stem[:12]


def find_captions(doc, pat):
    """PDF 里图题行合 pat 的：[(页序号, 图号, 图题, 图题所在的框)]。目录行（带一串点和页码）不算。"""
    out = []
    for i, pg in enumerate(doc):
        for b in pg.get_text('blocks'):
            lines = [x.strip() for x in b[4].split('\n') if x.strip()]
            for j, ln in enumerate(lines):
                if re.match(r'^(?:附)?图\s*\d+(?:\.\d+)*-\d+\s*$', ln) and j + 1 < len(lines):
                    ln = ln + ' ' + lines[j + 1]
                m = RE_CAP.match(ln)
                if m and re.search(pat, m.group(2)) and not re.search(r'[.．…·]{3,}|\s\d+$', ln):
                    out.append((i, m.group(1), m.group(2).strip(), b[:4]))
    return out


def _clip(pg, cap):
    """图题上方的图（位图或矢量图）的范围；找不到就取整页去掉页眉页脚。"""
    import pymupdf
    page = pg.rect
    if pg.rotation:
        return page
    top = cap[1]
    boxes = [r for x in pg.get_images(full=True) for r in pg.get_image_rects(x[0])
             if r.y1 <= top + 4 and r.width > page.width * 0.25 and r.height > 40]
    if not boxes:
        boxes = [d['rect'] for d in pg.get_drawings() if d['rect'].y1 <= top + 4 and d['rect'].y0 > page.height * 0.06
                 and not (d['rect'].height < 2 and d['rect'].width > page.width * 0.6)]
    if not boxes:
        return pymupdf.Rect(page.x0 + 20, page.y0 + page.height * 0.06, page.x1 - 20, top)
    r = pymupdf.Rect(boxes[0])
    for b in boxes[1:]:
        r |= b
    r = r & pymupdf.Rect(page.x0 + 5, page.y0 + 5, page.x1 - 5, top)
    return r if r.width > 50 and r.height > 50 else pymupdf.Rect(page.x0 + 20, page.y0 + page.height * 0.06, page.x1 - 20, top)


def grab(src, page_no, cap, out_png):
    """把 src 第 page_no 页（从 0 起）图题上方的图截成 PNG（RGB，WPS 不认 RGBA，见版式规范）；返回 (宽, 高) 像素。"""
    import pymupdf
    with pymupdf.open(src) as doc:
        pg = doc[page_no]
        r = _clip(pg, cap)
        zoom = max(2.0, min(6.0, 2400 / max(r.width, 1)))
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=r, alpha=False)
        pix.save(str(out_png))
        return pix.width, pix.height, bool(re.search(r'审图号|GS\s*[（(]\d{4}', pg.get_text()))


# 放哪：(二级、三级标题正则, 是否横向页)。区域位置图放 2.1.1 节末，平面布置图放总体布局一节末（K020 横向页）
PLACE = {'位置': (r'^###\s+\d+(?:\.\d+)*\s+(项目基本情况|建设地点|地理位置|项目概况)', False),
         '布局': (r'^##\s+\d+(?:\.\d+)*\s+(总体布局|总平面布置)', True),
         '工艺': (r'^##\s+\d+(?:\.\d+)*\s+(生产工艺)', True)}
# 设计专篇：主报告只放总平面布置图（2.2.1 总平面布置及竖向布置，三级标题）；
# 附件册第2章放区域位置图（2.1.1 区域位置）、工艺流程图（2.3 工艺流程及职业病危害因素分布），风频玫瑰图跟着 2.1 的风频表
PLACES = {DT.PRE: PLACE,
          DT.DESIGN: {'布局': (r'^###\s+\d+(?:\.\d+)*\s+(总平面布置|总体布局)', True)},
          DT.ANNEX: {'位置': (r'^###\s+\d+(?:\.\d+)*\s+(区域位置|项目位置|地理位置)', False),
                     '工艺': (r'^##\s+\d+(?:\.\d+)*\s+(工艺流程|生产工艺)', True)}}
# 流程图要照的节和因素表（预评价：第2.5节、表4.1-1；设计专篇附件册：附件册第2.3节、主报告表3.1-1）
FLOW_SRC = {DT.PRE: ('第2.5节', '4.1-1', '表4.1-1'), DT.ANNEX: ('附件册第2.3节', '3.1-1', '主报告表3.1-1')}


def places(root):
    return PLACES.get(tools.report_type(root), PLACE)


def _span(root, group):
    """(文件名, 那一节标题在第几行, 那一节到第几行为止)：到下一个同级或更高一级标题之前。找不到返回 None。"""
    if group not in places(root):
        return None
    pat, _ = places(root)[group]
    for f, p in _body(root):
        lines = tools.read(p).split('\n')
        for i, ln in enumerate(lines):
            if re.match(pat, ln):
                lvl = len(ln) - len(ln.lstrip('#'))
                j = i + 1
                while j < len(lines) and not (lines[j].startswith('#') and len(lines[j]) - len(lines[j].lstrip('#')) <= lvl):
                    j += 1
                return f, i, j
    return None


def _place_line(root, group):
    """(文件名, 插在第几行前)：那一节最后。找不到那一节返回 None。"""
    s = _span(root, group)
    return (s[0], s[2]) if s else None


def _clean(kind, title):
    """(图题, 括号里的说明)：区域位置图一律叫“项目区域位置图”；括号里的说明挪到图下注。"""
    m = re.match(r'^(.*?)\s*[（(](.+)[）)]\s*$', title)
    base, extra = (m.group(1), m.group(2)) if m else (title, '')
    return ('项目区域位置图' if kind == '区域位置图' else re.sub(r'[\s\\/:*?"<>|]+', '', base)), extra


def _width(px, landscape):
    w, h = LANDSCAPE if landscape else PORTRAIT
    return '%.2fcm' % min(w, h * px[0] / max(px[1], 1))


def capture(root):
    """区域位置图、平面布置图：正文没有的，从资料 PDF 里截。返回 (截了的 [图号 图题], 没找到的 [类别])。"""
    import pymupdf
    have = ' '.join(cap for _, _, _, cap in figures(root))
    todo = [c for c in CAPTURE if c[3] in places(root) and not re.search(c[2], have)]
    if not todo:
        return [], []
    srcs, found, miss = sources(root), {}, []
    for kind, pat, _, group in todo:
        hit = None
        for alt in pat.split('|'):                          # 前面的说法优先（“区域位置”先于“工程位置”）
            for s in srcs:
                with pymupdf.open(s) as doc:
                    caps = find_captions(doc, alt)
                if caps:
                    hit = (s,) + caps[0]
                    break
            if hit:
                break
        if hit:
            found.setdefault(group, []).append((kind, hit))
        elif kind != '机位布置图':                       # 机位布置图只有风电场有，找不到不算缺
            miss.append(kind)
    done, rec = [], _record(root)
    for group, items in found.items():
        where = _place_line(root, group)
        if not where:
            miss += [k for k, _ in items]
            continue
        f, line = where
        p = Path(root) / '正文' / f
        prefix = _prefix(tools.read(p).split('\n'), line, f)
        land = places(root)[group][1]
        block, made = (['<!-- 横向 -->', ''] if land else []), []
        with tempfile.TemporaryDirectory(prefix='pe_fig_') as d:
            shots = [grab(s, pg, box, Path(d) / ('%d.png' % j)) for j, (_, (s, pg, _, _, box)) in enumerate(items)]
            for j, ((kind, (s, pg, num, title, _)), (pw, ph, gs)) in enumerate(zip(items, shots)):   # 截成了再改正文
                name, extra = _clean(kind, title)
                png = _free(Path(root) / '图' / ('%s.png' % name))
                png.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(d) / ('%d.png' % j), png)
                tmp = '%s-%d' % (prefix, TEMP + j)
                if j:
                    block += ['<!-- 分页 -->', '']
                block += ['![图%s  %s](图/%s){宽=%s}' % (tmp, name, png.name, _width((pw, ph), land)), '图%s  %s' % (tmp, name),
                          '', '<!-- 段落 字号=五号 -->', '注：引自%s图%s%s。' % (_doc(s), num, '（%s）' % extra if extra else ''), '']
                rec[png.name] = {'类别': kind, '资料': s.name, '页': pg + 1, '原图题': '图%s %s' % (num, title),
                                 '审图号': gs, '时间': time.strftime('%Y-%m-%d %H:%M')}
                made.append((tmp, name, s, pg))
        if land:
            block += ['<!-- 纵向 -->']
        _insert(p, line, block)
        mp = _renumber(root, prefix)
        done += ['图%s %s（%s第%d页）' % (mp.get(tmp, tmp), name, _doc(s), pg + 1) for tmp, name, s, pg in made]
    _save_record(root, rec)
    return done, miss


def _obj(props, req=None):
    return {'type': 'object', 'additionalProperties': False, 'required': req or list(props), 'properties': props}


S, I, B = {'type': 'string'}, {'type': 'integer'}, {'type': 'boolean'}
FLOW = _obj({'图': {'type': 'array', 'items': _obj({
    '图题': S,
    '框': {'type': 'array', 'items': _obj({'号': S, '行': I, '列': I, '名称': S, '说明': S, '因素': {'type': 'array', 'items': I},
                                            '虚线': B})},
    '连线': {'type': 'array', 'items': _obj({'从': S, '到': S, '物料': S, '虚线': B})},
    '因素': {'type': 'array', 'items': S},
    '注': {'type': 'array', 'items': S}})}})
FLOW_RULE = '''任务：照上面第2.5节，排“工艺流程及职业病危害因素分布图”的框（程序照你给的位置画图）。
- 框里的事实只取第2.5节写到的，不新增（K018）：名称写设施或工序；说明写一项主要参数或防护设施，没有写就留空；因素写该处的危害因素编号。
- 依托的、已有的设施框画虚线（虚线=true）；检修、吹扫置换等不是正常物料走向的连线画虚线。
- 按评价单元或系统分图，一张图 4 列、最多 3 行（再多印出来字太小，K020），一格只放一个框，行列都从 1 起；
  框多的分成几张图。物料走向尽量从左到右、从上到下，一行放满 4 个再往下排。
- 字要短：名称不超过 12 个字，说明不超过 16 个字，左右相邻两框之间的物料字不超过 4 个字（上下相邻的可到 8 个字）。
- 因素：每张图单独从 1 编号，只列这张图里用到的；名称照“表4.1-1 职业病危害因素”里的写法，一个字不改。
- 图题写“××工艺流程及职业病危害因素分布图”；注写一两行，以“注：”开头，说明框内第二行和虚线是什么意思。
- 下面是格式的例子（内容是虚构的，只学格式）。'''
FLOW_EXAMPLE = {'图': [{'图题': '燃气锅炉工艺流程及职业病危害因素分布图', '框': [
    {'号': 'A1', '行': 1, '列': 1, '名称': '煤气母管', '说明': '电动蝶阀、快切阀', '因素': [1, 2], '虚线': True},
    {'号': 'A2', '行': 1, '列': 2, '名称': '煤气加热器', '说明': '50→150 ℃', '因素': [2, 3], '虚线': False},
    {'号': 'A3', '行': 1, '列': 3, '名称': '燃烧器阀组', '说明': '两道快切阀', '因素': [1, 2], '虚线': False},
    {'号': 'A4', '行': 1, '列': 4, '名称': '锅炉炉膛', '说明': '', '因素': [1, 3], '虚线': False},
    {'号': 'B2', '行': 2, '列': 2, '名称': '放散及氮气吹扫', '说明': '放散管、取样管', '因素': [2], '虚线': False},
    {'号': 'B4', '行': 2, '列': 4, '名称': '引风机及烟囱', '说明': '烟囱高100 m', '因素': [1], '虚线': False}],
    '连线': [{'从': 'A1', '到': 'A2', '物料': '煤气', '虚线': False}, {'从': 'A2', '到': 'A3', '物料': '热煤气', '虚线': False},
           {'从': 'A3', '到': 'A4', '物料': '燃烧', '虚线': False}, {'从': 'A2', '到': 'B2', '物料': '检修置换', '虚线': True},
           {'从': 'A4', '到': 'B4', '物料': '烟气', '虚线': False}],
    '因素': ['噪声', '一氧化碳', '高温'],
    '注': ['注：框内第二行为主要设施参数或防护设施；虚线框为依托设施，虚线箭头为检修、吹扫置换时的作业关系。']}]}


def factor_names(root):
    """表4.1-1“职业病危害因素”列里的因素名（流程图图例要和它一字不差）。没有这张表返回 []。
    设计专篇附件册用主报告的表3.1-1。"""
    t = tools.report_type(root)
    key = FLOW_SRC.get(t, FLOW_SRC[DT.PRE])[1]
    try:
        tb = tools.Project(DT.main_root(root)).tables.get(key)
    except (FileNotFoundError, ValueError):
        return []
    grid = table_grid(tb)[0] if tb else []
    c = next((i for i, h in enumerate(grid[0]) if '因素' in h), None) if grid else None
    out = []
    for row in grid[1:] if c is not None else []:
        for x in re.split(r'[、，,；;（）()\s]+', row[c]):
            if x and not x.startswith(('无', '如')) and x not in out:
                out.append(x)
    return out


def _spec(fig, n):
    """模型给的一张图 → flowchart 的说明（去掉图题，空说明不写）。"""
    boxes = [{k: v for k, v in b.items() if not (k == '说明' and not v)} for b in fig['框']]
    return {'图号': '图%d' % n, '框': boxes, '连线': fig['连线'], '因素': fig['因素'], '注': fig['注']}


def flows(job, root):
    """第2.5节没有工艺流程图的：请模型排框，查过、画好，插在第2.5节末的横向页。返回 (画了的图题, 没画成的说明)。"""
    if any(re.search(r'工艺流程|生产流程|流程及职业病危害', cap) for _, _, _, cap in figures(root)):
        return [], []
    t = tools.report_type(root)
    if t not in FLOW_SRC:
        return [], []                                    # 设计专篇主报告不放流程图（在附件册）
    sec, _key, ftab = FLOW_SRC[t]
    where = _span(root, '工艺')
    if not where:
        return [], ['工艺流程图（正文里没找到%s“%s”）' % (sec, '生产工艺' if t == DT.PRE else '工艺流程及职业病危害因素分布')]
    if not WR.browser():
        return [], ['工艺流程图（这台电脑上没找到 Chrome 或 Edge，画不了）']
    f, h, end = where
    p = Path(root) / '正文' / f
    text = '\n'.join(tools.read(p).split('\n')[h:end])
    names = factor_names(root)
    rule = FLOW_RULE if t == DT.PRE else FLOW_RULE.replace('第2.5节', sec).replace('表4.1-1', ftab)
    content = (C.doc('第2.5节 生产工艺' if t == DT.PRE else sec + ' 工艺流程', text) + '\n\n'
               + C.doc('%s 职业病危害因素' % ftab, '、'.join(names) or '（没有%s）' % ftab)
               + '\n\n' + rule + '\n' + json.dumps(FLOW_EXAMPLE, ensure_ascii=False))
    ask, good, probs = content, [], []
    with tempfile.TemporaryDirectory(prefix='pe_flow_') as d:
        for attempt in range(2):
            out = llm.call_retry(job, '排工艺流程图' if attempt == 0 else '排工艺流程图（改）', C.system(t), ask,
                                 effort='medium', schema=FLOW, phase='配图')
            good, probs = [], []
            for n, fig in enumerate(out.get('图', []), 1):
                spec = _spec(fig, n)
                bad = FC.validate(spec)
                if names:
                    bad += ['因素“%s”和%s的写法不一样' % (x, ftab) for x in fig['因素'] if x not in names]
                if not fig['图题'].endswith('工艺流程及职业病危害因素分布图'):
                    bad.append('图题要以“工艺流程及职业病危害因素分布图”结尾')
                if any(b['行'] > FLOW_ROWS for b in fig['框']):
                    bad.append('超过 %d 行，印出来字太小，要分成两张图' % FLOW_ROWS)
                if not bad:
                    try:
                        FC.draw(spec, Path(d) / ('%d.png' % n))
                        with Image.open(Path(d) / ('%d.png' % n)) as im:
                            if im.size[1] / im.size[0] > FLOW_TALL:          # 太高：缩到横向页能放下时字小于 6.5 磅
                                bad.append('图太高，横向页放下后字太小，要分成两张图或少一行')
                    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
                        bad.append(str(e))
                (probs.append('第%d张“%s”：%s' % (n, fig['图题'], '；'.join(bad))) if bad else good.append((fig, n)))
            if not out.get('图'):
                probs.append('一张图也没排出来')
            if not probs or attempt:
                break
            ask = (content + '\n\n上次排的：\n' + json.dumps(out, ensure_ascii=False) + '\n\n程序查出这些问题，只改有问题的地方，'
                   '别的不动，几张图全部重新给一遍：\n' + '\n'.join('- ' + x for x in probs))
        if not good:
            return [], ['工艺流程图（模型排的图没过检查：%s）' % '；'.join(probs)[:200]]
        prefix = _prefix(tools.read(p).split('\n'), end, f)
        block = ['<!-- 横向 -->', '']
        for j, (fig, n) in enumerate(good):
            name, _ = _clean('', fig['图题'])
            png = _free(Path(root) / '图' / ('%s.png' % name))
            png.parent.mkdir(parents=True, exist_ok=True)
            for s in ('.png', '.svg', '.json'):
                if (Path(d) / ('%d%s' % (n, s))).is_file():
                    shutil.copyfile(Path(d) / ('%d%s' % (n, s)), png.with_suffix(s))
            with Image.open(png) as im:
                px = im.size
            if j:
                block += ['<!-- 分页 -->', '']
            tmp = '%s-%d' % (prefix, TEMP + j)
            block += ['![图%s  %s](图/%s){宽=%s}' % (tmp, name, png.name, _width(px, True)), '图%s  %s' % (tmp, name), '']
    block += ['<!-- 纵向 -->']
    _insert(p, end, block)
    _renumber(root, prefix)
    return [name for name in (_clean('', fig['图题'])[0] for fig, _ in good)], ['工艺流程图（%s）' % x for x in probs]


# 设计专篇的图承接预评价：正文写了图片行、图片文件还没有的，按图题类别从预评价 图/ 拷（11-05 田林专篇：
# 写作时模型照骨架写好了图片行，配图各步看到“正文已有这类图”就都跳过，出稿时图片文件不存在）
PRE_FIG_KINDS = [('区域位置', r'区域位置|地理位置|位置图'), ('风频', r'玫瑰'), ('机位', r'机位布置|风机布置|风电机组布置'),
                 ('平面', r'总平面|平面布置'), ('工艺', r'工艺流程|生产流程|流程及职业病危害')]


def _fig_kind(cap):
    return next((k for k, pat in PRE_FIG_KINDS if re.search(pat, cap)), None)


def take_pre(root):
    """设计专篇一卷：图片文件不存在的图，从承接的预评价拷同类的图。预评价同类有几张（工艺流程图常有好几张）的，
    这一张换成几张，本节后面的图号和正文里的“见图××”跟着往后挪。返回 (拷了的 [图题], 没找到的 [说明])。"""
    pre = DT.pre_root(DT.main_root(root))
    if pre is None or tools.report_type(root) == DT.PRE:
        return [], []
    src = {}
    for g, i, _k, cap in figures(pre):
        m = RE_IMG.match(tools.read(Path(pre) / '正文' / g).split('\n')[i].strip())
        kind = _fig_kind(cap)
        if m and kind and (Path(pre) / m.group(2)).is_file():
            src.setdefault(kind, []).append((RE_FIG.sub('', cap).strip(), Path(pre) / m.group(2), m.group(2)))
    took, miss, seen = [], [], set()
    while True:
        hit = _missing_fig(root, src, seen, miss)
        if hit is None:
            return took, miss
        f, i, img, cap, key, have = hit
        (Path(root) / '图').mkdir(exist_ok=True)
        p = Path(root) / '正文' / f
        if len(have) == 1:
            shutil.copy2(have[0][1], Path(root) / img)
            took.append(cap)
            continue
        # 几张（工艺流程图）：本节后面的图号先往后挪，再把这一张换成几张，每张放横向页（K020）
        pf, first = key.rsplit('-', 1)
        first, extra = int(first), len(have) - 1
        _shift(root, pf, first, extra)
        lines = tools.read(p).split('\n')
        block = [] if any(x.strip() == '<!-- 横向 -->' for x in lines[max(0, i - 3):i]) else ['<!-- 横向 -->', '']
        for n, (title, fp, rel) in enumerate(have):
            dst = Path(root) / '图' / Path(rel).name
            shutil.copy2(fp, dst)
            if n:
                block += ['<!-- 分页 -->', '']
            block += ['![%s](图/%s){宽=%.2fcm}' % (title, dst.name, LANDSCAPE[0]), '图%s-%d  %s' % (pf, first + n, title), '']
            took.append('图%s-%d  %s' % (pf, first + n, title))
            seen.add((f, '图/' + dst.name))
        j = i + 2
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines) or not lines[j].strip().startswith(('<!-- 横向', '<!-- 纵向')):
            block += ['<!-- 纵向 -->', '']             # 后面没写换页方向的，回纵向页
        lines[i:i + 2] = block
        # 正文里“见图2.3-1”：一张换成了几张，改成“见图2.3-1～图2.3-4”
        span = re.compile(r'(?<![～~])图%s-%d(?![\d～~])' % (re.escape(pf), first))
        for g, q in _body(root):
            ls = tools.read(q).split('\n') if g != f else lines
            ls = [ln if RE_IMG.match(ln.strip()) or RE_FIG.match(ln.strip())
                  else span.sub('图%s-%d～图%s-%d' % (pf, first, pf, first + extra), ln) for ln in ls]
            if g == f:
                lines = ls
            else:
                tools.write(q, '\n'.join(ls))
        tools.write(p, '\n'.join(lines))


def _missing_fig(root, src, seen, miss):
    """下一张图片文件不存在、预评价里有同类图的：(文件, 图片行号, 图片路径, 图题, 图号, 预评价同类图)。
    预评价里没有同类的记进 miss，不再找它。"""
    for f, i, key, cap in figures(root):
        img = RE_IMG.match(tools.read(Path(root) / '正文' / f).split('\n')[i].strip()).group(2)
        if (f, img) in seen or (Path(root) / img).is_file():
            continue
        seen.add((f, img))
        have = src.get(_fig_kind(cap) or '', [])
        if not have:
            miss.append('%s（预评价里也没有同类的图）' % cap)
            continue
        return f, i, img, cap, key, have
    return None


def _shift(root, pf, first, extra):
    """本卷 pf 这一节、号大于 first 的图号（图题、图片行说明、正文引用）都加 extra。别的文件的图（可研、预评价报告、另一卷）不动。"""
    pat = re.compile(r'(图\s*)%s-(\d+)(?!\d)' % re.escape(pf))

    def one(s):
        def sub(m):
            n = int(m.group(2))
            pre_txt = s[:m.start()]
            if n <= first or pre_txt[-2:] in EXT2 or pre_txt.endswith(('主报告', '附件册', '预评价报告', '预评价')):
                return m.group(0)
            return '%s%s-%d' % (m.group(1), pf, n + extra)
        return pat.sub(sub, s)

    for _f, p in _body(root):
        t = tools.read(p)
        t2 = '\n'.join(one(ln) for ln in t.split('\n'))
        if t2 != t:
            tools.write(p, t2)


class _Skip(Exception):
    """这一卷不做这一项。"""


def run(job, root):
    """配图一遍：风频玫瑰图、区域位置图、平面布置图、工艺流程图（这张要调一次模型）。
    改正文前先备份；什么都没改就不留备份。"""
    before = {f: tools.read(p) for f, p in _body(root)}
    bak = tools.backup_body(root, '配图前')
    out = {'画了': [], '截了': [], '没找到': []}
    took, miss = take_pre(root)                  # 设计专篇：正文已写图片行的，先从预评价拷同类的图
    out['截了'] += ['%s（承接预评价）' % x for x in took]
    out['没找到'] += miss
    try:
        if not WIND_HEAD.get(tools.report_type(root), True):
            raise _Skip()                            # 设计专篇主报告：风频玫瑰图在附件册
        done, why = wind_table(root)                 # 没有风频表：用最近的国家气象站数据写表、画图
        out['画了'] += [done] if done else []
        out['没找到'] += [why] if why else []
        w = None if done else wind(root)             # 写了表的，图在 wind_table 里已经画过
        if w:
            out['画了'].append(w)
    except _Skip:
        pass
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as e:
        out['没找到'].append('风频玫瑰图（%s）' % e)
    try:
        out['截了'], miss = capture(root)
        out['没找到'] += miss
    except Exception as e:  # noqa: BLE001  PDF 读不了（加密、损坏）只记一句，不拦写稿
        out['没找到'].append('区域位置图、平面布置图（资料 PDF 读不了：%s）' % e)
    try:
        drew, miss = flows(job, root)
        out['画了'] += ['工艺流程图：' + x for x in drew]
        out['没找到'] += miss
    except llm.CallError as e:                  # 网络断了、拒答：这一步不画，不拦写稿；取消、余额不够照常往上抛
        if e.fatal:
            raise
        out['没找到'].append('工艺流程图（%s）' % str(e)[:120])
    if all(tools.read(p) == before.get(f) for f, p in _body(root)):
        shutil.rmtree(bak, ignore_errors=True)
    for x in out['画了'] + out['截了']:
        job.info('配图：' + x)
    if out['没找到']:
        job.info('配图：没有 %s（资料欠缺清单里列上）' % '、'.join(out['没找到']))
    return out


def run_all(job, root):
    """一份报告各卷都配一遍图（设计专篇：主报告和附件册）。返回合起来的结果。"""
    out = {'画了': [], '截了': [], '没找到': []}
    for i, vol in enumerate(DT.volumes(root)):
        r = run(job, vol)
        for k in out:
            out[k] += [('附件册：' if i else '') + x for x in r[k]]
    return out


def job_figures(job):
    job.stage('配图')
    out = run_all(job, job.project)
    c = tools.check(job.project)
    out['检查'] = {k: c[k] for k in ('错误', '提示', '待办')}
    return out
