"""流水线现有程序（scripts/）的包装和文件操作：检查、新建项目、资料转文字、出稿、改正文、摘表。"""
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import config

if str(config.SCRIPTS) not in sys.path:
    sys.path.insert(0, str(config.SCRIPTS))
from pe import checks as _checks  # noqa: E402
from pe import mdparse  # noqa: E402
from pe.model import Project  # noqa: E402

TODO = re.compile(r'【(?:待填|待核|待补|待定|待确认)[^】]*】')


def read(p):
    return Path(p).read_text(encoding='utf-8')


def write(p, text):
    """先写临时文件再换名：并行检查时不会读到写了一半的文件。"""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name('.%s.%d.tmp' % (p.name, os.getpid()))
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, p)


def env():
    e = dict(os.environ)
    e.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return e


def report(*args, timeout=3600):
    """运行 scripts/report.py，返回 (退出码, 输出)。"""
    r = subprocess.run([sys.executable, str(config.REPORT), *[str(a) for a in args]], capture_output=True,
                       text=True, encoding='utf-8', errors='replace', env=env(), timeout=timeout)
    out = (r.stdout or '').strip()
    if (r.stderr or '').strip():
        out += '\n' + r.stderr.strip()[-2000:]
    return r.returncode, out


# ---------------- 项目登记 ----------------

def registered():
    rows = []
    if not config.REGISTER.exists():
        return rows
    for ln in read(config.REGISTER).split('\n'):
        cells = [x.strip() for x in ln.strip().strip('|').split('|')]
        if len(cells) >= 3 and cells[0] not in ('项目', '') and not set(cells[0]) <= set('-: ') and ln.lstrip().startswith('|'):
            rows.append({'项目': cells[0], '方法': cells[1], '文件夹': cells[2], '样板': cells[3] if len(cells) > 3 else '',
                         '定稿': cells[4] if len(cells) > 4 else ''})
    return rows


def register_add(name, method, folder):
    folder = str(Path(folder).resolve())
    if any(str(Path(r['文件夹']).expanduser().resolve()) == folder for r in registered()):
        return False
    text = read(config.REGISTER).rstrip('\n') + '\n| %s | %s | %s | | |\n' % (name, method, folder)
    write(config.REGISTER, text)
    return True


def project_row(root):
    root = str(Path(root).resolve())
    for r in registered():
        if str(Path(r['文件夹']).expanduser().resolve()) == root:
            return r
    return None


def project_name(root):
    r = project_row(root)
    if r:
        return r['项目']
    try:
        return Project(root).info.get('项目名称') or Path(root).name
    except Exception:  # noqa: BLE001
        return Path(root).name


def method_of(root):
    r = project_row(root)
    if r and r['方法'] in ('类比法', '风险评估法'):
        return r['方法']
    for p in sorted((Path(root) / '正文').glob('03*.md')):
        head = next((ln for ln in read(p).split('\n') if ln.startswith('# ')), '')
        if '风险评估' in head:
            return '风险评估法'
        if '类比' in head:
            return '类比法'
    return '类比法'


def sample_for(method, exclude=None):
    """项目登记里方法相同、样板为“是”的项目文件夹。"""
    ex = str(Path(exclude).resolve()) if exclude else ''
    for r in registered():
        f = Path(r['文件夹']).expanduser()
        if r['方法'] == method and r['样板'] == '是' and str(f.resolve()) != ex and (f / '正文').is_dir():
            return f
    return None


# ---------------- 检查 ----------------

def format_errors(root):
    """逐个文件查格式（表格列数、注释结束符等）。整份正文读不了时，好知道是哪几个文件、各错在哪。"""
    out = {}
    for name in body_files(root):
        p = Path(root) / '正文' / name
        try:
            mdparse.parse_text(read(p), p.name)
        except mdparse.ParseError as e:
            m = re.match(r'^(\S+?\.md):(\d+) (.*)$', str(e))
            out[p.name] = ['第%s行 %s' % (m.group(2), m.group(3))] if m else [str(e)]
    return out


