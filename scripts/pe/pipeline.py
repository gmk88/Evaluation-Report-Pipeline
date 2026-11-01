"""流水线用的命令：按章检查（给子助手看的简短结果）、资料转文本、一键出稿、各项目总览、token 用量。
都只打印简短结果，长内容写文件，免得把对话撑大。"""
import collections
import datetime
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import checks
from .model import Project

try:
    import fcntl          # macOS、Linux
except ImportError:       # Windows 没有 fcntl，用 msvcrt 锁文件
    fcntl = None
    import msvcrt


def _try_lock(fh):
    """拿不到锁抛 BlockingIOError（两种系统一样）。"""
    if fcntl:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise BlockingIOError('锁被占用')


def _unlock(fh):
    if fcntl:
        fcntl.flock(fh, fcntl.LOCK_UN)
        return
    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _wps_busy():
    """WPS 命令行是不是正被别的程序占着。Windows 上不查（WPS 导出另有做法）。"""
    if not shutil.which('pgrep'):
        return ''
    return subprocess.run(['pgrep', '-fl', 'wpscli|kwpsconvert|kpdfcli'], capture_output=True, text=True).stdout.strip()


def _docx_text(path):
    """没有 textutil（Windows）时读 .docx：段落和表格按文档顺序取文字。"""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    d = docx.Document(str(path))
    out = []
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 'p':
            out.append(Paragraph(el, d).text)
        elif tag == 'tbl':
            for row in Table(el, d).rows:
                cells = []
                for c in row.cells:
                    t = c.text.strip().replace('\n', ' ')
                    if not cells or cells[-1] != t:     # 合并格只取一次
                        cells.append(t)
                out.append('\t'.join(cells))
    return '\n'.join(out)


def _pdf_text(path):
    """没有 pdftotext 时用 PyMuPDF 取文字层。"""
    import pymupdf
    with pymupdf.open(str(path)) as d:
        return '\n'.join(p.get_text() for p in d)

SKILL = Path(__file__).resolve().parents[2]
REPORT = SKILL / 'scripts' / 'report.py'
REGISTER = SKILL / '项目登记.md'
CAP = {'错误': 30, '提示': 10, '待办': 30}


def fail(msg):
    print('出错：' + msg)
    sys.exit(2)


# ---------------- check --json / --章 ----------------

def check_view(pj, issues, prefixes, as_json):
    """按章筛选检查结果；--json 时按文件分组，每类最多列 CAP 条。不写 校验报告.md。"""
    pre = [p.strip() for p in re.split(r'[,，、\s]+', prefixes or '') if p.strip()]

    def keep(src):
        return not pre or any(src.split(':')[0].startswith(p) for p in pre)

    sel = [(lv, s, m) for lv, s, m in issues if keep(s)]
    c = checks.summary(sel)
    if not as_json:
        print('错误 %d、提示 %d、待办 %d' % (c['错误'], c['提示'], c['待办']))
        for lv, s, m in sel:
            if lv == '错误':
                print('  错误【%s】%s' % (s, m))
        return 1 if c['错误'] else 0
    by_file = collections.OrderedDict()
    for lv, s, m in sel:
        d = by_file.setdefault(s.split(':')[0], {'错误': [], '提示': [], '待办': []})
        d[lv].append(('%s %s' % (s.split(':', 1)[1], m)) if ':' in s else m)
    out = {'错误': c['错误'], '提示': c['提示'], '待办': c['待办'], '按文件': {}}
    for name, d in by_file.items():
        out['按文件'][name] = {lv: v[:CAP[lv]] + (['……另有 %d 条' % (len(v) - CAP[lv])] if len(v) > CAP[lv] else [])
                             for lv, v in d.items() if v}
    body = pj.root / '正文'
    out['正文文件'] = [{'文件': p.name, '字数': len(p.read_text(encoding='utf-8'))}
                     for p in sorted(body.glob('*.md'))] if body.exists() else []
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 1 if c['错误'] else 0


# ---------------- 资料：转文本、分块 ----------------

RE_HEAD = re.compile(r'^\s*(第[一二三四五六七八九十百零\d]+[章节篇部分]|\d{1,2}(?:\.\d{1,2}){0,3}[\s、．.]?\s*[一-龥])')


