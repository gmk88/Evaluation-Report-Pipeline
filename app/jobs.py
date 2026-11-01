"""任务：每个任务一个线程。步骤、日志、用量存到 任务/<号>/任务.json，网页轮询看进度，命令行跑的任务网页上也看得到。

同一个项目同时只能有一个任务；改标准的任务（改稿、提炼标准）同时只能有一个。
锁是项目文件夹里的 .流水线运行中 文件，带心跳时间，程序崩了 10 分钟后自动作废。
"""
import datetime
import json
import os
import threading
import time
import traceback
import uuid
from pathlib import Path

from . import config, llm

JOBS_DIR = config.DATA / '任务'
LOCK_NAME = '.流水线运行中'
STALE = 600
DONE = ('完成', '失败', '已取消', '中断')


class Busy(Exception):
    pass


class Step:
    def __init__(self, job, sid, name, phase):
        self.job = job
        self.d = {'号': sid, '名称': name, '阶段': phase, '状态': '排队'}

    def update(self, **kw):
        with self.job.lock:
            if kw.get('状态') in ('完成', '失败', '已取消', '跳过') and '结束' not in self.d:
                kw['结束'] = time.time()
            self.d.update(kw)
        self.job.touch()


class Job:
    def __init__(self, kind, title, params, project=None):
        self.id = datetime.datetime.now().strftime('%m%d-%H%M%S-') + uuid.uuid4().hex[:4]
        self.kind, self.title, self.params = kind, title, params
        self.project = str(Path(project).expanduser().resolve()) if project else ''
        self.project_name = ''
        self.dir = JOBS_DIR / self.id
        self.archive = self.dir / '调用'
        self.state, self.error, self.trace, self.result = '等待', '', '', None
        self.steps, self.logs = [], []
        self.usage = {'输入': 0, '输出': 0, '美元': 0.0, '次数': 0}
        self.created, self.started, self.ended = time.time(), None, None
        self.phase = ''
        self.cancelled = False
        self.lock = threading.RLock()
        self._seq = 0
        self._saved = 0.0
        self._locks = []

    # ---- 给流程用 ----
    def step(self, name, phase=''):
        with self.lock:
            s = Step(self, len(self.steps) + 1, name, phase or self.phase)
            self.steps.append(s)
        self.touch()
        return s

    def stage(self, phase):
        with self.lock:
            self.phase = phase
        self.info('—— %s' % phase)

    def info(self, text):
        with self.lock:
            self.logs.append((time.time(), str(text)))
        self.touch()
        if self.params.get('_回显'):
            print(time.strftime('%H:%M:%S ') + str(text), flush=True)

    def add_usage(self, tin, tout, usd):
        with self.lock:
            self.usage['输入'] += tin
            self.usage['输出'] += tout
            self.usage['美元'] = round(self.usage['美元'] + usd, 4)
            self.usage['次数'] += 1

    def next_seq(self):
        with self.lock:
            self._seq += 1
            return self._seq

    def check_cancel(self):
        if self.cancelled:
            raise llm.Cancelled()

    # ---- 存盘 ----
    def to_dict(self, brief=False):
        with self.lock:
            d = {'号': self.id, '类别': self.kind, '标题': self.title, '项目': self.project, '项目名': self.project_name,
                 '状态': self.state, '阶段': self.phase, '错误': self.error, '用量': dict(self.usage),
                 '建立': self.created, '开始': self.started, '结束': self.ended, '结果': self.result,
                 '记录': str(self.dir)}
            if not brief:
                d.update(参数={k: v for k, v in self.params.items() if not k.startswith('_')},
                         步骤=[dict(s.d) for s in self.steps], 日志=list(self.logs[-400:]), 详情=self.trace)
            else:
                d['步骤数'] = len(self.steps)
                d['进行中'] = [s.d['名称'] for s in self.steps if s.d['状态'] == '进行'][:6]
        return d

    def touch(self):
        """有动静时存盘（最多一秒一次）、给锁续时间。存盘出错不能打断任务。"""
        now = time.time()
        if now - self._saved > 1.0:
            try:
                self.save()
            except OSError:
                pass
        self.beat(now)

    def beat(self, now=None):
        now = now or time.time()
        for p in list(self._locks):
            try:
                if now - p.stat().st_mtime > 30:
                    os.utime(p)
            except OSError:
                pass

    def save(self):
        with self.lock:      # 几个线程同时存盘时排队，免得互相覆盖临时文件
            self._saved = time.time()
            self.dir.mkdir(parents=True, exist_ok=True)
            tmp = self.dir / ('任务.json.%d.tmp' % threading.get_ident())
            tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1, default=str), encoding='utf-8')
            os.replace(tmp, self.dir / '任务.json')


def _lock_paths(job):
    """要锁的文件：任务的项目、参数 _锁 里列的项目（改稿一次改几个项目），改标准的任务再锁 标准/。"""
    paths = []
    if job.project:
        paths.append(Path(job.project) / LOCK_NAME)
    for p in job.params.get('_锁', []):
        q = Path(p).resolve() / LOCK_NAME
        if q not in paths:
            paths.append(q)
    if job.kind in ('改稿', '提炼标准'):
        paths.append(config.STD / LOCK_NAME)
    return paths