def check(root, prefixes=()):
    """机器检查（程序内置规则＋通用检查规则＋项目检查规则）。返回各级条数和按文件分组的条目。
    有文件格式读不了时，只返回各文件的格式错误（格式错误＝True），其余检查等格式改好再做。"""
    out = {'错误': 0, '提示': 0, '待办': 0, '按文件': {}, '正文文件': []}
    fmt = format_errors(root)
    if fmt:
        out.update(错误=sum(len(v) for v in fmt.values()), 格式错误=True,
                   按文件={k: {'错误': v, '提示': [], '待办': []} for k, v in fmt.items()},
                   正文文件=body_files(root))
        return out
    try:
        pj = Project(root).resolve()
        issues = _checks.run(pj)
    except (FileNotFoundError, mdparse.ParseError, ValueError) as e:
        # 格式错误（表格列数不对、注释没结束）会让整份正文读不了；归到出错的那个文件，好交给模型改
        m = re.match(r'^(\S+?\.md):(\d+) (.*)$', str(e))
        name, msg = (m.group(1), '第%s行 %s' % (m.group(2), m.group(3))) if m else ('项目', str(e))
        out['错误'] = 1
        out['格式错误'] = True
        out['按文件'][name] = {'错误': [msg], '提示': [], '待办': []}
        body = Path(root) / '正文'
        out['正文文件'] = [p.name for p in sorted(body.glob('*.md'))] if body.is_dir() else []
        return out
    pre = [p for p in prefixes if p]
    for lv, src, msg in issues:
        name = src.split(':')[0]
        if pre and not any(name.startswith(p) for p in pre):
            continue
        d = out['按文件'].setdefault(name, {'错误': [], '提示': [], '待办': []})
        d[lv].append(('第%s行 %s' % (src.split(':', 1)[1], msg)) if ':' in src else msg)
        out[lv] += 1
    body = Path(root) / '正文'
    out['正文文件'] = [p.name for p in sorted(body.glob('*.md'))] if body.is_dir() else []
    return out


def errors_of(chk, fname):
    return chk['按文件'].get(fname, {}).get('错误', [])


def overview():
    rows = []
    for r in registered():
        root = Path(r['文件夹']).expanduser()
        row = dict(r, 存在=root.is_dir())
        if root.is_dir():
            c = check(root)
            row.update(错误=c['错误'], 提示=c['提示'], 待办=c['待办'])
            mds = list((root / '正文').glob('*.md'))
            row['正文修改'] = max(p.stat().st_mtime for p in mds) if mds else None
            pdfs = sorted((root / '输出').glob('*.pdf'), key=lambda p: p.stat().st_mtime)
            if pdfs:
                row['PDF'] = str(pdfs[-1])
                row['PDF时间'] = pdfs[-1].stat().st_mtime
                row['页数'] = pdf_pages(pdfs[-1])
        rows.append(row)
    return rows


def pdf_pages(p):
    try:
        import pymupdf
        with pymupdf.open(str(p)) as d:
            return d.page_count
    except Exception:  # noqa: BLE001
        return None


# ---------------- 正文文件 ----------------

def body_files(root):
    """正文文件名（与生成 Word 时一样：下划线、点开头的不算）。"""
    return [p.name for p in sorted((Path(root) / '正文').glob('*.md')) if not p.name.startswith(('_', '.'))]


def chapter(fname):
    return fname[:2]


def backup_body(root, tag):
    root = Path(root)
    base = '正文_%s_%s' % (datetime.datetime.now().strftime('%Y%m%d_%H%M%S'), tag)
    dst, n = root / '备份' / base, 1
    while dst.exists():                  # 同一秒里备份两次（接着做、连着审两章）时不撞名
        n += 1
        dst = root / '备份' / ('%s_%d' % (base, n))
    shutil.copytree(root / '正文', dst)
    return dst


def numbered(text):
    return '\n'.join('%d\t%s' % (i + 1, ln) for i, ln in enumerate(text.split('\n')))


def _strip_numbers(s):
    return re.sub(r'^\s*\d+\t', '', s, flags=re.M)


def parse_error(text, fname='正文.md'):
    """Markdown 格式错误（表格格数、注释结束符等），没有返回空字符串。"""
    try:
        mdparse.parse_text(text, fname)
        return ''
    except mdparse.ParseError as e:
        return str(e)


def format_ok(text, fname='正文.md'):
    return not parse_error(text, fname)


def insert_item(md, section, line):
    """把一行插到标题为 section 的那一节末尾（下一个标题之前）；找不到这一节就在文末新开一节。"""
    lines = md.split('\n')
    key = section.strip().lstrip('#').strip()
    heads = [(i, ln.lstrip('#').strip()) for i, ln in enumerate(lines) if re.match(r'^#{1,4}\s', ln)]
    pick = next((i for i, h in heads if h == key), None)
    if pick is None:
        pick = next((i for i, h in heads if h.startswith(key) or (len(h) >= 2 and key.startswith(h))), None)
    if pick is None and len(key) >= 4:
        pick = next((i for i, h in heads if h.startswith(key[:4])), None)
    item = line.strip() if re.match(r'^([-*]\s|\|)', line.strip()) else '- ' + line.strip()
    if pick is None:
        return md.rstrip('\n') + '\n\n## %s\n%s\n' % (key, item)
    end = next((i for i, _ in heads if i > pick), len(lines))
    while end > pick + 1 and not lines[end - 1].strip():
        end -= 1
    lines.insert(end, item)
    return '\n'.join(lines)