def xlsx_text(path):
    import zipfile
    import xml.etree.ElementTree as ET
    m = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    with zipfile.ZipFile(path) as z:
        shared = []
        if 'xl/sharedStrings.xml' in z.namelist():
            for si in ET.fromstring(z.read('xl/sharedStrings.xml')).iter(m + 'si'):
                shared.append(''.join(t.text or '' for t in si.iter(m + 't')))
        out = []
        for name in sorted(n for n in z.namelist() if re.match(r'xl/worksheets/sheet\d+\.xml$', n)):
            out.append('## ' + name.rsplit('/', 1)[1])
            for row in ET.fromstring(z.read(name)).iter(m + 'row'):
                vals = []
                for cell in row.iter(m + 'c'):
                    v = cell.find(m + 'v')
                    if cell.get('t') == 's' and v is not None:
                        vals.append(shared[int(v.text)])
                    elif cell.get('t') == 'inlineStr':
                        vals.append(''.join(x.text or '' for x in cell.iter(m + 't')))
                    else:
                        vals.append(v.text if v is not None and v.text else '')
                if any(vals):
                    out.append('\t'.join(vals))
    return '\n'.join(out)


def to_text(f):
    """返回 (文字, 说明)；转不了时文字为 None。"""
    suf = f.suffix.lower()
    try:
        if suf in ('.doc', '.docx', '.rtf', '.odt', '.wps', '.htm', '.html'):
            if not shutil.which('textutil'):
                if suf == '.docx':
                    text = _docx_text(f)
                    return (text, 'python-docx') if text.strip() else (None, '转不出文字')
                if sys.platform == 'win32':
                    # Windows：借本机 WPS 或 Word 先另存成 .docx 再读
                    import tempfile
                    from . import office_win
                    with tempfile.TemporaryDirectory(prefix='pe_doc_') as d:
                        tmp = Path(d) / (f.stem + '.docx')
                        try:
                            office_win.to_docx(f, tmp)
                        except office_win.OfficeError as e:
                            return None, '%s 转不了：%s' % (suf, e)
                        text = _docx_text(tmp)
                    return (text, 'WPS/Word 另存') if text.strip() else (None, '转不出文字')
                return None, '这台电脑转不了 %s，请另存为 .docx 或 PDF 再放进来' % suf
            r = subprocess.run(['textutil', '-convert', 'txt', '-encoding', 'UTF-8', '-stdout', str(f)],
                               capture_output=True, timeout=300)
            text = r.stdout.decode('utf-8', 'replace')
            return (text, 'textutil') if text.strip() else (None, 'textutil 转不出文字')
        if suf == '.pdf':
            if shutil.which('pdftotext'):
                r = subprocess.run(['pdftotext', '-enc', 'UTF-8', str(f), '-'], capture_output=True, timeout=300)
                text, how = r.stdout.decode('utf-8', 'replace'), 'pdftotext'
            else:
                text, how = _pdf_text(f), 'PyMuPDF'
            if len(re.sub(r'\s', '', text)) < 50:
                return None, '扫描件，没有文字层'
            return text, how
        if suf in ('.txt', '.md', '.csv'):
            raw = f.read_bytes()
            for enc in ('utf-8-sig', 'gb18030'):      # Windows 记事本存的中文 txt 常是 GBK
                try:
                    return raw.decode(enc), '文本'
                except UnicodeDecodeError:
                    pass
            return raw.decode('utf-8', 'replace'), '文本'
        if suf == '.xlsx':
            return xlsx_text(f), 'xlsx'
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, ImportError, RuntimeError) as e:
        return None, '转换出错：%s' % e
    return None, '不是文字资料（%s）' % (suf or '无扩展名')


def split_chunks(text, size):
    paras = [p for p in re.split(r'\n', text.replace('\r', '')) if p.strip()]
    chunks, cur, n = [], [], 0
    for p in paras:
        while len(p) > size:
            chunks.append(p[:size])
            p = p[size:]
        if n + len(p) > size and cur:
            chunks.append('\n'.join(cur))
            cur, n = [], 0
        cur.append(p)
        n += len(p) + 1
    if cur:
        chunks.append('\n'.join(cur))
    return chunks


