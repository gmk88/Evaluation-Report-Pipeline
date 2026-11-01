"""本机网页服务：页面在 app/web/index.html，接口在 /api/*。只监听 127.0.0.1，凭启动时生成的口令访问。

在流水线文件夹里运行：python -m app.server [--port 8765] [--open] [--no-reuse]
  桌面上的“预评价编制工作台”就是跑这个。已有服务在跑时不开第二个，直接打开那个网址。
浏览器关掉后服务照样跑，任务不中断。
"""
import argparse
import base64
import datetime
import json
import os
import re
import secrets
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, flows, gaps, jobs, llm, tools

WEB = Path(__file__).resolve().parent / 'web' / 'index.html'
COOKIE = 'rp_t'
MAX_BODY = 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
JOB_ID = re.compile(r'^\d{4}-\d{6}-[0-9a-f]{4}$')
OPEN_EXT = {'.pdf', '.docx', '.doc', '.md', '.txt', '.xlsx', '.json', '.log', '.png', '.jpg'}
READ_EXT = {'.md', '.txt', '.json', '.log'}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
CLAUDE_DIR = Path.home() / '.claude'          # 本机 Claude Code 的设置（settings.json 里有密钥），永远不给看
JUNK = ('.DS_Store', 'desktop.ini', 'Thumbs.db')  # 系统自己生成的文件，不算“文件夹不是空的”（和 flows/new.py 一致）
BAD_TOKEN = '口令不对或已过期：请双击桌面上的“预评价编制工作台”重新打开'
NO_PAGE = '页面还没装好。服务已经在跑，等页面文件装好后刷新这一页。'

STATE = {'port': 0, 'token': '', 'url': ''}
MANAGER = jobs.Manager()
_overview_cache = {'t': 0.0, 'v': None}
_overview_lock = threading.Lock()
_log_lock = threading.Lock()


class ApiError(Exception):
    """接口出错：带状态码和一句中文，回给页面。"""

    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


# ---- 路径（运行时取模块变量，测试里改了也生效） ----
def info_file():
    return config.DATA / 'server.json'


def log_file():
    return config.DATA / 'server.log'


def log(text):
    """追加写 server.log（pythonw 启动时没有控制台，只能看这个文件）。"""
    line = time.strftime('%Y-%m-%d %H:%M:%S ') + str(text).rstrip() + '\n'
    try:
        with _log_lock, log_file().open('a', encoding='utf-8') as fh:
            fh.write(line)
    except OSError:
        pass


# ---- 允许打开、读取的范围 ----
def _roots():
    """允许范围：登记的各项目文件夹、流水线文件夹、任务记录文件夹。"""
    out = [config.ROOT, Path(jobs.JOBS_DIR)]
    try:
        out += [Path(r['文件夹']).expanduser() for r in tools.registered() if r.get('文件夹')]
    except Exception:  # noqa: BLE001  项目登记读不了时只少几个项目
        pass
    return [_resolve(p) for p in out]


def _resolve(p):
    try:
        return Path(p).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return Path(p).expanduser().absolute()


def _inside(p, root, fold=False):
    """p 是 root 本身或在 root 里面。fold=True 不分大小写：Mac、Windows 的磁盘一般不分大小写，
    “不许碰”的判断都用它，防 CONFIG.JSON 这种写法绕过；“放行”的判断按原样比，宁可多拒。"""
    a, b = (unicodedata.normalize('NFC', str(x)) for x in (p, root))
    if fold or config.IS_WIN:             # Windows 的路径本来就不分大小写，放行也按不分大小写比
        a, b = a.casefold(), b.casefold()
    b = b.rstrip('/\\')
    return a == b or a.startswith(b + os.sep) or (os.altsep is not None and a.startswith(b + os.altsep))


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _is_secret(p):
    """存着密钥、口令的地方：本程序设置文件夹（任务记录、用量、日志除外）、本机 Claude Code 的设置文件夹，
    以及指向这些文件的硬链接。登记的项目文件夹再大（比如整个用户目录），这些也不放行。"""
    if _inside(p, _resolve(CLAUDE_DIR), True):
        return True
    if _inside(p, _resolve(config.DATA), True):
        ok = (_resolve(jobs.JOBS_DIR), _resolve(llm.USAGE), _resolve(log_file()))
        if not any(_inside(p, r) for r in ok):
            return True
    keys = (config.CONFIG, config.DATA / 'config.json', info_file(), CLAUDE_DIR / 'settings.json',
            CLAUDE_DIR / 'settings.local.json', CLAUDE_DIR.parent / '.claude.json')
    return p.is_file() and any(_same_file(p, k) for k in keys)


def allowed(raw):
    """检查页面给的路径，返回解析后的 Path；不在范围内抛 ApiError(403)。先 resolve 再判断，防 .. 和符号链接。"""
    s = clean_path(raw)
    if not s:
        raise ApiError(400, '没有给路径')
    # Windows：盘符以外的冒号是“隐藏数据流”写法；\\?\ \\.\ 开头的是设备路径，都不收
    if '\x00' in s or config.IS_WIN and (':' in s[2:] or s.replace('/', '\\').startswith(('\\\\?\\', '\\\\.\\'))):
        raise ApiError(400, '路径里有不能用的字符')
    p = Path(s).expanduser()
    if not p.is_absolute():
        raise ApiError(400, '路径要写完整（从盘符或 / 开始）')
    p = _resolve(p)
    if _is_secret(p):
        raise ApiError(403, '这个文件存着密钥或口令，不能打开')
    if any(_inside(p, r) for r in [_resolve(llm.USAGE), _resolve(log_file())] + _roots()):
        return p
    raise ApiError(403, '只能打开项目文件夹、流水线文件夹和任务记录里的东西')


def clean_path(x):
    """去掉首尾空白和引号（Windows“复制为路径”会带引号）。"""
    if not isinstance(x, str):
        return ''
    return x.strip().strip('"\'“”‘’').strip()