def _acquire(job):
    got = []
    for p in _lock_paths(job):
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and time.time() - p.stat().st_mtime < STALE:
            try:
                who = json.loads(p.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                who = {}
            for q in got:
                q.unlink(missing_ok=True)
            where = '这个项目' if p.parent != config.STD else '标准'
            raise Busy('%s正在跑别的任务（%s %s），等它做完再来' % (where, who.get('类别', ''), who.get('号', '')))
        p.write_text(json.dumps({'号': job.id, '类别': job.kind, 'pid': os.getpid()}, ensure_ascii=False), encoding='utf-8')
        got.append(p)
    job._locks = got


def lock_more(job, roots):
    """任务开始后才知道要动哪些项目的（改稿“全部”），逐个加锁；有项目正被别的任务占着就报错停下。"""
    mine = {str(p) for p in job._locks}
    want = [Path(r).resolve() / LOCK_NAME for r in roots]
    busy = [p.parent for p in want if str(p) not in mine and p.exists() and time.time() - p.stat().st_mtime < STALE]
    if busy:
        from . import tools
        raise Busy('这些项目正在跑别的任务，等它们做完再改：%s' % '、'.join(tools.project_name(b) for b in busy))
    for p in want:
        if str(p) not in mine:
            p.write_text(json.dumps({'号': job.id, '类别': job.kind, 'pid': os.getpid()}, ensure_ascii=False), encoding='utf-8')
            job._locks.append(p)


def _release(job):
    for p in job._locks:
        try:
            if json.loads(p.read_text(encoding='utf-8')).get('号') == job.id:
                p.unlink()
        except (OSError, ValueError):
            pass
    job._locks = []


def run(job, fn, locked=False):
    """在当前线程里跑完一个任务（命令行直接调，网页由 Manager 开线程调）。locked：Manager.start 已经加好锁。"""
    with job.lock:
        job.state, job.started = '进行', time.time()
    if not locked:
        try:
            _acquire(job)
        except Busy as e:
            job.state, job.error, job.ended = '失败', str(e), time.time()
            job.save()
            return job
    def heartbeat():
        # 模型长时间思考、出稿导出 PDF 时没有别的动静；每 20 秒存一次盘、续一次锁，免得被当成中断
        while job.ended is None:
            time.sleep(20)
            if job.ended is None:
                try:
                    job.save()
                except OSError:
                    pass
                job.beat()

    threading.Thread(target=heartbeat, daemon=True, name='beat-' + job.id).start()
    try:
        job.save()
        job.result = fn(job)
        job.state = '已取消' if job.cancelled else '完成'
    except llm.Cancelled:
        job.state = '已取消'
    except Exception as e:  # noqa: BLE001  任务出错要记下来给人看，不能让线程悄悄死掉
        # 流程自己报的错（RuntimeError、调用出错、项目正忙）本来就是中文；别的是程序内部出错，前面补一句中文
        msg = str(e) if isinstance(e, (RuntimeError, Busy, llm.CallError)) and str(e) else \
            '程序出错（%s：%s），详情见任务记录' % (type(e).__name__, str(e)[:200])
        job.state, job.error, job.trace = '失败', msg, traceback.format_exc()
        job.info('出错：%s' % msg)
    finally:
        _release(job)
        job.ended = time.time()
        for s in job.steps:
            if s.d['状态'] in ('排队', '进行'):
                s.d.update(状态='已取消' if job.cancelled else '中断', 结束=time.time())
        job.save()
    return job


class Manager:
    def __init__(self):
        self.jobs = {}
        self.lock = threading.Lock()

    def start(self, kind, title, params, fn, project=None):
        job = Job(kind, title, params, project)
        if project:
            from . import tools
            job.project_name = tools.project_name(project)
        with self.lock:      # 查锁、加锁一起做：同一项目两个请求同时到，第二个当场报“正在跑”
            _acquire(job)
            self.jobs[job.id] = job
        try:
            job.save()
            threading.Thread(target=run, args=(job, fn, True), daemon=True, name='job-' + job.id).start()
        except Exception:
            _release(job)
            with self.lock:
                self.jobs.pop(job.id, None)
            raise
        return job

    def get(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
        if j:
            return j.to_dict()
        p = JOBS_DIR / jid / '任务.json'
        if p.exists():
            x = json.loads(p.read_text(encoding='utf-8'))
            if x.get('状态') in ('进行', '等待') and time.time() - p.stat().st_mtime > STALE:
                x['状态'] = '中断'      # 跑它的程序已经退出（命令行窗口关了、电脑重启了），心跳停了
            return x
        return None

    def cancel(self, jid):
        with self.lock:
            j = self.jobs.get(jid)
        if not j or j.state in DONE:
            return False
        j.cancelled = True
        j.info('已请求取消，正在进行的调用停下后结束')
        return True

    def list(self, limit=60):
        out = {}
        if JOBS_DIR.is_dir():
            for d in sorted(JOBS_DIR.iterdir(), reverse=True)[:limit]:
                p = d / '任务.json'
                if p.exists():
                    try:
                        x = json.loads(p.read_text(encoding='utf-8'))
                    except ValueError:
                        continue
                    if x.get('状态') in ('进行', '等待') and d.name not in self.jobs and time.time() - p.stat().st_mtime > STALE:
                        x['状态'] = '中断'
                    steps = x.pop('步骤', [])
                    x['步骤数'] = len(steps)
                    # 命令行起的任务也列出正在做哪几步（和 Job.to_dict(brief=True) 一样）
                    x['进行中'] = [] if x.get('状态') in DONE else [s.get('名称') for s in steps if s.get('状态') == '进行'][:6]
                    for k in ('日志', '详情', '参数'):
                        x.pop(k, None)
                    out[d.name] = x
        with self.lock:
            for jid, j in self.jobs.items():
                out[jid] = j.to_dict(brief=True)
        return sorted(out.values(), key=lambda x: x.get('建立') or 0, reverse=True)[:limit]
