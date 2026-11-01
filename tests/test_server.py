"""网页服务自测：只用自带库，不调模型、不花钱、不弹窗口。

在流水线文件夹里运行：python -m unittest tests/test_server.py -v
设置、任务记录、用量、项目登记都指到临时文件夹，测完删掉；打开文件的函数换成假的，不会真打开东西。
"""
import datetime
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, jobs, llm, server, tools  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix='rp_server_test_')).resolve()
DATA = TMP / 'data'
PROJ = TMP / '项目甲'
EMPTY = TMP / '空文件夹'
FAKE_KEY = 'sk-fake-test-key-0000-do-not-use'
_saved = {}
OPENED = []


def _patch():
    DATA.mkdir(parents=True)
    (PROJ / '正文').mkdir(parents=True)
    (PROJ / '正文' / '01_总论.md').write_text('# 1 总论\n\n测试用的正文。\n', encoding='utf-8')
    EMPTY.mkdir()
    reg = TMP / '项目登记.md'
    reg.write_text('| 项目 | 方法 | 文件夹 | 样板 | 定稿 |\n|---|---|---|---|---|\n'
                   '| 测试项目甲 | 风险评估法 | %s | | |\n' % PROJ, encoding='utf-8')
    for mod, name, val in ((config, 'DATA', DATA), (config, 'CONFIG', DATA / 'config.json'),
                           (config, 'REGISTER', reg), (jobs, 'JOBS_DIR', DATA / '任务'),
                           (llm, 'USAGE', DATA / '用量.jsonl'), (tools, 'open_path', OPENED.append)):
        _saved[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)