# ---- 接口函数：每个返回可转 JSON 的对象，出错抛 ApiError ----
def api_overview(q, body):
    with _overview_lock:          # 几个页面同时刷新时只跑一次检查
        if _overview_cache['v'] is None or time.time() - _overview_cache['t'] > 5:
            _overview_cache['v'] = {'项目': tools.overview()}
            _overview_cache['t'] = time.time()
        return _overview_cache['v']


def api_jobs_list(q, body):
    return MANAGER.list()


def api_job_get(jid):
    try:
        d = MANAGER.get(jid)
    except (OSError, ValueError):
        raise ApiError(500, '任务记录读不出来') from None
    if d is None:
        raise ApiError(404, '没有这个任务')
    d.pop('详情', None)                   # 英文堆栈不回给页面（任务.json 里有，管理员要看去任务记录里看）
    return d                              # 10 分钟没动静的记录，jobs.Manager.get 已经改成“中断”


AGAIN = ('新报告', '事实库', '补资料', '按新资料改')    # 能“接着做”的任务：做完的步骤有记录，不重做（别的任务重新开始就行）


def api_job_again(jid):
    """失败、中断、取消的任务接着做：照原来的参数另起一个任务，做完的步骤不重做。"""
    d = api_job_get(jid)
    kind, P = d.get('类别'), d.get('参数')
    if kind not in AGAIN or not isinstance(P, dict):
        raise ApiError(400, '这种任务不能接着做，请重新开始一个')
    if d.get('状态') not in ('失败', '中断', '已取消'):
        raise ApiError(400, '这个任务还在做' if d.get('状态') in ('进行', '等待') else '这个任务已经做完了，不用接着做')
    P = {k: v for k, v in P.items() if isinstance(k, str) and not k.startswith('_')}
    if kind in ('新报告', '补资料'):
        title, params, root = build_job(kind, P, d.get('项目'), resume=True)
    else:
        title, params, root = build_job(kind, P, d.get('项目'))
    job = MANAGER.start(kind, title, params, flows.get(kind), root)
    log('接着做 %s：起任务 %s %s %s' % (jid, job.id, kind, root or ''))
    return {'号': job.id}


def api_job_cancel(jid):
    api_job_get(jid)                      # 没有这个任务就回 404
    if MANAGER.cancel(jid):
        return {'取消': True, '说明': '已请求取消，正在进行的调用停下后结束'}
    return {'取消': False, '说明': '任务已经结束，或不是这个网页服务启动的（命令行跑的任务要在命令行窗口里按 Ctrl-C）'}


def _bool(v, default):
    if v is None or v == '':
        return default
    if isinstance(v, (bool, int)):
        return bool(v)
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on', '是')


def _text(v, name, limit=2000):
    if v is None:
        return ''
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        raise ApiError(400, '%s要是文字' % name)
    v = str(v).strip()
    if len(v) > limit:
        raise ApiError(400, '%s太长了（最多 %d 字）' % (name, limit))
    return v


def _list(v, name):
    """文字列表；给一段文字时按换行分开（一行一个）。"""
    if v is None or v == '':
        return []
    if isinstance(v, str):
        v = v.splitlines()
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ApiError(400, '%s要是文字列表' % name)
    return [x.strip() for x in v if x.strip()]


def _abs(raw, name):
    s = clean_path(raw)
    if not s:
        raise ApiError(400, '没有填%s' % name)
    p = Path(s).expanduser()
    if not p.is_absolute():
        raise ApiError(400, '%s要写完整路径（从盘符或 / 开始）：%s' % (name, s))
    return _resolve(p)


def _project_with_body(raw):
    p = _abs(raw, '项目文件夹')
    if not (p / '正文').is_dir():
        raise ApiError(400, '这个文件夹里没有“正文”文件夹，不是报告项目：%s' % p)
    return p


def _registered_roots():
    return {str(_resolve(Path(r['文件夹']).expanduser())) for r in tools.registered() if r.get('文件夹')}


def _sp():
    """补充资料的流程（用到时才导入：流程出问题不影响网页别的部分）。"""
    from .flows import supplement
    return supplement


def _materials(raw, root, resume=False):
    """页面给的资料路径（新报告、补资料共用）。resume：接着做时找不到的不管（上次已拷进项目、转成文字了）。"""
    given = []
    for x in _list(raw, '资料'):
        s = Path(clean_path(x)).expanduser()
        if not clean_path(x):
            continue
        if not s.is_absolute() or not s.exists():
            if resume:
                continue
            raise ApiError(400, '这份资料找不到，检查一下路径：%s' % clean_path(x))
        r = _resolve(s)
        # 资料会整个拷进项目：存着密钥的文件、包含设置文件夹的大文件夹（比如整个用户目录）都不行
        if _is_secret(r) or any(_inside(d, r, True) for d in (_resolve(config.DATA), _resolve(CLAUDE_DIR))):
            raise ApiError(400, '这份资料里有程序设置或密钥，不能当资料：%s' % clean_path(x))
        if _inside(root, r, True):
            raise ApiError(400, '资料文件夹不能包含项目文件夹本身：%s' % clean_path(x))
        given.append(str(r))
    return given