def _replace_once(text, old, new):
    """原文在文字里只出现一次时替换；模型抄原文时带了行号或多了首尾空白的，去掉再找。返回 (新文字, 原因)。"""
    n = text.count(old)
    if n == 1:
        return text.replace(old, new, 1), ''
    if n > 1:
        return None, '原文在文件里出现 %d 处，不能唯一定位' % n
    for a, b in ((_strip_numbers(old), _strip_numbers(new)), (old.strip(), new.strip())):
        if a and a != old and text.count(a) == 1:
            return text.replace(a, b, 1), ''
    return None, '文件里找不到原文'


def rule_words(root, hints=False):
    """机器检查会报的词：通用、本项目检查规则的禁用和正则禁用，检查程序内置的禁用说法和【待…】占位；
    hints 为真时再加两边的“提示”词。本项目“豁免”的不算。返回 [(说明, 判断函数)]，给 common.guard 用。"""
    from . import rules
    out, exempt = [], set()
    files = [p for p in (config.STD / '通用检查规则.md', Path(root) / '检查规则.md') if p.exists()]
    for p in files:
        exempt |= {w for s, w, _ in rules.items(p) if s == '豁免'}
    for p in files:
        for s, w, _ in rules.items(p):
            if w in exempt:
                continue
            if s == '禁用' or (hints and s == '提示'):
                out.append(('“%s”' % w, lambda t, w=w: w in t))
            elif s == '正则禁用':
                try:
                    rx = re.compile(w)
                except re.error:
                    continue
                out.append(('/%s/' % w, lambda t, rx=rx: rx.search(t) is not None))
    for w, _ in list(_checks.GENERIC_ERRORS) + (list(_checks.GENERIC_NOTES) if hints else []):
        out.append(('“%s”' % w, lambda t, w=w: w in t))
    out.append(('【待…】占位', lambda t: _checks.RE_TODO.search(t) is not None))
    return out


def apply_edits(text, edits, fname=None):
    """按“原文→新文”逐条替换，原文要在文件里唯一。给了 fname 时，每改一处查一次 Markdown 格式（表格列数、注释），
    把原本好的格式改坏了的那一处不改。返回 (新文字, 改成的条目, 没改成的[(条目, 原因)])。"""
    done, failed = [], []
    for e in edits:
        old, new = e.get('原文') or '', e.get('新文') or ''
        if not old.strip():
            failed.append((e, '原文为空'))
            continue
        if old == new:
            failed.append((e, '原文和新文一样'))
            continue
        res, why = _replace_once(text, old, new)
        if res is None:
            failed.append((e, why))
            continue
        if fname and not format_ok(res, fname) and format_ok(text, fname):
            failed.append((e, '改后表格或注释格式不对'))
            continue
        text = res
        done.append(e)
    return text, done, failed


# ---------------- 摘表、摘要 ----------------

def tables_by_caption(md, pattern):
    """按表题（正则）摘出整张表：表题、表格指令、表格行、表注。"""
    lines = md.split('\n')
    res = []
    for i, ln in enumerate(lines):
        if not re.match(pattern, ln):
            continue
        block, j = [ln], i + 1
        while j < len(lines) and j <= i + 3 and (lines[j].startswith('<!--') or not lines[j].strip()):
            block.append(lines[j])
            j += 1
        while j < len(lines) and lines[j].startswith('|'):
            block.append(lines[j])
            j += 1
        while j < len(lines) and lines[j].startswith('注'):
            block.append(lines[j])
            j += 1
        if len(block) > 2:
            res.append('\n'.join(block))
    return res


def _cut_cells(ln, cell):
    cells = ln.strip().strip('|').split('|')
    return '| ' + ' | '.join(c.strip()[:cell] + ('…' if len(c.strip()) > cell else '') for c in cells) + ' |'