def _unpatch():
    for (mod, name), val in _saved.items():
        setattr(mod, name, val)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _patch()
        cls.thread = threading.Thread(target=server.main, args=(['--no-reuse', '--port', '0'],), daemon=True)
        cls.thread.start()
        info = DATA / 'server.json'
        for _ in range(100):
            if info.exists() and server.STATE.get('port'):
                break
            time.sleep(0.05)
        cls.info = json.loads(info.read_text(encoding='utf-8'))
        cls.port, cls.token = cls.info['端口'], cls.info['口令']

    @classmethod
    def tearDownClass(cls):
        for j in list(server.MANAGER.jobs.values()):     # 没跑完的任务先取消，等它放掉锁
            j.cancelled = True
        for _ in range(100):
            if all(j.ended for j in server.MANAGER.jobs.values()):
                break
            time.sleep(0.1)
        srv = server.STATE.get('server')
        if srv:
            srv.shutdown()
        cls.thread.join(5)
        _unpatch()
        shutil.rmtree(TMP, ignore_errors=True)

    # ---- 发请求 ----
    def req(self, method, path, body=None, token=True, headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        h = {}
        if token:
            h['X-Token'] = self.token
        data = raw
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        text = r.read().decode('utf-8', errors='replace')
        conn.close()
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        return r.status, r, obj, text

    @staticmethod
    def fq(path):
        return '/api/file?' + server.urllib.parse.urlencode({'路径': path})

    def wait_job(self, jid, limit=20):
        for _ in range(limit * 10):
            code, _r, d, _t = self.req('GET', '/api/jobs/' + jid)
            if code == 200 and d.get('状态') in jobs.DONE:
                return d
            time.sleep(0.1)
        self.fail('任务 %s 没在 %d 秒内结束' % (jid, limit))

    # ---- 启动 ----
    def test_server_json(self):
        self.assertEqual(self.info['pid'], os.getpid())
        self.assertEqual(self.info['网址'], 'http://127.0.0.1:%d/?t=%s' % (self.port, self.token))
        if not config.IS_WIN:
            self.assertEqual((DATA / 'server.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(server.running(), self.info['网址'])     # 第二次启动会认出这个服务

    # ---- 口令、Host、Origin ----
    def test_ping_no_token(self):
        code, _r, d, _t = self.req('GET', '/api/ping', token=False)
        self.assertEqual(code, 200)
        self.assertEqual(d, {'ok': True, 'pid': os.getpid()})

    def test_no_token_403(self):
        for path in ('/api/jobs', '/api/overview', '/api/settings', '/'):
            code, _r, d, text = self.req('GET', path, token=False)
            self.assertEqual(code, 403, path)
            self.assertIn('口令不对', text)
        code, _r, d, _t = self.req('GET', '/api/jobs', token=False, headers={'X-Token': 'wrong'})
        self.assertEqual(code, 403)
        self.assertEqual(d['错误'], server.BAD_TOKEN)

    def test_index_sets_cookie(self):
        code, r, _d, _t = self.req('GET', '/?t=' + self.token, token=False)
        self.assertEqual(code, 303)
        self.assertEqual(r.getheader('Location'), '/')
        c = r.getheader('Set-Cookie')
        for part in ('rp_t=' + self.token, 'HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(part, c)
        code, r, _d, text = self.req('GET', '/', token=False, headers={'Cookie': 'rp_t=' + self.token})
        self.assertEqual(code, 200)
        self.assertIn("frame-ancestors 'none'", r.getheader('Content-Security-Policy'))
        self.assertEqual(r.getheader('X-Content-Type-Options'), 'nosniff')
        self.assertEqual(r.getheader('Cache-Control'), 'no-store')
        self.assertIn('<', text)
        code, _r, _d, _t = self.req('GET', '/?t=wrong', token=False)
        self.assertEqual(code, 403)
        code, _r, _d, _t = self.req('GET', '/api/jobs', token=False, headers={'Cookie': 'a=b; rp_t=' + self.token})
        self.assertEqual(code, 200)

    def test_token_header_200(self):
        code, _r, d, _t = self.req('GET', '/api/jobs')
        self.assertEqual(code, 200)
        self.assertIsInstance(d, list)

    def test_bad_host_403(self):
        for host in ('evil.example.com:%d' % self.port, '127.0.0.1:1', 'localhost'):
            code, _r, _d, _t = self.req('GET', '/api/jobs', headers={'Host': host})
            self.assertEqual(code, 403, host)
        code, _r, _d, _t = self.req('GET', '/api/jobs', headers={'Host': 'localhost:%d' % self.port})
        self.assertEqual(code, 200)

    def test_bad_origin_403(self):
        body = {'类别': '自测', '参数': {'秒': 0}}
        code, _r, _d, _t = self.req('POST', '/api/jobs', body, headers={'Origin': 'http://evil.example.com'})
        self.assertEqual(code, 403)
        code, _r, _d, _t = self.req('POST', '/api/jobs', body, headers={'Origin': 'http://127.0.0.1:1'})
        self.assertEqual(code, 403)
        code, _r, d, _t = self.req('POST', '/api/jobs', body, headers={'Origin': 'http://127.0.0.1:%d' % self.port})
        self.assertEqual(code, 200)
        self.wait_job(d['号'])
        # 127.0.0.1 别的端口上的网页算同站、会带 cookie：GET 读文件也要拒
        md = str(PROJ / '正文' / '01_总论.md')
        for path in ('/api/jobs', self.fq(md)):
            code, _r, _d, _t = self.req('GET', path, headers={'Origin': 'http://127.0.0.1:3000'})
            self.assertEqual(code, 403, path)
        code, _r, _d, _t = self.req('GET', self.fq(md))
        self.assertEqual(code, 200)

    def test_body_rules(self):
        code, _r, _d, _t = self.req('POST', '/api/jobs', raw=b'{"a":1}', headers={'Content-Type': 'text/plain'})
        self.assertEqual(code, 415)
        code, _r, _d, _t = self.req('POST', '/api/jobs', raw=b'not json', headers={'Content-Type': 'application/json'})
        self.assertEqual(code, 400)
        code, _r, _d, _t = self.req('POST', '/api/jobs', raw=b'[1]', headers={'Content-Type': 'application/json'})
        self.assertEqual(code, 400)
        big = b'{"a":"' + b'x' * (1024 * 1024) + b'"}'
        code, _r, _d, _t = self.req('POST', '/api/jobs', raw=big, headers={'Content-Type': 'application/json'})
        self.assertEqual(code, 413)

    def test_other_methods_json(self):
        code, _r, d, _t = self.req('PUT', '/api/jobs', {'a': 1})
        self.assertEqual(code, 501)
        self.assertIn('错误', d)

    def test_unknown_paths_404(self):
        for path in ('/api/nothing', '/index.html', '/app/config.py', '/web/'):
            code, _r, d, _t = self.req('GET', path)
            self.assertEqual(code, 404, path)
            self.assertIn('错误', d)

    # ---- 任务 ----
    def test_overview(self):
        code, _r, d, _t = self.req('GET', '/api/overview')
        self.assertEqual(code, 200)
        self.assertIsInstance(d['项目'], list)
        self.assertEqual([x['项目'] for x in d['项目']], ['测试项目甲'])
        self.assertTrue(d['项目'][0]['存在'])

    def test_selftest_job_runs(self):
        code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '自测', '参数': {'秒': 1, '_回显': True}})
        self.assertEqual(code, 200)
        jid = d['号']
        self.assertRegex(jid, server.JOB_ID.pattern)
        done = self.wait_job(jid)
        self.assertEqual(done['状态'], '完成')
        self.assertEqual(done['结果'], {'步数': 1})
        self.assertNotIn('_回显', done['参数'])
        self.assertNotIn('_回显', server.MANAGER.jobs[jid].params)
        code, _r, rows, _t = self.req('GET', '/api/jobs')
        self.assertIn(jid, [x['号'] for x in rows])
        code, _r, c, _t = self.req('POST', '/api/jobs/%s/cancel' % jid)
        self.assertEqual(code, 200)
        self.assertFalse(c['取消'])

    def test_same_project_busy_409(self):
        proj = TMP / '自测项目'
        proj.mkdir(exist_ok=True)
        body = {'类别': '自测', '参数': {'秒': 3}, '项目': str(proj)}
        code, _r, d, _t = self.req('POST', '/api/jobs', body)
        self.assertEqual(code, 200)
        code2, _r, d2, _t = self.req('POST', '/api/jobs', body)
        self.assertEqual(code2, 409)
        self.assertIn('正在跑', d2['错误'])
        code, _r, c, _t = self.req('POST', '/api/jobs/%s/cancel' % d['号'])
        self.assertTrue(c['取消'])
        self.assertEqual(self.wait_job(d['号'])['状态'], '已取消')
        self.assertFalse((proj / jobs.LOCK_NAME).exists())

    def test_bad_kind_400(self):
        for body in ({'类别': '删库'}, {}, {'类别': ['自测']}, {'类别': '自测', '参数': [1]}):
            code, _r, d, _t = self.req('POST', '/api/jobs', body)
            self.assertEqual(code, 400, body)
            self.assertIn('错误', d)

    def test_job_rules(self):
        bad = [
            {'类别': '新报告', '参数': {'文件夹': 'relative/path', '方法': '类比法'}},
            {'类别': '新报告', '参数': {'文件夹': str(config.ROOT / '新项目'), '方法': '类比法'}},
            {'类别': '新报告', '参数': {'文件夹': str(PROJ), '方法': '类比法'}},
            {'类别': '新报告', '参数': {'文件夹': str(TMP / '新'), '方法': '检查表法'}},
            {'类别': '新报告', '参数': {'文件夹': str(TMP / '新'), '方法': '类比法', '资料': [str(TMP / '没有')]}},
            {'类别': '审稿', '项目': str(EMPTY)},
            {'类别': '出稿', '项目': str(TMP / '没有')},
            {'类别': '事实库', '项目': '项目甲'},
            {'类别': '改稿', '参数': {'意见': '  '}},
            {'类别': '改稿', '参数': {'意见': '改一下', '项目': [str(EMPTY)]}},
        ]
        for body in bad:
            code, _r, d, _t = self.req('POST', '/api/jobs', body)
            self.assertEqual(code, 400, body)
            self.assertIn('错误', d)
        self.assertFalse((TMP / '新').exists())
        # 资料路径去掉首尾空白和引号（Windows“复制为路径”带引号）
        doc = TMP / '可研.txt'
        doc.write_text('资料', encoding='utf-8')
        title, params, root = server.build_job('新报告', {'文件夹': ' "%s" ' % (TMP / '新'), '方法': '风险评估法',
                                                        '资料': ['  "%s"  ' % doc, '']}, None)
        self.assertEqual((title, root, params['资料']), ('新报告', str(TMP / '新'), [str(doc)]))
        self.assertTrue(params['审稿'] and params['出稿'])
        title, params, root = server.build_job('改稿', {'意见': '把“建设单位”统一改成“企业”', '项目': [str(PROJ)]}, None)
        self.assertEqual((params['项目'], params['_锁'], root), ([str(PROJ)], [str(PROJ)], None))
        self.assertTrue(title.startswith('改稿：'))
        title, params, root = server.build_job('改稿', {'意见': '改'}, '全部')
        self.assertEqual(params['项目'], '全部')
        title, params, root = server.build_job('审稿', {'章': ['07', '05'], '修改': False}, str(PROJ))
        self.assertEqual((title, params['章'], params['修改'], root), ('审稿 第07,05章', '07,05', False, str(PROJ)))

    def test_bad_job_id_404(self):
        for jid in ('../../etc', 'abc', '1031-202210-8A63', '1031-202210-8a63x', '..%2F..%2Fconfig'):
            code, _r, d, _t = self.req('GET', '/api/jobs/' + jid)
            self.assertEqual(code, 404, jid)
        code, _r, _d, _t = self.req('POST', '/api/jobs/x/cancel')
        self.assertEqual(code, 404)
        code, _r, _d, _t = self.req('GET', '/api/jobs/1031-202210-8a63')
        self.assertEqual(code, 404)

    # ---- 打开、读文件 ----
    def test_open_and_file_refuse(self):
        (DATA / 'config.json').write_text(json.dumps({'密钥': FAKE_KEY}), encoding='utf-8')
        link = PROJ / '链接到设置.json'
        try:
            link.symlink_to(DATA / 'config.json')
        except OSError:
            link = None
        bad = ['/etc/passwd', str(PROJ / '..' / '..' / '..' / 'etc' / 'passwd'), str(PROJ / '正文' / '..' / '..'),
               str(DATA / 'config.json'), str(DATA / 'server.json'), str(DATA / '任务' / '..' / 'config.json'),
               'relative.md', '']
        if link:
            bad.append(str(link))
        for p in bad:
            code, _r, d, text = self.req('POST', '/api/open', {'路径': p})
            self.assertIn(code, (400, 403), p)
            code, _r, d, text = self.req('GET', self.fq(p))
            self.assertIn(code, (400, 403), p)
            self.assertNotIn(FAKE_KEY, text)
            self.assertNotIn(self.token, text)
        self.assertEqual(OPENED, [])

    def test_open_and_file_allowed(self):
        md = PROJ / '正文' / '01_总论.md'
        code, _r, d, _t = self.req('GET', self.fq('"%s"' % md))
        self.assertEqual(code, 200)
        self.assertEqual(d['路径'], str(md))
        self.assertIn('测试用的正文', d['文字'])
        run = PROJ / '运行.command'
        run.write_text('echo hi', encoding='utf-8')
        code, _r, _d, _t = self.req('POST', '/api/open', {'路径': str(run)})
        self.assertEqual(code, 403)
        code, _r, _d, _t = self.req('GET', self.fq(str(run)))
        self.assertEqual(code, 403)
        self.assertEqual(OPENED, [])
        code, _r, d, _t = self.req('POST', '/api/open', {'路径': str(PROJ / '正文')})
        self.assertEqual(code, 200)
        code, _r, d, _t = self.req('POST', '/api/open', {'路径': str(md)})
        self.assertEqual(code, 200)
        self.assertEqual([str(x) for x in OPENED], [str(PROJ / '正文'), str(md)])
        OPENED.clear()
        server.log('测试日志一行')
        code, _r, d, _t = self.req('GET', self.fq(str(DATA / 'server.log')))
        self.assertEqual(code, 200)
        self.assertIn('测试日志一行', d['文字'])

    # ---- 设置 ----
    def test_settings_hide_key(self):
        code, _r, d, _t = self.req('POST', '/api/settings', {'密钥': FAKE_KEY, '并发': 3, '地址': 'https://relay.example.com'})
        self.assertEqual(code, 200)
        code, _r, d, text = self.req('GET', '/api/settings')
        self.assertEqual(code, 200)
        self.assertNotIn('密钥', d)
        self.assertNotIn(FAKE_KEY, text)
        self.assertEqual((d['并发'], d['有密钥'], d['密钥来源']), (3, True, '本程序设置'))
        code, _r, d, _t = self.req('POST', '/api/settings', {'密钥': '', '并发': '2'})
        self.assertEqual((code, d['并发'], d['有密钥']), (200, 2, True))     # 密钥空着＝不改
        self.assertEqual(json.loads((DATA / 'config.json').read_text(encoding='utf-8'))['密钥'], FAKE_KEY)
        self.assertFalse(d['走系统代理'])                                   # 默认直连
        self.assertTrue(d['思考摘要'])                                      # 默认照常发回思考摘要
        code, _r, d, _t = self.req('POST', '/api/settings', {'思考摘要': False})
        self.assertEqual((code, d['思考摘要']), (200, False))
        code, _r, d, _t = self.req('POST', '/api/settings', {'走系统代理': True})
        self.assertEqual((code, d['走系统代理']), (200, True))
        code, _r, d, _t = self.req('POST', '/api/settings', {'走系统代理': False})
        self.assertEqual((code, d['走系统代理']), (200, False))
        for body in ({'并发': 0}, {'并发': 9}, {'并发': 2.5}, {'并发': True}, {'地址': 'ftp://x'}, {'地址': 'relay'}, {}):
            code, _r, d, _t = self.req('POST', '/api/settings', body)
            self.assertEqual(code, 400, body)

    # ---- 用量 ----
    def test_usage_summary(self):
        today = datetime.date.today()
        old = (today - datetime.timedelta(days=30)).isoformat()
        rows = [
            {'时间': today.isoformat() + 'T09:00:00', '项目': '甲', '任务': '审稿', '输入': 100, '输出': 10, '估算美元': 0.5},
            {'时间': today.isoformat() + 'T10:00:00', '项目': '乙', '任务': '审稿', '输入': 200, '输出': 20, '估算美元': 0.25},
            {'时间': (today - datetime.timedelta(days=1)).isoformat() + 'T10:00:00', '项目': '甲', '任务': '改稿',
             '输入': 300, '输出': 30, '估算美元': 1.0},
            {'时间': old + 'T10:00:00', '项目': '甲', '任务': '改稿', '输入': 9999, '输出': 99, '估算美元': 9.0},
        ]
        Path(llm.USAGE).write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n坏的一行\n',
                                   encoding='utf-8')
        code, _r, d, _t = self.req('GET', '/api/usage?' + server.urllib.parse.urlencode({'天': 7}))
        self.assertEqual(code, 200)
        self.assertEqual(d['合计'], {'次数': 3, '输入': 600, '输出': 60, '估算美元': 1.75})
        self.assertEqual([x['日期'] for x in d['按天']], [(today - datetime.timedelta(days=1)).isoformat(), today.isoformat()])
        by = {x['项目']: x for x in d['按项目']}
        self.assertEqual((by['甲']['次数'], by['甲']['估算美元'], by['乙']['输入']), (2, 1.5, 200))
        self.assertEqual({x['任务']: x['次数'] for x in d['按任务']}, {'审稿': 2, '改稿': 1})
        code, _r, d, _t = self.req('GET', '/api/usage?' + server.urllib.parse.urlencode({'天': 60}))
        self.assertEqual(d['合计']['次数'], 4)
        code, _r, _d, _t = self.req('GET', '/api/usage?' + server.urllib.parse.urlencode({'天': 'x'}))
        self.assertEqual(code, 400)

    def test_standards_list(self):
        code, _r, d, _t = self.req('GET', '/api/standards')
        self.assertEqual(code, 200)
        self.assertIsInstance(d['文件'], list)
        for x in d['文件'][:3]:
            self.assertTrue(x['路径'].endswith('.md'))
            self.assertIsInstance(x['字数'], int)


    # ---- 验收时补的 ----
    def test_secret_paths_refused(self):
        """大小写换写法、硬链接、登记的“项目”把设置文件夹包在里面、本机 Claude Code 的设置：都拒绝。"""
        (DATA / 'config.json').write_text(json.dumps({'密钥': FAKE_KEY}), encoding='utf-8')
        claude = TMP / 'home' / '.claude'
        claude.mkdir(parents=True, exist_ok=True)
        (claude / 'settings.json').write_text(json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': FAKE_KEY}}), encoding='utf-8')
        hard = PROJ / '硬链接.json'
        try:
            os.link(DATA / 'config.json', hard)
        except OSError:
            hard = None
        old_dir, old_reg = server.CLAUDE_DIR, tools.registered
        server.CLAUDE_DIR = claude
        tools.registered = lambda: [{'项目': '整个临时文件夹', '文件夹': str(TMP)}]
        try:
            bad = [str(DATA / 'CONFIG.JSON'), str(DATA / 'Server.Json'), str(DATA / 'config.json'), str(DATA / '任务' / '..' / 'Config.json'),
                   str(claude / 'settings.json'), str(claude / 'SETTINGS.JSON'), str(claude), str(DATA / 'venv' / 'pyvenv.cfg'),
                   str(PROJ / 'x\x00.md')]
            if hard:
                bad.append(str(hard))
            for p in bad:
                code, _r, _d, text = self.req('GET', self.fq(p))
                self.assertIn(code, (400, 403), p)
                self.assertNotIn(FAKE_KEY, text)
                code, _r, _d, _t = self.req('POST', '/api/open', {'路径': p})
                self.assertIn(code, (400, 403), p)
            server.log('验收日志一行')                       # 日志、任务记录照样能看
            code, _r, d, _t = self.req('GET', self.fq(str(DATA / 'server.log')))
            self.assertEqual(code, 200)
            code, _r, _d, _t = self.req('POST', '/api/open', {'路径': str(DATA / '任务')})
            self.assertIn(code, (200, 404))
        finally:
            server.CLAUDE_DIR, tools.registered = old_dir, old_reg
            if hard:
                hard.unlink()
            OPENED.clear()

    def test_windows_style_paths(self):
        # Mac 上这些都不是完整路径（400）；Windows 上盘符路径不在范围内（403），设备路径、隐藏数据流写法 400
        for p in ('C:\\Windows\\win.ini', '\\\\?\\C:\\Windows\\win.ini', '\\\\.\\C:\\x.md', 'C:\\a.md:secret',
                  '..\\..\\etc\\passwd', 'C:config.json', '~/../../etc/passwd'):
            code, _r, _d, _t = self.req('GET', self.fq(p))
            self.assertIn(code, (400, 403), p)
            code, _r, _d, _t = self.req('POST', '/api/open', {'路径': p})
            self.assertIn(code, (400, 403), p)
        self.assertEqual(OPENED, [])

    def test_open_refuses_app_bundles(self):
        fake = PROJ / '假文档.pdf'                 # 扩展名改成 .pdf 的 Mac 程序包
        (fake / 'Contents' / 'MacOS').mkdir(parents=True, exist_ok=True)
        (fake / 'Contents' / 'Info.plist').write_text('<plist/>', encoding='utf-8')
        saver = PROJ / '屏保.saver'
        saver.mkdir(exist_ok=True)
        try:
            for p in (fake, saver, PROJ / '假文档.pdf' / 'Contents' / '..'):
                code, _r, _d, _t = self.req('POST', '/api/open', {'路径': str(p)})
                self.assertEqual(code, 403, p)
            self.assertEqual(OPENED, [])
        finally:
            shutil.rmtree(fake, ignore_errors=True)
            shutil.rmtree(saver, ignore_errors=True)

    def test_origin_null_and_token_not_logged(self):
        code, _r, _d, _t = self.req('POST', '/api/jobs', {'类别': '自测', '参数': {'秒': 0}}, headers={'Origin': 'null'})
        self.assertEqual(code, 403)
        self.req('GET', '/?t=wrong-token-abc', token=False)
        self.req('GET', '/api/nothing?t=' + self.token)
        text = (DATA / 'server.log').read_text(encoding='utf-8')
        self.assertIn('/?t=***', text)                 # 确实记了这一行，只是口令换成了 ***
        self.assertNotIn('wrong-token-abc', text)
        self.assertNotIn(self.token, text)

    def test_new_report_extra_rules(self):
        root = TMP / '新2'
        root.mkdir(exist_ok=True)
        (root / 'desktop.ini').write_text('', encoding='utf-8')     # Windows 自己放的文件，不算“有东西”
        (root / 'Thumbs.db').write_text('', encoding='utf-8')
        (root / '._x').write_text('', encoding='utf-8')
        (DATA / 'config.json').write_text(json.dumps({'密钥': FAKE_KEY}), encoding='utf-8')
        try:
            _title, _params, r = server.build_job('新报告', {'文件夹': str(root), '方法': '类比法'}, None)
            self.assertEqual(r, str(root))
            # 资料会整个拷进新项目：设置文件夹、包着设置文件夹的大文件夹、包着新项目自己的文件夹，都不收
            for docs in ([str(TMP)], [str(DATA)], [str(DATA / 'config.json')], [str(root)]):
                with self.assertRaises(server.ApiError, msg=str(docs)) as cm:
                    server.build_job('新报告', {'文件夹': str(root / '子'), '方法': '类比法', '资料': docs}, None)
                self.assertEqual(cm.exception.code, 400)
            with self.assertRaises(server.ApiError):
                server.build_job('新报告', {'文件夹': str(DATA / '新'), '方法': '类比法'}, None)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_revise_all_without_projects_400(self):
        old = tools.registered
        tools.registered = lambda: []
        try:
            code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '改稿', '参数': {'意见': '改一下', '项目': '全部'}})
        finally:
            tools.registered = old
        self.assertEqual(code, 400)
        self.assertIn('新报告', d['错误'])

    def test_failed_job_hides_trace(self):
        code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '自测', '参数': {'秒': 0, '失败': True}})
        self.assertEqual(code, 200)
        done = self.wait_job(d['号'])
        self.assertEqual((done['状态'], done['错误']), ('失败', '按要求失败'))
        self.assertNotIn('详情', done)
        self.assertNotIn('Traceback', json.dumps(done, ensure_ascii=False))

    def test_old_jobs_after_restart(self):
        code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '自测', '参数': {'秒': 0}})
        jid = d['号']
        self.wait_job(jid)
        fresh = jobs.Manager()                     # 服务重启后：内存里没有任务，只读任务记录
        self.assertIn(jid, [x['号'] for x in fresh.list()])
        self.assertEqual(fresh.get(jid)['状态'], '完成')
        # 命令行任务中途被杀：记录停在“进行”，10 分钟没动静，详情和列表都显示“中断”
        dead = '0101-000000-dead'
        p = Path(jobs.JOBS_DIR) / dead
        p.mkdir(parents=True, exist_ok=True)
        (p / '任务.json').write_text(json.dumps({'号': dead, '类别': '自测', '标题': '自测', '状态': '进行', '建立': 1,
                                                '步骤': [{'号': 1, '名称': '第1步', '状态': '进行'}]}, ensure_ascii=False), encoding='utf-8')
        old = time.time() - jobs.STALE - 60
        os.utime(p / '任务.json', (old, old))
        code, _r, d, _t = self.req('GET', '/api/jobs/' + dead)
        self.assertEqual((code, d['状态']), (200, '中断'))
        code, _r, rows, _t = self.req('GET', '/api/jobs')
        row = [x for x in rows if x['号'] == dead][0]
        self.assertEqual((row['状态'], row['进行中'], row['步骤数']), ('中断', [], 1))
        code, _r, c, _t = self.req('POST', '/api/jobs/%s/cancel' % dead)
        self.assertEqual((code, c['取消']), (200, False))
        shutil.rmtree(p)

    def test_job_again(self):
        # 失败、中断的新报告能“接着做”：照原参数另起一个，文件夹不要求是空的；别的任务、没做完的不行
        root = TMP / '接着做'
        (root / '正文').mkdir(parents=True)
        (root / '项目信息.md').write_text('- 项目名称：甲\n', encoding='utf-8')
        jid = '0101-000000-a9a1'
        p = Path(jobs.JOBS_DIR) / jid
        p.mkdir(parents=True, exist_ok=True)
        rec = {'号': jid, '类别': '新报告', '标题': '新报告', '状态': '失败', '项目': str(root), '建立': 1,
               '参数': {'文件夹': str(root), '方法': '类比法', '资料': [str(TMP / '早挪走了.pdf')], '编号': 'AT-1', '出稿': False}}
        (p / '任务.json').write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
        started = []

        def fake_start(kind, title, params, fn, project=None):
            started.append((kind, title, params, project))
            return mock.Mock(id='0101-000001-b0b0')

        with mock.patch.object(server.MANAGER, 'start', fake_start):
            code, _r, d, _t = self.req('POST', '/api/jobs/%s/again' % jid)
            self.assertEqual((code, d['号']), (200, '0101-000001-b0b0'))
            kind, title, params, project = started[0]
            self.assertEqual((kind, title, project), ('新报告', '新报告（接着做）', str(root)))
            self.assertEqual((params['接着做'], params['资料'], params['编号'], params['出稿']), (True, [], 'AT-1', False))
            for state in ('进行', '完成'):
                rec['状态'] = state
                (p / '任务.json').write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
                code, _r, d, _t = self.req('POST', '/api/jobs/%s/again' % jid)
                self.assertEqual(code, 400, state)
            rec.update(状态='失败', 类别='审稿')
            (p / '任务.json').write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
            code, _r, d, _t = self.req('POST', '/api/jobs/%s/again' % jid)
            self.assertEqual(code, 400)
            code, _r, d, _t = self.req('POST', '/api/jobs/0101-000000-ffff/again')
            self.assertEqual(code, 404)
            code, _r, d, _t = self.req('POST', '/api/jobs/%s/again' % jid, headers={'Origin': 'http://evil.example'})
            self.assertEqual(code, 403)
        self.assertEqual(len(started), 1)
        shutil.rmtree(p)

    # ---- 报告页 ----
    def test_project_page(self):
        pq = lambda path, x=None: '/api/project%s?' % (x or '') + server.urllib.parse.urlencode(path)  # noqa: E731
        code, _r, d, _t = self.req('GET', pq({'路径': str(PROJ)}))
        self.assertEqual(code, 200, d)
        self.assertEqual(d['项目'], '测试项目甲')
        c = d['章节'][0]
        self.assertEqual((c['文件'], c['标题'], c['写好']), ('01_总论.md', '1 总论', True))
        self.assertIsNone(d['PDF'])
        self.assertEqual(set(d['资料欠缺']) >= {'要补', '补了更准', '可以不补'}, True)   # 资料欠缺清单一起回
        # 不在登记里的文件夹、没口令、别的网站来的都不行
        self.assertEqual(self.req('GET', pq({'路径': str(EMPTY)}))[0], 403)
        self.assertEqual(self.req('GET', pq({'路径': str(PROJ)}), token=False)[0], 403)
        self.assertEqual(self.req('GET', pq({'路径': str(PROJ)}), headers={'Origin': 'http://evil.example'})[0], 403)

    def test_project_changes(self):
        pq = lambda q: '/api/project/changes?' + server.urllib.parse.urlencode(q)  # noqa: E731
        code, _r, d, _t = self.req('GET', pq({'路径': str(PROJ)}))
        self.assertEqual((code, d['备份'], d['改动'], d['审稿']), (200, [], [], []))
        bk = PROJ / '备份' / '正文_20261101_120000_审稿前'
        bk.mkdir(parents=True)
        (bk / '01_总论.md').write_text('# 1 总论\n\n测试的正文。\n', encoding='utf-8')
        rv = PROJ / '审查' / '审稿记录'
        rv.mkdir(parents=True)
        (rv / '01_总论.md').write_text('# 审稿记录 01_总论.md\n\n| 行 | 级别 | 问题 | 依据 | 原文 | 改成 | 结果 |\n|---|---|---|---|---|---|---|\n'
                                      '| 3 | 必改 | 用字 | K001 | 测试的正文 | 测试用的正文 | 已改 |\n| 4 | 建议 | a\\|b | R1 | x | y | 没改 |\n',
                                      encoding='utf-8')
        try:
            code, _r, d, _t = self.req('GET', pq({'路径': str(PROJ)}))
            self.assertEqual((code, d['对比']), (200, bk.name))
            self.assertEqual(d['备份'][0]['说明'], '审稿前')
            spot = d['改动'][0]['处'][0]
            self.assertEqual((spot['行'], spot['原文'], spot['改成']), (3, '测试的正文。', '测试用的正文。'))
            self.assertIn(['+', '用'], spot['逐字'])
            ops = d['审稿'][0]['意见']
            self.assertEqual([o['结果'] for o in ops], ['已改', '没改'])
            self.assertEqual(ops[1]['问题'], 'a|b')        # 表格里转义的竖线照原样还原
            self.assertEqual(self.req('GET', pq({'路径': str(PROJ), '备份': '../正文'}))[0], 404)
        finally:
            shutil.rmtree(PROJ / '备份')
            shutil.rmtree(PROJ / '审查')


    # ---- 补充资料 ----
    def test_supplement(self):
        sq = '/api/project/supplement?' + server.urllib.parse.urlencode({'路径': str(PROJ)})
        code, _r, d, _t = self.req('GET', sq)
        self.assertEqual((code, d['变化']), (200, None))
        self.assertEqual(self.req('GET', '/api/project/supplement?' + server.urllib.parse.urlencode({'路径': str(EMPTY)}))[0], 403)
        src = TMP / '初设.txt'
        src.write_text('初设', encoding='utf-8')
        cases = [({'资料': []}, '先选要补充的资料'), ({'资料': [str(TMP / '没有.txt')]}, '找不到'),
                 ({'资料': [str(DATA)]}, '程序设置或密钥'), ({'资料': [str(TMP)]}, '程序设置或密钥')]
        for P, want in cases:
            code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '补资料', '参数': P, '项目': str(PROJ)})
            self.assertEqual(code, 400, P)
            self.assertIn(want, d['错误'])
        code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '补资料', '参数': {'资料': [str(src)]}, '项目': str(EMPTY)})
        self.assertEqual(code, 403)                                    # 不在项目登记里
        started = []
        with mock.patch.object(server.MANAGER, 'start', lambda kind, title, params, fn, root: started.append((kind, title, params, root))
                               or type('J', (), {'id': '0101-000000-aaaa'})()):
            code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '补资料', '参数': {'资料': [str(src)], '说明': '初设'}, '项目': str(PROJ)})
            self.assertEqual(code, 200, d)
            self.assertEqual(started[-1], ('补资料', '补充资料', {'资料': [str(src)], '说明': '初设'}, str(PROJ)))
            # 没有待确认的变化时不能“按新资料改”
            code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '按新资料改', '参数': {}, '项目': str(PROJ)})
            self.assertEqual(code, 400)
            pend = PROJ / '资料' / '事实片段' / '补资料' / '待确认.json'
            pend.parent.mkdir(parents=True)
            pend.write_text(json.dumps({'变化': [{'编号': 1}, {'编号': 2}], '新资料': ['初设.txt']}, ensure_ascii=False), encoding='utf-8')
            try:
                self.assertEqual(self.req('GET', sq)[2]['变化']['新资料'], ['初设.txt'])
                for bad in ([9], 'all', [True]):
                    self.assertEqual(self.req('POST', '/api/jobs', {'类别': '按新资料改', '参数': {'采用': bad}, '项目': str(PROJ)})[0], 400, bad)
                code, _r, d, _t = self.req('POST', '/api/jobs', {'类别': '按新资料改', '参数': {'采用': [2, 9], '出稿': True}, '项目': str(PROJ)})
                self.assertEqual(code, 200, d)
                self.assertEqual(started[-1][2], {'采用': [2], '出稿': True})
                code, _r, d, _t = self.req('POST', '/api/project/supplement/discard', {'路径': str(PROJ)})
                self.assertEqual((code, d['变化']), (200, None))
                self.assertFalse(pend.exists())
                self.assertEqual(self.req('POST', '/api/project/supplement/discard', {'路径': str(EMPTY)})[0], 403)
            finally:
                shutil.rmtree(PROJ / '资料', ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