def cmd_materials(a):
    root = Path(a.folder).resolve()
    out = root / '资料' / '文本'
    out.mkdir(parents=True, exist_ok=True)
    for old in list(out.glob('[0-9][0-9][0-9]_*.txt')) + [out / '索引.json']:
        if old.exists():
            old.unlink()
    srcs = [Path(x).expanduser().resolve() for x in a.files] or [root / '资料']
    # 流水线自己写进 资料/ 的文件（事实库、口径表、岗位因素表、摘事实的中间稿、说明）不是资料，不转
    made = {root / '资料' / n for n in ('事实库.md', '口径.md', '岗位因素表.md', '说明.md')}
    frag = root / '资料' / '事实片段'
    files = []
    for s in srcs:
        if s.is_dir():
            files += sorted(p for p in s.rglob('*') if p.is_file() and out not in p.parents and frag not in p.parents
                            and p not in made and not p.name.startswith(('.', '~$')))
        elif s.exists():
            files.append(s)
        else:
            print('找不到：%s' % s)
    index = {'块': [], '跳过': []}
    for f in files:
        text, how = to_text(f)
        if text is None:
            index['跳过'].append('%s：%s' % (f.name, how))
            continue
        stem = re.sub(r'[\\/:*?"<>|\s()（）]+', '', f.stem)[:20] or '资料'
        for chunk in split_chunks(text, a.块长):
            name = '%03d_%s.txt' % (len(index['块']) + 1, stem)
            (out / name).write_text(chunk, encoding='utf-8')
            heads = [ln.strip()[:40] for ln in chunk.split('\n') if RE_HEAD.match(ln) and len(ln.strip()) < 60]
            index['块'].append({'文件': name, '来源': str(f), '字数': len(chunk), '标题': heads[:8]})
    (out / '索引.json').write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding='utf-8')
    total = sum(b['字数'] for b in index['块'])
    print('资料 %d 个文件 → %d 块，共 %d 字 → %s' % (len(files), len(index['块']), total, out))
    for s in index['跳过']:
        print('  没转：' + s)
    return 0


# ---------------- 出稿：检查为 0 → build → WPS 两轮 → 发布 ----------------

KEEP = re.compile(r'通过|页|目录|错误|出错|失败|输出|发布|字体|插图|核对|→')


def run_brief(cmd, label):
    r = subprocess.run(cmd, capture_output=True, text=True)
    lines = (r.stdout + r.stderr).strip().split('\n')
    shown = [ln for ln in lines if KEEP.search(ln)][:20] if r.returncode == 0 else lines[-15:]
    print('== %s（退出码 %d）' % (label, r.returncode))
    for ln in shown:
        print('  ' + ln[:200])
    return r.returncode


def cmd_publish(a):
    root = Path(a.folder).resolve()
    pj = Project(root).resolve()
    issues = checks.run(pj)
    c = checks.summary(issues)
    if c['错误']:
        print('检查有错误 %d 条，不出稿：' % c['错误'])
        for lv, s, m in [x for x in issues if x[0] == '错误'][:30]:
            print('  【%s】%s' % (s, m))
        return 1
    with open(SKILL / '.出稿锁', 'w') as fh:
        t0 = time.time()
        while True:
            try:
                _try_lock(fh)
                busy = _wps_busy()
                if not busy:
                    break
                _unlock(fh)
            except BlockingIOError:
                busy = '流水线里别的项目正在出稿'
            if time.time() - t0 > a.等待:
                fail('等了 %d 秒，WPS 还被占着：%s' % (a.等待, busy[:100]))
            time.sleep(10)
        rc = run_brief([sys.executable, str(REPORT), 'build', str(root), '--不出PDF'], 'Word')
        if rc:
            return rc
        cmd = [sys.executable, str(REPORT), 'wps', str(root)] + ([] if a.只核对 else ['--发布'])
        rc = run_brief(cmd, 'WPS 导出' + ('（只核对）' if a.只核对 else '并发布'))
    print('检查：错误 0、提示 %d、待办 %d' % (c['提示'], c['待办']))
    return rc


# ---------------- 总览：各项目检查结果和出稿情况 ----------------

def registered():
    if not REGISTER.exists():
        fail('没有 %s' % REGISTER)
    rows = []
    for ln in REGISTER.read_text(encoding='utf-8').split('\n'):
        cells = [x.strip() for x in ln.strip().strip('|').split('|')]
        if len(cells) >= 3 and cells[0] not in ('项目', '') and not set(cells[0]) <= set('-: '):
            rows.append({'项目': cells[0], '方法': cells[1], '文件夹': cells[2],
                         '样板': cells[3] if len(cells) > 3 else ''})
    return rows


def pdf_pages(p):
    try:
        if not shutil.which('pdfinfo'):
            import pymupdf
            with pymupdf.open(str(p)) as d:
                return str(d.page_count)
        r = subprocess.run(['pdfinfo', str(p)], capture_output=True, text=True, timeout=30)
        m = re.search(r'Pages:\s+(\d+)', r.stdout)
        return m.group(1) if m else '?'
    except (OSError, subprocess.SubprocessError, ImportError, RuntimeError):
        return '?'