def digest(md, cell=40, budget=40000):
    """章的摘要：各级标题、表题和表格（每格最多 cell 字）、小结段落。给后面各章看前文、跨章核对用。
    超过 budget 字时把每格再截短。"""
    while True:
        out, keep = [], False
        for ln in md.split('\n'):
            if ln.startswith('#'):
                out.append(ln)
                keep = bool(re.search(r'小结|结论|评价结果|总体评价', ln))
            elif re.match(r'^(续)?表\s*[0-9A-Z]', ln):
                out.append(ln)
            elif ln.startswith('|'):
                out.append(_cut_cells(ln, cell) if not set(ln.strip()) <= set('|-: ') else ln)
            elif (keep or ln.startswith('评价结果')) and ln.strip() and not ln.startswith('<!--'):
                out.append(ln)
        text = '\n'.join(out)
        if len(text) <= budget or cell <= 8:
            return text[:budget * 2]
        cell = cell // 2


def headings(md, level=2):
    return [ln for ln in md.split('\n') if re.match(r'^#{1,%d} ' % level, ln)]


# ---------------- 事实来源 ----------------

SETTINGS_FILE = '流水线设置.json'


def project_settings(root):
    p = Path(root) / SETTINGS_FILE
    try:
        return json.loads(read(p)) if p.exists() else {}
    except ValueError:
        return {}


def source_texts(root):
    """项目资料的文字版 [(资料名, 全文)]：流水线设置.json 的“事实来源” → 资料/文本/（同一来源的块拼回整份）
    → 上一级的 提取文本/（不含送审稿）。"""
    root = Path(root)
    picked = project_settings(root).get('事实来源')
    if picked:
        files = [Path(x) if Path(x).is_absolute() else root / x for x in picked]
        return [(p.name, read(p)) for p in files if p.is_file()]
    idx = root / '资料' / '文本' / '索引.json'
    if idx.exists():
        groups = {}
        for b in json.loads(read(idx)).get('块', []):
            p = idx.parent / b['文件']
            if p.exists():
                groups.setdefault(Path(b['来源']).name, []).append(read(p))
        if groups:
            return [(k, '\n'.join(v)) for k, v in groups.items()]
    t = root.parent / '提取文本'
    files = sorted(p for p in t.glob('*.txt') if not NOT_SOURCE.search(p.name)) if t.is_dir() else []
    return [(p.name, read(p)) for p in files if p.is_file()]


# 旧报告和标准原文不当事实来源
NOT_SOURCE = re.compile(r'送审稿|报告全文|^(GBZ|GB|AQ|HJ|WS|DB)')


def finished_projects():
    """项目登记里“定稿”为“是”的项目（已按用户全部意见改过），提炼标准、加禁用词时拿来验证。"""
    return [Path(r['文件夹']).expanduser() for r in registered()
            if r.get('定稿') == '是' and (Path(r['文件夹']).expanduser() / '正文').is_dir()]


def backup_std(reason):
    """改标准前把 标准/ 存一份到 标准/_历史/（不用 git：同事的 Windows 电脑上不一定有）。"""
    if not config.STD.is_dir():
        return None
    dst = config.STD / '_历史' / ('%s_%s' % (datetime.datetime.now().strftime('%Y%m%d_%H%M%S'), re.sub(r'[\\/:*?"<>|\s]+', '', reason)[:20]))
    shutil.copytree(config.STD, dst, ignore=shutil.ignore_patterns('_历史', '.流水线运行中'))
    return dst


def facts(root):
    """事实库、口径表、岗位因素表（有哪个给哪个）。"""
    out = []
    for name in ('事实库.md', '口径.md', '岗位因素表.md'):
        p = Path(root) / '资料' / name
        if p.exists():
            out.append((name, read(p)))
    return out


# 类比项目的资料（别的项目的控评、检测、体检报告）放在 资料/类比/，不算本项目资料：
# 不进 资料/文本/索引.json，不当事实来源，转资料时也不转（app/flows/analog.py）
ANALOG_DIR = '类比'


def analog_dir(root):
    return Path(root) / '资料' / ANALOG_DIR


def analog_texts(root):
    """类比资料的文字 [(资料名, 全文)]（没有就是空列表）。"""
    try:
        st = json.loads(read(analog_dir(root) / '类比资料.json'))
    except (OSError, ValueError):
        return []
    out = []
    for x in st.get('文本', []):
        p = analog_dir(root) / '文本' / x.get('文件', '')
        if p.is_file():
            out.append((x.get('资料', p.name), read(p)))
    return out


def analog_facts(root):
    """从类比资料摘的类比事实（没有返回 ''）。"""
    p = analog_dir(root) / '类比事实.md'
    return read(p) if p.is_file() else ''


def open_path(p):
    p = str(p)
    if config.IS_WIN:
        os.startfile(p)  # noqa: S606
    elif config.IS_MAC:
        subprocess.Popen(['open', p])
    else:
        subprocess.Popen(['xdg-open', p])
