"""调用模型：流式输出、结构化结果、用量记账、每次调用的输入输出存档。

一次调用只发一条用户消息（资料在前、任务在后），不走多轮对话：改错时把当前文件和错误重新发一遍，
这样每一步的输入都看得见，出了问题能查到是哪一步。
"""
import datetime
import json
import re
import threading
import time
import urllib.request

import anthropic

try:
    import httpx2 as _httpx          # 接口库用的网络库：回答读到一半断线时，报它自己的错，不报接口库的
except ImportError:                   # pragma: no cover  老版本接口库用 httpx
    import httpx as _httpx

from . import config

# 官方价（美元／百万 token：输入、输出）。中转按自己的价收，这里只作估算
PRICE = {'claude-opus-5-5': (4.0, 20.0), 'claude-fable-5-1': (10.0, 50.0), 'claude-sonnet-5-5': (2.0, 10.0)}
FALLBACK_BETA = 'server-side-fallback-2026-07-01'
USAGE = config.DATA / '用量.jsonl'

_lock = threading.Lock()
_sem = {'n': 0, 'sem': None}
CUT = '网络那头中途断了，没收到完整回答'
RETRY_WAIT = (20, 60)  # 没成时等几秒再重来（第 2 次、第 3 次之前）：网络那头断一下，多半一会儿就好


class Cancelled(Exception):
    pass


class CallError(Exception):
    """调用没成。retry＝False 的（没有密钥、拒答、输出太长、请求本身不对）再来一遍也一样，不重来。"""

    def __init__(self, msg='', retry=True):
        super().__init__(msg)
        self.retry = retry


def _semaphore():
    n = max(1, int(config.load().get('并发') or 4))
    with _lock:
        if _sem['n'] != n:
            _sem['n'], _sem['sem'] = n, threading.Semaphore(n)
        return _sem['sem']


def client(cfg=None):
    cfg = cfg or config.load()
    base, key, _ = config.credentials(cfg)
    if not key:
        raise CallError('没有密钥：在“设置”里填中转地址和密钥', retry=False)
    kw = {}
    if not cfg.get('走系统代理'):
        # 默认不照这台电脑的系统代理（代理软件）走：接口库会自动照它走，代理线路一断程序就连不上中转，对话却照常
        # （11-01 夜里断过半小时）。DefaultHttpxClient 不管 trust_env 都读系统代理，只能把各协议的代理挂载换成空的（空的＝直连）
        kw['http_client'] = anthropic.DefaultHttpxClient(mounts=dict.fromkeys(('http://', 'https://', 'all://')))
    return anthropic.Anthropic(base_url=base or None, auth_token=key, api_key=None, max_retries=3,
                               timeout=anthropic.Timeout(1800.0, connect=30.0), **kw)


def _proxy_hint(cfg):
    """连不上时补一句代理的情况：这台电脑设了代理、程序却没走的，多半要在设置里勾上“走系统代理”。"""
    if cfg.get('走系统代理'):
        return '（程序正照这台电脑的系统代理走；代理软件没开或线路断了也会这样，可以在设置里取消“走系统代理”再试）'
    if any(urllib.request.getproxies().get(k) for k in ('http', 'https', 'all')):
        return '（这台电脑设了系统代理，程序现在直连、不走它；这台电脑要靠代理才能上网的，在设置里勾上“走系统代理”再试）'
    return ''


def cost(model, tin, tout):
    pin, pout = PRICE.get(model, PRICE['claude-opus-5-5'])
    return round(tin / 1e6 * pin + tout / 1e6 * pout, 4)


def _record(row):
    with _lock:
        with USAGE.open('a', encoding='utf-8') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')


def _archive(folder, seq, label, system, content, out, meta):
    if not folder:
        return
    folder.mkdir(parents=True, exist_ok=True)
    name = '%03d_%s.md' % (seq, re.sub(r'[\\/:*?"<>|\s]+', '_', label.replace('.md', ''))[:40])
    body = ['# %s' % label, '', '```json', json.dumps(meta, ensure_ascii=False, indent=1), '```', '',
            '## 系统提示', '', system, '', '## 输入', '', content, '', '## 输出', '', out]
    (folder / name).write_text('\n'.join(body), encoding='utf-8')


def _base(cfg):
    """call 和 ping 共用的参数：测连接时照真调用的发，中转不认哪个参数，测连接就报出来，不等任务开跑才每步都错。"""
    kw = {'model': cfg['模型']}
    if cfg.get('思考摘要', True):
        # 思考过程照常发回（不显示、不存，也不多花钱）：模型想得久时连接上一直有字在走。
        # 田林核对事实库两次都断在出第一个字之前（想了 100～160 秒）
        kw['thinking'] = {'type': 'adaptive', 'display': 'summarized'}
    if cfg.get('兜底'):
        kw['betas'] = [FALLBACK_BETA]
        kw['fallbacks'] = 'default'
    if cfg.get('缓存'):
        kw['cache_control'] = {'type': 'ephemeral'}
    return kw