def build_job(kind, P, project, resume=False):
    """照 cli._job() 组织 (标题, 参数, 项目)，同时校验页面填的东西。
    resume：接着做上次没做完的新报告（文件夹是上次建好的，不要求是空的；资料已拷进去的，原件挪走了也不要紧）。"""
    if kind == '新报告':
        root = _abs(P.get('文件夹') or project, '新项目文件夹')
        if _inside(root, _resolve(config.ROOT), True) or _is_secret(root):
            raise ApiError(400, '新项目不能建在流水线文件夹或程序设置文件夹里面，换一个地方')
        if resume and not (root / '项目信息.md').is_file():
            resume = False                 # 上次连项目都没建成：照新报告从头做
        if root.exists() and not resume:
            if not root.is_dir():
                raise ApiError(400, '这个位置已经有一个同名文件：%s' % root)
            if [x for x in root.iterdir() if x.name not in JUNK + (jobs.LOCK_NAME,) and not x.name.startswith('._')]:
                raise ApiError(400, '这个文件夹里已经有东西了，新报告要用一个新的或空的文件夹：%s' % root)
        method = _text(P.get('方法'), '方法')
        if method not in ('类比法', '风险评估法'):
            raise ApiError(400, '方法只能选“类比法”或“风险评估法”')
        given = _materials(P.get('资料'), root, resume)
        params = {'文件夹': str(root), '方法': method, '资料': given,
                  **{k: _text(P.get(k), k, 200) for k in ('名称', '简称', '编号', '日期')},
                  '审稿': _bool(P.get('审稿'), True), '出稿': _bool(P.get('出稿'), True), '只写': _list(P.get('只写'), '只写')}
        if resume:
            params['接着做'] = True
            return '新报告（接着做）', params, str(root)
        return '新报告', params, str(root)
    if kind == '审稿':
        root = _project_with_body(project or P.get('项目'))
        ch = P.get('章')
        ch = ','.join(_list(ch, '章')) if isinstance(ch, list) else _text(ch, '章', 200)
        if '只看' in P and '修改' not in P:
            P['修改'] = not _bool(P.get('只看'), False)
        params = {'修改': _bool(P.get('修改'), True), '章': ch, '重点': _text(P.get('重点'), '重点'), '出稿': _bool(P.get('出稿'), False)}
        return '审稿' + (' 第%s章' % ch if ch else ''), params, str(root)
    if kind == '改稿':
        words = _text(P.get('意见'), '意见', 20000)
        if not words:
            raise ApiError(400, '修改意见不能空着')
        which = P.get('项目', project)
        if which in (None, '', '全部', ['全部'], []):
            which = '全部'
            # 和 flows/revise.py 的 targets() 一样只认有“正文”的项目；一个都没有时任务一开始就会失败，当场说清
            if not any((Path(r).expanduser() / '正文').is_dir() for r in _registered_roots()):
                raise ApiError(400, '还没有登记的项目可以改。先用“新报告”写完一份，再来改稿')
        else:
            known = _registered_roots()
            roots = []
            for x in _list(which, '项目'):
                p = _abs(x, '项目文件夹')
                if str(p) not in known:
                    raise ApiError(400, '这个文件夹不在项目登记里：%s' % p)
                if not (p / '正文').is_dir():
                    raise ApiError(400, '这个项目里没有“正文”文件夹：%s' % p)
                roots.append(str(p))
            which = roots or '全部'
        params = {'意见': words, '项目': which, '出稿': _bool(P.get('出稿'), True), '日期': _text(P.get('日期'), '日期', 40)}
        if isinstance(which, list):
            params['_锁'] = which        # 指定了项目就开始时一起加锁，有项目在忙当场回“正在跑”
        return '改稿：' + words[:20], params, None
    if kind == '补资料':
        root = _project_root(project or P.get('项目'))
        if not (root / '正文').is_dir():
            raise ApiError(400, '这个文件夹里没有“正文”文件夹，不是报告项目：%s' % root)
        given = _materials(P.get('资料'), root, resume)
        if not given and not (resume and (root / '资料' / _sp().DIR / '进行.json').is_file()):
            raise ApiError(400, '先选要补充的资料')
        return '补充资料', {'资料': given, '说明': _text(P.get('说明'), '说明', 500)}, str(root)
    if kind == '按新资料改':
        root = _project_root(project or P.get('项目'))
        data = _sp().pending(root)
        if not data:
            raise ApiError(400, '这个项目没有待确认的补充资料变化，先“补充资料”')
        want = P.get('采用')
        if want is not None:
            if not isinstance(want, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in want):
                raise ApiError(400, '采用要是编号列表')
            known = {x['编号'] for x in data['变化']}
            want = sorted(set(want) & known)
            if not want:
                raise ApiError(400, '一条变化都没勾上')
        return '按新资料改', {'采用': want, '出稿': _bool(P.get('出稿'), False)}, str(root)
    if kind in ('出稿', '事实库', '配图'):
        return kind, {}, str(_project_with_body(project or P.get('项目')))
    if kind == '提炼标准':
        return kind, {'只做': _list(P.get('只做'), '只做')}, None
    # 自测：不调模型；带项目时也加锁
    try:
        n = int(P.get('秒', 3))
    except (TypeError, ValueError):
        raise ApiError(400, '秒要是整数') from None
    if not 0 <= n <= 600:
        raise ApiError(400, '秒要在 0～600 之间')
    root = None
    if project or P.get('项目'):
        root = _abs(project or P.get('项目'), '项目文件夹')
        if not root.is_dir():
            raise ApiError(400, '这个文件夹不存在：%s' % root)
    return '自测', {'秒': n, '失败': _bool(P.get('失败'), False)}, str(root) if root else None


def api_job_start(q, body):
    kind = body.get('类别')
    if not isinstance(kind, str) or kind not in flows.KINDS:
        raise ApiError(400, '没有这种任务：%s（可选：%s）' % (kind, '、'.join(flows.KINDS)))
    P = body.get('参数') or {}
    if not isinstance(P, dict):
        raise ApiError(400, '参数要是一个 JSON 对象（{…}）')
    P = {k: v for k, v in P.items() if isinstance(k, str) and not k.startswith('_')}
    project = body.get('项目')
    if kind != '改稿' and project is not None and not isinstance(project, str):
        raise ApiError(400, '项目要是一个文件夹路径')
    title, params, root = build_job(kind, P, project)
    job = MANAGER.start(kind, title, params, flows.get(kind), root)
    log('起任务 %s %s %s' % (job.id, kind, root or ''))
    return {'号': job.id}


def api_settings_get(q, body):
    return config.public()