def cmd_overview(a):
    fmt = '%m-%d %H:%M'
    print('| 项目 | 方法 | 错误 | 其中通用规则 | 提示 | 待办 | 正文最后修改 | 最新PDF |')
    print('|---|---|---|---|---|---|---|---|')
    for r in registered():
        root = Path(r['文件夹']).expanduser()
        try:
            pj = Project(root).resolve()
            issues = checks.run(pj)
        except Exception as e:  # noqa: BLE001  旧格式的项目读不了，照实列出
            print('| %s | %s | 读不了：%s | | | | | |' % (r['项目'], r['方法'], str(e)[:40]))
            continue
        c = checks.summary(issues)
        common = sum(1 for lv, _, m in issues if lv == '错误' and m.startswith('通用规则'))
        mds = list((root / '正文').glob('*.md'))
        body_t = time.strftime(fmt, time.localtime(max(p.stat().st_mtime for p in mds))) if mds else '—'
        pdfs = sorted((root / '输出').glob('*.pdf'), key=lambda p: p.stat().st_mtime)
        pdf = ('%s，%s 页' % (time.strftime(fmt, time.localtime(pdfs[-1].stat().st_mtime)), pdf_pages(pdfs[-1]))
               if pdfs else '—')
        print('| %s | %s | %d | %d | %d | %d | %s | %s |' % (r['项目'], r['方法'], c['错误'], common, c['提示'],
                                                          c['待办'], body_t, pdf))
    return 0


# ---------------- 用量：读 ~/.claude/projects 的对话记录 ----------------

def cmd_usage(a):
    root = Path.home() / '.claude' / 'projects'
    since = datetime.datetime.utcnow() - datetime.timedelta(days=a.天)
    since_s = since.strftime('%Y-%m-%dT%H:%M:%S')
    desk = str(Path.home() / 'Desktop') + '/'
    sessions = []
    for p in root.glob('*/**/*.jsonl'):
        if p.stat().st_mtime < since.timestamp() - 86400:
            continue
        seen, ctx_sum, turns, max_ctx, out_tok, cwd, first = set(), 0, 0, 0, 0, '', ''
        with open(p, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if (e.get('timestamp') or '') < since_s:
                    continue
                cwd = cwd or e.get('cwd') or ''
                m = e.get('message') or {}
                if e.get('type') == 'user' and not first:
                    t = m.get('content')
                    t = t if isinstance(t, str) else ' '.join(b.get('text', '') for b in t or []
                                                              if isinstance(b, dict) and b.get('type') == 'text')
                    if t.strip() and not t.lstrip().startswith('<'):
                        first = t.strip()[:30].replace('\n', ' ')
                us = m.get('usage') if e.get('type') == 'assistant' else None
                mid = m.get('id')
                if us and mid not in seen:
                    seen.add(mid)
                    ctx = sum(us.get(k) or 0 for k in ('input_tokens', 'cache_creation_input_tokens',
                                                       'cache_read_input_tokens'))
                    ctx_sum += ctx
                    out_tok += us.get('output_tokens') or 0
                    max_ctx = max(max_ctx, ctx)
                    turns += 1
        if turns:
            parts = [x for x in cwd.replace(desk, '', 1).split('/') if x] if cwd.startswith(desk) else []
            proj = ('桌面' if not parts else parts[0][:16] if len(parts) == 1 or not parts[0].endswith('项目')
                    else parts[0][:6] + '/' + parts[1][:12])
            sessions.append({'项目': proj, '子': 'subagents' in p.parts, '读入': ctx_sum, '轮': turns,
                             '最大': max_ctx, '输出': out_tok, '开头': first})
    main = [s for s in sessions if not s['子']]
    sub = [s for s in sessions if s['子']]

    def tot(ss, k):
        return sum(s[k] for s in ss)

    mt = tot(main, '轮') or 1
    print('最近 %d 天：主对话读入 %.2f 亿 token（%d 轮，平均每轮 %.1f 万）；子助手读入 %.2f 亿；输出共 %.0f 万'
          % (a.天, tot(main, '读入') / 1e8, tot(main, '轮'), tot(main, '读入') / mt / 1e4,
             tot(sub, '读入') / 1e8, (tot(main, '输出') + tot(sub, '输出')) / 1e4))
    by = collections.defaultdict(lambda: [0, 0, 0])
    for s in sessions:
        by[s['项目']][1 if s['子'] else 0] += s['读入']
        if not s['子']:
            by[s['项目']][2] += s['轮']
    print('按项目（亿 token）：主对话 / 子助手 / 主对话轮数')
    for k, (m_, s_, t_) in sorted(by.items(), key=lambda x: -(x[1][0] + x[1][1])):
        print('  %-16s %6.2f  %6.2f  %5d' % (k, m_ / 1e8, s_ / 1e8, t_))
    print('最大的 8 个主对话：读入亿 / 轮数 / 最大上下文万 / 开头')
    for s in sorted(main, key=lambda s: -s['读入'])[:8]:
        print('  %5.2f  %5d  %5.1f  %s' % (s['读入'] / 1e8, s['轮'], s['最大'] / 1e4, s['开头']))
    return 0