def _api(cl, cfg):
    return cl.beta.messages if cfg.get('兜底') else cl.messages


def ping():
    """很短的一次调用，测中转通不通。返回 (通不通, 说明)。"""
    cfg = config.load()
    t = time.time()
    cl = None
    try:
        cl = client(cfg)
        with _api(cl, cfg).stream(**dict(_base(cfg), max_tokens=2000, output_config={'effort': 'low'},
                                         messages=[{'role': 'user', 'content': '只回答两个字：通了'}])) as s:
            m = s.get_final_message()
    except CallError as e:
        return False, str(e)
    except anthropic.APIStatusError as e:
        return False, '中转返回 %s：%s' % (e.status_code, str(e.message)[:200])
    except anthropic.APIConnectionError as e:
        return False, '连不上中转：%s%s' % (str(e)[:200], _proxy_hint(cfg))
    except (_httpx.TransportError, AssertionError):
        return False, CUT
    finally:
        if cl is not None:
            cl.close()        # 自己传的连接不会自动关
    text = ''.join(b.text for b in m.content if b.type == 'text')
    return True, '通了（%s，%.1f 秒，回答“%s”）' % (cfg['模型'], time.time() - t, text.strip()[:20])


def _keyed(schema):
    """结构化输出按键名的码位顺序生成，不按 schema 里写的顺序。中文键名的码位顺序和该写的先后对不上
    （审稿意见会先写“依据”“原文”“新文”，最后才写“问题”；提炼标准时“标题”里装了日期），
    所以发出去前给每个对象的键按原来的顺序加两位序号，拿回来用 _unkeyed 去掉。"""
    if isinstance(schema, list):
        return [_keyed(x) for x in schema]
    if not isinstance(schema, dict):
        return schema
    props = schema.get('properties') if isinstance(schema.get('properties'), dict) else None
    idx = {n: i for i, n in enumerate(props, 1)} if props else {}
    out = {}
    for k, v in schema.items():
        if k == 'properties' and props:
            out[k] = {'%02d%s' % (idx[n], n): _keyed(s) for n, s in props.items()}
        elif k == 'required' and props and isinstance(v, list):
            out[k] = ['%02d%s' % (idx[n], n) for n in v if n in idx]
        else:
            out[k] = _keyed(v)
    return out


def _unkeyed(x):
    """去掉 _keyed 加的两位序号。schema 里的对象都不许多出别的键，所以拿回来的每个键都带序号。"""
    if isinstance(x, list):
        return [_unkeyed(v) for v in x]
    if isinstance(x, dict):
        return {(k[2:] if len(k) > 2 and k[:2].isdigit() else k): _unkeyed(v) for k, v in x.items()}
    return x