def api_settings_post(q, body):
    """只改页面给了的项；密钥空着＝不改。"""
    ch = {}
    if '地址' in body:
        v = _text(body['地址'], '地址', 500)
        if v and not re.match(r'^https?://\S+$', v):
            raise ApiError(400, '地址要空着，或以 http:// 或 https:// 开头')
        ch['地址'] = v
    if '密钥' in body:
        ch['密钥'] = _text(body['密钥'], '密钥', 500)
    if '模型' in body:
        v = _text(body['模型'], '模型', 100)
        if not re.match(r'^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*$', v):
            raise ApiError(400, '模型名称不对（例如 claude-opus-5-5）')
        ch['模型'] = v
    if '并发' in body:
        v = body['并发']
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 8:
            raise ApiError(400, '并发要是 1～8 的整数')
        ch['并发'] = v
    if '兜底' in body:
        ch['兜底'] = _bool(body['兜底'], True)
    if '缓存' in body:
        ch['缓存'] = _bool(body['缓存'], False)
    if '走系统代理' in body:
        ch['走系统代理'] = _bool(body['走系统代理'], False)
    if '思考摘要' in body:
        ch['思考摘要'] = _bool(body['思考摘要'], True)
    if '项目位置' in body:
        v = clean_path(body['项目位置'])
        if v and not Path(v).expanduser().is_absolute():
            raise ApiError(400, '项目位置要写完整路径（从盘符或 / 开始）')
        ch['项目位置'] = v
    if not ch:
        raise ApiError(400, '没有要改的设置')
    config.save(ch)
    return config.public()


def api_settings_test(q, body):
    ok, msg = llm.ping()
    return {'通': bool(ok), '说明': str(msg)}


def _num(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def api_usage(q, body):
    """用量.jsonl 近 N 天（含今天）按天、按项目、按任务类别汇总。"""
    try:
        days = int(q.get('天', 7))
    except ValueError:
        raise ApiError(400, '天要是整数') from None
    if not 1 <= days <= 3660:
        raise ApiError(400, '天要在 1～3660 之间')
    start = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
    groups = {'按天': {}, '按项目': {}, '按任务': {}}
    total = {'次数': 0, '输入': 0, '输出': 0, '估算美元': 0.0}
    p = Path(llm.USAGE)
    lines = p.read_text(encoding='utf-8', errors='replace').splitlines() if p.is_file() else []
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        day = str(r.get('时间', ''))[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', day) or day < start:
            continue
        keys = {'按天': day, '按项目': str(r.get('项目') or '（不属于项目）'), '按任务': str(r.get('任务') or '（未注明）')}
        for g, k in keys.items():
            a = groups[g].setdefault(k, {'次数': 0, '输入': 0, '输出': 0, '估算美元': 0.0})
            for t in (a, total) if g == '按天' else (a,):
                t['次数'] += 1
                t['输入'] += int(_num(r.get('输入')))
                t['输出'] += int(_num(r.get('输出')))
                t['估算美元'] += _num(r.get('估算美元'))
    name = {'按天': '日期', '按项目': '项目', '按任务': '任务'}
    out = {'天数': days, '起': start, '止': datetime.date.today().isoformat()}
    for g, d in groups.items():
        rows = [{name[g]: k, **v, '估算美元': round(v['估算美元'], 4)} for k, v in d.items()]
        rows.sort(key=(lambda x: x['日期']) if g == '按天' else (lambda x: -x['估算美元']))
        out[g] = rows
    out['合计'] = dict(total, 估算美元=round(total['估算美元'], 4))
    return out


def api_standards(q, body):
    """标准/ 下的 .md 文件：相对路径、字数、修改时间；完整路径给 /api/file 用。"""
    std = Path(config.STD)
    rows = []
    if std.is_dir():
        for p in sorted(std.rglob('*.md')):
            if not p.is_file() or any(x.startswith('.') for x in p.relative_to(std).parts):
                continue
            try:
                n = len(p.read_text(encoding='utf-8', errors='replace'))
                rows.append({'路径': p.relative_to(std).as_posix(), '完整路径': str(p), '字数': n, '修改': p.stat().st_mtime})
            except OSError:
                continue
    return {'文件夹': str(std), '文件': rows}


PACKAGE_EXT = {'.app', '.workflow', '.action', '.pkg', '.mpkg', '.bundle', '.plugin', '.prefpane', '.appex',
               '.framework', '.kext', '.terminal', '.scptd', '.saver', '.service', '.osax', '.component',
               '.qlgenerator', '.mdimporter', '.wdgt', '.xpc', '.systemextension', '.shortcut'}


def api_open(q, body):
    """用系统程序打开文件夹或文档；能运行的文件（.command .bat .sh .py .exe .app 这类）不打开。"""
    p = allowed(body.get('路径'))
    if not p.exists():
        raise ApiError(404, '找不到这个文件或文件夹：%s' % p)
    if p.is_dir():
        # Mac 的程序、插件都是带 Contents/Info.plist 的文件夹，打开就会运行或安装；扩展名改过也认得出
        if p.suffix.lower() in PACKAGE_EXT or (p / 'Contents' / 'Info.plist').exists() or (p / 'Contents' / 'MacOS').exists():
            raise ApiError(403, '这是一个程序包，不能从网页打开')
    elif p.suffix.lower() not in OPEN_EXT:
        raise ApiError(403, '这种文件（%s）不能从网页打开，只能打开文档、表格、图片和文件夹' % (p.suffix or '没有扩展名'))
    try:
        tools.open_path(p)
    except OSError as e:
        log('打开失败 %s：%s' % (p, e))
        raise ApiError(500, '打不开：这台电脑上可能没有能打开这种文件的程序') from None
    return {'打开': True, '路径': str(p)}


def api_file(q, body):
    """读文字文件（.md .txt .json .log，2 MB 以内）给页面看。"""
    p = allowed(q.get('路径'))
    if not p.is_file():
        raise ApiError(404, '找不到这个文件：%s' % p)
    if p.suffix.lower() not in READ_EXT:
        raise ApiError(403, '只能看 .md .txt .json .log 文件')
    if p.stat().st_size > MAX_FILE:
        raise ApiError(413, '文件太大了（超过 2 MB），请用“打开”在电脑上看')
    raw = p.read_bytes()
    for enc in ('utf-8-sig', 'gb18030'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode('utf-8', errors='replace')
    return {'路径': str(p), '文字': text}


# ---- 报告页：一份报告的各章、问题、改过的地方（只读） ----
_check_cache, _check_lock = {}, threading.Lock()
RE_BACKUP = re.compile(r'^正文_(\d{8})_(\d{6})_?(.*)$')


def _project_root(raw):
    """页面给的项目文件夹：要在项目登记里。"""
    p = allowed(raw)
    if str(p) not in _registered_roots():
        raise ApiError(403, '这个文件夹不在项目登记里')
    if not p.is_dir():
        raise ApiError(404, '项目文件夹找不到了：%s' % p)
    return p


def _check(root):
    """机器检查，5 秒内同一个项目只跑一次（报告页每几秒刷新）。"""
    with _check_lock:
        hit = _check_cache.get(str(root))
        if hit and time.time() - hit[0] < 5:
            return hit[1]
        c = tools.check(root)
        _check_cache[str(root)] = (time.time(), c)
        return c


def _title(text, fname):
    """章节标题：文件里第一个标题行，去掉 # 和 **。"""
    for ln in text.split('\n')[:40]:
        s = ln.strip()
        if re.match(r'^#{1,3} ', s):
            return s.lstrip('#').replace('**', '').strip()[:60]
    return fname.rsplit('.', 1)[0]


def _skeleton_text(method, fname):
    p = config.ROOT / 'templates' / ('项目骨架_风险评估法' if method == '风险评估法' else '项目骨架') / '正文' / fname
    return tools.read(p) if p.is_file() else None


def _row_of(root):
    for r in tools.registered():
        if r.get('文件夹') and str(_resolve(Path(r['文件夹']).expanduser())) == str(root):
            return r
    return {}


def _project_jobs(root, limit=8):
    out = []
    for j in MANAGER.list():
        if j.get('项目') and str(_resolve(Path(j['项目']))) == str(root):
            out.append(j)
            if len(out) >= limit:
                break
    return out


def api_project(q, body):
    """一份报告的概况：各章（写好没有、字数、问题）、PDF、正在做的事和最近做过的事。"""
    root = _project_root(q.get('路径'))
    row = _row_of(root)
    method = row.get('方法') or tools.method_of(root)
    chk = _check(root)
    rec_p = root / '资料' / '事实片段' / '接着做' / '写作记录.json'
    try:
        rec = json.loads(tools.read(rec_p)) if rec_p.is_file() else {}
    except ValueError:
        rec = {}
    rv_dir = root / '审查' / '审稿记录'
    names = tools.body_files(root)
    chapters = []
    for f in names:
        p = root / '正文' / f
        text = tools.read(p)
        d = chk['按文件'].get(f, {})
        rv = rv_dir / f
        chapters.append({'文件': f, '标题': _title(text, f), '字数': len(text), '修改': p.stat().st_mtime,
                         '写好': isinstance(rec.get(f), dict) or text != _skeleton_text(method, f),
                         '审过': rv.stat().st_mtime if rv.is_file() else None,
                         '错误': d.get('错误', []), '提示': d.get('提示', []), '待办': d.get('待办', [])})
    other = [{'文件': k, '错误': v.get('错误', []), '提示': v.get('提示', []), '待办': v.get('待办', [])}
             for k, v in chk['按文件'].items() if k not in names and (v.get('错误') or v.get('提示') or v.get('待办'))]
    pdfs = sorted((root / '输出').glob('*.pdf'), key=lambda x: x.stat().st_mtime) if (root / '输出').is_dir() else []
    pdf = {'路径': str(pdfs[-1]), '时间': pdfs[-1].stat().st_mtime, '页数': tools.pdf_pages(pdfs[-1])} if pdfs else None
    note = root / '输出' / '交稿说明.md'
    try:
        gap = gaps.collect(root, chk)
    except Exception:  # noqa: BLE001  清单出错不影响报告页
        log('资料欠缺清单出错 %s\n%s' % (root, traceback.format_exc()))
        gap = None
    gap_md = root / '输出' / '资料欠缺清单.md'
    return {'项目': row.get('项目') or tools.project_name(root), '方法': method, '文件夹': str(root),
            '定稿': row.get('定稿') == '是', '错误': chk['错误'], '提示': chk['提示'], '待办': chk['待办'],
            '章节': chapters, '其他文件': other, 'PDF': pdf, '交稿说明': str(note) if note.is_file() else None,
            '做过的事': _project_jobs(root),
            '资料欠缺': gap, '资料欠缺文件': str(gap_md) if gap_md.is_file() else None}


def _segments(a, b, limit=4000):
    """一处改动里逐字对比：[['=', 字], ['-', 删掉的], ['+', 加上的]]。太长的不细比。"""
    if len(a) > limit or len(b) > limit:
        return None
    import difflib
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            out.append(['=', a[i1:i2]])
        else:
            if i2 > i1:
                out.append(['-', a[i1:i2]])
            if j2 > j1:
                out.append(['+', b[j1:j2]])
    return out


def _diff(old, new, limit=200):
    import difflib
    a, b = old.split('\n'), new.split('\n')
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        o, n = '\n'.join(a[i1:i2]), '\n'.join(b[j1:j2])
        out.append({'行': j1 + 1, '原文': o, '改成': n, '逐字': _segments(o, n) if op == 'replace' else None})
    return out[:limit], len(out)


def _review_rows(p):
    """审稿记录表（行｜级别｜问题｜依据｜原文｜改成｜结果）拆成一条条。"""
    rows = []
    for ln in tools.read(p).split('\n'):
        s = ln.strip()
        if not s.startswith('|') or s.startswith('|---') or s.startswith('| 行 |'):
            continue
        cells = [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', s.strip('|'))]
        if len(cells) < 7 or cells[2] == '没有意见':
            continue
        r = dict(zip(('行', '级别', '问题', '依据', '原文', '改成', '结果'), cells[:7]))
        r['逐字'] = _segments(r['原文'], r['改成']) if r['原文'] and r['改成'] and r['原文'] != r['改成'] else None
        rows.append(r)
    return rows


def api_supplement(q, body):
    """补充资料待确认的变化（没有时“变化”为 null）。"""
    root = _project_root(q.get('路径'))
    return {'项目': str(root), '变化': _sp().pending(root)}


def api_supplement_discard(q, body):
    """丢掉待确认的变化（新资料已经放进项目了，不删）。"""
    root = _project_root(body.get('路径') or q.get('路径'))
    if (root / jobs.LOCK_NAME).exists() and time.time() - (root / jobs.LOCK_NAME).stat().st_mtime < jobs.STALE:
        raise ApiError(409, '这个项目正在跑任务，等它做完再丢')
    _sp().discard(root)
    return {'项目': str(root), '变化': None}


def api_project_changes(q, body):
    """改过的地方：和某一份备份（默认最近一份）比，正文哪几处变了；加上各章的审稿意见。"""
    root = _project_root(q.get('路径'))
    bdir = root / '备份'
    backs = []
    for d in sorted(bdir.iterdir(), key=lambda x: x.name, reverse=True) if bdir.is_dir() else []:
        m = RE_BACKUP.match(d.name)
        if d.is_dir() and m:
            try:
                t = datetime.datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S').timestamp()
            except ValueError:
                continue
            backs.append({'名': d.name, '时间': t, '说明': m.group(3).replace('_', ' ')})
    want = q.get('备份') or (backs[0]['名'] if backs else '')
    if want and want not in {b['名'] for b in backs}:
        raise ApiError(404, '没有这份备份')
    files = []
    if want:
        old_dir = bdir / want
        names = sorted(p.name for p in old_dir.glob('*.md'))     # 手工备份可能只存了改的那一章：只比备份里有的
        for f in names:
            if f.startswith(('_', '.')):
                continue
            o = tools.read(old_dir / f) if (old_dir / f).is_file() else ''
            n = tools.read(root / '正文' / f) if (root / '正文' / f).is_file() else ''
            if o == n:
                continue
            spots, total = _diff(o, n)
            files.append({'文件': f, '标题': _title(n or o, f), '处': spots, '共': total})
    rv_dir = root / '审查' / '审稿记录'
    reviews = []
    for p in sorted(rv_dir.glob('*.md')) if rv_dir.is_dir() else []:
        if p.name == '跨章一致.md':
            continue
        reviews.append({'文件': p.name, '时间': p.stat().st_mtime, '意见': _review_rows(p)})
    cross = rv_dir / '跨章一致.md'
    return {'备份': backs, '对比': want, '改动': files, '审稿': reviews,
            '跨章': tools.read(cross) if cross.is_file() else ''}


MAC_PICK = '''on run argv
  set k to item 1 of argv
  set m to (item 2 of argv) is "1"
  set t to item 3 of argv
  activate
  if k is "folder" then
    set r to choose folder with prompt t multiple selections allowed m
  else
    set r to choose file with prompt t multiple selections allowed m
  end if
  if class of r is not list then set r to {r}
  set out to ""
  repeat with x in r
    set out to out & POSIX path of x & linefeed
  end repeat
  return out
end run'''

WIN_PICK = r'''
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
if ($env:RP_KIND -eq 'folder') {
  $d = New-Object System.Windows.Forms.FolderBrowserDialog
  $d.Description = $env:RP_PROMPT
  $d.ShowNewFolderButton = $true
  if ($d.ShowDialog($owner) -eq 'OK') { $d.SelectedPath }
} else {
  $d = New-Object System.Windows.Forms.OpenFileDialog
  $d.Title = $env:RP_PROMPT
  $d.Multiselect = ($env:RP_MULTI -eq '1')
  if ($d.ShowDialog($owner) -eq 'OK') { $d.FileNames }
}
$owner.Dispose()
'''


def api_pick(q, body):
    """弹出系统的选择框，回选中的路径；用户取消回空列表。只在用户点按钮时调。"""
    kind = 'folder' if body.get('类型', '文件夹') == '文件夹' else 'file'
    if body.get('类型', '文件夹') not in ('文件夹', '文件'):
        raise ApiError(400, '类型只能是“文件夹”或“文件”')
    multi = '1' if _bool(body.get('多选'), False) else '0'
    prompt = _text(body.get('提示'), '提示', 200) or ('选择文件夹' if kind == 'folder' else '选择文件')
    if config.IS_MAC:
        cmd, env, flags = ['osascript', '-e', MAC_PICK, kind, multi, prompt], None, 0
    elif config.IS_WIN:
        enc = base64.b64encode(WIN_PICK.encode('utf-16-le')).decode('ascii')     # 整段脚本编码后传，不怕引号、换行
        cmd = ['powershell', '-NoProfile', '-STA', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', enc]
        env = dict(os.environ, RP_KIND=kind, RP_MULTI=multi, RP_PROMPT=prompt)   # 提示走环境变量，不拼进命令
        flags = 0x08000000                                                       # 不弹黑色命令窗口
    else:
        raise ApiError(501, '这台电脑弹不出选择框，请直接把路径粘贴进来')
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900, env=env, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {'路径': []}
    except OSError as e:
        raise ApiError(500, '弹不出选择框：%s' % e) from None
    out = r.stdout.decode('utf-8', errors='replace').lstrip('\ufeff')
    if r.returncode != 0:
        err = r.stderr.decode('utf-8', errors='replace')
        if '-128' in err or not err.strip():      # 用户点了取消
            return {'路径': []}
        log('选择框出错：%s' % err.strip())
        raise ApiError(500, '选择框出错了，可以直接把路径粘贴进来')
    paths = [x.strip() for x in out.splitlines() if x.strip()]
    return {'路径': [x.rstrip('/') if len(x) > 1 and not x.endswith(':\\') else x for x in paths]}


# ---- 请求处理 ----
GET_ROUTES = {'/api/overview': api_overview, '/api/jobs': api_jobs_list, '/api/settings': api_settings_get,
              '/api/usage': api_usage, '/api/standards': api_standards, '/api/file': api_file,
              '/api/project': api_project, '/api/project/changes': api_project_changes,
              '/api/project/supplement': api_supplement}
POST_ROUTES = {'/api/jobs': api_job_start, '/api/settings': api_settings_post, '/api/settings/test': api_settings_test,
               '/api/open': api_open, '/api/pick': api_pick, '/api/project/supplement/discard': api_supplement_discard}
JOB_PATH = re.compile(r'^/api/jobs/([^/]+)(/cancel|/again)?$')


def hide_token(text):
    """写日志前把网址里的口令（?t=…）换成 ***，口令不进 server.log。"""
    return re.sub(r'([?&]t=)[^&\s]*', r'\1***', str(text))


def _page(msg):
    return ('<!doctype html><meta charset="utf-8"><title>预评价编制工作台</title>'
            '<p style="font:16px sans-serif;margin:40px">%s</p>' % msg).encode('utf-8')


class Handler(BaseHTTPRequestHandler):
    server_version = 'report-pipeline'
    sys_version = ''
    timeout = 120                         # 发了一半就不动的连接，两分钟后断开，不一直占着线程

    def send_error(self, code, message=None, explain=None):
        # 不支持的方法、坏请求：也回一句中文 JSON，不回英文网页
        self.close_connection = True
        try:
            self._json(code, {'错误': '请求不对（%d），请从流水线页面操作' % code})
        except OSError:
            pass

    def log_message(self, fmt, *args):
        log('%s %s' % (self.address_string(), fmt % args))

    def log_error(self, fmt, *args):
        if not fmt.startswith('Request timed out'):   # 浏览器留着不用的连接到时断开，是正常的，不记
            self.log_message(fmt, *args)

    def log_request(self, code='-', size='-'):
        try:
            if int(code) >= 400:          # 正常的请求不记，页面每秒轮询会把日志写满
                self.log_message('"%s" %s', hide_token(self.requestline), code)
        except (TypeError, ValueError):
            pass

    # ---- 回应 ----
    def _send(self, code, body, ctype, headers=()):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode('utf-8'), 'application/json; charset=utf-8')

    # ---- 检查 ----
    def _host_ok(self):
        port = STATE['port']
        return self.headers.get('Host', '') in ('127.0.0.1:%d' % port, 'localhost:%d' % port)

    def _token_ok(self):
        want = STATE['token'].encode()
        got = [self.headers.get('X-Token', '')]
        for part in self.headers.get('Cookie', '').split(';'):
            k, _, v = part.strip().partition('=')
            if k == COOKIE:
                got.append(v)
        return bool(want) and any(g and secrets.compare_digest(g.encode('utf-8', 'replace'), want) for g in got)

    def _origin_ok(self):
        o = self.headers.get('Origin')
        port = STATE['port']
        return o is None or o in ('http://127.0.0.1:%d' % port, 'http://localhost:%d' % port)

    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise ApiError(400, '请求长度不对') from None
        if n < 0:
            raise ApiError(400, '请求长度不对')
        if n > MAX_BODY:
            # 先把发来的内容读掉再回，不然浏览器还在发就被断开，看不到这句说明；太大的直接断开
            left = n if n <= 16 * MAX_BODY else 0
            while left > 0:
                chunk = self.rfile.read(min(left, 65536))
                if not chunk:
                    break
                left -= len(chunk)
            self.close_connection = True
            raise ApiError(413, '内容太多了（超过 1 MB）')
        data = self.rfile.read(n) if n else b''
        if n == 0:
            return {}
        if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
            raise ApiError(415, '只收 JSON 格式的内容')
        try:
            body = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise ApiError(400, '内容不是有效的 JSON') from None
        if not isinstance(body, dict):
            raise ApiError(400, '内容要是一个 JSON 对象（{…}）')
        return body

    # ---- 分发 ----
    def do_GET(self):  # noqa: N802
        self._handle('GET')

    def do_POST(self):  # noqa: N802
        self._handle('POST')

    def _index(self, q):
        t = q.get('t')
        if t is not None and secrets.compare_digest(t.encode('utf-8', 'replace'), STATE['token'].encode()):
            cookie = '%s=%s; HttpOnly; SameSite=Strict; Path=/' % (COOKIE, STATE['token'])
            return self._send(303, b'', 'text/plain; charset=utf-8', [('Location', '/'), ('Set-Cookie', cookie)])
        if not self._token_ok():
            return self._send(403, _page(BAD_TOKEN), 'text/html; charset=utf-8')
        if t is not None:                 # 网址里的旧口令不对，但浏览器已经有对的 cookie
            return self._send(303, b'', 'text/plain; charset=utf-8', [('Location', '/')])
        body = WEB.read_bytes() if WEB.is_file() else _page(NO_PAGE)
        self._send(200, body, 'text/html; charset=utf-8', [('X-Frame-Options', 'DENY')])

    def _handle(self, method):
        try:
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path.rstrip('/') or '/'
            q = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
            if not self._host_ok():
                raise ApiError(403, '只能在本机用 127.0.0.1 这个地址打开')
            if path == '/api/ping':
                return self._json(200, {'ok': True, 'pid': os.getpid()})
            if path == '/' and method == 'GET':
                return self._index(q)
            if not self._token_ok():
                raise ApiError(403, BAD_TOKEN)
            # 127.0.0.1 别的端口上的网页和这里算“同站”，浏览器照样带 cookie；带了别的 Origin 一律拒绝（GET 也拒）。
            # 本页面自己的 GET 不带 Origin，不受影响
            if not self._origin_ok():
                raise ApiError(403, '请求不是从流水线页面发出的，不能执行')
            body = self._body() if method == 'POST' else {}
            m = JOB_PATH.match(path)
            if m:
                if not JOB_ID.match(m.group(1)):
                    raise ApiError(404, '没有这个任务')
                if method == 'GET' and not m.group(2):
                    out = api_job_get(m.group(1))
                elif method == 'POST' and m.group(2) == '/cancel':
                    out = api_job_cancel(m.group(1))
                elif method == 'POST' and m.group(2) == '/again':
                    out = api_job_again(m.group(1))
                else:
                    raise ApiError(405, '不支持这样访问')
            else:
                fn = (GET_ROUTES if method == 'GET' else POST_ROUTES).get(path)
                if fn is None:
                    raise ApiError(404, '没有这个接口')
                out = fn(q, body)
            self._json(200, out)
        except (BrokenPipeError, ConnectionResetError):
            pass                          # 页面关掉了，不用回
        except ApiError as e:
            self._json(e.code, {'错误': e.msg})
        except jobs.Busy as e:
            self._json(409, {'错误': str(e)})
        except Exception:  # noqa: BLE001  出错写日志，页面上只说一句中文
            log('出错 %s %s\n%s' % (method, hide_token(self.path), traceback.format_exc()))
            try:
                self._json(500, {'错误': '服务出错了，详情记在 server.log 里'})
            except OSError:
                pass


# ---- 起服务 ----
class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not config.IS_WIN   # Windows 上这个选项会让两个程序占同一个端口
    allow_reuse_port = False

    def handle_error(self, request, client_address):
        log('连接出错 %s\n%s' % (client_address, traceback.format_exc()))

    def server_bind(self):
        if config.IS_WIN and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        # 不调 HTTPServer.server_bind：它会查本机名字（getfqdn），有的 Windows 电脑网络设置不对时要卡好几秒
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = '127.0.0.1', self.server_address[1]


def make_server(port=8765):
    """绑定 127.0.0.1 的端口，被占用就往后找空的；port=0 由系统给一个空的（测试用）。生成口令。"""
    last = None
    for p in (range(port, port + 50) if port else [0]):
        try:
            srv = Server(('127.0.0.1', p), Handler)
        except OSError as e:
            last = e
            continue
        STATE.update(port=srv.server_address[1], token=secrets.token_urlsafe(24), server=srv)
        STATE['url'] = 'http://127.0.0.1:%d/?t=%s' % (STATE['port'], STATE['token'])
        return srv
    raise OSError('从 %d 往后 50 个端口都被占用了（%s）' % (port, last))


def write_info():
    """写 server.json：端口、口令、pid、网址。只有自己能读。"""
    p = info_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name('server.json.%d.tmp' % os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        json.dump({'端口': STATE['port'], '口令': STATE['token'], 'pid': os.getpid(), '网址': STATE['url']}, fh,
                  ensure_ascii=False)
    if not config.IS_WIN:
        os.chmod(tmp, 0o600)
    for i in range(20):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:           # Windows：启动文件正好在读 server.json 时换不了，等一下再换
            if i == 19:
                raise
            time.sleep(0.1)


def remove_info():
    """退出时删 server.json；里面的 pid 是别的服务的就不动。"""
    p = info_file()
    try:
        if json.loads(p.read_text(encoding='utf-8')).get('pid') == os.getpid():
            p.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def running():
    """已有服务在跑（server.json 里的端口一秒内回 ok、pid 对得上）就返回它的网址，否则 None。"""
    try:
        info = json.loads(info_file().read_text(encoding='utf-8'))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机地址不走代理
        with opener.open('http://127.0.0.1:%d/api/ping' % int(info['端口']), timeout=1) as r:
            d = json.loads(r.read().decode('utf-8'))
        if d.get('ok') is True and d.get('pid') == info.get('pid'):
            return info.get('网址')
    except Exception:  # noqa: BLE001  文件没有、端口没人、回的不对，都当没在跑
        pass
    return None


def _fix_streams():
    """pythonw 启动时没有控制台（sys.stdout 是 None）：出错信息写进 server.log；带口令的网址不写进日志。"""
    if sys.stderr is None:
        sys.stderr = log_file().open('a', encoding='utf-8', buffering=1)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w', encoding='utf-8')
    for s in (sys.stdout, sys.stderr):    # 输出到不支持中文的地方（英文 Windows 的重定向）时不因为一个字报错退出
        try:
            s.reconfigure(errors='replace')
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m app.server', description='评价报告流水线网页服务')
    ap.add_argument('--port', type=int, default=8765, help='端口，被占用就往后找（0＝随便给一个）')
    ap.add_argument('--open', action='store_true', help='起来后打开浏览器')
    ap.add_argument('--no-reuse', action='store_true', help='不管已有的服务，另起一个（测试用）')
    ap.add_argument('--check', action='store_true', help='只看有没有服务在跑：在跑打印网址、返回 0，没有返回 1')
    a = ap.parse_args(argv)
    _fix_streams()
    if a.check:
        url = running()
        print(url or '流水线没有在运行', flush=True)
        return 0 if url else 1
    if not a.no_reuse:
        url = running()
        if url:
            print('流水线已经在运行：%s' % url, flush=True)
            if a.open:
                webbrowser.open(url)
            return 0
    try:
        srv = make_server(a.port)
    except OSError as e:
        log('启动失败：%s' % e)
        print('启动失败：%s' % e, flush=True)
        return 1
    write_info()
    log('启动：端口 %d，pid %d' % (STATE['port'], os.getpid()))
    print('流水线已启动：%s' % STATE['url'], flush=True)
    if threading.current_thread() is threading.main_thread():
        def stop(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        if hasattr(signal, 'SIGBREAK'):
            signal.signal(signal.SIGBREAK, stop)
    if a.open:
        threading.Thread(target=webbrowser.open, args=(STATE['url'],), daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        remove_info()
        log('退出：端口 %d' % STATE['port'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