def call(job, label, system, content, *, effort='high', schema=None, max_tokens=128000, phase=''):
    """调一次模型。schema 不为空时返回 dict（结构化输出），否则返回文字。
    job 提供：cancelled、step()、project、archive、kind；用量记进 用量.jsonl，输入输出存进 job.archive。"""
    cfg = config.load()
    model = cfg['模型']
    step = job.step(label, phase)
    sem = _semaphore()
    step.update(状态='排队')
    with sem:
        if job.cancelled:
            step.update(状态='已取消')
            raise Cancelled()
        step.update(状态='进行', 开始=time.time())
        oc = {'effort': effort}
        if schema:
            oc['format'] = {'type': 'json_schema', 'schema': _keyed(schema)}
        kw = dict(_base(cfg), max_tokens=max_tokens, system=system, output_config=oc,
                  messages=[{'role': 'user', 'content': content}])
        t0 = time.time()
        chars = 0
        cl = None
        try:
            cl = client(cfg)
            with _api(cl, cfg).stream(**kw) as s:
                last = 0
                for ev in s:
                    if job.cancelled:
                        raise Cancelled()
                    if ev.type == 'text':
                        chars += len(ev.text)
                        if time.time() - last > 1:
                            step.update(字数=chars)
                            last = time.time()
                try:
                    m = s.get_final_message()
                except AssertionError:          # 连回答的开头都没收到，流就结束了
                    m = None
        except Cancelled:
            step.update(状态='已取消')
            raise
        except anthropic.APIStatusError as e:
            step.update(状态='失败', 说明='中转返回 %s' % e.status_code)
            # 请求本身不对、密钥不对、余额不够、模型名或地址不对、太长的，再来也一样；
            # 别的（忙、5xx、回答中途发来的出错消息）等一会儿再来
            raise CallError('%s：中转返回 %s %s' % (label, e.status_code, str(e.message)[:200]),
                            retry=e.status_code not in (400, 401, 402, 403, 404, 413))
        except anthropic.APIConnectionError as e:
            step.update(状态='失败', 说明='连不上中转')
            raise CallError('%s：连不上中转（%s）%s' % (label, str(e)[:120], _proxy_hint(cfg)))
        except _httpx.TransportError as e:      # 回答读到一半连接断了（接口库不替它换成自己的错）
            step.update(状态='失败', 说明='中途断了')
            raise CallError('%s：%s（收到 %d 字；%s）' % (label, CUT, chars, type(e).__name__))
        finally:
            if cl is not None:
                cl.close()
    if m is None:
        step.update(状态='失败', 说明='中途断了')
        raise CallError('%s：%s（一个字也没收到）' % (label, CUT))
    u = m.usage
    tin = (u.input_tokens or 0) + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0)
    tout = u.output_tokens or 0
    secs = round(time.time() - t0, 1)
    text = ''.join(b.text for b in m.content if b.type == 'text')
    # 实际回答的模型（兜底换了模型时和设置里的不一样）、三种输入分开记：试审时有一次输入记得偏多，存档分不清是哪种
    real = getattr(m, 'model', None)
    real = real if isinstance(real, str) and real else model
    usd = cost(real if real in PRICE else model, tin, tout)
    split = {'缓存读': u.cache_read_input_tokens or 0, '写缓存': u.cache_creation_input_tokens or 0}
    meta = dict({'模型': model, '实际模型': real, '用力': effort, '停止原因': m.stop_reason, '输入': tin, '输出': tout,
                 '秒': secs}, **split)
    _record(dict({'时间': datetime.datetime.now().isoformat(timespec='seconds'), '项目': job.project_name or '',
                  '任务': job.kind, '任务号': job.id, '步骤': label, '模型': model, '实际模型': real, '输入': tin,
                  '输出': tout, '秒': secs, '估算美元': usd}, **split))
    _archive(job.archive, job.next_seq(), label, system, content, text, meta)
    job.add_usage(tin, tout, usd)
    step.update(字数=len(text), 输入=tin, 输出=tout, 秒=secs)
    if m.stop_reason is None:
        # 没收到结束标记，流就正常关了：接口库不报错，回答只有半截。11-01 田林核对事实库两次这样（0 字），
        # 原先当成“结果不是合法 JSON”；不带格式的调用（写章、摘事实）会把半截当成全文用下去
        step.update(状态='失败', 说明='中途断了')
        raise CallError('%s：%s（收到 %d 字）' % (label, CUT, len(text)))
    if m.stop_reason == 'refusal':
        cat = getattr(getattr(m, 'stop_details', None), 'category', None)
        step.update(状态='失败', 说明='模型拒答')
        raise CallError('%s：模型拒答（类别 %s）' % (label, cat), retry=False)
    if m.stop_reason in ('max_tokens', 'model_context_window_exceeded'):
        step.update(状态='失败', 说明='输出太长被截断')
        raise CallError('%s：输出太长被截断' % label, retry=False)
    if schema:
        try:
            data = json.loads(text)
        except ValueError:
            step.update(状态='失败', 说明='结果不是 JSON')
            raise CallError('%s：结果不是合法 JSON' % label)
        step.update(状态='完成')
        return _unkeyed(data)
    step.update(状态='完成')
    return text


def call_retry(job, label, system, content, tries=3, **kw):
    """中转中途断流、连不上或返回 5xx（SDK 已重试 3 次）时，等一会儿整次调用再来一遍，最多 tries 次。
    取消、拒答和再来也一样的（retry＝False）不重来。"""
    for i in range(tries):
        try:
            return call(job, label if i == 0 else '%s（重来%s）' % (label, '' if i == 1 else ' %d' % i), system, content, **kw)
        except CallError as e:
            if '拒答' in str(e) or not e.retry or i == tries - 1:
                raise
            wait = RETRY_WAIT[min(i, len(RETRY_WAIT) - 1)] if RETRY_WAIT else 0
            job.info('%s 没成，%d 秒后重来：%s' % (label, wait, str(e)[:100]))
            end = time.time() + wait
            while time.time() < end:
                if job.cancelled:
                    raise Cancelled() from None
                time.sleep(min(1.0, max(0.0, end - time.time())))
    return None


def sections(text, names):
    """按 <<<名字>>> 标记切分模型输出。返回 {名字: 内容}；没有标记时整段归第一个名字。"""
    out = {}
    pat = re.compile(r'^<<<\s*(%s)\s*>>>\s*$' % '|'.join(re.escape(n) for n in names), re.M)
    hits = list(pat.finditer(text))
    if not hits:
        return {names[0]: text.strip()}
    head = text[:hits[0].start()].strip('\n')
    if head.strip() and names[0] not in {h.group(1) for h in hits}:
        out[names[0]] = head     # 模型漏写了第一个标记：标记前的内容就是第一段，不能丢
    for i, h in enumerate(hits):
        end = hits[i + 1].start() if i + 1 < len(hits) else len(text)
        out[h.group(1)] = text[h.end():end].strip('\n')
    return out
