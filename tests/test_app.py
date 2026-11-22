"""核心部分自测：不调模型、不花钱、不碰真文件。

在流水线文件夹里运行：~/.report-pipeline/venv/bin/python -m unittest tests/test_app.py -v
- 设置、任务记录、用量、标准（config.STD）都指到临时文件夹；项目用 _试跑/新材料副本 复制到临时文件夹的副本，测完删掉。
- 模型换成假的：llm.client 一调就报错；要走流程的地方，llm.call_retry / llm.call 按标签返回事先写好的结果。
- tools.write 包一层，写到临时文件夹以外就记下、测试失败；测完比较 项目登记.md 和定稿项目 正文/ 的修改时间和哈希
  （标准/ 和 200MW 有别人在改，不比）。
- 检查程序（scripts/pe）读的是流水线里真的 标准/通用检查规则.md，不跟 config.STD 走；测试结论不依赖它的内容。
"""
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import cli, config, flows, jobs, llm, rules, tools  # noqa: E402
from app.flows import analog, common, distill, facts, new, review, revise, supplement  # noqa: E402

SRC = ROOT / '_试跑' / '新材料副本'
TMP = Path(tempfile.mkdtemp(prefix='rp_app_test_')).resolve()
BASE = TMP / '新材料副本'          # 从 _试跑 复制一次，各测试再从它复制
FAKE_KEY = 'sk-fake-app-test-0000-do-not-use'
OUTSIDE, NO_CLIENT = [], []
_saved, _snap = {}, {}
_real_write = tools.write
_mu = threading.Lock()
SENT, ROW = '', ''      # 副本第7章里真有的一句、一行表格（setUpModule 里从副本挑，副本以后被试审改了也能用）
SEP = re.compile(r'^\|(\s*:?-+:?\s*\|)+$')
CJK = re.compile(r'[一-鿿]')


def _inside(p):
    try:
        Path(p).resolve().relative_to(TMP)
        return True
    except ValueError:
        return False


def _guard_write(p, text):
    """tools.write 的保护层：只许写临时文件夹里的文件。"""
    if not _inside(p):
        with _mu:
            OUTSIDE.append(str(p))
        raise AssertionError('测试写到了临时文件夹以外：%s' % p)
    return _real_write(p, text)


def _no_client(*a, **k):
    with _mu:
        NO_CLIENT.append('llm.client')
    raise AssertionError('测试里不许真调模型')


def _no_open(p):
    raise AssertionError('测试里不许打开文件：%s' % p)


class FakeModel:
    """假模型：按标签开头找处理函数，返回事先写好的结果。“清零”默认不改。没准备的标签记下来、报错。"""

    def __init__(self):
        self.handlers, self.calls, self.unknown = [], [], []

    def reset(self, *handlers):
        self.handlers = list(handlers) + [('清零 ', lambda **k: {'改动': [], '跳过': []})]
        self.calls, self.unknown = [], []

    def __call__(self, job, label, system, content, tries=2, **kw):
        with _mu:
            self.calls.append({'标签': label, 'schema': kw.get('schema'), '内容': content, '参数': kw})
        for prefix, fn in self.handlers:
            if label.startswith(prefix):
                return copy.deepcopy(fn(label=label, content=content, schema=kw.get('schema')))
        with _mu:
            self.unknown.append(label)
        raise AssertionError('假模型没有准备这个标签：%s' % label)

    def labels(self):
        return [c['标签'] for c in self.calls]


FAKE = FakeModel()


def _snapshot():
    """项目登记.md 和定稿项目（项目登记里定稿为“是”的）正文/ 的修改时间、哈希。"""
    out = {}
    files = [config.REGISTER] + [p for r in tools.finished_projects() for p in sorted((r / '正文').glob('*'))]
    for p in files:
        if p.is_file():
            out[str(p)] = (p.stat().st_mtime, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


def _pick(text):
    """挑一句在文件里只出现一次的正文句子、一行只出现一次的表格行。"""
    lines = text.split('\n')
    sent = next(x for ln in lines if len(ln) > 30 and not re.match(r'^(#|\||<!--|表|注|-|!)', ln) and '。' in ln
                for x in [ln.split('。')[0] + '。'] if len(x) >= 12 and text.count(x) == 1 and not set(x) & set('\\|`'))
    row = next(lines[i + 1] for i, ln in enumerate(lines[:-1]) if SEP.match(ln) and lines[i + 1].startswith('|')
               and '\\|' not in lines[i + 1] and lines[i + 1].count('|') >= 4 and text.count(lines[i + 1]) == 1)
    return sent, row


def setUpModule():
    global SENT, ROW
    _snap.update(_snapshot())
    data = TMP / 'data'
    data.mkdir()
    shutil.copytree(SRC, BASE, ignore=shutil.ignore_patterns('.DS_Store', '.流水线运行中', '*.tmp', 'WPS核对', '备份', '审查'))
    (BASE / tools.SETTINGS_FILE).unlink(missing_ok=True)      # 免得去建事实库
    shutil.copytree(config.STD, TMP / '标准', ignore=shutil.ignore_patterns('_历史', '.流水线运行中', '*.tmp', '.DS_Store'))
    SENT, ROW = _pick(tools.read(ch07(BASE)))
    (data / 'config.json').write_text(json.dumps({'密钥': FAKE_KEY, '并发': 4}, ensure_ascii=False), encoding='utf-8')
    for mod, name, val in ((config, 'DATA', data), (config, 'CONFIG', data / 'config.json'), (config, 'STD', TMP / '标准'),
                           (jobs, 'JOBS_DIR', data / '任务'), (llm, 'USAGE', data / '用量.jsonl'),
                           (tools, 'write', _guard_write), (tools, 'open_path', _no_open),
                           (llm, 'client', _no_client), (llm, 'call_retry', FAKE), (llm, 'call', FAKE)):
        _saved[(mod, name)] = getattr(mod, name)
        setattr(mod, name, val)


def tearDownModule():
    for (mod, name), val in _saved.items():
        setattr(mod, name, val)
    time.sleep(0.3)        # 让后台任务线程把最后一次存盘做完
    shutil.rmtree(TMP, ignore_errors=True)
    if _snap and _snapshot() != _snap:      # 只跑其中几项时也查一遍
        raise AssertionError('项目登记.md 或定稿项目的 正文/ 在测试期间变了')


def project(name):
    """从 BASE 复制一份给这个测试用（不带 WPS核对）。"""
    dst = TMP / name
    shutil.copytree(BASE, dst)
    return dst.resolve()


def ch07(root):
    return next((Path(root) / '正文').glob('07_*.md'))


def wait(job, secs=30):
    """等任务线程收尾（_release 之后才设 ended）。"""
    t = time.time()
    while job.ended is None and time.time() - t < secs:
        time.sleep(0.05)
    return job


class Base(unittest.TestCase):
    def setUp(self):
        FAKE.reset()

    def tearDown(self):
        bad, nc, unk = list(OUTSIDE), list(NO_CLIENT), list(FAKE.unknown)
        del OUTSIDE[:], NO_CLIENT[:]
        self.assertEqual(bad, [], '有写到临时文件夹以外的')
        self.assertEqual(nc, [], '有地方真去调模型')
        self.assertEqual(unk, [], '假模型收到没准备的标签')


# ==== 测试从这里往下 ====

class T01_改文件和检查(Base):
    @classmethod
    def setUpClass(cls):
        cls.root = project('t01')
        cls.f = ch07(cls.root)
        cls.text = tools.read(cls.f)
        cls.row, cls.old = ROW, SENT

    def test_原文唯一时改上(self):
        self.assertEqual(self.text.count(self.old), 1)
        new = self.old[:-1] + '（测试改动）。'
        out, done, failed = tools.apply_edits(self.text, [{'原文': self.old, '新文': new}], self.f.name)
        self.assertEqual((len(done), failed), (1, []))
        self.assertIn(new, out)
        self.assertEqual(out.replace(new, self.old, 1), self.text)

    def test_原文带行号也能改上(self):
        lines = self.text.split('\n')
        i = next(k for k, ln in enumerate(lines) if self.old in ln)
        old = '%d\t%s' % (i + 1, lines[i])
        out, done, _ = tools.apply_edits(self.text, [{'原文': old, '新文': '%d\t%s' % (i + 1, lines[i] + '（测试）')}], self.f.name)
        self.assertEqual(len(done), 1)
        self.assertEqual(out.split('\n')[i], lines[i] + '（测试）')

    def test_原文出现两次时跳过并说明(self):
        old = '个人使用的职业病防护用品'
        n = self.text.count(old)
        self.assertGreaterEqual(n, 2)
        out, done, failed = tools.apply_edits(self.text, [{'原文': old, '新文': '个体防护装备'}], self.f.name)
        self.assertEqual((out, done), (self.text, []))
        self.assertEqual(len(failed), 1)
        self.assertIn('不能唯一定位', failed[0][1])
        self.assertIn(str(n), failed[0][1])

    def test_改后表格格数不对时跳过(self):
        broken = self.row.rsplit('|', 2)[0] + '|'          # 去掉最后一格：4 格变 3 格
        e = {'原文': self.row, '新文': broken}
        out, done, failed = tools.apply_edits(self.text, [e], self.f.name)
        self.assertEqual((out, done), (self.text, []))
        self.assertIn('格式', failed[0][1])
        out2, done2, _ = tools.apply_edits(self.text, [dict(e)])   # 不给文件名不查格式
        self.assertEqual(len(done2), 1)
        self.assertFalse(tools.format_ok(out2, self.f.name))

    def test_几条一起改_好的改上坏的跳过(self):
        new = self.old + '（测试）'
        edits = [{'原文': self.old, '新文': new}, {'原文': self.row, '新文': self.row.rsplit('|', 2)[0] + '|'},
                 {'原文': '文件里没有的一句话', '新文': 'x'}, {'原文': '  ', '新文': 'x'}, {'原文': new, '新文': new}]
        out, done, failed = tools.apply_edits(self.text, edits, self.f.name)
        self.assertEqual(done, [edits[0]])
        self.assertEqual([why for _, why in failed], ['改后表格或注释格式不对', '文件里找不到原文', '原文为空', '原文和新文一样'])
        self.assertTrue(tools.format_ok(out, self.f.name))
        self.assertIn(new, out)

    def test_check_正文格式坏掉时只报格式错误(self):
        root = project('t01_bad')
        f = ch07(root)
        text = tools.read(f)
        self.assertFalse(tools.check(root).get('格式错误'))
        line = text.split('\n').index(self.row) + 1
        tools.write(f, text.replace(self.row, self.row.rsplit('|', 2)[0] + '|', 1))
        c = tools.check(root)
        self.assertIs(c.get('格式错误'), True)
        self.assertEqual(list(c['按文件']), [f.name])
        self.assertEqual((c['错误'], c['提示'], c['待办']), (1, 0, 0))
        self.assertEqual(c['按文件'][f.name]['提示'], [])
        self.assertTrue(c['按文件'][f.name]['错误'][0].startswith('第%d行 ' % line), c['按文件'][f.name])
        self.assertEqual(c['正文文件'], tools.body_files(root))


class T02_检查规则文件(Base):
    def setUp(self):
        super().setUp()
        d = TMP / ('t02_' + self._testMethodName)
        d.mkdir()
        self.p = d / '检查规则.md'

    def pairs(self):
        return [(s, w) for s, w, _ in rules.items(self.p)]

    def test_add_文件不存在时建文件并写文件头(self):
        self.assertEqual(rules.items(self.p), [])
        n = rules.add(self.p, [('禁用', '甲乙丙'), ('提示', '丁戊')], 'K900 2026-10-31')
        self.assertEqual(n, 2)
        t = tools.read(self.p)
        self.assertTrue(t.startswith(rules.COMMON_HEADER + '\n'))
        lines = t.split('\n')
        i = lines.index('- `甲乙丙`')
        self.assertLess(lines.index('## 禁用'), i)
        self.assertLess(i, lines.index('## 正则禁用'))
        self.assertEqual(lines[i - 1], '<!-- K900 2026-10-31 -->')
        self.assertEqual(self.pairs(), [('禁用', '甲乙丙'), ('提示', '丁戊')])
        p2 = self.p.with_name('项目规则.md')
        rules.add(p2, [('必备', '个人防护')], '用户 10-31', '# 检查规则')
        self.assertTrue(tools.read(p2).startswith('# 检查规则\n'))
        self.assertEqual(rules.items(p2)[-1][:2], ('必备', '个人防护'))

    def test_add_在对应节下追加(self):
        tools.write(self.p, '# 检查规则\n\n## 禁用\n\n- `旧词`\n\n## 提示（人工看）\n\n- `旧提示`\n')
        n = rules.add(self.p, [('禁用', '新词'), ('提示', '新提示'), ('禁用', '旧词'), ('正则禁用', r'第\d+节'),
                               ('乱写', '某词'), ('禁用', '  ')], 'K901 -- 注释')
        self.assertEqual(n, 3)          # 已有的、节名不对的、空词不加
        self.assertEqual(self.pairs(), [('禁用', '旧词'), ('禁用', '新词'), ('提示', '旧提示'), ('提示', '新提示'),
                                        ('正则禁用', r'第\d+节')])
        t = tools.read(self.p)
        self.assertEqual(t.count('<!-- K901 — 注释 -->'), 3)    # 注释里的 -- 换掉，免得提前结束注释
        self.assertNotIn('\n\n\n', t)
        self.assertEqual(rules.add(self.p, [('禁用', '新词')], 'K902'), 0)

    SAMPLE = ('# 检查规则\n\n<!-- 说明：每行一项\n- `注释里的词`\n-->\n\n## 禁用\n\n<!-- K003 2026-10-30 -->\n- `甲词`\n'
              '<!-- 用户 10-31：一组 -->\n- `乙词`\n- `丙词`\n<!-- K0031 别的号 -->\n- `丁词`\n\n## 正则禁用\n\n'
              '<!-- K003 2026-10-30 -->\n- `第\\d+节`\n- `([未`\n\n## 提示\n\n- `戊词`\n\n## 豁免\n\n- `甲词`\n')

    def test_items_跳过注释_去反引号_记行号(self):
        tools.write(self.p, self.SAMPLE)
        got = rules.items(self.p)
        lines = self.SAMPLE.split('\n')
        self.assertEqual([(s, w) for s, w, _ in got],
                         [('禁用', '甲词'), ('禁用', '乙词'), ('禁用', '丙词'), ('禁用', '丁词'),
                          ('正则禁用', r'第\d+节'), ('正则禁用', '([未'), ('提示', '戊词'), ('豁免', '甲词')])
        for s, w, i in got:
            self.assertEqual(lines[i], '- `%s`' % w)

    def test_remove_删词连同它的注释_别的节不动(self):
        tools.write(self.p, self.SAMPLE)
        self.assertEqual(rules.remove(self.p, '甲词'), 1)
        t = tools.read(self.p)
        self.assertNotIn('- `甲词`\n<!-- 用户', t)
        self.assertEqual(t.count('<!-- K003 2026-10-30 -->'), 1)      # 甲词上面那条注释删了，正则禁用的那条还在
        self.assertIn(('豁免', '甲词'), self.pairs())                    # 豁免节不在默认的节里
        before = self.p.stat().st_mtime_ns
        self.assertEqual(rules.remove(self.p, '没有的词'), 0)
        self.assertEqual(self.p.stat().st_mtime_ns, before)              # 没命中不重写文件

    def test_remove_几条共用的注释不该跟着删(self):
        # 10-31 主对话已改（原是程序毛病）：remove 只看上一行是不是注释，不管这条注释是不是几条共用；删“乙词”时把“丙词”的出处注释也删了
        tools.write(self.p, self.SAMPLE)
        rules.remove(self.p, '乙词')
        self.assertIn('<!-- 用户 10-31：一组 -->\n- `丙词`', tools.read(self.p))

    def test_demote_禁用降为提示(self):
        tools.write(self.p, self.SAMPLE)
        self.assertEqual(rules.demote(self.p, '丁词', 'K009 2026-10-31：降为提示'), '提示')
        self.assertNotIn(('禁用', '丁词'), self.pairs())
        self.assertIn(('提示', '丁词'), self.pairs())
        lines = tools.read(self.p).split('\n')
        self.assertEqual(lines[lines.index('- `丁词`') - 1], '<!-- K009 2026-10-31：降为提示 -->')

    def test_demote_提示里已有的只删禁用_正则禁用的直接删(self):
        tools.write(self.p, self.SAMPLE + '\n')
        rules.add(self.p, [('提示', '甲词')], '已有提示')
        rules.demote(self.p, '甲词', 'K009')
        self.assertEqual([s for s, w in self.pairs() if w == '甲词'], ['提示', '豁免'])   # 不重复加提示
        self.assertEqual(rules.demote(self.p, r'第\d+节', 'K009'), '删除')
        self.assertFalse([w for s, w in self.pairs() if w == r'第\d+节'])

    def test_remove_by_note_按K号删(self):
        tools.write(self.p, self.SAMPLE)
        # 一条注释管紧跟着的几行：正则禁用节 K003 注释下的两条一起删
        self.assertEqual(rules.remove_by_note(self.p, ['K003']), 3)
        left = self.pairs()
        for x in (('禁用', '甲词'), ('正则禁用', r'第\d+节'), ('正则禁用', '([未')):
            self.assertNotIn(x, left)
        for x in (('禁用', '乙词'), ('禁用', '丙词'), ('禁用', '丁词'), ('提示', '戊词'), ('豁免', '甲词')):
            self.assertIn(x, left)                                       # K0031 不算 K003；注释后隔了别的行的不删
        t = tools.read(self.p)
        self.assertNotIn('K003 2026', t)
        self.assertIn('<!-- K0031 别的号 -->', t)
        before = self.p.stat().st_mtime_ns
        self.assertEqual(rules.remove_by_note(self.p, []), 0)
        self.assertEqual(rules.remove_by_note(self.p.with_name('没有.md'), ['K003']), 0)
        self.assertEqual(self.p.stat().st_mtime_ns, before)

    def test_remove_by_note_一条注释下几条一起删(self):
        tools.write(self.p, self.SAMPLE.replace('用户 10-31：一组', 'K005 一组'))
        self.assertEqual(rules.remove_by_note(self.p, ['K005']), 2)
        self.assertFalse([w for s, w in self.pairs() if w in ('乙词', '丙词')])

    def test_valid_regex_写错的正则去掉(self):
        tools.write(self.p, self.SAMPLE.replace('## 提示\n', '## 提示\n\n- `([未`\n'))
        self.assertEqual(rules.valid_regex(self.p), ['([未'])
        left = self.pairs()
        self.assertEqual([w for s, w in left if s == '正则禁用'], [r'第\d+节'])
        self.assertIn(('提示', '([未'), left)                              # 提示按字面查，不是正则，不删
        self.assertEqual(rules.valid_regex(self.p), [])


def _objects(s):
    """schema 里所有带 properties 的对象（含嵌套数组里的），按遍历顺序。"""
    if isinstance(s, dict):
        if isinstance(s.get('properties'), dict):
            yield s
        for k, v in s.items():
            for sub in (v.values() if k == 'properties' and isinstance(v, dict) else [v]):
                yield from _objects(sub)
    elif isinstance(s, list):
        for x in s:
            yield from _objects(x)


def _unschema(s):
    """把 _keyed 加在 properties、required 上的两位序号去掉，其余照搬（用来比 schema 别的地方动没动）。"""
    if isinstance(s, list):
        return [_unschema(x) for x in s]
    if not isinstance(s, dict):
        return s
    out = {}
    for k, v in s.items():
        if k == 'properties' and isinstance(v, dict):
            out[k] = {n[2:]: _unschema(x) for n, x in v.items()}
        elif k == 'required' and isinstance(s.get('properties'), dict):
            out[k] = [n[2:] for n in v]
        else:
            out[k] = _unschema(v)
    return out


def _sample(s):
    """照 schema 编一份结果。字符串值故意以数字开头：读回来时不能被当成序号去掉。"""
    if 'enum' in s:
        return s['enum'][-1]
    t = s.get('type')
    if t == 'object':
        return {k: _sample(v) for k, v in s['properties'].items()}
    if t == 'array':
        return [_sample(s['items']), _sample(s['items'])]
    return {'integer': 7, 'boolean': True}.get(t, '07 第七章')


def _as_model(x):
    """模型按键名的码位顺序写：把每个对象的键排序。"""
    if isinstance(x, list):
        return [_as_model(v) for v in x]
    if isinstance(x, dict):
        return {k: _as_model(x[k]) for k in sorted(x)}
    return x


def _key_order(x):
    if isinstance(x, list):
        return [_key_order(v) for v in x]
    if isinstance(x, dict):
        return [(k, _key_order(v)) for k, v in x.items()]
    return None


SCHEMAS = {'review.REVIEW': review.REVIEW, 'review.CROSS': review.CROSS, 'revise.DECIDE': revise.DECIDE,
           'common.EDITS': common.EDITS, 'facts.FIX': facts.FIX, 'new.INFO': new.INFO}
DISTILL = {'distill.K_SCHEMA': distill.K_SCHEMA, 'distill.R_DIM': distill.R_DIM, 'distill.K_ENTRY': distill.K_ENTRY,
           'distill._card_schema': distill._card_schema(['07.md', '03_风险评估法.md']),
           'distill._fill_schema': distill._fill_schema(['07.md'])}


class T03_结构化输出加序号(Base):
    def check_schema(self, name, s):
        before = copy.deepcopy(s)
        k = llm._keyed(s)
        self.assertEqual(s, before, '%s：_keyed 改了原 schema' % name)
        self.assertEqual(_unschema(k), s, '%s：除了键名序号，别处也动了' % name)
        pairs = list(zip(_objects(s), _objects(k)))
        self.assertEqual(len(pairs), len(list(_objects(s))))
        self.assertEqual(len(list(_objects(k))), len(pairs))
        for o, kk in pairs:
            names, keyed = list(o['properties']), list(kk['properties'])
            self.assertEqual(keyed, sorted(keyed), '%s：加序号后键的码位顺序不等于原来的顺序' % name)
            self.assertEqual(keyed, ['%02d%s' % (i, n) for i, n in enumerate(names, 1)])
            self.assertEqual([x[2:] for x in kk['required']], o['required'])
            self.assertTrue(set(kk['required']) <= set(keyed))
        return k

    def test_各流程的schema(self):
        nested = 0
        for name, s in SCHEMAS.items():
            k = self.check_schema(name, s)
            nested += len(list(_objects(s))) - 1
            want = _sample(s)
            got = llm._unkeyed(_as_model(_sample(k)))
            self.assertEqual(got, want, name)
            self.assertEqual(_key_order(got), _key_order(want), '%s：去掉序号后键的先后不是原来写的顺序' % name)
        self.assertGreaterEqual(nested, 6)          # 审稿意见、改动、规则、要点卡……数组里的对象都查到了

    def test_审稿意见按原来写的顺序生成(self):
        names = list(review.REVIEW['properties']['意见']['items']['properties'])
        self.assertNotEqual(names, sorted(names))   # 不加序号时“问题”排在“依据”“原文”“新文”后面
        k = llm._keyed(review.REVIEW)['properties']['01意见']['items']
        self.assertEqual(list(k['properties']), ['01行', '02级别', '03问题', '04依据', '05原文', '06新文'])
        self.assertEqual(k['properties']['02级别']['enum'], ['必改', '建议'])
        self.assertEqual(k['additionalProperties'], False)

    def test_distill_自己的一位序号_套两层(self):
        for name, s in DISTILL.items():
            for o in _objects(s):
                ks = list(o['properties'])
                self.assertEqual(ks, sorted(ks), '%s：distill 自己的序号顺序不对' % name)
            k = self.check_schema(name, s)
            want = distill._plain(_sample(s))
            got = distill._plain(llm._unkeyed(_as_model(_sample(k))))
            self.assertEqual(got, want, name)
            self.assertEqual(_key_order(got), _key_order(want), name)
        entry = distill._plain(llm._unkeyed(_as_model(_sample(llm._keyed(distill.K_SCHEMA)))))['条目'][0]
        self.assertEqual(list(entry), ['主题', '标题', '日期', '规矩', '用户原话', '例外', '出处', '检查', '审稿'])
        self.assertEqual(list(entry['检查'][0]), ['节', '词'])

    def test_读回来只去键名的序号(self):
        x = {'01意见': [{'01行': 3, '03问题': '12处', '05原文': '07 原文'}], '02跳过': ['01 不去'], '说明': '无序号'}
        self.assertEqual(llm._unkeyed(x), {'意见': [{'行': 3, '问题': '12处', '原文': '07 原文'}], '跳过': ['01 不去'], '说明': '无序号'})
        self.assertEqual(llm._unkeyed('01字'), '01字')
        self.assertEqual(llm._unkeyed({'12': 1, '1a': 2}), {'12': 1, '1a': 2})   # 只有两位数字的键不去成空键

    def test_结果照schema粗查(self):
        for name, s in {**SCHEMAS, **DISTILL}.items():
            self.assertEqual(llm._misfit(_sample(s), s), '', name)       # 照 schema 出的都放过
        ok = {'意见': [{'行': 27, '级别': '必改', '问题': 'q', '依据': 'K1', '原文': 'a', '新文': 'b'}]}
        self.assertEqual(llm._misfit(ok, review.REVIEW), '')
        self.assertEqual(llm._misfit({'意见': []}, review.REVIEW), '')
        # 11-03 田林审第1章：中转回了光秃秃的数组，键名也不对（“行号”，还是文字）
        bare = [{'行号': '27', '级别': '必改', '问题': 'q', '依据': 'K1', '原文': 'a', '新文': 'b'}]
        self.assertIn('数组', llm._misfit(bare, review.REVIEW))
        self.assertIn('缺“行”', llm._misfit({'意见': bare}, review.REVIEW))
        self.assertIn('不在可选值', llm._misfit({'意见': [dict(ok['意见'][0], 级别='必须改')]}, review.REVIEW))
        self.assertIn('整数', llm._misfit({'意见': [dict(ok['意见'][0], 行=True)]}, review.REVIEW))


def _lock(root):
    return Path(root) / jobs.LOCK_NAME


def _record_file(jid, state, steps=(), age=0):
    """在 任务/ 里放一份记录文件（像命令行跑的任务留下的），age 秒以前更新的。"""
    d = jobs.JOBS_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    p = d / '任务.json'
    p.write_text(json.dumps({'号': jid, '类别': '审稿', '标题': '审稿', '状态': state, '建立': time.time() - age,
                             '步骤': list(steps), '日志': [[0, 'x']], '详情': '', '参数': {'章': '07'}}, ensure_ascii=False),
                 encoding='utf-8')
    if age:
        os.utime(p, (time.time() - age, time.time() - age))
    return p


class T04_任务和锁(Base):
    def setUp(self):
        super().setUp()
        self.root = TMP / ('t04_' + self._testMethodName)
        (self.root / '正文').mkdir(parents=True)
        self.m = jobs.Manager()
        self.started = []

    def tearDown(self):
        for j in self.started:
            j.cancelled = True
        for j in self.started:
            wait(j)
        super().tearDown()

    def start(self, secs=5, fail=False, root=None):
        j = self.m.start('自测', '自测', {'秒': secs, '失败': fail}, flows.get('自测'), root or self.root)
        self.started.append(j)
        return j

    def test_同一项目第二个当场抛Busy(self):
        a = self.start()
        lock = json.loads(_lock(self.root).read_text(encoding='utf-8'))
        self.assertEqual((lock['号'], lock['类别']), (a.id, '自测'))
        with self.assertRaises(jobs.Busy) as cm:
            self.start()
        self.assertIn(a.id, str(cm.exception))
        self.assertEqual(len([j for j in self.m.list() if j.get('项目') == str(self.root)]), 1)
        other = TMP / 't04_另一个项目'
        (other / '正文').mkdir(parents=True, exist_ok=True)
        self.start(root=other)                     # 别的项目不受影响

    def test_锁10分钟没续就作废_新任务接过去(self):
        p = _lock(self.root)
        p.write_text(json.dumps({'号': '旧任务', '类别': '审稿', 'pid': 1}, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(jobs.Busy) as cm:
            self.start()
        self.assertIn('旧任务', str(cm.exception))
        old = time.time() - jobs.STALE - 5
        os.utime(p, (old, old))
        j = self.start()
        self.assertEqual(json.loads(p.read_text(encoding='utf-8'))['号'], j.id)

    def test_取消_自测5秒变已取消_锁删掉(self):
        j = self.start(secs=5)
        time.sleep(1.3)
        self.assertTrue(self.m.cancel(j.id))
        wait(j, 10)
        self.assertEqual(j.state, '已取消')
        self.assertLess(j.ended - j.started, 4)
        self.assertFalse(_lock(self.root).exists())
        self.assertFalse(self.m.cancel(j.id))       # 已结束的不能再取消
        self.assertFalse(self.m.cancel('没有这个号'))
        self.assertEqual(self.m.get(j.id)['状态'], '已取消')
        time.sleep(0.2)
        self.assertEqual(json.loads((j.dir / '任务.json').read_text(encoding='utf-8'))['状态'], '已取消')

    def test_出错_状态失败_有中文说明_锁删掉(self):
        j = wait(self.start(secs=0, fail=True), 10)
        self.assertEqual(j.state, '失败')
        self.assertTrue(CJK.search(j.error), j.error)
        self.assertIn('RuntimeError', j.trace)
        self.assertFalse(_lock(self.root).exists())
        self.assertTrue(any('出错' in t for _, t in j.logs))

    def test_出错_程序内部的英文异常也该有中文说明(self):
        # 10-31 主对话已改（原是程序毛病）：jobs.run 把 str(e) 原样当“错误”；KeyError 只剩 "'意见'"，OSError 是英文，网页上看不懂
        j = jobs.Job('改稿', '改稿', {}, None)
        jobs.run(j, revise.job_revise)               # 参数里没有“意见” → KeyError
        self.assertEqual(j.state, '失败')
        self.assertTrue(CJK.search(j.error.replace('意见', '')), j.error)

    def test_命令行run遇到占着的项目_失败不动别人的锁(self):
        a = self.start()
        j = jobs.run(jobs.Job('审稿', '审稿', {}, self.root), lambda job: self.fail('不该开始'))
        self.assertEqual(j.state, '失败')
        self.assertIn('正在跑别的任务', j.error)
        self.assertEqual(json.loads(_lock(self.root).read_text(encoding='utf-8'))['号'], a.id)

    def test_lock_more(self):
        free, stale = TMP / 't04_空闲项目', TMP / 't04_过期项目'
        for d in (free, stale):
            (d / '正文').mkdir(parents=True, exist_ok=True)
        a = self.start()                            # 占着 self.root
        b = jobs.Job('改稿', '改稿', {}, None)
        with self.assertRaises(jobs.Busy):
            jobs.lock_more(b, [free, self.root])
        self.assertFalse(_lock(free).exists())      # 报错前一个都不锁
        _lock(stale).write_text('{"号": "旧"}', encoding='utf-8')
        old = time.time() - jobs.STALE - 5
        os.utime(_lock(stale), (old, old))
        jobs.lock_more(b, [free, stale])
        jobs.lock_more(b, [free])                   # 自己锁过的不算占着
        for d in (free, stale):
            self.assertEqual(json.loads(_lock(d).read_text(encoding='utf-8'))['号'], b.id)
        jobs._release(b)
        self.assertFalse(_lock(free).exists() or _lock(stale).exists())
        self.assertEqual(json.loads(_lock(self.root).read_text(encoding='utf-8'))['号'], a.id)

    def test_改标准的任务锁标准_同时只能一个(self):
        a = jobs.Job('改稿', '改稿', {}, None)
        jobs._acquire(a)
        self.assertTrue(_lock(config.STD).exists())
        with self.assertRaises(jobs.Busy) as cm:
            jobs._acquire(jobs.Job('提炼标准', '提炼标准', {}, None))
        self.assertIn('标准', str(cm.exception))
        jobs._release(a)
        self.assertFalse(_lock(config.STD).exists())

    def test_get_10分钟没更新的进行记录返回中断(self):
        _record_file('1031-000001-aaaa', '进行', age=jobs.STALE + 30)
        _record_file('1031-000002-bbbb', '进行')
        self.assertEqual(self.m.get('1031-000001-aaaa')['状态'], '中断')
        self.assertEqual(self.m.get('1031-000002-bbbb')['状态'], '进行')
        self.assertIsNone(self.m.get('1031-没有'))

    def test_list_记录文件来的任务也有进行中(self):
        steps = [{'名称': '审 07', '状态': '进行'}, {'名称': '审 05', '状态': '完成'}, {'名称': '审 06', '状态': '进行'}]
        _record_file('1031-000003-cccc', '进行', steps)
        _record_file('1031-000004-dddd', '进行', steps, age=jobs.STALE + 30)
        a = self.start()
        rows = {x['号']: x for x in self.m.list()}
        x = rows['1031-000003-cccc']
        self.assertEqual((x['状态'], x['步骤数'], x['进行中']), ('进行', 3, ['审 07', '审 06']))
        self.assertFalse({'日志', '详情', '参数', '步骤'} & set(x))
        y = rows['1031-000004-dddd']
        self.assertEqual((y['状态'], y['进行中']), ('中断', []))
        self.assertIn('进行中', rows[a.id])
        self.assertIn('步骤数', rows[a.id])


class T05_命令行参数(Base):
    def job(self, *argv):
        return cli._job(cli._parser().parse_args([str(x) for x in argv]))

    def test_审稿(self):
        self.assertEqual(self.job('审稿', '/tmp/某项目', '--章', '07,05', '--只看', '--重点', '防护用品', '--出稿'),
                         ('审稿', '审稿 第07,05章', {'修改': False, '章': '07,05', '重点': '防护用品', '出稿': True}, '/tmp/某项目'))
        self.assertEqual(self.job('审稿', '/tmp/某项目'),
                         ('审稿', '审稿', {'修改': True, '章': '', '重点': '', '出稿': False}, '/tmp/某项目'))

    def test_改稿_不带项目(self):
        words = '第7章不写安全帽、防静电服这些安全防护用品'
        k, title, p, proj = self.job('改稿', words)
        self.assertEqual((k, title, proj), ('改稿', '改稿：' + words[:20], None))
        self.assertEqual(p, {'意见': words, '项目': '全部', '出稿': True, '日期': ''})

    def test_改稿_带项目时参数里有锁(self):
        a, b = TMP / 't05_甲', TMP / 't05_乙'
        for d in (a, b):
            d.mkdir(exist_ok=True)
        rel = os.path.relpath(b)
        k, title, p, proj = self.job('改稿', '意见', '--项目', a, rel, TMP / 't05_没有', '--不出稿', '--日期', '2026-11-02')
        self.assertEqual((k, proj), ('改稿', None))
        self.assertEqual(p['项目'], [str(a), rel, str(TMP / 't05_没有')])
        self.assertEqual(p['_锁'], [str(a.resolve()), str(b.resolve())])     # 不存在的不锁，相对路径转成绝对路径
        self.assertEqual((p['出稿'], p['日期']), (False, '2026-11-02'))

    def test_新报告_出稿_事实库_提炼标准_自测(self):
        k, title, p, proj = self.job('新报告', '/tmp/新项目', '--方法', '风险评估法', '--资料', 'a.pdf', 'b.docx', '--名称', '甲项目',
                                     '--编号', 'AT-2026-Y099', '--不审稿', '--只写', '07', '05')
        self.assertEqual((k, title, proj), ('新报告', '新报告', '/tmp/新项目'))
        self.assertEqual(p, {'文件夹': '/tmp/新项目', '方法': '风险评估法', '资料': ['a.pdf', 'b.docx'], '名称': '甲项目', '简称': '',
                             '编号': 'AT-2026-Y099', '日期': '', '审稿': False, '出稿': True, '只写': ['07', '05'], '接着做': False})
        self.assertTrue(self.job('新报告', '/tmp/新项目', '--方法', '类比法', '--接着做')[2]['接着做'])
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            self.job('新报告', '/tmp/新项目', '--方法', '别的法')
        self.assertEqual(self.job('出稿', '/tmp/甲'), ('出稿', '出稿', {}, '/tmp/甲'))
        self.assertEqual(self.job('事实库', '/tmp/甲'), ('事实库', '事实库', {}, '/tmp/甲'))
        self.assertEqual(self.job('提炼标准', '--只做', '口径', '补漏'), ('提炼标准', '提炼标准', {'只做': ['口径', '补漏']}, None))
        self.assertEqual(self.job('自测', '--秒', '5', '--失败'), ('自测', '自测', {'秒': 5, '失败': True}, None))
        k, title, p, proj = self.job('类比资料', '/tmp/甲', '--资料', TMP / '控评.pdf', '--说明', '古交正沟控评')
        self.assertEqual((k, title, proj, p), ('类比资料', '补类比资料', '/tmp/甲', {'资料': [str((TMP / '控评.pdf').resolve())], '说明': '古交正沟控评'}))
        self.assertTrue(self.job('类比资料', '/tmp/甲', '--接着做')[2]['接着做'])

    def test_main_跑自测(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(['自测', '--秒', '1'])
        self.assertEqual(rc, 0)
        self.assertIn('== 完成', buf.getvalue())
        self.assertIn('第1步完成', buf.getvalue())


class T10_切分和设置(Base):
    def test_sections_按标记切分(self):
        text = '<<<正文>>>\n\n# 7 标题\n\n正文一段。\n<<< 可研没有 >>>\n- 可研没有写耳塞型号\n<<<别的>>>不算标记\n'
        got = llm.sections(text, ['正文', '可研没有'])
        self.assertEqual(got, {'正文': '# 7 标题\n\n正文一段。', '可研没有': '- 可研没有写耳塞型号\n<<<别的>>>不算标记'})
        self.assertEqual(llm.sections('  只有正文 \n', ['正文', '可研没有']), {'正文': '只有正文'})
        self.assertEqual(llm.sections('前言 <<<正文>>>\n甲', ['正文']), {'正文': '前言 <<<正文>>>\n甲'})   # 不在行首的不算

    def test_sections_漏了第一个标记时正文不该丢(self):
        # 10-31 主对话已改（原是程序毛病）：模型漏写 <<<正文>>>、只写了 <<<可研没有>>> 时，第一个标记前的正文整段丢掉；
        # new._write_file 随后报“模型给的正文太短”，这一章白写（facts.build 的 <<<事实库>>> 一样）
        got = llm.sections('# 7 标题\n\n正文一段。\n<<<可研没有>>>\n无', ['正文', '可研没有'])
        self.assertEqual(got.get('正文'), '# 7 标题\n\n正文一段。')

    def test_public_不带密钥(self):
        pub = config.public()
        self.assertNotIn('密钥', pub)
        self.assertFalse(FAKE_KEY in json.dumps(pub, ensure_ascii=False), 'public 里带了密钥')
        self.assertEqual((pub['有密钥'], pub['密钥来源']), (True, '本程序设置'))
        self.assertEqual(pub['模型'], config.DEFAULTS['模型'])
        _, key, _ = config.credentials({'模型': 'x'})       # 没填密钥：从环境变量或本机 Claude Code 设置拿（只比，不打印）
        pub2 = config.public({'模型': 'x'})
        self.assertNotIn('密钥', pub2)
        self.assertEqual(pub2['有密钥'], bool(key))
        self.assertFalse(key and key in json.dumps(pub2, ensure_ascii=False), 'public 里带了本机密钥')

    def test_默认不走系统代理(self):
        # 11-01 夜里：系统代理（代理软件）线路断了，接口库照它走，程序连不上中转，对话却照常
        real = _saved[(llm, 'client')]
        env = {'HTTPS_PROXY': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9', 'NO_PROXY': '', 'no_proxy': ''}
        with mock.patch.dict(os.environ, env):
            for on in (False, True):
                cl = real({'密钥': FAKE_KEY, '地址': 'https://relay.example.com', '走系统代理': on})
                try:
                    hx = cl._client
                    via = hx._transport_for_url(hx.build_request('POST', 'https://relay.example.com/v1/messages').url)
                    self.assertEqual(via is hx._transport, not on, '走系统代理=%s' % on)
                    self.assertEqual(cl.timeout.read, 1800.0)
                finally:
                    cl.close()
            self.assertIn('勾上“走系统代理”', llm._proxy_hint({}))
            self.assertIn('取消“走系统代理”', llm._proxy_hint({'走系统代理': True}))
        self.assertFalse(config.DEFAULTS['走系统代理'])


def _std_copy(name):
    """给一个测试单独一份标准副本（从模块开头复制的 TMP/标准 再复制）。"""
    dst = TMP / name
    shutil.copytree(TMP / '标准', dst)
    return dst


class T06_改稿的小函数(Base):
    def setUp(self):
        super().setUp()
        self.root = TMP / ('t06_' + self._testMethodName)
        body = self.root / '正文'
        body.mkdir(parents=True)
        tools.write(body / '01_甲.md', '# 1 甲\n\n甲词在这里，甲词又一次。\n<!-- 甲词 注释里的 -->\n<!--\n甲词 多行注释里的\n-->\n第二行甲词。\n'
                                       '括号写错([未 的正则\n')
        tools.write(body / '02_乙.md', '# 2 乙\n\n没有。\n')
        tools.write(body / '_草稿.md', '甲词\n')
        self.std = _std_copy('t06_标准_' + self._testMethodName)
        self.patch = mock.patch.object(config, 'STD', self.std)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def test_count_hits_不算注释里的(self):
        self.assertEqual(revise.count_hits(self.root, '禁用', '甲词'), 2)       # 按行数；注释、下划线开头的文件不算
        self.assertEqual(revise.count_hits(self.root, '正则禁用', r'甲.又'), 1)
        self.assertEqual(revise.count_hits(self.root, '正则禁用', '([未'), 0)    # 写错的正则不算命中
        self.assertEqual(revise.count_hits(self.root, '提示', '([未'), 1)        # 提示按字面查

    def test_hit_lines_写错的正则按字面查(self):
        self.assertEqual(revise._hit_lines(self.root, ['([未']), {'01_甲.md': {9}})
        self.assertEqual(revise._hit_lines(self.root, [r'第.行', '  ']), {'01_甲.md': {8}})
        self.assertEqual(revise._hit_lines(self.root, ['甲词'])['01_甲.md'] - {6}, {3, 8})   # 单行注释不算
        self.assertEqual(revise._hit_lines(self.root, ['没有的词']), {})

    def test_hit_lines_多行注释里的也不该算(self):
        # 10-31 主对话已改（原是程序毛病）：_hit_lines 只跳过以 <!-- 开头的行，多行注释中间的行照算（count_hits 是去掉注释再查的，两边口径不一）；
        # 只在骨架注释里出现的词也会让这个文件多调一次模型
        self.assertEqual(revise._hit_lines(self.root, ['甲词']), {'01_甲.md': {3, 8}})

    def test_next_k(self):
        p = self.std / '用户口径.md'
        p.unlink()
        self.assertEqual(revise._next_k(), 'K001')            # 没有文件
        tools.write(p, '')
        self.assertEqual(revise._next_k(), 'K001')            # 空文件
        tools.write(p, '# 用户口径\n\n## K001 甲（2026-10-01）\n- 规矩：…\n\n## K003 乙（2026-10-02）（已被 K004 取代）\n- 见 K009\n')
        self.assertEqual(revise._next_k(), 'K004')

    def d(self, **kw):
        base = {'范围': '通用', '标题': '新条', '规矩': '不写某词。', '例外': '', '取代': [], '规则': [], '要点卡': [],
                '改法': '删掉', '关键词': [], '涉及章': [], '要核事实': False}
        base.update(kw)
        return base

    def test_add_k_推翻旧条(self):
        p = self.std / '用户口径.md'
        tools.write(p, '# 用户口径\n\n## K001 甲（2026-10-01）\n- 规矩：甲\n\n## K0011 别的\n\n## K002 乙（2026-10-02）（已被 K003 取代）\n- 规矩：乙\n')
        revise._add_k('K004', self.d(取代=['K001', 'K002']), '用户原话', '2026-10-31', [('禁用', '某词'), ('提示', '别词')])
        t = tools.read(p)
        self.assertIn('## K001 甲（2026-10-01）（已被 K004 取代）\n', t)
        self.assertIn('## K0011 别的\n', t)                                     # K0011 不是 K001
        self.assertIn('## K002 乙（2026-10-02）（已被 K003 取代）\n', t)         # 已经被取代过的不再加
        self.assertTrue(t.endswith('## K004 新条（2026-10-31）\n- 规矩：不写某词。\n- 用户原话：“用户原话”\n'
                                   '- 怎么查：禁用“某词”、提示“别词”\n- 例外：无\n'), t[-200:])
        p.unlink()
        revise._add_k('K001', self.d(), '话', '2026-10-31', [])
        t = tools.read(p)
        self.assertTrue(t.startswith('# 用户口径\n\n<!-- '))
        self.assertIn('## K001 新条（2026-10-31）\n', t)
        self.assertIn('- 怎么查：审稿\n', t)

    def test_add_cards_加到4红线节末尾(self):
        card = self.std / '各章要点' / '07.md'
        tools.write(card, '# 第7章要点\n\n## 3 怎么判\n- 判一\n\n## 4 红线\n- 旧红线一\n- 旧红线二\n\n## 5 常见错误\n- 错一\n')
        tools.write(card.with_name('09.md'), '# 第9章要点\n\n## 1 必答问题\n- 问一\n')
        revise._add_cards([{'卡': '07', '条目': '- 新红线 '}, {'卡': '09.md', '条目': '九章红线'},
                           {'卡': '没有这张卡.md', '条目': 'x'}], 'K050')
        self.assertEqual(tools.read(card), '# 第7章要点\n\n## 3 怎么判\n- 判一\n\n## 4 红线\n- 旧红线一\n- 旧红线二\n'
                                           '- 新红线（K050）\n\n## 5 常见错误\n- 错一\n')
        self.assertTrue(tools.read(card.with_name('09.md')).endswith('- 问一\n- 九章红线（K050）\n'))
        self.assertFalse((self.std / '各章要点' / '没有这张卡.md').exists())

    def test_targets_只取有正文的(self):
        empty = TMP / 't06_没有正文'
        empty.mkdir(exist_ok=True)
        self.assertEqual(revise.targets([str(self.root), str(empty), str(TMP / 't06_不存在')]), [self.root.resolve()])
        self.assertEqual(revise.targets(str(self.root)), [self.root.resolve()])
        allp = revise.targets('全部')        # 项目登记里的项目（只读）
        self.assertTrue(allp and all((p / '正文').is_dir() for p in allp))
        self.assertEqual(revise.targets(['全部']), allp)
        self.assertEqual(revise.targets(None), allp)



def _decide(scope, word):
    return {'范围': scope, '标题': '不写%s' % word, '规矩': '正文不写“%s”。' % word, '例外': '', '取代': [],
            '规则': [{'节': '禁用', '词': word}], '要点卡': [{'卡': '07.md', '条目': '不写%s' % word}] if scope == '通用' else [],
            '改法': '删掉这个词，句子读顺。', '关键词': [word], '涉及章': ['07'], '要核事实': False}


class T07_改稿走一遍(Base):
    def run_revise(self, name, scope, word):
        root = project(name)
        f = ch07(root)
        bad = SENT.replace('。', word + '。')
        tools.write(f, tools.read(f).replace(SENT, bad, 1))
        self.text0 = tools.read(f)
        std = _std_copy(name + '_标准')

        def modify(content, **_):
            return {'改动': [{'原文': bad, '新文': SENT, '说明': '删掉%s' % word}] if bad in content else [], '跳过': []}

        FAKE.reset(('定口径', lambda **k: _decide(scope, word)), ('改 ', lambda **k: modify(**k)))
        params = {'意见': '第7章不要写%s' % word, '项目': [str(root)], '出稿': False, '日期': '2026-10-31'}
        with mock.patch.object(config, 'STD', std):
            k0 = revise._next_k()
            j = jobs.run(jobs.Job('改稿', '改稿', params, None), revise.job_revise)
        self.assertEqual(j.state, '完成', j.error + '\n' + j.trace)
        self.assertFalse(_lock(root).exists() or _lock(std).exists(), '锁没删')
        self.assertEqual(tools.read(f), self.text0.replace(bad, SENT, 1))
        self.assertEqual(sum(1 for x in FAKE.labels() if x.startswith('改 ')), 1)    # 只命中第7章，只改它
        self.assertTrue(list((root / '备份').glob('正文_*_改稿前')))
        return root, std, k0, j.result

    def test_范围通用(self):
        word = '测试禁用词甲'
        root, std, k0, res = self.run_revise('t07_通用', '通用', word)
        self.assertEqual((res['口径号'], res['规则'], res['太宽改提示'], res['降为提示']), (k0, ['禁用：' + word], [], []))
        self.assertEqual(res['项目'][0]['改'], 1)          # 检查条数跟着真的通用检查规则走，不比
        ks = re.findall(r'^## (K\d+)', tools.read(std / '用户口径.md'), re.M)
        self.assertEqual(ks.count(k0), 1)
        self.assertEqual(ks[-1], k0)
        self.assertIn('## %s 不写%s（2026-10-31）\n' % (k0, word), tools.read(std / '用户口径.md'))
        self.assertIn(('禁用', word), [(s, w) for s, w, _ in rules.items(std / '通用检查规则.md')])
        self.assertIn('<!-- %s 2026-10-31 -->\n- `%s`' % (k0, word), tools.read(std / '通用检查规则.md'))
        card = tools.read(std / '各章要点' / '07.md')
        red = re.search(r'^## 4 红线.*?(?=^## |\Z)', card, re.M | re.S).group(0)
        self.assertIn('- 不写%s（%s）' % (word, k0), red)
        self.assertTrue(list((std / '_历史').glob('*_改稿' + k0)))
        note = tools.read(root / '输出' / '交稿说明.md')
        self.assertIn('## 按意见修改 %s（' % k0, note)
        self.assertIn('- 改 1 处：%s 1 处' % ch07(root).name, note)
        self.assertNotIn(word, tools.read(TMP / '标准' / '通用检查规则.md'))         # 别的标准副本没动

    def test_范围项目(self):
        word = '测试禁用词乙'
        root, std, k0, res = self.run_revise('t07_项目', '项目', word)
        self.assertEqual((res['口径号'], res['规则']), ('项目', ['禁用：' + word]))
        self.assertIn(('禁用', word), [(s, w) for s, w, _ in rules.items(root / '检查规则.md')])
        self.assertIn('<!-- 用户 2026-10-31：第7章不要写%s -->\n- `%s`' % (word, word), tools.read(root / '检查规则.md'))
        with mock.patch.object(config, 'STD', std):
            self.assertEqual(revise._next_k(), k0)                                   # 用户口径没加新条
        self.assertNotIn(word, tools.read(std / '通用检查规则.md'))
        self.assertFalse(list((std / '_历史').glob('*改稿项目')))                      # 范围为项目的不动标准、不备份标准
        self.assertIn('## 按意见修改 项目（', tools.read(root / '输出' / '交稿说明.md'))
        self.assertEqual(res['项目'][0]['改'], 1)


class T08_审稿走一遍(Base):
    def test_审第7章_按意见改(self):
        root = project('t08')
        f = ch07(root)
        text0 = tools.read(f)
        no = next(i for i, ln in enumerate(text0.split('\n'), 1) if SENT in ln)
        fixed = SENT[:-1] + '（测试改动）。'
        ops = [{'行': no, '级别': '必改', '问题': '本章还要评价', '依据': '审稿清单 R1', '原文': SENT, '新文': fixed},
               {'行': 99, '级别': '建议', '问题': '假意见', '依据': '无', '原文': '文件里没有的一句话。', '新文': '另一句。'}]
        FAKE.reset(('审 ', lambda **k: {'意见': ops}))
        job = jobs.Job('审稿', '审稿', {'章': '07'}, root)
        out = review.run(job, root, modify=True, chapters='07', focus='', publish=False)
        self.assertEqual(sorted(x for x in FAKE.labels() if not x.startswith('清零')),
                         sorted('审 %s·%s' % (f.name, n) for n, _, _ in review.PASSES))       # 分几遍审，三遍报的同一处只算一条
        call = FAKE.calls[0]
        self.assertIs(call['schema'], review.REVIEW)
        self.assertIn('%d\t%s' % (no, text0.split('\n')[no - 1]), call['内容'])                                   # 带行号的正文发过去了
        self.assertEqual(tools.read(f), text0.replace(SENT, fixed, 1))
        self.assertEqual({k: out[k] for k in ('审了', '没审成', '必改', '建议', '已改', '跨章')},
                         {'审了': 1, '没审成': [], '必改': 1, '建议': 1, '已改': 1, '跨章': None})
        self.assertEqual(len(out['跳过']), 1)
        self.assertIn('找不到原文', out['跳过'][0])
        self.assertEqual(set(out['检查']), {'错误', '提示', '待办'})
        rec = root / '审查' / '审稿记录' / f.name
        self.assertTrue(rec.exists())
        r = tools.read(rec)
        self.assertIn('| %d | 必改 | 本章还要评价 |' % no, r)
        self.assertIn('| 已改 |', r)
        self.assertIn('文件里找不到原文', r)
        self.assertEqual(out['记录'], str(root / '审查' / '审稿记录'))
        self.assertTrue(Path(out['备份']).is_dir())
        self.assertIn('审了 1 个文件：必改 1 条、建议 1 条，改了 1 处', tools.read(root / '输出' / '交稿说明.md'))

    def test_分遍审_各遍只带自己的R_一遍没成照用别的遍(self):
        root = project('t08_分遍')
        f = ch07(root)
        text0 = tools.read(f)
        fixed = SENT[:-1] + '（测试改动）。'
        names = [n for n, _, _ in review.PASSES]

        def fake(label, content, **k):
            if label.endswith(names[0]):
                raise llm.CallError('结果格式不对')
            if label.endswith(names[1]):
                return {'意见': [{'行': 2, '级别': '建议', '问题': '判定', '依据': 'R7', '原文': SENT, '新文': fixed}]}
            return {'意见': [{'行': 2, '级别': '必改', '问题': '名称', '依据': 'R2', '原文': SENT, '新文': fixed}]}
        FAKE.reset(('审 ', fake))
        out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=True, chapters='07')
        self.assertEqual((out['必改'], out['建议'], out['已改']), (1, 0, 1))                 # 同一处两遍都报：留必改那条
        self.assertEqual(out['没审全'], ['%s（%s 没审）' % (f.name, names[0])])
        self.assertEqual(tools.read(f), text0.replace(SENT, fixed, 1))
        self.assertIn('重复', tools.read(root / '审查' / '审稿记录' / f.name))
        self.assertEqual(review._sections('# x\n\n## R1 甲\n- a\n\n## R2 乙\n- b\n\n## R10 丙\n- c\n', (2, 10)),
                         '## R2 乙\n- b\n\n\n## R10 丙\n- c\n')
        FAKE.reset(('审 ', lambda **k: (_ for _ in ()).throw(llm.CallError('都不行'))))
        out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=False, chapters='07')
        self.assertEqual((out['审了'], out['没审成']), (0, [f.name]))                         # 几遍都没成：这章算没审成

    def test_说查不到的_资料里有就不改(self):
        # 11-01 第二次试审：事实库不摘电气内容，审稿把可研明写的“金属铠装”当成查不到删了
        root = project('t08_查不到')
        f = ch07(root)
        text0 = tools.read(f)
        words = [w for w in re.findall(r'[一-鿿]{4}', SENT) if text0.count(w) == 1]
        self.assertGreaterEqual(len(words), 2)
        keep, drop = words[0], words[-1]
        ops = [{'行': 1, '级别': '必改', '问题': '“%s”在资料里查不到' % keep, '依据': '事实库', '原文': SENT, '新文': SENT.replace(keep, '', 1)}]
        FAKE.reset(('审 ', lambda **k: {'意见': ops}))
        with mock.patch.object(tools, 'source_texts', lambda r: [('可研.txt', '……采用%s……' % keep)]):
            out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=True, chapters='07')
        self.assertEqual(out['已改'], 0)
        self.assertIn('资料原文里有', out['跳过'][0])
        self.assertEqual(tools.read(f), text0)
        ops[0].update(问题='“%s”在资料里查不到' % drop, 新文=SENT.replace(drop, '', 1))
        with mock.patch.object(tools, 'source_texts', lambda r: [('可研.txt', '……采用%s……' % keep)]):
            out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=True, chapters='07')
        self.assertEqual(out['已改'], 1)                                        # 资料里真没有的照删
        self.assertNotIn(drop, tools.read(f))

    def test_只看不改(self):
        root = project('t08_只看')
        f = ch07(root)
        text0 = tools.read(f)
        FAKE.reset(('审 ', lambda **k: {'意见': [{'行': 3, '级别': '必改', '问题': 'x', '依据': 'R1', '原文': SENT, '新文': SENT + '甲'}]}))
        out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=False, chapters='07')
        self.assertEqual((out['必改'], out['已改'], out['备份']), (1, 0, ''))
        self.assertEqual(tools.read(f), text0)
        self.assertFalse((root / '备份').exists())
        self.assertIn('## 审稿（只看不改）（', tools.read(root / '输出' / '交稿说明.md'))

    def test_试审查出的三种改动不自动改(self):
        # 11-01 试审第 7 章：只改章节号的“必改”改错过；“建议”往小结里加过“防寒”；新文带出禁用词的也不改
        root = project('t08_拦下')
        f = ch07(root)
        text0 = tools.read(f)
        sents = [x for x in dict.fromkeys(s + '。' for ln in text0.split('\n') if not re.match(r'^(#|\||<!--|表|注|!)', ln)
                                          for s in ln.split('。')[:-1] if len(s) >= 12 and not set(s) & set('\\|`'))
                 if text0.count(x) == 1][:4]
        self.assertEqual(len(sents), 4, '第 7 章里挑不出 4 句只出现一次的句子')
        a, b, c, d = sents
        ops = [{'行': 1, '级别': '必改', '问题': '章节号', '依据': 'R1', '原文': a, '新文': a[:-1] + '，判定方法同第2.3节。'},
               {'行': 1, '级别': '必改', '问题': '章节号', '依据': 'R1', '原文': a[:-1] + '，判定方法同第2.3节。', '新文': a},
               {'行': 1, '级别': '建议', '问题': '补防寒', '依据': 'R1', '原文': b, '新文': b[:-1] + '，不涉及防寒。'},
               {'行': 1, '级别': '必改', '问题': '禁用词', '依据': 'R5', '原文': c, '新文': c[:-1] + '，可研未写。'},
               {'行': 1, '级别': '必改', '问题': '正常', '依据': 'R1', '原文': d, '新文': d[:-1] + '（测试）。'}]
        self.assertTrue(review._ref_only(ops[1]['原文'], ops[1]['原文'].replace('2.3', '2.2')))
        ops[1] = dict(ops[1], 原文=a[:-1] + '，判定方法同第2.2节。', 新文=a[:-1] + '，判定方法同第2.3节。')     # 只改了节号的那条
        FAKE.reset(('审 ', lambda **k: {'意见': ops}))
        out = review.run(jobs.Job('审稿', '审稿', {'章': '07'}, root), root, modify=True, chapters='07', publish=False)
        now = tools.read(f)
        self.assertIn(d[:-1] + '（测试）。', now)                     # 正常的照改
        for x in (b, c):
            self.assertIn(x, now)                                     # 带出“防寒”“可研未写”的没改
        self.assertNotIn('防寒。', now.replace(text0, ''))
        self.assertEqual(out['已改'], 2)                              # 正常一条＋加了句子的第一条（它不止改了节号）
        why = ' '.join(out['跳过'])
        self.assertIn('改后带出检查词', why)
        self.assertIn('只改了章节号或表号', why)                      # 只改节号的那条：还没去文件里找原文，先被拦下
        rec = tools.read(root / '审查' / '审稿记录' / f.name)
        self.assertIn('只改了章节号或表号', rec)

    def test_审稿带上全文标题和表题(self):
        root = project('t08_目录')
        FAKE.reset(('审 ', lambda **k: {'意见': []}))
        review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=True, chapters='07', publish=False)
        content = FAKE.calls[0]['内容']
        self.assertIn('报告各章标题和表题', content)
        self.assertIn('# 2 ', content.split('报告各章标题和表题')[1][:3000])

    def test_审稿记录_没有意见那一行格数不对(self):
        # 10-31 主对话已改（原是程序毛病）：review._record 没有意见时补的那一行只有 6 格，表头 7 格
        root = TMP / 't08_记录'
        review._record(root, '07_甲.md', [])
        self.assertEqual(tools.parse_error(tools.read(root / '审查' / '审稿记录' / '07_甲.md'), '07_甲.md'), '')


class T08b_事实库核对(Base):
    def test_更正只许整条换整条(self):
        # 11-01 试审：模型把节标题填进“原文”、旧行填进“新文”，三个节标题被换成了事实
        fm = '# 事实库\n## 产品、原辅材料及用量\n- 甲 10 t（出处：可研 3.1）\n- 乙（出处：可研 3.2）\n## 生产工艺\n- 丙（出处：可研 4.1）\n'
        edits = [{'原文': '## 产品、原辅材料及用量', '新文': '- 甲 10 t（出处：可研 3.1）', '说明': '- 甲 12 t'},
                 {'原文': '- 乙（出处：可研 3.2）', '新文': '## 乙', '说明': 'x'},
                 {'原文': '- 甲 10 t（出处：可研 3.1）', '新文': '- 甲 12 t（出处：可研 3.1）', '说明': '数字'},
                 {'原文': '甲 10 t', '新文': '甲 12 t', '说明': '只给了半行'}]
        good, bad = facts._fix_ok(fm, edits)
        self.assertEqual([e['说明'] for e in good], ['数字'])
        self.assertEqual(len(bad), 3)
        new, done, _ = tools.apply_edits(fm, good)
        self.assertEqual(new.count('\n## '), 2)
        self.assertIn('- 甲 12 t', new)

    def test_摘事实带上报告各章(self):
        root = project('t08b')
        (root / '资料' / '事实库.md').unlink(missing_ok=True)
        src = [('可研.txt', '第一章 总论\n' + '本项目年产甲产品 10 万吨。\n' * 40)]
        good = '# 事实库\n## 项目概况\n' + ''.join('- 事实%d（出处：可研 1.%d）\n' % (i, i) for i in range(25))
        FAKE.reset(('摘事实', lambda **k: '<<<事实库>>>\n' + good + '<<<口径>>>\n# 口径\n'),
                   ('核对事实库', lambda **k: {'补充': [], '更正': [{'原文': '## 项目概况', '新文': '- 事实0（出处：可研 1.0）', '说明': '坏的'}]}))
        old = tools.source_texts
        tools.source_texts = lambda r: src
        try:
            res = facts.build(jobs.Job('事实库', '事实库', {}, root), root)
        finally:
            tools.source_texts = old
        self.assertEqual(res['更正'], 0)
        self.assertIn('## 项目概况', tools.read(root / '资料' / '事实库.md'))
        self.assertIn('报告各章', FAKE.calls[0]['内容'])


class T09_新报告写一章(Base):
    def test_写进文件前先改格式(self):
        root = project('t09')
        f = ch07(root)
        skel = tools.read(f)
        row = ROW
        broken = row.rsplit('|', 2)[0] + '|'
        body = skel.strip().replace(row, broken, 1)
        self.assertFalse(tools.format_ok(body, f.name))
        seen = {}

        def fmt(content, **_):
            seen['盘上'] = tools.read(f)                     # 改格式时文件还没写
            seen['内容'] = content
            return {'改动': [{'原文': broken, '新文': row, '说明': '补一格'}], '跳过': []}

        FAKE.reset(('写 ', lambda **k: '<<<正文>>>\n%s\n<<<可研没有>>>\n- 可研没有写耳塞的降噪值\n无\n' % body),
                   ('改格式 ', lambda **k: fmt(**k)))
        job = jobs.Job('新报告', '新报告', {}, root)
        with mock.patch.object(common, 'fix_format', wraps=common.fix_format) as ff:
            res = new._write_file(job, root, '风险评估法', None, f.name, [], [])
        ff.assert_called_once_with(job, f.name, body)
        self.assertEqual([x for x in FAKE.labels() if not x.startswith('清零')], ['写 ' + f.name, '改格式 ' + f.name])
        self.assertIs(FAKE.calls[1]['schema'], common.EDITS)
        self.assertEqual(seen['盘上'], skel)
        self.assertIn('格式错误：', seen['内容'])
        out = tools.read(f)
        self.assertTrue(tools.format_ok(out, f.name), tools.parse_error(out, f.name))
        self.assertEqual(out, skel.strip() + '\n')
        self.assertFalse(tools.check(root).get('格式错误'))
        self.assertEqual(res['可研没有'], ['可研没有写耳塞的降噪值'])
        self.assertEqual(res['字数'], len(skel.strip()))

    def test_正文太短时报错不写(self):
        root = project('t09_短')
        f = ch07(root)
        skel = tools.read(f)
        FAKE.reset(('写 ', lambda **k: '<<<正文>>>\n# 7 太短\n'))
        with self.assertRaises(llm.CallError):
            new._write_file(jobs.Job('新报告', '新报告', {}, root), root, '风险评估法', None, f.name, [], [])
        self.assertEqual(tools.read(f), skel)


class _FakeStream:
    def __init__(self, text):
        self.text = text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter([mock.Mock(type='text', text=self.text)])

    def get_final_message(self):
        u = mock.Mock(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        return mock.Mock(usage=u, content=[mock.Mock(type='text', text=self.text)], stop_reason='end_turn')


class _FakeClient:
    """假的 anthropic 客户端：记下发出去的参数，按码位顺序回一份带序号的 JSON。"""

    def __init__(self, text):
        self.text, self.kw = text, None
        self.messages = self
        self.beta = mock.Mock(messages=self)

    def stream(self, **kw):
        self.kw = kw
        return _FakeStream(self.text)

    def close(self):
        self.closed = True


class T03b_真call发出带序号的schema(Base):
    def test_发出去带序号_拿回来去掉(self):
        want = _sample(review.REVIEW)
        fake = _FakeClient(json.dumps(_as_model(_sample(llm._keyed(review.REVIEW))), ensure_ascii=False))
        job = jobs.Job('审稿', '审稿', {}, None)
        with mock.patch.object(llm, 'client', lambda cfg=None: fake):
            got = _saved[(llm, 'call')](job, '审 07_甲.md', '系统', '内容', schema=review.REVIEW)
        self.assertEqual(fake.kw['output_config']['format'], {'type': 'json_schema', 'schema': llm._keyed(review.REVIEW)})
        self.assertEqual(got, want)
        self.assertEqual(_key_order(got), _key_order(want))
        self.assertEqual(job.usage['次数'], 1)
        self.assertTrue(list(job.archive.glob('001_*.md')))                   # 输入输出存档在临时文件夹
        row = json.loads(llm.USAGE.read_text(encoding='utf-8').strip().split('\n')[-1])
        self.assertEqual((row['步骤'], row['输入'], row['输出']), ('审 07_甲.md', 100, 20))
        self.assertTrue(_inside(llm.USAGE))

    def test_结果格式不对_算没成_可以重来(self):
        # 以前这样的结果直接交给审稿那一步，一用就报错，这章就没审，也不重来
        bare = [{'行号': '27', '级别': '必改', '问题': 'q', '依据': 'K1', '原文': 'a', '新文': 'b'}]
        fake = _FakeClient(json.dumps(bare, ensure_ascii=False))
        job = jobs.Job('审稿', '审稿', {}, None)
        with mock.patch.object(llm, 'client', lambda cfg=None: fake), self.assertRaises(llm.CallError) as cm:
            _saved[(llm, 'call')](job, '审 01_总论.md', '系统', '内容', schema=review.REVIEW)
        self.assertTrue(cm.exception.retry)
        self.assertIn('结果格式不对', str(cm.exception))


class _StuckStream(_FakeStream):
    """先来一个事件，然后一直不来（中转只发心跳）；被关掉后迭代报错，像接口库那样。"""

    def __init__(self):
        super().__init__('')
        self.closed = threading.Event()

    def __iter__(self):
        yield mock.Mock(type='message_start')
        self.closed.wait(10)
        raise RuntimeError('stream closed')

    def close(self):
        self.closed.set()


class T03e_光有心跳不来内容(Base):
    def test_看门狗关流_算中途断了可以重来(self):
        # 11-05 田林专篇审稿：中转断线后有两遍挂了 70 多分钟
        stuck = _StuckStream()
        fake = _FakeClient('')
        fake.stream = lambda **kw: stuck
        job = jobs.Job('审稿', '审稿', {}, None)
        t0 = time.time()
        with mock.patch.object(llm, 'client', lambda cfg=None: fake), mock.patch.object(llm, 'IDLE', 0.3), \
                self.assertRaises(llm.CallError) as cm:
            _saved[(llm, 'call')](job, '审 04_3.md', '系统', '内容', schema=review.REVIEW)
        self.assertLess(time.time() - t0, 5)
        self.assertTrue(cm.exception.retry)
        self.assertIn(llm.CUT, str(cm.exception))

    def test_正常回答不受影响(self):
        fake = _FakeClient(json.dumps(_as_model(_sample(llm._keyed(review.REVIEW))), ensure_ascii=False))
        job = jobs.Job('审稿', '审稿', {}, None)
        with mock.patch.object(llm, 'client', lambda cfg=None: fake), mock.patch.object(llm, 'IDLE', 0.3):
            self.assertEqual(_saved[(llm, 'call')](job, '审 07.md', '系统', '内容', schema=review.REVIEW), _sample(review.REVIEW))


class T03d_跨章改动按文件名对(Base):
    def test_写成正文斜杠文件名也改(self):
        # 11-01 起的审稿：模型把文件写成“正文/07_甲.md”，跟正文文件名对不上，跨章一致一处也没改
        import tempfile
        got = []
        r = {'改动': [{'文件': '正文/07_甲.md', '原文': '甲', '新文': '乙', '说明': 's'},
                      {'文件': '附件/x.md', '原文': '甲', '新文': '乙', '说明': 's'}], '没改': []}
        job = jobs.Job('审稿', '审稿', {}, None)
        with mock.patch.object(llm, 'call_retry', lambda *a, **k: r), \
                mock.patch.object(tools, 'body_files', lambda root: ['05_乙.md', '07_甲.md']), \
                mock.patch.object(tools, 'read', lambda p: '甲'), mock.patch.object(tools, 'numbered', lambda t: t), \
                mock.patch.object(tools, 'write', lambda *a, **k: None), \
                mock.patch.object(common, 'fact_docs', lambda root: []), mock.patch.object(common, 'names_table', lambda root: ''), \
                mock.patch.object(common, 'apply', lambda job, root, f, edits, label: (got.append(f), (len(edits), []))[1]), \
                mock.patch.object(common, 'fix_errors', lambda *a, **k: None):
            out = review.cross(job, tempfile.mkdtemp(), '类比法', True)
        self.assertEqual(got, ['07_甲.md'])
        self.assertEqual(out['改'], 1)
        self.assertTrue(any('x.md 不是正文里的文件' in t for _, t in job.logs))


class T03c_整次重来(Base):
    def test_call_retry_断流重来_拒答不重来(self):
        real = _saved[(llm, 'call_retry')]
        job = jobs.Job('审稿', '审稿', {}, None)
        seen = []

        def flaky(job, label, *a, **k):
            seen.append(label)
            if len(seen) == 1:
                raise llm.CallError('%s：中转返回 502 bad gateway' % label)
            return {'意见': []}

        with mock.patch.object(llm, 'call', flaky), mock.patch.object(llm, 'RETRY_WAIT', (0, 0)):
            self.assertEqual(real(job, '审 07', 's', 'c', schema=review.REVIEW), {'意见': []})
        self.assertEqual(seen, ['审 07', '审 07（重来）'])
        self.assertTrue(any('没成' in t and '后重来' in t for _, t in job.logs))
        seen.clear()

        def refuse(job, label, *a, **k):
            seen.append(label)
            raise llm.CallError('%s：模型拒答（类别 x）' % label)

        with mock.patch.object(llm, 'call', refuse), self.assertRaises(llm.CallError):
            real(job, '审 05', 's', 'c')
        self.assertEqual(seen, ['审 05'])

    def test_连断三次才算没成_再来也一样的不重来(self):
        # 11-01 田林：核对事实库两次都中途断了，第三次之前任务就失败了
        real = _saved[(llm, 'call_retry')]
        job = jobs.Job('审稿', '审稿', {}, None)
        seen = []

        def cut(job, label, *a, **k):
            seen.append(label)
            if len(seen) < 3:
                raise llm.CallError('%s：%s（收到 0 字）' % (label, llm.CUT))
            return '好了'

        with mock.patch.object(llm, 'call', cut), mock.patch.object(llm, 'RETRY_WAIT', (0, 0)):
            self.assertEqual(real(job, '核对事实库', 's', 'c'), '好了')
        self.assertEqual(seen, ['核对事实库', '核对事实库（重来）', '核对事实库（重来 2）'])
        seen.clear()

        def no_key(job, label, *a, **k):
            seen.append(label)
            raise llm.CallError('没有密钥', retry=False)

        with mock.patch.object(llm, 'call', no_key), mock.patch.object(llm, 'RETRY_WAIT', (0, 0)), \
                self.assertRaises(llm.CallError):
            real(job, '审 05', 's', 'c')
        self.assertEqual(seen, ['审 05'])

    def test_余额不够整个任务停下(self):
        # 11-05 田林专篇：中转余额用完返回 403，清零、各章各遍照样一个个去试，日志刷了上百条
        e = llm.CallError('x', fatal=True)
        self.assertFalse(e.retry)
        job = jobs.Job('审稿', '审稿', {}, None)
        seen = []

        def one(x):
            seen.append(x)
            if x == 'b':
                raise llm.CallError('审 b：中转返回 403', fatal=True)
            if x == 'c':
                raise llm.CallError('审 c：中途断了')
            return x

        with self.assertRaises(llm.CallError) as cm:
            review.pmap(job, one, ['a', 'b', 'c'])
        self.assertTrue(cm.exception.fatal)
        self.assertTrue(any('审 c' in t for _, t in job.logs))     # 不是余额问题的照常记日志
        self.assertEqual(review.pmap(job, lambda x: x, ['a']), ['a'])

    def test_等着重来时取消就停(self):
        real = _saved[(llm, 'call_retry')]
        job = jobs.Job('审稿', '审稿', {}, None)

        def cut(job, label, *a, **k):
            job.cancelled = True
            raise llm.CallError('%s：%s' % (label, llm.CUT))

        t = time.time()
        with mock.patch.object(llm, 'call', cut), self.assertRaises(llm.Cancelled):
            real(job, '审 05', 's', 'c')
        self.assertLess(time.time() - t, 5)


class _CutStream(_FakeStream):
    """网络那头中途断了：流正常关了，没有结束标记（stop_reason 是 None）。"""

    def get_final_message(self):
        m = super().get_final_message()
        m.stop_reason = None
        return m


class _NoStartStream(_FakeStream):
    """连回答的开头都没收到，流就结束了：接口库在 get_final_message 里断言失败。"""

    def __iter__(self):
        return iter([])

    def get_final_message(self):
        raise AssertionError()


class _DropStream(_FakeStream):
    """回答读到一半连接断了：网络库报自己的错（接口库不替它换）。"""

    def __iter__(self):
        yield mock.Mock(type='text', text='半截')
        raise llm._httpx.RemoteProtocolError('peer closed connection without sending complete message body')


class T03d_中途断了(Base):
    def _call(self, stream, schema=None):
        fake = _FakeClient(self.text)
        fake.stream = lambda **kw: stream(self.text)
        job = jobs.Job('新报告', '新报告', {}, None)
        with mock.patch.object(llm, 'client', lambda cfg=None: fake):
            with self.assertRaises(llm.CallError) as cm:
                _saved[(llm, 'call')](job, '写 07_甲.md', '系统', '内容', schema=schema)
        return job, str(cm.exception), cm.exception

    def test_没收到结束标记_报中途断了(self):
        # 11-01 田林：核对事实库两次 0 字就断，原先报“结果不是合法 JSON”；不带格式的调用会把半截当全文用
        self.text = '{"01补充": []'
        job, msg, e = self._call(_CutStream, schema=facts.FIX)
        self.assertIn(llm.CUT, msg)
        self.assertNotIn('JSON', msg)
        self.assertTrue(e.retry)
        self.assertEqual(job.steps[-1].d['说明'], '中途断了')
        self.assertEqual(job.usage['次数'], 1)                                  # 用量照记
        self.text = '# 7 半截正文'
        _job, msg, _e = self._call(_CutStream)
        self.assertIn(llm.CUT, msg)

    def test_一个字没收到_或读到一半断线(self):
        self.text = ''
        _job, msg, e = self._call(_NoStartStream)
        self.assertIn('一个字也没收到', msg)
        self.assertTrue(e.retry)
        self.text = 'x'
        job, msg, e = self._call(_DropStream)
        self.assertIn(llm.CUT, msg)
        self.assertIn('收到 2 字', msg)
        self.assertTrue(e.retry)
        self.assertEqual(job.steps[-1].d['状态'], '失败')

    def test_发出去带上思考过程照常发回(self):
        fake = _FakeClient('好')
        job = jobs.Job('审稿', '审稿', {}, None)
        with mock.patch.object(llm, 'client', lambda cfg=None: fake):
            self.assertEqual(_saved[(llm, 'call')](job, '审 07', '系统', '内容'), '好')
        self.assertEqual(fake.kw['thinking'], {'type': 'adaptive', 'display': 'summarized'})
        self.assertTrue(fake.closed)                                           # 用完把连接关掉


class T08c_事实库接着做(Base):
    SRC = [('可研.txt', '第一章 总论\n' + '本项目年产甲产品 10 万吨。\n' * 40)]
    GOOD = '# 事实库\n## 项目概况\n' + ''.join('- 事实%d（出处：可研 1.%d）\n' % (i, i) for i in range(25))

    def build(self, root, src=None):
        with mock.patch.object(tools, 'source_texts', lambda r: src or self.SRC):
            return facts.build(jobs.Job('事实库', '事实库', {}, root), root)

    def test_核对断了_下次不重摘(self):
        root = project('t08c')
        (root / '资料' / '事实库.md').unlink(missing_ok=True)

        def boom(**k):
            raise llm.Cancelled()            # 核对时任务停了（取消、程序关了）；断流现在不停，见 T09c

        FAKE.reset(('摘事实', lambda **k: '<<<事实库>>>\n' + self.GOOD + '<<<口径>>>\n# 口径\n'), ('核对事实库', boom))
        with self.assertRaises(llm.Cancelled):
            self.build(root)
        self.assertFalse((root / '资料' / '事实库.md').exists())
        self.assertTrue((root / '资料' / facts.DRAFT / '汇总.md').exists())        # 摘好的先存着
        FAKE.reset(('核对事实库', lambda **k: {'补充': [{'标题': '项目概况', '条目': '新事实（出处：可研 2.1）'}], '更正': []}))
        res = self.build(root)
        self.assertEqual(FAKE.labels(), ['核对事实库'])                           # 没有重摘
        self.assertEqual((res['事实'], res['补充']), (26, 1))
        self.assertIn('- 新事实（出处：可研 2.1）', tools.read(root / '资料' / '事实库.md'))
        self.assertFalse((root / '资料' / '事实片段').exists())                   # 写好后中间结果删掉

    def test_资料变了就重摘(self):
        root = project('t08c_变')
        (root / '资料' / '事实库.md').unlink(missing_ok=True)
        facts._draft_put(root, '汇总.md', facts._stamp(self.SRC), '<<<事实库>>>\n# 事实库\n## 项目概况\n- 旧的\n')
        FAKE.reset(('摘事实', lambda **k: '<<<事实库>>>\n' + self.GOOD + '<<<口径>>>\n# 口径\n'),
                   ('核对事实库', lambda **k: {'补充': [], '更正': []}))
        self.build(root, [('可研.txt', self.SRC[0][1] + '补了一页。\n')])
        self.assertEqual(FAKE.labels(), ['摘事实', '核对事实库'])

    def test_长资料切开核对_每批存进度_重复的不补(self):
        root = project('t08c_长')
        (root / '资料' / '事实库.md').unlink(missing_ok=True)
        long = [('可研.txt', ''.join('第%d节 设备\n' % i + '甲设备 %d 台。\n' % i * 30 for i in range(400)))]
        self.assertGreater(len(long[0][1]), facts.CHECK_BATCH)
        n = len(facts._batches(facts._pieces(long, facts.CHECK_BATCH), facts.CHECK_BATCH))
        self.assertGreater(n, 1)
        seen = []

        def check(content, **k):
            seen.append(content)
            if len(seen) == 2:
                raise llm.Cancelled()
            return {'补充': [{'标题': '项目概况', '条目': '- 新事实（出处：可研 9.9）'}], '更正': []}

        FAKE.reset(('摘事实', lambda **k: '<<<事实库>>>\n' + self.GOOD + '<<<口径>>>\n# 口径\n'),
                   ('汇总事实库', lambda **k: '<<<事实库>>>\n' + self.GOOD + '<<<口径>>>\n# 口径\n'),
                   ('核对事实库', check))
        with self.assertRaises(llm.Cancelled):
            self.build(root, long)
        self.assertTrue(all(len(c) < facts.CHECK_BATCH + 20000 for c in seen))   # 一次给的资料不超过一批
        self.assertIn('（第1段，共', seen[0])
        FAKE.reset(('核对事实库', check))
        res = self.build(root, long)
        self.assertEqual(len(FAKE.labels()), n - 1)                               # 从断的那一批接着核
        self.assertEqual(res['补充'], 1)                                          # 每批都补同一条，只补一次


class T08d_摘事实写不完(Base):
    """11-06 新材料 7 万字、11-07 200MW 22.6 万字一次摘事实：设计专篇 23 节写不完（截断），整个任务停下。"""
    GOOD = T08c_事实库接着做.GOOD

    def _design(self, name):
        root = project(name)
        (root / '资料' / '事实库.md').unlink(missing_ok=True)
        info = root / '项目信息.md'
        tools.write(info, tools.read(info).replace('# 项目信息', '# 项目信息\n\n- 报告类型：设计专篇', 1))
        self.assertTrue(tools.is_design(root))
        return root

    @staticmethod
    def cut(label):
        raise llm.CallError('%s：输出太长被截断' % label, retry=False)

    def test_一次写不完_改成分批摘再汇总(self):
        root = self._design('t08d_分批')
        src = [('初设.txt', '第一章\n' + '设备甲 3 台。\n' * 3000)]          # 不到一次摘的上限
        self.assertLessEqual(len(src[0][1]), facts.DESIGN_ONE_CALL)
        FAKE.reset(('摘事实 第', lambda **k: '## 项目概况\n- 甲（出处：初设 1.1）\n'),
                   ('摘事实', lambda label, **k: self.cut(label)),
                   ('汇总事实库', lambda **k: '<<<事实库>>>\n' + self.GOOD + '<<<口径>>>\n# 口径\n'),
                   ('核对事实库', lambda **k: {'补充': [], '更正': []}))
        with mock.patch.object(tools, 'source_texts', lambda r: src):
            res = facts.build(jobs.Job('事实库', '事实库', {}, root), root)
        labels = FAKE.labels()
        self.assertEqual(labels[0], '摘事实')
        self.assertGreaterEqual(sum(x.startswith('摘事实 第') for x in labels), 2)   # 单份资料也切成至少两批
        self.assertIn('汇总事实库', labels)
        self.assertEqual(res['事实'], 25)

    def test_一批写不完_切两半重摘_切到底还不行就停(self):
        root = self._design('t08d_切半')
        src = [('初设.txt', ''.join('第%d节\n' % i + '设备%d 台。\n' % i * 40 for i in range(900)))]
        self.assertGreater(len(src[0][1]), facts.DESIGN_ONE_CALL)
        seen = []

        def batch(label, content, **k):
            seen.append((label, len(content)))
            if label == '摘事实 第1批':
                self.cut(label)                       # 第1批写不完：切两半各摘一遍
            return '## 项目概况\n- %s（出处：初设）\n' % label

        FAKE.reset(('摘事实 第', batch),
                   ('汇总事实库', lambda **k: '<<<事实库>>>\n' + self.GOOD + '<<<口径>>>\n# 口径\n'),
                   ('核对事实库', lambda **k: {'补充': [], '更正': []}))
        with mock.patch.object(tools, 'source_texts', lambda r: src):
            facts.build(jobs.Job('事实库', '事实库', {}, root), root)
        labels = [x for x, _ in seen]
        self.assertIn('摘事实 第1批-1', labels)
        self.assertIn('摘事实 第1批-2', labels)
        first = dict(seen)['摘事实 第1批']
        self.assertLess(dict(seen)['摘事实 第1批-1'], first)            # 切开后一次给的资料少了
        merged = next(c['内容'] for c in FAKE.calls if c['标签'] == '汇总事实库')
        self.assertIn('摘事实 第1批-1', merged)                         # 两半的摘录都进了汇总
        self.assertIn('摘事实 第1批-2', merged)
        # 每一层都写不完：切到 SPLIT_DEPTH 层还不行，报错停下（不无限切）
        root2 = self._design('t08d_切到底')
        FAKE.reset(('摘事实 第', lambda label, **k: self.cut(label)))
        with mock.patch.object(tools, 'source_texts', lambda r: src), self.assertRaises(llm.CallError):
            facts.build(jobs.Job('事实库', '事实库', {}, root2), root2)
        self.assertLessEqual(max(x.count('-') for x in FAKE.labels()), facts.SPLIT_DEPTH)

    def test_汇总写不完_按节分组写_口径单独写(self):
        root = self._design('t08d_汇总')
        src = [('初设.txt', ''.join('第%d节\n' % i + '设备%d 台。\n' % i * 40 for i in range(900)))]
        heads = facts.heads_of(tools.report_type(root))

        def group(label, content, **k):
            # 回答只含任务里列出的几节；模型把同一节写两遍、带“# 事实库”标题时，程序去重
            hs = [h.strip()[3:] for h in content.split('\n') if h.startswith('  ## ')]
            one = ''.join('## %s\n- 条（出处：初设 1）\n' % h for h in hs)
            return '<<<事实库>>>\n# 事实库\n' + one + one
        FAKE.reset(('摘事实 第', lambda **k: '# 事实库\n## 项目概况\n- 甲（出处：初设 1.1）\n' * 800),
                   ('汇总事实库 口径', lambda **k: '<<<口径>>>\n# 口径\n## 说法不一\n'),
                   ('汇总事实库 ', group),
                   ('核对事实库', lambda **k: {'补充': [], '更正': []}))
        with mock.patch.object(tools, 'source_texts', lambda r: src):
            res = facts.build(jobs.Job('事实库', '事实库', {}, root), root)
        fact_md = tools.read(root / '资料' / '事实库.md')
        self.assertEqual(res['事实'], len(heads))
        self.assertTrue(all(fact_md.count('## ' + h + '\n') == 1 for h in heads))   # 每节只一次
        self.assertEqual(fact_md.count('# 事实库'), 1)
        self.assertIn('## 说法不一', tools.read(root / '资料' / '口径.md'))
        groups = [c for c in FAKE.calls if c['标签'].startswith('汇总事实库 ') and '口径' not in c['标签']]
        self.assertEqual(len(groups), -(-len(heads) // facts.MERGE_GROUP))
        self.assertTrue(all(c['参数'].get('max_tokens') == facts.MERGE_TOKENS for c in groups))
        self.assertIsNone(next(c for c in FAKE.calls if c['标签'] == '汇总事实库 口径')['参数'].get('max_tokens'))   # 口径表思考多，用默认上限
        kj = next(c['内容'] for c in FAKE.calls if c['标签'] == '汇总事实库 口径')
        self.assertIn('第1批摘录', kj)                                           # 口径那次看全部摘录
        self.assertNotIn('\n# 事实库', kj)                                       # 输入里各批摘录的“# 事实库”标题去掉了

    def test_汇总口径没写完_接着做不重写各组(self):
        root = self._design('t08d_汇总续')
        src = [('初设.txt', ''.join('第%d节\n' % i + '设备%d 台。\n' % i * 40 for i in range(900)))]

        def group(label, content, **k):
            hs = [h.strip()[3:] for h in content.split('\n') if h.startswith('  ## ')]
            return '<<<事实库>>>\n' + ''.join('## %s\n- 条（出处：初设 1）\n' % h for h in hs)
        ok = {'on': False}

        def kj(label, **k):
            if not ok['on']:
                raise llm.CallError('%s：输出太长被截断' % label, retry=False)
            return '<<<口径>>>\n# 口径\n## 说法不一\n'
        FAKE.reset(('摘事实 第', lambda **k: '## 项目概况\n- 甲（出处：初设 1.1）\n' * 800),
                   ('汇总事实库 口径', kj), ('汇总事实库 ', group), ('核对事实库', lambda **k: {'补充': [], '更正': []}))
        with mock.patch.object(tools, 'source_texts', lambda r: src):
            with self.assertRaises(llm.CallError):
                facts.build(jobs.Job('事实库', '事实库', {}, root), root)
            n = sum(c['标签'].startswith('汇总事实库 ') and '口径' not in c['标签'] for c in FAKE.calls)
            self.assertGreater(n, 1)
            ok['on'] = True
            facts.build(jobs.Job('事实库', '事实库', {}, root), root)
        again = sum(c['标签'].startswith('汇总事实库 ') and '口径' not in c['标签'] for c in FAKE.calls)
        self.assertEqual(again, n)                                            # 第二次没重写各组
        self.assertIn('## 说法不一', tools.read(root / '资料' / '口径.md'))
        self.assertEqual(list((root / '资料' / facts.DRAFT).glob('汇总_*.md')), [])   # 写好事实库后中间结果删掉

    def test_补摘只带节标题_不带整份事实库(self):
        root = self._design('t08d_补摘')
        fact_md = '# 事实库\n## 项目概况\n' + ''.join('- 很长的事实第%d条，写着设备型号和参数（出处：可研 1.%d）\n' % (i, i) for i in range(300))
        tools.write(root / '资料' / '事实库.md', fact_md)
        src = [('初设.txt', '第五章 暖通\n' + '35 kV配电室事故通风 12 次/h。\n' * 50)]
        FAKE.reset(('补摘事实', lambda **k: '## 暖通设计（各房间、舱体的通风方式、换气次数、风机型号和风量、事故通风、空调）\n'
                                              '- 35 kV配电室事故通风 12 次/h（出处：初设 5.1）\n'))
        with mock.patch.object(tools, 'source_texts', lambda r: src):
            res = facts.build_extra(jobs.Job('专篇', '专篇', {}, root), root, facts.HEADS_DESIGN)
        sent = FAKE.calls[0]['内容']
        self.assertIn('项目概况（300 条）', sent)
        self.assertNotIn('很长的事实第200条', sent)                         # 不带整份事实库
        self.assertEqual(res['条数'], 1)
        self.assertIn('35 kV配电室事故通风 12 次/h', tools.read(root / '资料' / '事实库.md'))


class T09b_新报告接着做(Base):
    def test_写好的不重写_缺章不审不出(self):
        root = project('t09b')
        files = tools.body_files(root)
        rec = {f: {'字数': 100, '可研没有': ['可研没有写%s' % f[:2]], '剩余错误': 0} for f in files[1:]}
        tools.write(new._record_path(root), json.dumps(rec, ensure_ascii=False))
        seen = []

        def write(job, root, method, sample, fname, deps, gaps, **k):
            seen.append((fname, list(gaps)))
            raise llm.CallError('写 %s：%s' % (fname, llm.CUT))

        job = jobs.Job('新报告', '新报告', {}, root)
        with mock.patch.object(new, '_write_file', write):
            wr = new.write_all(job, root, '风险评估法', None)
        self.assertEqual([f for f, _ in seen], files[:1])                         # 只写记录里没有的
        self.assertEqual(sorted(k for k, v in wr.items() if v), sorted(files[1:]))
        seen.clear()
        ten = [f for f in files if f.startswith('10')][0]
        del rec[ten]
        tools.write(new._record_path(root), json.dumps(rec, ensure_ascii=False))
        with mock.patch.object(new, '_write_file', write):
            new.write_all(job, root, '风险评估法', None)
        gaps = dict(seen)[ten]
        self.assertIn('可研没有写05', gaps)                                       # 上次写好的章记下的事项照样交给第10章

    def test_没写全就停_不审稿不出稿(self):
        root = project('t09b_缺')
        (root / '资料' / '文本').mkdir(parents=True, exist_ok=True)
        (root / '资料' / '文本' / '001_可研.txt').write_text('第一章 总论\n本项目年产甲产品 10 万吨。\n', encoding='utf-8')
        (root / '资料' / '文本' / '索引.json').write_text(json.dumps(
            {'块': [{'文件': '001_可研.txt', '来源': '/x/可研.pdf', '字数': 20, '标题': []}], '跳过': []}, ensure_ascii=False),
            encoding='utf-8')
        form = {'文件夹': str(root), '方法': '风险评估法', '资料': [], '接着做': True}
        job = jobs.Job('新报告', '新报告', form, root)
        with mock.patch.object(new, '_fill_info', lambda *a: {'项目名称': '甲', '方法线索': ''}), \
                mock.patch.object(tools, 'register_add', lambda *a: False), \
                mock.patch.object(new, '_post_table', lambda *a, **k: {}), \
                mock.patch.object(new, 'write_all', lambda *a, **k: {}), \
                mock.patch.object(new.RV, 'clear_all') as ca, mock.patch.object(new.P, 'publish') as pub:
            with self.assertRaises(RuntimeError) as cm:
                new.job_new(job)
        self.assertIn('接着做', str(cm.exception))
        ca.assert_not_called()
        pub.assert_not_called()
        self.assertTrue(any('资料上次已转成文字' in t for _, t in job.logs))


class T09c_接着做不白花(Base):
    INFO = {'项目名称': '甲项目', '封面名称': ['甲公司', '甲项目'], '建设单位': '', '报告编号': '', '报告日期': '', '方法线索': ''}

    def _info(self, root, text):
        tools.write(root / '项目信息.md', text)
        return new._fill_info(jobs.Job('新报告', '新报告', {}, root), root, None, {})

    def test_建设单位没找到_接着做不重填_封面不叠行(self):
        # 11-01 田林：建设单位资料里没有，留着【待填】；原先接着做会再填一遍，封面名称两行叠成三行
        root = project('t09c_info')
        skel = tools.read(new._skeleton('风险评估法') / '项目信息.md')
        FAKE.reset(('填项目信息', lambda **k: self.INFO))
        self._info(root, skel)
        s = tools.read(root / '项目信息.md')
        self.assertEqual(re.findall(r'^- 封面名称：(.*)$', s, re.M), ['甲公司', '甲项目'])
        self.assertIn('- 建设单位：【待填】', s)
        FAKE.reset()
        self.assertEqual(self._info(root, s)['项目名称'], '甲项目')
        self.assertEqual(FAKE.labels(), [])                                       # 不再调模型
        self.assertEqual(tools.read(root / '项目信息.md'), s)

    def test_再填一遍封面名称整组换掉(self):
        root = project('t09c_cover')
        s = tools.read(new._skeleton('风险评估法') / '项目信息.md').replace(
            '- 封面名称：【待填】', '- 封面名称：旧一\n- 封面名称：旧二')                 # 项目名称还是【待填】：要填
        FAKE.reset(('填项目信息', lambda **k: dict(self.INFO, 封面名称=['新一', '新二', '新三'])))
        self._info(root, s)
        got = tools.read(root / '项目信息.md')
        self.assertEqual(re.findall(r'^- 封面名称：(.*)$', got, re.M), ['新一', '新二', '新三'])
        self.assertLess(got.index('- 项目名称：'), got.index('- 封面名称：新一'))      # 还在原来的位置

    def test_核对没成的批_接着写并记下(self):
        root = project('t09c_check')
        (root / '资料' / '事实库.md').unlink(missing_ok=True)
        long = [('可研.txt', ''.join('第%d节 设备\n' % i + '甲设备 %d 台。\n' % i * 30 for i in range(1000)))]
        n = len(facts._batches(facts._pieces(long, facts.CHECK_BATCH), facts.CHECK_BATCH))
        self.assertGreaterEqual(n, 3)
        good = '<<<事实库>>>\n' + T08c_事实库接着做.GOOD + '<<<口径>>>\n# 口径\n'
        stop = {'在': 3}

        def check(label, **k):
            if label.endswith('第2批'):
                raise llm.CallError(label + '：' + llm.CUT)                     # 重来几次还是断
            if label.endswith('第%d批' % stop['在']):
                raise llm.CallError(label + '：没有密钥', retry=False)             # 再来也一样：停下
            return {'补充': [], '更正': []}

        FAKE.reset(('摘事实', lambda **k: good), ('汇总事实库', lambda **k: good), ('核对事实库', check))
        job = jobs.Job('事实库', '事实库', {}, root)
        with mock.patch.object(tools, 'source_texts', lambda r: long), self.assertRaises(llm.CallError):
            facts.build(job, root)
        self.assertFalse((root / '资料' / '事实库.md').exists())
        self.assertTrue(any('第2批没成' in t for _, t in job.logs))
        stop['在'] = 0
        FAKE.reset(('核对事实库', check))
        with mock.patch.object(tools, 'source_texts', lambda r: long):
            res = facts.build(jobs.Job('事实库', '事实库', {}, root), root)
        self.assertEqual(FAKE.labels()[0], '核对事实库 第3批')                       # 从停下的那批接着核
        self.assertEqual(res['没核对'], [2])
        self.assertEqual(facts.skipped_checks(root), '2')
        fx = tools.read(root / '资料' / '事实库.md')
        self.assertTrue(fx.startswith('<!-- 没核对：第 2 批（共 %d 批' % n))
        self.assertEqual(res['事实'], 25)

    def test_前面的章没写成_后面的不硬写(self):
        root = project('t09c_dep')
        files = tools.body_files(root)
        seen = []

        def write(job, root, method, sample, fname, deps, gaps, written=None):
            seen.append(fname)
            if fname.startswith('03'):
                raise llm.CallError('写 %s：%s' % (fname, llm.CUT))
            r = {'字数': 500, '可研没有': [], '剩余错误': None}
            written(r)
            return dict(r, 剩余错误=0)

        job = jobs.Job('新报告', '新报告', {}, root)
        with mock.patch.object(new, '_write_file', write):
            wr = new.write_all(job, root, '风险评估法', None)
        ok = [f for f in files if f[:2] in ('02', '08')]                        # 不靠第3章的
        later = [f for f in files if f[:2] not in ('02', '03', '08')]           # 直接或间接靠第3章的
        self.assertEqual(sorted(f for f in seen if not f.startswith('03')), sorted(ok))
        skipped = [s.d['名称'] for s in job.steps if s.d['状态'] == '跳过']
        self.assertEqual(len(skipped), len(later))
        rec = json.loads(tools.read(new._record_path(root)))
        self.assertEqual(sorted(rec), sorted(ok))                               # 没写的不记成写好了
        self.assertEqual(sorted(k for k, v in wr.items() if v), sorted(ok))
        self.assertEqual(rec[files[1]]['剩余错误'], 0)                               # 清零后的结果也记上


class T03e_测连接和余额(Base):
    def test_测连接发的参数和真调用一样(self):
        for on in (True, False):
            cfg = dict(config.load(), 思考摘要=on, 兜底=True)
            fake_call, fake_ping = _FakeClient('好'), _FakeClient('通了')
            with mock.patch.object(config, 'load', lambda: cfg):
                with mock.patch.object(llm, 'client', lambda c=None: fake_call):
                    _saved[(llm, 'call')](jobs.Job('审稿', '审稿', {}, None), '审 07', '系统', '内容')
                with mock.patch.object(llm, 'client', lambda c=None: fake_ping):
                    ok, why = llm.ping()
            self.assertTrue(ok, why)
            for k in ('model', 'thinking', 'betas', 'fallbacks'):
                self.assertEqual(fake_ping.kw.get(k), fake_call.kw.get(k), k)
            self.assertEqual('thinking' in fake_call.kw, on)

    def test_余额不够_模型不对_不重来(self):
        for code in (402, 404):
            req = llm._httpx.Request('POST', 'https://relay.example.com/v1/messages')
            err = llm.anthropic.APIStatusError('x', response=llm._httpx.Response(code, request=req), body=None)
            fake = _FakeClient('')

            def boom(**kw):
                raise err

            fake.stream = boom
            with mock.patch.object(llm, 'client', lambda c=None: fake), self.assertRaises(llm.CallError) as cm:
                _saved[(llm, 'call')](jobs.Job('审稿', '审稿', {}, None), '审 07', '系统', '内容')
            self.assertFalse(cm.exception.retry, code)


class T11_出稿后交稿说明开头跟着改(Base):
    def test_出稿通过_开头的PDF和检查两行换成新的(self):
        from app.flows import publish
        root = project('t11')
        tools.write(root / '输出' / '交稿说明.md', '# 交稿说明\n\n## 报告\n\n- 项目：甲\n- PDF：没出或没通过\n'
                    '- 检查：错误 1、提示 2、待办 3\n\n## 请你确认\n\n- 检查：这一行不是开头的，不动\n')
        with mock.patch.object(tools, 'report', return_value=(0, 'WPS 两轮导出核对通过：共 12 页')):
            res = publish.publish(jobs.Job('出稿', '出稿', {}, root), root)
        c = tools.check(root)
        text = tools.read(root / '输出' / '交稿说明.md')
        self.assertEqual((res['通过'], res['页数']), (True, 12))
        self.assertTrue((root / '输出' / '资料欠缺清单.md').is_file())
        self.assertIn('- PDF：共 12 页，目录页码逐页核对通过\n', text)
        self.assertIn('- 检查：错误 %d、提示 %d、待办 %d\n' % (c['错误'], c['提示'], c['待办']), text)
        self.assertNotIn('没出或没通过', text)
        self.assertIn('- 检查：这一行不是开头的，不动', text)


from app import gaps  # noqa: E402
from app.flows import figures, publish  # noqa: E402

WIND = ('表2.1-3  晋城市近三年全年和夏季风向频率统计\n'
        '| 风向 | 全年（%） | 夏季（%） | 风向 | 全年（%） | 夏季（%） |\n|---|---|---|---|---|---|\n'
        '| N | 3.51 | 2.00 | S | 2.21 | 4.00 |\n| NNE | 4.72 | 2.10 | SSW | 1.89 | 3.00 |\n'
        '| NE | 5.86 | 3.50 | SW | 4.20 | 1.90 |\n| ENE | 2.03 | 3.00 | WSW | 6.14 | 6.00 |\n'
        '| E | 2.83 | 5.00 | W | 2.01 | 8.00 |\n| ESE | 8.83 | 6.00 | WNW | 21.94 | 5.00 |\n'
        '| SE | 3.28 | 15.50 | NW | 15.49 | 3.00 |\n| SSE | 2.10 | 8.00 | NNW | 1.71 | 2.00 |\n'
        '| C（静风） | 11.25 | 22.00 | 合计 | 100.00 | 100.00 |\n\n注：建设单位提供。\n')


def fig_project(name, wind=True, later=True):
    """只有第2章的小项目：2.1.1 基本情况、2.1.2 风频表（可选）、表后已有一张图（可选）、2.3 总体布局。"""
    root = TMP / name
    (root / '正文').mkdir(parents=True)
    tools.write(root / '项目信息.md', '- 项目名称：配图测试\n')
    later_fig = '![图2.1-1  周边环境图](图/周边.png)\n图2.1-1  周边环境图\n\n周边见图2.1-1；可研图2.1-1是资料里的图。\n\n'
    tools.write(root / '正文' / '02_1_工程概况.md',
                '# 2 建设项目工程分析\n\n## 2.1 工程概况分析\n\n### 2.1.1 项目基本情况\n\n建在某地。\n\n'
                '### 2.1.2 自然环境概况\n\n风向见表2.1-3。\n\n' + (WIND + '\n' if wind else '') +
                (later_fig if later else '') + '## 2.3 总体布局分析与评价\n\n分区布置。\n')
    return root


def fake_draw(tb, png):
    png = Path(png)
    png.parent.mkdir(parents=True, exist_ok=True)
    for s in ('.png', '.svg'):
        png.with_suffix(s).write_bytes(b'x')
    png.with_suffix('.json').write_text(json.dumps({'指纹': figures.WR.fingerprint(tb)}), encoding='utf-8')


class T12_配图(Base):
    def test_有风频表就画上_插在表注后面_后面的图和见图跟着改号(self):
        root = fig_project('t12_wind')
        with mock.patch.object(figures.WR, 'draw', side_effect=fake_draw) as d:
            msg = figures.wind(root)
        text = tools.read(root / '正文' / '02_1_工程概况.md')
        self.assertEqual(d.call_count, 1)
        self.assertEqual(msg, '画了风频玫瑰图（图2.1-1，按表2.1-3）')
        self.assertIn('注：建设单位提供。\n\n![图2.1-1  晋城市近三年全年和夏季风频玫瑰图](图/风频玫瑰图.png){宽=14.10cm}\n'
                      '图2.1-1  晋城市近三年全年和夏季风频玫瑰图\n', text)
        self.assertIn('![图2.1-2  周边环境图](图/周边.png)\n图2.1-2  周边环境图', text)
        self.assertIn('周边见图2.1-2；可研图2.1-1是资料里的图。', text)
        self.assertTrue((root / '图' / '风频玫瑰图.json').is_file())
        with mock.patch.object(figures.WR, 'draw', side_effect=fake_draw) as d:
            self.assertIsNone(figures.wind(root))                    # 再跑一遍：图在、表没改，不画不插
        self.assertEqual(d.call_count, 0)
        self.assertEqual(tools.read(root / '正文' / '02_1_工程概况.md'), text)
        self.assertEqual([m for lv, s, m in tools._checks.run(tools.Project(root).resolve())
                          if m.startswith('图') and lv == '错误'], [])      # 图号连续、不重复

    def test_表改了重画_手画的图不动_画不成正文不改(self):
        root = fig_project('t12_redraw', later=False)
        with mock.patch.object(figures.WR, 'draw', side_effect=fake_draw):
            figures.wind(root)
        p = root / '正文' / '02_1_工程概况.md'
        tools.write(p, tools.read(p).replace('| N | 3.51 | 2.00 | S | 2.21 |', '| N | 3.50 | 2.00 | S | 2.22 |'))
        with mock.patch.object(figures.WR, 'draw', side_effect=fake_draw) as d:
            self.assertEqual(figures.wind(root), '风频玫瑰图（图2.1-1）按表2.1-3重画')
        (root / '图' / '风频玫瑰图.json').unlink()                   # 没有指纹＝手画的
        tools.write(p, tools.read(p).replace('| N | 3.50 |', '| N | 3.49 |').replace('| S | 2.22 |', '| S | 2.23 |'))
        with mock.patch.object(figures.WR, 'draw', side_effect=fake_draw) as d:
            self.assertIsNone(figures.wind(root))
        self.assertEqual(d.call_count, 0)
        root2 = fig_project('t12_fail', later=False)
        before = tools.read(root2 / '正文' / '02_1_工程概况.md')
        with mock.patch.object(figures.WR, 'draw', side_effect=RuntimeError('没找到 Chrome')):
            self.assertIsNone(figures.refresh_wind(jobs.Job('出稿', '出稿', {}, root2), root2))
        self.assertEqual(tools.read(root2 / '正文' / '02_1_工程概况.md'), before)

    def test_从资料PDF截区域位置图和平面布置图_目录行不算_再跑不重复(self):
        import io
        import pymupdf
        from PIL import Image
        root = fig_project('t12_grab', wind=False, later=False)
        (root / '资料').mkdir()
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 100), '图1.3-1 某项目地理位置示意图……………… 9', fontname='china-s', fontsize=11)
        p1 = doc.new_page()
        buf = io.BytesIO()
        Image.new('RGB', (400, 300), (200, 220, 240)).save(buf, 'PNG')
        p1.insert_image(pymupdf.Rect(72, 120, 520, 450), stream=buf.getvalue())
        p1.insert_text((200, 480), '图1.3-1  某项目地理位置示意图', fontname='china-s', fontsize=11)
        p2 = doc.new_page()
        for k in range(6):
            p2.draw_rect(pymupdf.Rect(90 + k * 60, 150, 140 + k * 60, 400), color=(0, 0, 0))
        p2.insert_text((220, 440), '图9.7-2  升压站平面布置图（示意）', fontname='china-s', fontsize=11)
        doc.save(str(root / '资料' / '某项目初步设计.pdf'))
        done, miss = figures.capture(root)
        text = tools.read(root / '正文' / '02_1_工程概况.md')
        self.assertEqual(done, ['图2.1-1 项目区域位置图（初设第2页）', '图2.3-1 升压站平面布置图（初设第3页）'])
        self.assertEqual(miss, [])
        self.assertIn('建在某地。\n\n![图2.1-1  项目区域位置图](图/项目区域位置图.png){宽=', text)
        self.assertIn('注：引自初设图1.3-1。\n\n### 2.1.2', text)
        self.assertIn('分区布置。\n\n<!-- 横向 -->\n\n![图2.3-1  升压站平面布置图](图/升压站平面布置图.png){宽=', text)
        self.assertIn('注：引自初设图9.7-2（示意）。\n\n<!-- 纵向 -->', text)
        rec = json.loads(tools.read(root / '图' / '配图记录.json'))
        self.assertEqual((rec['项目区域位置图.png']['页'], rec['项目区域位置图.png']['审图号']), (2, False))
        self.assertTrue((root / '图' / '升压站平面布置图.png').stat().st_size > 1000)
        self.assertEqual(figures.capture(root), ([], []))             # 图已在正文里：不再截
        self.assertEqual(tools.read(root / '正文' / '02_1_工程概况.md'), text)

    def test_资料欠缺清单_三类分开_重复的不列两遍(self):
        root = fig_project('t12_gaps', wind=False, later=False)
        tools.write(root / '项目信息.md', '- 项目名称：配图测试\n- 建设单位：【待填】\n')
        tools.write(root / '正文' / '03_类比企业（项目）调查与分析.md',
                    '# 3 类比企业（项目）调查与分析\n\n本报告选择【待填：类比项目名称】，资料见【待填：资料名称】。\n')
        tools.write(root / '图' / '配图记录.json', json.dumps({'项目区域位置图.png': {
            '类别': '区域位置图', '资料': '某项目初步设计.pdf', '页': 15, '审图号': False}}, ensure_ascii=False))
        tools.write(root / '资料' / '岗位因素表.md', '## 按不利情况定的\n\n- 工种和人数：可研没有写各岗位人数分配，按定员10人分。\n')
        tools.write(root / '资料' / '口径.md', '## 可研没有写的\n\n- 各岗位人数分配：第2章、第4章\n- SF₆充气量：第2章、第4章\n')
        d = gaps.write(root)
        need = ' '.join(x['缺什么'] for x in d['要补'])
        self.assertIn('建设单位', need)
        self.assertIn('类比项目资料', need)
        self.assertIn('当地气象站近三年全年和夏季风向频率统计', need)
        self.assertIn('没有审图号', need)
        self.assertEqual([x['缺什么'] for x in d['补了更准']], ['工种和人数'])
        self.assertEqual(d['可以不补'], [{'缺什么': 'SF₆充气量', '用在': '第2章、第4章'}])
        md = tools.read(root / '输出' / '资料欠缺清单.md')
        self.assertIn('| 序号 | 缺什么 | 用在 | 找谁 | 现在报告里 |', md)
        self.assertIn('不进报告', md)
        self.assertNotIn('工艺流程', need)                            # 工艺流程图照可研画，不算缺资料

    def test_工艺流程图_模型排框_查出问题退回改一次_插在2点5节末(self):
        root = fig_project('t12_flow', wind=False, later=False)
        tools.write(root / '正文' / '02_3_工艺及设备.md', '## 2.5 生产工艺分析与评价\n\n风机发电，经箱变升压送至升压站。\n\n'
                    '## 2.6 生产设备及布局分析与评价\n\n设备。\n')
        tools.write(root / '正文' / '04_职业病危害因素.md', '# 4 职业病危害因素\n\n表4.1-1  因素分布\n'
                    '| 工作场所 | 职业病危害因素 |\n|---|---|\n| 风机 | 噪声、工频电磁场 |\n')
        fig = {'图题': '风电场工艺流程及职业病危害因素分布图', '框': [
            {'号': 'A1', '行': 1, '列': 1, '名称': '风电机组', '说明': '', '因素': [1], '虚线': False},
            {'号': 'A2', '行': 1, '列': 2, '名称': '箱式变压器', '说明': '35 kV', '因素': [1, 2], '虚线': False}],
            '连线': [{'从': 'A1', '到': 'A2', '物料': '电能', '虚线': False}],
            '因素': ['噪音', '工频电磁场'], '注': ['注：框内第二行为主要参数。']}
        good = dict(fig, 因素=['噪声', '工频电磁场'])

        def draw(spec, png):
            from PIL import Image
            Image.new('RGB', (3200, 1600), 'white').save(png)

        with mock.patch.object(figures.llm, 'call_retry', side_effect=[{'图': [fig]}, {'图': [good]}]) as call, \
                mock.patch.object(figures.FC, 'draw', side_effect=draw), mock.patch.object(figures.WR, 'browser', return_value='/x'):
            drew, miss = figures.flows(jobs.Job('配图', '配图', {}, root), root)
        self.assertEqual(call.call_count, 2)
        self.assertIn('“噪音”和表4.1-1的写法不一样', call.call_args_list[1].args[3])     # 图例名称和表4.1-1不一样，退回去改
        self.assertEqual((drew, miss), (['风电场工艺流程及职业病危害因素分布图'], []))
        text = tools.read(root / '正文' / '02_3_工艺及设备.md')
        self.assertIn('送至升压站。\n\n<!-- 横向 -->\n\n![图2.5-1  风电场工艺流程及职业病危害因素分布图]'
                      '(图/风电场工艺流程及职业病危害因素分布图.png){宽=23.00cm}', text)
        self.assertIn('图2.5-1  风电场工艺流程及职业病危害因素分布图\n\n<!-- 纵向 -->\n\n## 2.6', text)
        with mock.patch.object(figures.llm, 'call_retry') as call:
            self.assertEqual(figures.flows(jobs.Job('配图', '配图', {}, root), root), ([], []))   # 有了就不再调模型
        self.assertEqual(call.call_count, 0)
        root2 = fig_project('t12_flow_nobrowser', wind=False, later=False)
        tools.write(root2 / '正文' / '02_3_工艺及设备.md', '## 2.5 生产工艺分析与评价\n\n工艺。\n')
        with mock.patch.object(figures.WR, 'browser', return_value=None), mock.patch.object(figures.llm, 'call_retry') as call:
            drew, miss = figures.flows(jobs.Job('配图', '配图', {}, root2), root2)
        self.assertEqual(call.call_count, 0)                                            # 画不了就不花钱
        self.assertIn('没找到 Chrome', miss[0])

    def test_没有风频表_用最近的国家站写表画图_后面的表跟着改号(self):
        root = fig_project('t12_wtab', wind=False, later=False)
        p = root / '正文' / '02_1_工程概况.md'
        tools.write(p, '# 2 建设项目工程分析\n\n## 2.1 工程概况分析\n\n### 2.1.1 项目基本情况\n\n'
                    '场址中心坐标为东经 106°00′00″，北纬 24°20′00″。\n\n表2.1-1  项目基本情况\n| 项目 | 内容 |\n|---|---|\n| 地点 | 某地 |\n\n'
                    '### 2.1.2 自然环境概况\n\n（3）风向。可研没有给出风向频率。\n\n（4）其他。\n\n'
                    '### 2.1.3 项目组成\n\n组成见表2.1-2。\n\n表2.1-2  项目组成\n| 名称 | 内容 |\n|---|---|\n| 主体 | 风机 |\n\n'
                    '## 2.3 总体布局分析与评价\n\n分区布置。\n')
        yr = {'全年': [400] * 16 + [200], '夏季': [100] * 16 + [50]}
        yr['全年'][0] += 1500
        yr['夏季'][15] += 400
        data = {'站': {'592110': {'名称': '近站', '坐标': [24.6, 106.0, 100], '年': {y: yr for y in ('2022', '2023', '2024')}}}}
        with mock.patch.object(figures.WS, 'load', return_value=data), mock.patch.object(figures.WR, 'draw', side_effect=fake_draw):
            done, why = figures.wind_table(root)
        text = tools.read(p)
        self.assertIsNone(why)
        self.assertIn('近站', done)
        self.assertIn('（3）风向。可研没有给出风向频率。据距项目最近的国家气象观测站近站近三年逐时观测数据（见表2.1-2、图2.1-1），'
                      '全年最多风向为N（', text)
        self.assertIn('夏季最多风向为NNW（', text)
        self.assertIn('\n表2.1-2  近站近三年全年和夏季风向频率统计\n', text)
        self.assertIn('注：据国家气象科学数据中心近站近三年逐时观测数据统计；静风为风速不大于0.2 m/s。\n\n'
                      '![图2.1-1  近站近三年全年和夏季风频玫瑰图]', text)
        self.assertIn('组成见表2.1-3。', text)                                   # 后面的表和“见表××”跟着改号
        self.assertIn('\n表2.1-3  项目组成\n', text)
        self.assertEqual(json.loads(tools.read(root / '图' / '配图记录.json'))['风频表']['站'], '近站')
        self.assertEqual([m for lv, s, m in tools._checks.run(tools.Project(root).resolve())
                          if lv == '错误' and m.startswith(('表', '图'))], [])
        g = gaps.collect(root)
        self.assertIn('近站', ' '.join(x['现在'] for x in g['补了更准']))           # 代用站：列进“补了更准”
        self.assertNotIn('风频玫瑰图', ' '.join(x['现在'] for x in g['要补']))
        with mock.patch.object(figures.WS, 'load', return_value=data):
            self.assertEqual(figures.wind_table(root), (None, None))               # 有了就不再写
        tools.write(p, tools.read(p).replace('表2.1-2  近站近三年', '表2.1-2  某县气象站近三年')
                    .replace('据国家气象科学数据中心近站', '据某县气象站'))             # 改稿、补资料换成当地气象站的数据
        with mock.patch.object(figures.WR, 'draw', side_effect=fake_draw):
            self.assertIn('重画', figures.wind(root))
        text = tools.read(p)
        self.assertRegex(text, r'\n!\[图2\.1-1  某县气象站近三年全年和夏季风频玫瑰图\]\(图/[^)]+\)\{宽=14\.10cm\}\n'
                               r'图2\.1-1  某县气象站近三年全年和夏季风频玫瑰图\n')      # 图题跟着表题换
        self.assertNotIn('近站近三年全年和夏季风频玫瑰图', text)
        self.assertNotIn('近站', ' '.join(x['现在'] for x in gaps.collect(root)['补了更准']))   # 不再列代用站
        far ={'站': {'1': dict(data['站']['592110'], 坐标=[27.0, 106.0, 100])}}
        root2 = fig_project('t12_wtab_far', wind=False, later=False)
        tools.write(root2 / '正文' / '02_1_工程概况.md', '# 2 建设项目工程分析\n\n## 2.1 工程概况分析\n\n### 2.1.1 项目基本情况\n\n'
                    '场址中心坐标为东经 106°00′00″，北纬 24°20′00″。\n\n### 2.1.2 自然环境概况\n\n（3）风向。可研没有给出风向频率。\n')
        with mock.patch.object(figures.WS, 'load', return_value=far):
            done, why = figures.wind_table(root2)
        self.assertIsNone(done)
        self.assertIn('100 公里内没有', why)

    def test_命令行和出稿都接上了(self):
        root = fig_project('t12_cli', later=False)
        self.assertEqual(cli._job(cli._parser().parse_args(['配图', str(root)])), ('配图', '配图', {}, str(root)))
        self.assertEqual(flows.get('配图').__name__, 'job_figures')
        with mock.patch('sys.stdout', new=__import__('io').StringIO()):
            self.assertEqual(cli.main(['资料欠缺', str(root)]), 0)
        self.assertTrue((root / '输出' / '资料欠缺清单.md').is_file())
        with mock.patch.object(figures.WR, 'draw', side_effect=fake_draw), \
                mock.patch.object(tools, 'report', return_value=(0, 'WPS 两轮导出核对通过：共 9 页')):
            res = publish.publish(jobs.Job('出稿', '出稿', {}, root), root)
        self.assertIn('风频玫瑰图.png', tools.read(root / '正文' / '02_1_工程概况.md'))   # 出稿前把图画上
        self.assertGreater(res['资料欠缺'], 0)


class T11_补充资料(Base):
    OLD = '- 定员表：合计56人（出处：可研202607_带节号 12.4 表14.4-1）'
    GAP = '- 建设性质（新建、改建、扩建）：影响到第2章'

    def changes(self, **k):
        return {'变化': [
            {'类别': '更正', '节': '劳动定员与班制', '原文': self.OLD, '新文': '- 定员表：合计60人（出处：初设 8.1）',
             '影响': ['99_没有这个.md'], '说明': '可研56人，初设60人'},
            {'类别': '更正', '节': '劳动定员与班制', '原文': '- 事实库里没有这一行', '新文': '- 乱改（出处：初设）', '影响': [], '说明': ''},
            {'类别': '新增', '节': '主要设备', '原文': '', '新文': '- 新增引风机 2 台（出处：初设 5.2）', '影响': ['05_职业病防护设施分析与评价.md'], '说明': ''},
            {'类别': '新增', '节': '主要设备', '原文': '', '新文': '- 新增引风机 2 台（出处：初设 5.3）', '影响': [], '说明': '重复'},
            {'类别': '补上', '节': '项目概况', '原文': self.GAP, '新文': '- 建设性质：改建（出处：初设 1.1）', '影响': ['02_1_工程概况.md'], '说明': ''},
            {'类别': '补上', '节': '项目概况', '原文': '- 口径里没有这一行', '新文': '- 随便（出处：初设）', '影响': [], '说明': ''},
        ]}

    def supplement(self, name):
        root = project(name)
        src = TMP / (name + '_新资料')
        src.mkdir()
        (src / '初设.txt').write_text('第一章 总论\n本项目为改建。\n第八章 劳动定员\n全厂定员60人。\n' * 5, encoding='utf-8')
        FAKE.reset(('摘新资料', self.changes))
        job = jobs.Job('补资料', '补充资料', {'资料': [str(src)], '说明': '初设'}, root)
        res = supplement.job_supplement(job)
        return root, res

    def test_补资料_只列变化_不动事实库和正文(self):
        root, res = self.supplement('t11_a')
        fx = tools.read(root / '资料' / '事实库.md')
        body = {f: tools.read(root / '正文' / f) for f in tools.body_files(root)}
        self.assertEqual(FAKE.labels(), ['摘新资料'])
        self.assertEqual(res['变化'], {'新增': 1, '更正': 1, '补上': 1})          # 不合格的、重复的不列
        d = supplement.pending(root)
        items = {x['类别']: x for x in d['变化']}
        self.assertEqual([x['编号'] for x in d['变化']], [1, 2, 3])
        fix = items['更正']['影响']
        self.assertNotIn('99_没有这个.md', fix)                                  # 正文里没有的文件去掉
        self.assertIn('02_1_工程概况.md', fix)                                    # 正文里写着“56人”的也算上
        self.assertTrue(all('56人' in body[f] for f in fix))
        self.assertEqual(set(d['预计']), set(res['要改']))
        # 新资料记进项目资料：原件拷进来，文字以后审稿、重摘事实库都读得到
        self.assertTrue(any(p.name == '初设.txt' for p in (root / '资料' / '原件').rglob('*')))
        names = [n for n, _ in tools.source_texts(root)]
        self.assertTrue(any('初设' in n for n in names), names)
        self.assertEqual(tools.read(root / '资料' / '事实库.md'), fx)
        self.assertEqual({f: tools.read(root / '正文' / f) for f in tools.body_files(root)}, body)

    def test_按新资料改_只改勾上的和受影响的章(self):
        root, _ = self.supplement('t11_b')
        tools.write(root / '资料' / '岗位因素表.md', '# 岗位因素表\n\n| 评价单元 | 工种 | 定员 |\n|---|---|---|\n| 烧结 | 运行工 | 56 |\n')
        d = supplement.pending(root)
        fix = next(x for x in d['变化'] if x['类别'] == '更正')
        add = next(x for x in d['变化'] if x['类别'] == '新增')
        want = {f for f in fix['影响']}
        seen, fail = [], {'02_1_工程概况.md'}

        def modify(label, **k):
            f = label[len('按新资料改 '):]
            seen.append(f)
            if f in fail:
                raise llm.CallError(label + '：' + llm.CUT)
            return {'改动': [], '跳过': []}

        FAKE.reset(('按新资料改 ', modify),
                   ('改 岗位因素表', lambda **k: {'改动': [{'原文': '| 烧结 | 运行工 | 56 |', '新文': '| 烧结 | 运行工 | 60 |', '说明': ''}], '跳过': []}))
        job = jobs.Job('按新资料改', '按新资料改', {'采用': [fix['编号'], add['编号']]}, root)
        with self.assertRaises(RuntimeError):
            supplement.job_apply(job)                                            # 一章断了：任务失败，别的章改好的记着
        self.assertEqual(set(seen), want | set(add['影响']))
        fx = tools.read(root / '资料' / '事实库.md')
        self.assertIn('- 定员表：合计60人（出处：初设 8.1）', fx)
        self.assertNotIn(self.OLD, fx)
        self.assertIn('- 新增引风机 2 台（出处：初设 5.2）', fx)
        self.assertNotIn('建设性质：改建', fx)                                     # 没勾上的不改
        kj = tools.read(root / '资料' / '口径.md')
        self.assertIn(self.GAP, kj)
        self.assertIn('定员表：合计60人（出处：初设 8.1） | 说法2（补充资料）', kj)   # 旧说法记进“说法不一”
        self.assertIn('| 60 |', tools.read(root / '资料' / '岗位因素表.md'))
        # 接着做：事实库不再改一遍，改好的章不重改
        seen.clear()
        fail.clear()
        del FAKE.calls[:]
        res = supplement.job_apply(jobs.Job('按新资料改', '按新资料改', {'采用': [fix['编号'], add['编号']]}, root))
        self.assertEqual(seen, ['02_1_工程概况.md'])
        self.assertNotIn('改 岗位因素表', FAKE.labels())
        self.assertEqual(tools.read(root / '资料' / '事实库.md').count('合计60人'), 1)
        self.assertIsNone(supplement.pending(root))                              # 做完待确认的删掉
        self.assertFalse((root / '资料' / supplement.DIR).exists())
        self.assertTrue(Path(res['备份']).is_dir())
        self.assertIn('## 补充资料', tools.read(root / '输出' / '交稿说明.md'))

    def test_补上_从可研没有写的里删掉(self):
        root, _ = self.supplement('t11_c')
        gap = next(x for x in supplement.pending(root)['变化'] if x['类别'] == '补上')
        FAKE.reset(('按新资料改 ', lambda **k: {'改动': [], '跳过': []}))
        supplement.job_apply(jobs.Job('按新资料改', '按新资料改', {'采用': [gap['编号']]}, root))
        self.assertNotIn(self.GAP, tools.read(root / '资料' / '口径.md'))
        fx = tools.read(root / '资料' / '事实库.md')
        self.assertIn('- 建设性质：改建（出处：初设 1.1）', fx.split('## 选址')[0])      # 插在项目概况这一节

    def test_没事实库_扫描件(self):
        root = project('t11_d')
        (root / '资料' / '事实库.md').unlink()
        src = TMP / 't11_d_新资料.txt'
        src.write_text('第一章 总论\n本项目为改建。\n' * 20, encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '还没有事实库'):
            supplement.job_supplement(jobs.Job('补资料', '补充资料', {'资料': [str(src)]}, root))
        empty = TMP / 't11_d_扫描.pdf'
        empty.write_bytes(b'%PDF-1.4\n%%EOF\n')
        with self.assertRaisesRegex(RuntimeError, '没转出文字'):
            supplement.job_supplement(jobs.Job('补资料', '补充资料', {'资料': [str(empty)]}, project('t11_e')))

    def test_有索引的项目_块加进索引(self):
        root = project('t11_f')
        t = root / '资料' / '文本'
        t.mkdir(parents=True)
        (t / '001_可研.txt').write_text('可研原文', encoding='utf-8')
        (t / '索引.json').write_text(json.dumps({'块': [{'文件': '001_可研.txt', '来源': str(root / '资料' / '可研.docx'), '字数': 4, '标题': []}],
                                                '跳过': []}, ensure_ascii=False), encoding='utf-8')
        job = jobs.Job('补资料', '补充资料', {}, root)
        supplement._register(job, root, '1101-2300', [('初设.txt', str(root / '资料' / '原件' / '补充_1101-2300' / '初设.txt'), '初设原文')])
        self.assertEqual(dict(tools.source_texts(root)), {'可研.docx': '可研原文', '初设.txt': '初设原文'})
        self.assertFalse((root / tools.SETTINGS_FILE).exists())


class T13_补类比资料(Base):
    """类比项目（别的项目）的报告：不当本项目资料，摘成类比事实、重写第3章、改引用第3章的地方。"""
    ANALOG = '古交正沟控评.txt'
    OLD = '结合第3章类比检测结果（见表3.9-9）。'
    NEW = '结合第3章类比检测结果（见表3.3-2）。'

    def setUp(self):
        super().setUp()
        self.fail_on = set()

    def make(self, name, misfiled=True):
        """类比法的项目：第3章换成类比法骨架；第4章引用第3章，第5章不引用。misfiled：以前当本项目资料补进来过一次。"""
        root = project(name)
        for p in (root / '正文').glob('*.md'):
            if not p.name.startswith(('01', '02_1', '04_1', '05')):
                p.unlink()
        shutil.copy2(config.ROOT / 'templates' / '项目骨架' / '正文' / '03_类比企业（项目）调查与分析.md', root / '正文')
        f4 = next((root / '正文').glob('04_1*.md'))
        tools.write(f4, tools.read(f4).rstrip('\n') + '\n\n' + self.OLD + '\n')
        f5 = next((root / '正文').glob('05*.md'))
        tools.write(f5, analog.RE_REF.sub('本项目', tools.read(f5)))
        d = root / '资料'
        tools.write(d / '原件' / '可研.txt', '第一章 总论\n本项目为风电场。\n' * 10)
        if misfiled:
            blocks = [{'文件': '001_可研.txt', '来源': str(d / '原件' / '可研.txt'), '字数': 10, '标题': []},
                      {'文件': '补充/1102-1726_01_古交正沟控评.txt', '来源': str(d / '原件' / '补充_1102-1726' / self.ANALOG), '字数': 10, '标题': []}]
            tools.write(d / '文本' / '索引.json', json.dumps({'块': blocks, '跳过': []}, ensure_ascii=False))
            tools.write(d / '文本' / '001_可研.txt', '可研原文')
            tools.write(d / '文本' / '补充' / '1102-1726_01_古交正沟控评.txt', '控评原文（误记）')
            tools.write(d / '原件' / '补充_1102-1726' / self.ANALOG, '控评原文（误记）')
            tools.write(d / '事实片段' / '补资料' / '待确认.json', json.dumps({'新资料': [self.ANALOG], '变化': []}, ensure_ascii=False))
        src = TMP / (name + '_类比资料')
        tools.write(src / self.ANALOG, '9.1.2 职业健康检查结果\n本次体检应检 4 人，实检 4 人。\n6.5 检测结果\n风机机舱噪声 82.3 dB(A)，合格。\n' * 20)
        (src / '体检报告.pdf').write_bytes(b'%PDF-1.4\n%%EOF\n')
        return root, [str(src / self.ANALOG), str(src / '体检报告.pdf')]

    def handlers(self, root):
        skel = tools.read(root / '正文' / '03_类比企业（项目）调查与分析.md')
        body = re.sub(r'【待填[^】]*】', '古交正沟风电二期项目', re.sub(r'<!-- 状态：.*?-->\s*', '', skel, flags=re.S))

        def ref(label, **k):
            f = label[len('改引用 '):]
            if f in self.fail_on:
                raise llm.CallError(label + '：' + llm.CUT)
            hit = f.startswith('04_1') and self.OLD in tools.read(root / '正文' / f)
            return {'改动': [{'原文': self.OLD, '新文': self.NEW, '说明': ''}] if hit else [], '跳过': []}

        return (('摘类比事实', lambda **k: '# 类比事实\n\n## 资料\n\n- 古交正沟控评报告 XLH-KP-2025-D008（出处：控评 封面）\n\n'
                                        '## 职业健康检查\n\n- 应检 4 人，实检 4 人（出处：控评 9.1.2）\n'),
                ('写 03_', lambda **k: '<<<正文>>>\n%s\n<<<可研没有>>>\n无\n' % body.strip()),
                ('改引用 ', ref))

    def test_挪走误记的_摘类比事实_写第3章_改引用(self):
        root, given = self.make('t13_a')
        FAKE.reset(*self.handlers(root))
        with mock.patch.object(tools, 'sample_for', return_value=None):
            res = analog.job_analog(jobs.Job('类比资料', '补类比资料', {'资料': given, '说明': '控评'}, root))
        refs = sorted(f for f in tools.body_files(root) if f != '03_类比企业（项目）调查与分析.md' and analog.RE_REF.search(tools.read(root / '正文' / f)))
        labels = [x for x in FAKE.labels() if not x.startswith('清零')]
        self.assertEqual(labels[:2], ['摘类比事实', '写 03_类比企业（项目）调查与分析.md'])
        self.assertEqual(sorted(labels[2:]), ['改引用 ' + f for f in refs])
        self.assertTrue(any(f.startswith('04_1') for f in refs) and not any(f.startswith('05') for f in refs), refs)
        write3 = next(c['内容'] for c in FAKE.calls if c['标签'].startswith('写 03_'))
        self.assertIn('类比事实.md（类比项目的，不是本项目的事实）', write3)
        self.assertIn('第3章里类比项目的内容只取“类比事实.md”', write3)
        ref4 = next(c for c in FAKE.calls if c['标签'].startswith('改引用 04_1'))
        self.assertIn('刚写好的第3章', ref4['内容'])
        self.assertIn('拿不准、要用户定的，写进“跳过”', ref4['内容'])       # 田林那次第4章没说清，模型改用文字反问，两次不是 JSON
        self.assertIs(ref4['schema'], common.EDITS)
        self.assertEqual((ref4['参数'].get('effort'), ref4['参数'].get('max_tokens')), ('medium', analog.REF_MAX))
        self.assertNotIn('【待填', tools.read(root / '正文' / '03_类比企业（项目）调查与分析.md'))
        self.assertIn(self.NEW, tools.read(next((root / '正文').glob('04_1*.md'))))
        # 以前当本项目资料记进来的那份挪走：索引里没有了，文字、原件、改之前的索引在 资料/类比/移出/ 里；那次空的待确认清单丢掉
        idx = json.loads(tools.read(root / '资料' / '文本' / '索引.json'))
        self.assertEqual([b['文件'] for b in idx['块']], ['001_可研.txt'])
        out = tools.analog_dir(root) / '移出'
        self.assertTrue(list(out.glob('*/文本/补充/1102-1726_01_古交正沟控评.txt')) and list(out.glob('*/原件/补充_1102-1726/' + self.ANALOG)))
        self.assertTrue(list(out.glob('*/索引.json')))
        self.assertFalse((root / '资料' / '原件' / '补充_1102-1726').exists())
        self.assertIsNone(supplement.pending(root))
        self.assertEqual([n for n, _ in tools.source_texts(root)], ['可研.txt'])
        self.assertEqual([n for n, _ in tools.analog_texts(root)], [self.ANALOG])
        self.assertIn('类比事实.md（类比项目的，不是本项目的事实）', '\n'.join(common.fact_docs(root)))
        self.assertEqual((res['类比资料'], res['移出'], res['第3章']['待填']), ([self.ANALOG], [self.ANALOG], 0))
        self.assertTrue(res['没转'] and res['没转'][0].startswith('体检报告.pdf'), res['没转'])
        self.assertTrue(Path(res['备份']).is_dir())
        self.assertIn('## 补类比资料', tools.read(root / '输出' / '交稿说明.md'))
        # 以后整份重转资料：资料/类比/ 里的不当本项目资料
        rc, _out = tools.report('资料', root)
        self.assertEqual(rc, 0)
        self.assertEqual([n for n, _ in tools.source_texts(root)], ['可研.txt'])

    def test_改引用断了_接着做只补改那个文件(self):
        root, given = self.make('t13_b', misfiled=False)
        f4 = next(f for f in tools.body_files(root) if f.startswith('04_1'))
        self.fail_on = {f4}
        FAKE.reset(*self.handlers(root))
        with mock.patch.object(tools, 'sample_for', return_value=None):
            with self.assertRaisesRegex(RuntimeError, '点“接着做”只补改这几个'):
                analog.job_analog(jobs.Job('类比资料', '补类比资料', {'资料': given}, root))
            self.assertTrue(analog.unfinished(root))
            self.fail_on = set()
            FAKE.reset(*self.handlers(root))
            res = analog.job_analog(jobs.Job('类比资料', '补类比资料', {'资料': [], '接着做': True}, root))
        self.assertEqual([x for x in FAKE.labels() if not x.startswith('清零')], ['改引用 ' + f4])   # 不重摘、不重写第3章
        self.assertIn(self.NEW, tools.read(root / '正文' / f4))
        self.assertFalse(analog.unfinished(root))
        self.assertEqual(res['引用'][f4], 1)

    def test_风险评估法的报告_扫描件(self):
        root = project('t13_c')
        with self.assertRaisesRegex(RuntimeError, '用不上'):
            analog.job_analog(jobs.Job('类比资料', '补类比资料', {'资料': [str(TMP / '随便.txt')]}, root))
        self.assertFalse(tools.analog_dir(root).exists())
        root, _ = self.make('t13_d', misfiled=False)
        scan = TMP / 't13_d_扫描.pdf'
        scan.write_bytes(b'%PDF-1.4\n%%EOF\n')
        with self.assertRaisesRegex(RuntimeError, '没转出文字'):
            analog.job_analog(jobs.Job('类比资料', '补类比资料', {'资料': [str(scan)]}, root))
        self.assertFalse((tools.analog_dir(root) / analog.STATE).exists())
        self.assertEqual(tools.analog_texts(root), [])


from app.tools import doctype  # noqa: E402

TL_PRE = Path(os.environ.get('DC_TL_PRE') or Path(os.sep, 'tmp', 'dc-build', 'tl_pre'))      # 田林预评价的复制品（不含资料/原件），没有时专篇新报告那一项跳过


def _design(name, pre=None):
    """从设计专篇骨架建一个临时专篇项目（主报告＋附件册）；给了 pre 就在 流水线设置.json 记上承接的预评价。"""
    dst = TMP / name
    shutil.copytree(doctype.skeleton(doctype.DESIGN), dst)
    if pre:
        tools.write(dst / tools.SETTINGS_FILE, json.dumps({'预评价': str(pre)}, ensure_ascii=False))
    return dst.resolve()


class T14_设计专篇(Base):
    def test_类型_没写的是预评价_附件册取主报告(self):
        root = _design('t14_type')
        self.assertEqual((tools.report_type(root), tools.report_type(root / '附件册')), (doctype.DESIGN, doctype.ANNEX))
        self.assertEqual(tools.report_type(BASE), doctype.PRE)                      # 老项目没写“报告类型”
        self.assertEqual(tools.method_of(root), '')                                 # 专篇不回落成类比法
        self.assertEqual(doctype.main_root(root / '附件册'), root)
        self.assertEqual(doctype.volumes(root), [root, root / '附件册'])
        tools.write(root / '资料' / '事实库.md', '# 事实库\n\n## 项目概况\n- 甲\n')
        self.assertEqual([n for n, _ in tools.facts(root / '附件册')], ['事实库.md'])   # 附件册没有自己的资料

    def test_整章断流_按节分两半写_半截还断再分(self):
        root = _design('t14_split')
        fname = '04_2_防护设施设计.md'
        skel = tools.read(root / '正文' / fname)
        units = new._units(skel)
        self.assertGreaterEqual(len(units), 4)
        tools.write(root / '资料' / '事实库.md', '# 事实库\n\n## 项目概况\n- 甲\n')

        def body(label, content, **k):
            if label == '写 ' + fname:                                          # 整章：断流
                raise llm.CallError('%s：%s（收到 11537 字）' % (label, llm.CUT))
            m = re.search(r'第(\d+)～(\d+)节', label)
            lo, hi = int(m.group(1)), int(m.group(2))
            if hi - lo + 1 > len(units) // 2 and lo == 1:                       # 前半整段也断一次：再对半分
                raise llm.CallError('%s：%s' % (label, llm.CUT))
            out = ('# 4 设计\n' if lo == 1 else '# 4 设计\n') + ''.join('%s\n- 第%d节内容%s\n' % (units[i][0], i + 1, '很长' * 400)
                                                                   for i in range(lo - 1, hi))
            return '<<<正文>>>\n' + out
        FAKE.reset(('写 ', body), ('清零 ', lambda **k: {'改动': [], '跳过': []}))
        res = new._write_design(jobs.Job('新报告', '新报告', {}, root), root, doctype.DESIGN, None, fname, [])
        text = tools.read(root / '正文' / fname)
        for i, (h, _) in enumerate(units):
            self.assertEqual(text.count(h + '\n'), 1, h)                         # 每节一次
            self.assertIn('第%d节内容' % (i + 1), text)
        self.assertEqual(text.count('# 4 设计'), 1)                               # 后一段重复的文件标题去掉
        labels = FAKE.labels()
        self.assertTrue(any('第' in x and '～' in x for x in labels))
        self.assertEqual(sum(x == '写 ' + fname for x in labels), 1)              # 整章只试一次就分半，没有白等第二次、第三次
        self.assertGreater(res['字数'], 100)

    def test_整章断流_一张大表按分组行拆_拼出的表不断(self):
        root = _design('t14_table')
        annex = root / '附件册'
        fname = '05_设施清单核对.md'
        skel = tools.read(annex / '正文' / fname)
        units = new._units(skel)
        self.assertGreaterEqual(len(units), 3)
        self.assertTrue(all(u[0].startswith('|') for u in units))                 # 骨架只有一个“## 5.1”，按“一、二、三”分组行拆
        tools.write(root / '资料' / '事实库.md', '# 事实库\n\n## 项目概况\n- 甲\n')
        head = '# 附件5 设计文件职业病防护设施清单核对\n\n核对范围和方法。\n\n## 5.1 职业病防护设施逐项核对\n\n表5.1-1  职业病防护设施清单核对\n' \
               '<!-- 表格 列宽=450,1500 字号=21 对齐=中,左 -->\n| 序号 | 设施 |\n|---|---|\n'

        def body(label, content, **k):
            if label == '写 附件册/' + fname:
                raise llm.CallError('%s：%s（收到 17134 字）' % (label, llm.CUT))
            m = re.search(r'第(\d+)～(\d+)分组', label)
            self.assertIsNotNone(m, label)
            lo, hi = int(m.group(1)), int(m.group(2))
            rows = ''
            for i in range(lo - 1, hi):
                g = units[i][0].split('|')[1].strip()
                rows += '| %s | < |\n| %d | %s |\n' % (g, i + 1, '内容' * 200)
            # 后一段模型又写了表题、表头：程序去掉；表后的内容在最后一段
            out = (head if lo == 1 else '表5.1-1  职业病防护设施清单核对\n| 序号 | 设施 |\n|---|---|\n') + rows
            return '<<<正文>>>\n' + out + ('\n<!-- 纵向 -->' if hi == len(units) else '')
        FAKE.reset(('写 ', body), ('清零 ', lambda **k: {'改动': [], '跳过': []}))
        new._write_design(jobs.Job('新报告', '新报告', {}, annex), annex, doctype.ANNEX, None, fname, [])
        text = tools.read(annex / '正文' / fname)
        self.assertEqual(text.count('| 序号 | 设施 |'), 1)                           # 表头只一份
        self.assertEqual(text.count('表5.1-1  职业病防护设施清单核对'), 1)
        self.assertNotIn('|\n\n|', text)                                          # 表行之间没有空行，表不会断
        for i, (h, _) in enumerate(units):
            self.assertEqual(text.count(h.split('|')[1].strip()), 1)
        self.assertEqual(text.count('<!-- 纵向 -->'), 1)
        self.assertTrue(tools.parse_error(text, fname) in (None, ''))

    def test_整章断流_骨架拆不开_整章再试一次(self):
        root = _design('t14_nosplit')
        fname = '02_工程分析资料.md'
        tools.write(root / '附件册' / '正文' / fname, '# 附件2\n\n只有一节没有表。\n')
        tools.write(root / '资料' / '事实库.md', '# 事实库\n\n## 项目概况\n- 甲\n')
        n = {'k': 0}

        def body(label, content, **k):
            n['k'] += 1
            if label == '写 附件册/' + fname:
                raise llm.CallError('%s：%s' % (label, llm.CUT))
            return '<<<正文>>>\n# 附件2\n\n' + '正文很长。' * 100
        FAKE.reset(('写 ', body), ('清零 ', lambda **k: {'改动': [], '跳过': []}))
        new._write_design(jobs.Job('新报告', '新报告', {}, root / '附件册'), root / '附件册', doctype.ANNEX, None, fname, [])
        self.assertIn('（整章再试）', ''.join(FAKE.labels()))
        self.assertEqual(n['k'], 2)

    def test_写章模型只思考没写字_降档重写一次(self):
        root = _design('t14_empty')
        fname = '01_概述.md'
        tools.write(root / '资料' / '事实库.md', '# 事实库\n\n## 项目概况\n- 甲\n')
        seen = []

        def body(label, content, **k):
            seen.append(label)
            return '' if '降档' not in label else '<<<正文>>>\n# 1 概述\n\n' + '正文很长。' * 300
        FAKE.reset(('写 ', body), ('清零 ', lambda **k: {'改动': [], '跳过': []}))
        res = new._write_design(jobs.Job('新报告', '新报告', {}, root), root, doctype.DESIGN, None, fname, [])
        self.assertEqual(seen, ['写 ' + fname, '写 ' + fname + '（降档重写）'])        # 空回答不当成断流去拆，降档写一次就成
        self.assertGreater(res['字数'], 300)

    def test_写作先后_依赖对_没有环_附件册在主报告之后(self):
        main = tools.body_files(doctype.skeleton(doctype.DESIGN))
        annex = tools.body_files(doctype.skeleton(doctype.DESIGN) / '附件册')
        self.assertTrue(main and annex)
        deps = {f: doctype.deps_of(doctype.DESIGN, f, main) for f in main}
        self.assertEqual(deps[next(f for f in main if f.startswith('02_1'))], [])
        f45 = next(f for f in main if f.startswith('04_5'))
        self.assertEqual(sorted(deps[f45]), sorted(f for f in main if f[:2] in ('02', '03') or f[:4] in ('04_1', '04_2', '04_3', '04_4')))
        self.assertEqual(sorted(deps[next(f for f in main if f.startswith('01'))]), sorted(f for f in main if f[:2] != '01'))
        for k in ('02', '03', '04_1', '04_2', '04_3', '04_4', '04_5', '05', '01'):    # 每个文件都有自己的键（注释吞掉键会退回到“04”或不等）
            self.assertIn(k, doctype.DEPS[doctype.DESIGN])
        self.assertEqual(sorted(deps[next(f for f in main if f.startswith('04_3'))]), sorted(f for f in main if f[:2] in ('02', '03')))
        seen, left = [], dict(deps)
        while left:                                                                # 拓扑排序排得完＝没有环
            ready = [f for f, d in left.items() if all(x in seen for x in d)]
            self.assertTrue(ready, '有环：%s' % left)
            seen += ready
            for f in ready:
                del left[f]
        # 附件册各文件等主报告全写好；册内只有附件4 等附件2（舱室净容积取表2.2-1）
        self.assertTrue(all(doctype.deps_of(doctype.ANNEX, f, annex) == [] for f in annex if not f.startswith('04')))
        self.assertEqual([x[:2] for x in doctype.deps_of(doctype.ANNEX, next(f for f in annex if f.startswith('04')), annex)], ['02'])
        pre = ['01_总论.md', '08_职业卫生管理.md', '09_关键控制点.md', '10_补充建议.md', '11_结论与建议.md']
        self.assertEqual(doctype.pre_files(doctype.DESIGN, f45, pre), pre[1:])                # 采纳情况给预评价第8～11章

    def test_要点卡按类型(self):
        d = config.STD / '设计专篇' / '各章要点'
        tools.write(d / '04.md', '# 专篇第4章卡\n')
        tools.write(d / '附件.md', '# 附件册的卡\n')
        tools.write(config.STD / '各章要点' / '04.md', '# 预评价第4章卡\n')
        self.assertEqual(common.card(doctype.DESIGN, '', '04_2_防护设施设计.md'), '# 专篇第4章卡\n')
        self.assertEqual(common.card(doctype.ANNEX, '', '04_计算书.md'), '# 附件册的卡\n')
        self.assertEqual(common.card(doctype.PRE, '类比法', '04_职业病危害因素.md'), '# 预评价第4章卡\n')
        self.assertEqual(common.card('类比法', '04_职业病危害因素.md'), '# 预评价第4章卡\n')    # 老的两参数写法照旧

    def test_角色和口径按类型(self):
        tools.write(config.STD / '设计专篇' / '口径.md', '# 设计专篇口径\n\n- K007 在专篇里不用\n')
        pre, des = common.system(), common.system(doctype.DESIGN, ('甲', '乙'))
        self.assertTrue(pre.startswith(common.ROLE) and des.startswith(common.ROLE_DESIGN))
        self.assertIn('可研没写的，写“可研没有写……”并提补充措施', common.ROLE)
        self.assertNotIn('可研没有写……”并提补充措施', common.ROLE_DESIGN)
        self.assertIn('采购技术协议应满足', common.ROLE_DESIGN)
        self.assertIn('K007 在专篇里不用', des)
        self.assertNotIn('K007 在专篇里不用', pre)
        self.assertIn('<资料 名称="甲">', des)
        self.assertEqual(common.system(('甲', '乙')).count('<资料 名称="甲">'), 1)           # 不带类型的老写法照旧

    def test_名称表_专篇用岗位因素表_附件册用主报告的(self):
        root = _design('t14_names')
        self.assertIn('表1.3-1', common.names_table(root))                      # 没有岗位因素表：用第1章表1.3-1、第3章表3.1-x
        f3 = next(f for f in tools.body_files(root) if f.startswith('03'))
        tools.write(root / '正文' / f3, '# 3 职业病危害因素分析及危害程度预测\n\n表3.1-1  生产过程职业病危害因素\n\n| 场所 | 因素 |\n|---|---|\n| 升压站 | 噪声 |\n')
        self.assertIn('升压站', common.names_table(root))
        tools.write(root / '资料' / '岗位因素表.md', '# 岗位因素表\n\n| 评价单元 | 工作场所 |\n|---|---|\n| 甲 | 乙 |\n')
        self.assertIn('岗位因素表（名称以此为准）', common.names_table(root / '附件册'))

    def test_预评价没有岗位因素表_照表4点1减1拼_不调模型(self):
        # 11-07 晋钢烧结、200MW：预评价是手工改出来的，资料/ 里没有岗位因素表；名称照预评价正文的表，一字不改
        pre = project('t14_post_pre')
        (pre / '资料' / '岗位因素表.md').unlink(missing_ok=True)
        f1 = next(f for f in tools.body_files(pre) if f.startswith('01'))
        f4 = next(f for f in tools.body_files(pre) if f.startswith('04'))
        tools.write(pre / '正文' / f4, '# 4 职业病危害因素\n\n表4.1-1  运行期职业病危害因素分布及接触预测\n'
                    '<!-- 表格 列宽=1,1,1,1,1,1 -->\n| 评价单元 | 工作场所 | 工作地点 | 工种（岗位） | 职业病危害因素 | 来源 |\n|---|---|---|---|---|---|\n'
                    '| 燃料储运系统 | 料场 | 卸车点 | 料场人员 | 矽尘、噪声 | 卸车扬尘 |\n| ^ | 碎料机室 | 碎料机 | 料场人员 | 矽尘 | 破碎 |\n\n'
                    '表4.1-2  各工种（岗位）职业病危害因素接触情况\n| 工种（岗位） | 人数 |\n|---|---|\n| 料场人员 | 4 |\n')
        tools.write(pre / '正文' / f1, tools.read(pre / '正文' / f1) + '\n\n表1.8-1  评价单元及工作场所划分\n| 序号 | 评价单元 | 工作场所 |\n|---|---|---|\n| 1 | 燃料储运系统 | 料场 |\n')
        md = new._post_from_pre(pre)
        lines = md.split('\n')
        head = next(ln for ln in lines if ln.startswith('|'))
        self.assertEqual(head, '| 评价单元 | 工作场所 | 工种（岗位） | 工作地点 | 职业病危害因素 | 来源 |')   # 前三列换成单元、场所、工种
        self.assertIn('| 燃料储运系统 | 料场 | 料场人员 | 卸车点 | 矽尘、噪声 | 卸车扬尘 |', md)                 # 内容一字不改
        self.assertIn('预评价报告表1.8-1（原表）', md)
        self.assertIn('| 料场人员 | 4 |', md)
        self.assertEqual(new._table_counts(md.split('\n## ')[0])['场所'], 2)
        root = _design('t14_post', pre)
        FAKE.reset()
        job = jobs.Job('新报告', '新报告', {}, root)
        self.assertTrue(new._ensure_post(job, root, pre))
        self.assertEqual(FAKE.calls, [])                                         # 不调模型
        self.assertIn('料场人员', common.names_table(root / '附件册'))
        self.assertFalse(new._ensure_post(job, root, pre))                        # 已有的不动
        tools.write(pre / '正文' / f4, '# 4 职业病危害因素\n\n没有表。\n')
        self.assertEqual(new._post_from_pre(pre), '')                              # 找不到表4.1-1：不拼

    def test_第2章按节名找预评价文件(self):
        # 200MW 预评价第2章 02_4＝生产设备、02_5＝建筑卫生；照文件名前缀会把建筑卫生学漏掉
        files = ['01_总论.md', '02_1_工程概况.md', '02_2_选址与总体布局.md', '02_3_原辅材料与工艺.md', '02_4_生产设备.md',
                 '02_5_建筑卫生与辅助用室.md', '05_防护设施.md', '10_1_问题.md', '10_2_建议.md']
        titles = {'02_1_工程概况.md': '## 2.1 工程概况分析', '02_2_选址与总体布局.md': '## 2.2 选址分析与评价\n## 2.3 总体布局分析与评价',
                  '02_3_原辅材料与工艺.md': '## 2.4 产品及原辅材料分析\n## 2.5 生产工艺分析与评价',
                  '02_4_生产设备.md': '## 2.6 生产设备及布局分析与评价', '02_5_建筑卫生与辅助用室.md': '## 2.7 建筑卫生学分析与评价\n## 2.8 辅助用室分析与评价'}
        got = doctype.pre_files(doctype.DESIGN, '04_1_构建筑物设计.md', files, titles)
        self.assertEqual(got, ['02_2_选址与总体布局.md', '02_5_建筑卫生与辅助用室.md', '05_防护设施.md', '10_1_问题.md', '10_2_建议.md'])
        self.assertEqual(doctype.pre_files(doctype.DESIGN, '04_4_x.md', files, titles), ['02_5_建筑卫生与辅助用室.md', '10_1_问题.md', '10_2_建议.md'])
        self.assertEqual(doctype.pre_files(doctype.ANNEX, '04_x.md', files, titles)[0], '02_5_建筑卫生与辅助用室.md')
        self.assertEqual(doctype.pre_files(doctype.DESIGN, '04_1_x.md', files)[:2], ['02_2_选址与总体布局.md', '02_4_生产设备.md'])   # 不给标题：照前缀

    def test_要点卡按预评价方法加说明(self):
        pre = project('t14_method_pre')
        root = _design('t14_method', pre)
        f3 = next(f for f in tools.body_files(root) if f.startswith('03'))
        f2 = next(f for f in tools.body_files(root) if f.startswith('02_1'))
        with mock.patch.object(tools, 'method_of', lambda r: '风险评估法'):
            c3 = common.card(doctype.DESIGN, '', f3, root=root)
            self.assertIn('承接的预评价用风险评估法', c3)
            self.assertIn('预评价风险评估结果摘要', common.card(doctype.ANNEX, '', '03_x.md', root=root / '附件册'))
            self.assertNotIn('本项目的方法', common.card(doctype.DESIGN, '', f2, root=root))         # 第2章不加
            self.assertNotIn('本项目的方法', common.card(doctype.DESIGN, '', f3))                    # 不给 root 照旧
        with mock.patch.object(tools, 'method_of', lambda r: '类比法'):
            self.assertIn('承接的预评价用类比法', common.card(doctype.DESIGN, '', f3, root=root))

    def test_登记第6列类型_兼容老5列(self):
        reg = TMP / 't14_登记.md'
        reg.write_text('| 项目 | 方法 | 文件夹 | 样板 | 定稿 |\n|---|---|---|---|---|\n| 老甲 | 类比法 | /x/老甲 | 是 | |\n', encoding='utf-8')
        with mock.patch.object(config, 'REGISTER', reg):
            self.assertEqual(tools.registered()[0]['类型'], doctype.PRE)
            self.assertTrue(tools.register_add('专篇乙', '', TMP / 't14_乙', doctype.DESIGN))
            self.assertTrue(tools.register_add('预评价丙', '风险评估法', TMP / 't14_丙'))
            rows = tools.registered()
            self.assertEqual([(r['项目'], r['方法'], r['类型']) for r in rows],
                             [('老甲', '类比法', '预评价'), ('专篇乙', '—', '设计专篇'), ('预评价丙', '风险评估法', '预评价')])
            text = reg.read_text(encoding='utf-8')
            self.assertIn('| 专篇乙 | — | %s | | | 设计专篇 |' % (TMP / 't14_乙'), text)
            self.assertIn('| 预评价丙 | 风险评估法 | %s | | | |' % (TMP / 't14_丙'), text)
            self.assertEqual(tools.sample_for('类比法'), None)                          # 老甲的文件夹不存在
            self.assertEqual(revise.targets('全部', doctype.DESIGN), [])                # 专篇乙没有正文

    def test_配图位置按类型(self):
        root = _design('t14_fig')
        self.assertEqual(sorted(figures.places(root)), ['布局'])                       # 主报告只放总平面布置图
        self.assertEqual(figures._span(root, '布局')[0][:4], '02_2')
        self.assertIsNone(figures._wind_para(root))
        self.assertEqual(sorted(figures.places(root / '附件册')), ['位置', '工艺'])
        self.assertTrue(figures._wind_para(root / '附件册'))

    def test_配图_图片行有了图没有的_从预评价拷(self):
        # 11-05 田林专篇：模型照骨架写了图片行，配图各步看到“已有这类图”都跳过，出稿时图片文件不存在
        pre = TMP / 't14_figpre'
        (pre / '正文').mkdir(parents=True)
        (pre / '图').mkdir()
        from PIL import Image
        for n in ('总平面.png', '流程甲.png', '流程乙.png'):
            Image.new('RGB', (40, 30), 'white').save(pre / '图' / n)
        tools.write(pre / '正文' / '02.md', '# 2 甲\n\n![图2.3-1  升压站平面布置图](图/总平面.png){宽=23cm}\n图2.3-1  升压站平面布置图\n\n'
                    '![图2.5-1  甲工艺流程图](图/流程甲.png){宽=23cm}\n图2.5-1  甲工艺流程图\n\n'
                    '![图2.5-2  乙工艺流程图](图/流程乙.png){宽=23cm}\n图2.5-2  乙工艺流程图\n')
        root = _design('t14_figtake', pre)
        ann = root / '附件册'
        tools.write(root / '正文' / '02_2_工程分析.md', '# 2 乙\n\n## 2.2 丙\n\n总平面见图2.2-1。\n\n'
                    '![总平面布置图](图/总平面布置图.png){宽=22cm}\n图2.2-1  总平面布置图\n')
        for f in tools.body_files(ann):
            (ann / '正文' / f).unlink()
        tools.write(ann / '正文' / '02_工程分析资料.md', '# 附件2 甲\n\n## 2.3 工艺流程\n\n见图2.3-1。\n\n'
                    '![工艺流程图](图/工艺流程图.png){宽=15cm}\n图2.3-1  工艺流程及职业病危害因素分布图\n\n'
                    '![位置图](图/位置.png){宽=14cm}\n图2.3-2  项目区域位置图\n')
        self.assertEqual(figures.take_pre(root), (['图2.2-1  总平面布置图'], []))
        self.assertTrue((root / '图' / '总平面布置图.png').is_file())
        took, miss = figures.take_pre(ann)
        self.assertEqual(took, ['图2.3-1  甲工艺流程图', '图2.3-2  乙工艺流程图'])
        self.assertEqual(miss, ['图2.3-3  项目区域位置图（预评价里也没有同类的图）'])
        text = tools.read(ann / '正文' / '02_工程分析资料.md')
        self.assertIn('见图2.3-1～图2.3-2。', text)
        self.assertIn('图2.3-2  乙工艺流程图', text)
        self.assertIn('图2.3-3  项目区域位置图', text)                    # 后面的图跟着重编
        self.assertTrue((ann / '图' / '流程甲.png').is_file() and (ann / '图' / '流程乙.png').is_file())
        self.assertEqual(figures.take_pre(ann), ([], ['图2.3-3  项目区域位置图（预评价里也没有同类的图）']))   # 再跑不重拷
        self.assertEqual(figures.take_pre(BASE), ([], []))                 # 预评价不动

    def test_命令行_设计专篇(self):
        a = cli._parser().parse_args(['新报告', '/tmp/专篇', '--类型', '设计专篇', '--预评价', str(BASE), '--编号', 'AT-2026-Z020'])
        k, title, p, proj = cli._job(a)
        self.assertEqual((p['类型'], p['预评价'], p['方法'], p['编号']), ('设计专篇', str(BASE), '', 'AT-2026-Z020'))
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            cli.main(['新报告', '/tmp/专篇', '--类型', '设计专篇'])                     # 没给预评价
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'):
            cli.main(['新报告', '/tmp/预评价'])                                         # 预评价没给方法

    def test_类比资料_专篇不用(self):
        root = _design('t14_analog')
        with self.assertRaisesRegex(RuntimeError, '设计专篇用承接的预评价报告的类比事实'):
            analog.job_analog(jobs.Job('类比资料', '补类比资料', {'资料': []}, root))

    def test_审稿_专篇审两卷_用专篇的清单和遍(self):
        root = _design('t14_rv')
        tools.write(config.STD / '设计专篇' / '审稿清单.md', '# 专篇审稿清单\n\n## R1 事实\n- 专篇甲\n\n## R6 完整\n- 专篇乙\n')
        FAKE.reset(('审 ', lambda **k: {'意见': []}), ('跨章一致', lambda **k: {'改动': [], '没改': []}))
        out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=False)
        main, annex = tools.body_files(root), tools.body_files(root / '附件册')
        self.assertEqual(out['审了'], len(main) + len(annex))
        labels = FAKE.labels()
        self.assertIn('审 附件册/%s·%s' % (annex[0], review.PASSES[0][0]), labels)
        c = next(x for x in FAKE.calls if x['标签'] == '审 %s·%s' % (main[0], review.PASSES[1][0]))
        self.assertIn('设计完整性', c['内容'])
        self.assertIn('设计专篇主报告', c['内容'])
        cr = next(x for x in FAKE.calls if x['标签'] == '跨章一致')
        self.assertIn('正文/附件册/%s（带行号）' % annex[0], cr['内容'])
        self.assertIn('第4.8节采纳情况', cr['内容'])

    def test_审稿_只补审没成的遍_记录接在后面(self):
        # 11-05 田林专篇：中转断线，17 遍没审成；补审只审这几遍，别的遍不重花钱
        root = _design('t14_rv_pass')
        main, annex = tools.body_files(root), tools.body_files(root / '附件册')
        old = root / '审查' / '审稿记录' / main[0]
        tools.write(old, '# 审稿记录 %s\n\n| 上次的意见 |\n' % main[0])
        FAKE.reset(('审 ', lambda **k: {'意见': []}), ('跨章一致', lambda **k: {'改动': [], '没改': []}))
        n1, n3 = review.PASSES[0][0], review.PASSES[2][0]
        out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=False,
                         passes={main[0]: [n3], '附件册/' + annex[0]: [n1, n3]})
        labels = [x for x in FAKE.labels() if x.startswith('审 ')]
        self.assertEqual(sorted(labels), sorted(['审 %s·%s' % (main[0], n3), '审 附件册/%s·%s' % (annex[0], n1),
                                                 '审 附件册/%s·%s' % (annex[0], n3)]))
        self.assertEqual(out['审了'], 2)
        self.assertIn('跨章一致', FAKE.labels())
        rec = tools.read(old)
        self.assertIn('上次的意见', rec)
        self.assertIn('## 补审：' + n3, rec)
        with self.assertRaisesRegex(RuntimeError, '补审的文件或遍名不对'):
            review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=False, passes={main[0]: ['没有这遍']})
        a = cli._parser().parse_args(['审稿', str(root), '--补遍', '%s=%s' % (main[0], n3), '附件册/%s=%s' % (annex[0], n1)])
        self.assertEqual(cli._job(a)[2]['遍'], {main[0]: [n3], '附件册/' + annex[0]: [n1]})

    @unittest.skipUnless((TL_PRE / '正文').is_dir() and (TL_PRE / '资料' / '文本' / '索引.json').is_file(), '没有田林预评价的复制品')
    def test_新报告_设计专篇_建项目拷资料写两卷(self):
        pre = TMP / 't14_tl_pre'
        shutil.copytree(TL_PRE, pre)
        root = TMP / 't14_tl_专篇'
        reg = TMP / 't14_tl_登记.md'
        reg.write_text('| 项目 | 方法 | 文件夹 | 样板 | 定稿 | 类型 |\n|---|---|---|---|---|---|\n| 田林 | 类比法 | %s | | | |\n' % pre, encoding='utf-8')
        sk = doctype.skeleton(doctype.DESIGN)

        def write(label, **k):
            f = label[len('写 '):]
            src = sk / '附件册' / '正文' / f[len('附件册/'):] if f.startswith('附件册/') else sk / '正文' / f
            body = re.sub(r'【待填[^】]*】', '甲', tools.read(src))
            return '<<<正文>>>\n%s\n' % body.strip()

        FAKE.reset(('补摘事实', lambda **k: '## 竖向布置（各建构筑物标高）\n- 升压站室内外高差 0.3 m（出处：初设 5.2）\n'), ('写 ', write))
        form = {'文件夹': str(root), '类型': doctype.DESIGN, '预评价': str(pre), '方法': '', '资料': [], '编号': 'AT-2026-Z099',
                '日期': '2026年11月', '审稿': False, '出稿': False}
        job = jobs.Job('新报告', '新报告', form, root)
        with mock.patch.object(config, 'REGISTER', reg), mock.patch.object(figures, 'run_all', lambda *a: {}) as _fg:
            res = new.job_new(job)
            row = tools.project_row(root)
        # 建项目：主报告＋附件册
        self.assertEqual((tools.report_type(root), tools.report_type(root / '附件册')), (doctype.DESIGN, doctype.ANNEX))
        self.assertEqual(json.loads(tools.read(root / tools.SETTINGS_FILE))['预评价'], str(pre.resolve()))
        self.assertEqual(doctype.pre_root(root), pre.resolve())
        self.assertEqual((row['类型'], row['方法']), (doctype.DESIGN, '—'))
        # 项目信息照预评价拷，编号、日期用表单，人员类别对到专篇
        info = tools.read(root / '项目信息.md')
        pinfo = tools.read(pre / '项目信息.md')
        for k in ('项目名称', '建设单位', '评价单位', '法定代表人'):
            self.assertIn('- %s：%s' % (k, new._info_val(pinfo, k)), info)
        self.assertEqual(re.findall(r'^- 封面名称：(.*)$', info, re.M), re.findall(r'^- 封面名称：(.*)$', pinfo, re.M))
        self.assertIn('- 报告编号：AT-2026-Z099', info)
        self.assertIn('| 专篇编写人 |', info)
        self.assertNotIn('| 报告签发人 |', info)                                   # 专篇声明里没有签发人
        # 资料从预评价拷：事实库（补摘了专篇的几节）、口径、岗位因素表、类比事实、文本
        for name in ('口径.md', '岗位因素表.md', '类比/类比事实.md', '文本/索引.json'):
            self.assertTrue((root / '资料' / name).is_file(), name)
        self.assertEqual(tools.read(root / '资料' / '岗位因素表.md'), tools.read(pre / '资料' / '岗位因素表.md'))
        fx = tools.read(root / '资料' / '事实库.md')
        self.assertTrue(fx.startswith(tools.read(pre / '资料' / '事实库.md').rstrip('\n')[:2000]))
        self.assertIn('升压站室内外高差 0.3 m', fx)
        self.assertTrue(all(('## ' + h[:6]) in fx for h in facts.HEADS_DESIGN))
        self.assertNotIn('## 可研没有写的', tools.read(root / '资料' / '口径.md'))
        self.assertTrue(tools.source_texts(root))
        # 写作：主报告每个文件一次，附件册每个文件一次，附件册全在主报告之后；依赖先写
        labels = [x for x in FAKE.labels() if x.startswith('写 ')]
        main, annex = tools.body_files(root), tools.body_files(root / '附件册')
        self.assertEqual(sorted(labels), sorted(['写 ' + f for f in main] + ['写 附件册/' + f for f in annex]))
        last_main = max(labels.index('写 ' + f) for f in main)
        self.assertTrue(all(labels.index('写 附件册/' + f) > last_main for f in annex))
        for f in main:
            for d in doctype.deps_of(doctype.DESIGN, f, main):
                self.assertLess(labels.index('写 ' + d), labels.index('写 ' + f), '%s 要等 %s' % (f, d))
        call = lambda lab: next(c for c in FAKE.calls if c['标签'] == lab)  # noqa: E731
        c42 = call('写 ' + next(f for f in main if f.startswith('04_2')))
        for pf in doctype.pre_files(doctype.DESIGN, '04_2', tools.body_files(pre)):
            self.assertIn('承接的预评价报告 正文/%s（全文）' % pf, c42['内容'])
        self.assertIn('类比事实.md（类比项目的，不是本项目的事实）', c42['内容'])
        self.assertNotIn('<<<可研没有>>>', c42['内容'])
        self.assertIn('附件册表1.1-1', c42['内容'])
        c06 = call('写 附件册/' + next(f for f in annex if f.startswith('06')))
        self.assertIn('主报告 正文/%s' % next(f for f in main if f.startswith('04_5')), c06['内容'])
        self.assertTrue(any(x.startswith('承接的预评价报告 正文/10') for x in re.findall(r'名称="([^"]+)"', c06['内容'])))
        self.assertTrue(all(c['标签'] != '填项目信息' and c['标签'] != '岗位因素表' for c in FAKE.calls))   # 不调模型
        self.assertEqual((res['类型'], res['写作'], res['附件册']), (doctype.DESIGN, len(main), len(annex)))
        note = tools.read(root / '输出' / '交稿说明.md')
        self.assertIn('主报告＋资料性附件册两卷', note)
        self.assertIn(str(pre.resolve()), note)
        self.assertFalse(new._record_path(root).exists() or new._record_path(root / '附件册').exists())


class T15_设计专篇审查修正(Base):
    """审查报的问题：预评价交稿说明、跨卷核对、改稿规则去向、审稿附件册章键、只写附件册、承接预评价的校验。"""

    def test_预评价交稿说明_gaps模块不被遮住(self):
        root = project('t15_note')
        tools.write(root / '资料' / '口径.md', '# 口径\n\n## 可研没有写的\n\n- 甲：第5章\n')
        chk = tools.check(root)
        table = {'单元': 1, '场所': 1, '工种': 1}
        new._note(root, {'方法': '类比法'}, {'项目名称': '测试'}, chk, None, None, table)
        note = tools.read(root / '输出' / '交稿说明.md')
        self.assertIn('## 可研没有写的', note)
        self.assertTrue((root / '输出' / gaps.FILE).is_file())
        (root / '资料' / '口径.md').unlink()                           # 没有这一节也不出错
        new._note(root, {'方法': '类比法'}, {'项目名称': '测试'}, chk, None, None, table)

    def test_附件册还是骨架_主报告不报条数错误(self):
        root = _design('t15_adopt')
        f45 = next(f for f in tools.body_files(root) if f.startswith('04_5'))
        p = root / '正文' / f45
        text = tools.read(p)
        for n, a in (('5', '5'), ('34', '30'), ('24', '20')):
            text = text.replace('| 2【待填】 | 【待填】 | 【待填】 | 【待填】 |', '| %s | %s | %s | 0 |' % (n, a, int(n) - int(a)), 1)
        tools.write(p, text)
        errs = [m for m in tools.check(root)['按文件'].get(f45, {}).get('错误', []) if '附件册' in m]
        self.assertEqual(errs, [])
        hints = ' '.join(tools.check(root)['按文件'].get('附件册', {}).get('提示', []))
        self.assertIn('没写处理方式', hints)

    def test_附件册格式坏了_主报告照查(self):
        root = _design('t15_bad')
        f = tools.body_files(root / '附件册')[2]
        p = root / '附件册' / '正文' / f
        tools.write(p, tools.read(p) + '\n<!-- 没有结束符\n')
        c = tools.check(root)
        self.assertFalse(c.get('格式错误'))
        self.assertFalse(any(f in k for k in c['按文件']))                # 附件册的文件不算主报告的
        self.assertIn('附件册读不了', ' '.join(c['按文件'].get('附件册', {}).get('提示', [])))

    def test_引附件册不存在的表报错(self):
        root = _design('t15_ref')
        f1 = tools.body_files(root)[0]
        p = root / '正文' / f1
        tools.write(p, tools.read(p).replace('## 1.1 任务来源及目的\n', '## 1.1 任务来源及目的\n\n见附件册表1.1-1、附件册表9.9-1。\n', 1))
        errs = tools.check(root)['按文件'][f1]['错误']
        self.assertTrue(any('附件册表9.9-1不存在' in m for m in errs), errs)
        self.assertFalse(any('表1.1-1' in m for m in errs), errs)

    def test_附件册检查不要总平面图(self):
        todo = ' '.join(x for v in tools.check(_design('t15_fig') / '附件册')['按文件'].values() for x in v.get('待办', []))
        self.assertIn('区域位置图', todo)
        self.assertNotIn('总平面布置图', todo)

    def test_资料欠缺_本公司填的不找建设单位(self):
        g = gaps.collect(_design('t15_gap'))
        self.assertFalse(any(x['缺什么'] in gaps.OWN_KEYS for x in g['要补']))
        self.assertIn('资质证书编号', g['本公司填'])
        self.assertTrue(any(x['缺什么'] == '建设单位' for x in g['要补']))
        self.assertEqual(gaps.collect(BASE).get('本公司填'), [])

    def test_改稿规则_按适用类型写到哪(self):
        self.assertEqual(revise.rule_file(doctype.DESIGN), config.STD / '设计专篇' / '检查规则.md')
        self.assertEqual(revise.rule_file(doctype.PRE), revise.COMMON())
        self.assertEqual(revise.rule_file('全部'), revise.COMMON())
        self.assertEqual(revise._of_kind([BASE], doctype.DESIGN), [])
        self.assertEqual(revise._of_kind([BASE], doctype.PRE), [BASE])

    def test_改稿只适用专篇_规则进专篇检查规则_预评价不受影响(self):
        dsg = _design('t15_rv_d')
        pre = project('t15_rv_p')
        common_before = tools.read(revise.COMMON())
        dec = {'范围': '通用', '适用类型': doctype.DESIGN, '标题': '专篇不写某词', '规矩': '专篇不写“专篇测试词”', '例外': '',
               '取代': [], '规则': [{'节': '禁用', '词': '专篇测试词'}], '要点卡': [], '改法': '删掉', '关键词': [],
               '涉及章': [], '要核事实': False}
        FAKE.reset(('定口径', lambda **k: dec))
        with mock.patch.object(revise, 'targets', lambda p, kind='全部': [dsg] if kind == doctype.DESIGN else [dsg, pre]):
            revise.run(jobs.Job('改稿', '改稿', {}, None), '专篇不写专篇测试词', '全部', publish=False)
        self.assertEqual(tools.read(revise.COMMON()), common_before)
        self.assertIn('专篇测试词', tools.read(config.STD / '设计专篇' / '检查规则.md'))
        p = ch07(pre)
        tools.write(p, tools.read(p) + '\n专篇测试词。\n')
        self.assertFalse(any('专篇测试词' in m for m in tools.check(pre)['按文件'][p.name]['错误']))

    def test_审稿_网页的附NN章键选中附件册(self):
        root = _design('t15_rvsel')
        FAKE.reset(('审 ', lambda **k: {'意见': []}))
        out = review.run(jobs.Job('审稿', '审稿', {}, root), root, modify=False, chapters='附01,附04')
        self.assertEqual(out['审了'], 2)
        self.assertTrue(all(lab.startswith('审 附件册/') for lab in FAKE.labels() if lab.startswith('审 ')))

    def test_承接预评价的校验(self):
        root = TMP / 't15_pre_new'
        self.assertEqual(new.check_pre(BASE, root), '')
        self.assertIn('不是预评价', new.check_pre(_design('t15_pre_d'), root))
        self.assertIn('里面', new.check_pre(BASE, BASE / '专篇'))
        self.assertIn('没有正文', new.check_pre(TMP / '没有', root))
        job = jobs.Job('新报告', '新报告', {'文件夹': str(root), '类型': doctype.DESIGN, '预评价': ''}, root)
        with self.assertRaisesRegex(RuntimeError, '要给承接的预评价'):
            new.job_design(job)

    def test_命令行_接着做按已建项目定类型_专篇要求登记(self):
        root = _design('t15_cli')
        with mock.patch.object(cli, '_job', side_effect=SystemExit(9)):
            with self.assertRaises(SystemExit) as cm:
                cli.main(['新报告', str(root), '--接着做'])       # 不再报“预评价报告要写 --方法”
            self.assertEqual(cm.exception.code, 9)
        with self.assertRaises(SystemExit), mock.patch('sys.stderr'), mock.patch.object(tools, 'registered', lambda: []):
            cli.main(['新报告', str(TMP / 't15_cli2'), '--类型', '设计专篇', '--预评价', str(BASE)])
        self.assertIn('附件册/02', cli._parser().format_help())

    def test_只写附件册的文件(self):
        root = _design('t15_only', pre=BASE)
        seen = []
        with mock.patch.object(new, 'write_all', lambda job, r, m, s, only=None: seen.append((Path(r).name, only)) or {}), \
                mock.patch.object(new, '_copy_pre', lambda *a: []), mock.patch.object(new, '_take_extra', lambda *a: None), \
                mock.patch.object(new.F, 'build_extra', lambda *a: {}), mock.patch.object(tools, 'source_texts', lambda r: [('a', 'b')]), \
                mock.patch.object(tools, 'register_add', lambda *a: True):
            tools.write(root / '项目信息.md', tools.read(root / '项目信息.md').replace('- 项目名称：【待填】', '- 项目名称：甲'))
            tools.write(root / '资料' / '事实库.md', '# 事实库\n')
            new.job_design(jobs.Job('新报告', '新报告', {'文件夹': str(root), '类型': doctype.DESIGN, '只写': ['附件册/06', '04_5']}, root))
        self.assertEqual(seen, [(root.name, ['04_5']), ('附件册', ['06'])])

    def test_口径表章号换成专篇的节(self):
        kj = '# 口径\n\n## 可研没有写的\n\n- 化粪池检测：第4章、第8章\n- 氧含量检测仪器：第6章\n- 其他：影响防护\n-\n'
        out = new._design_gaps(kj)
        self.assertIn('## 设计文件没有写的', out)
        self.assertIn('- 化粪池检测：第3章、第4.3.7节', out)
        self.assertIn('- 氧含量检测仪器：第4.4节', out)
        self.assertIn('- 其他：影响防护', out)
        self.assertNotIn('\n-\n', out)

    def test_写作材料_预评价项目信息和单节_附件册给主报告全文(self):
        pre = TMP / 't15_pre_src'
        shutil.copytree(BASE, pre)
        root = _design('t15_docs', pre=pre)
        names = lambda docs: [re.search(r'名称="([^"]+)"', d).group(1) for d in docs]  # noqa: E731
        d01 = names(common.pre_docs(root, '01_概述.md'))
        self.assertIn('承接的预评价报告 项目信息', d01)
        self.assertTrue(any(x.startswith('承接的预评价报告 正文/11') for x in d01), d01)
        self.assertEqual(doctype.annex_main_full('06_采纳明细.md', tools.body_files(root)),
                         [f for f in tools.body_files(root) if f.startswith('04_5')])
        deps41 = doctype.deps_of(doctype.DESIGN, next(f for f in tools.body_files(root) if f.startswith('04_1')), tools.body_files(root))
        for k in ('04_2', '04_3', '04_4'):    # 表4.1-1“详见”列要引它们的表号项号，所以 04_1 排在后面
            self.assertTrue(any(f.startswith(k) for f in deps41), deps41)

    def test_写作任务不用计数占位符(self):
        self.assertNotIn('{{计数:表4.3-1}}', new.DESIGN_TASK)
        self.assertIn('{{行数:表4.3-1}}', new.DESIGN_TASK)
        self.assertIn('第1.3节、第3.1.5节按骨架各写一句', common.ROLE_DESIGN)
        self.assertNotIn('最高优先', common.system(doctype.DESIGN))

    def test_写作提示带篇幅预算和新写法(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / '项目信息.md').write_text('- 报告类型：设计专篇\n', encoding='utf-8')
            (root / '流水线设置.json').write_text('{"篇幅档": "B"}', encoding='utf-8')
            line = new.design_budget_line(root, doctype.DESIGN, '04_2_防护设施设计.md')
            self.assertIn('目标约 4400 字', line)          # 5000×0.88
            self.assertIn('最多不超过 5280 字', line)       # ×1.2
            self.assertIn('B 档', line)
            task = new.DESIGN_TASK % ('04_2_防护设施设计.md', '', new.DESIGN_KIND[doctype.DESIGN], line, '', '')
            self.assertIn('目标约 4400 字', task)
            self.assertNotIn('承接预评价条目', task)
            self.assertIn('附件册附件6', task)
            self.assertIn('附件册表1.1-1', new.DESIGN_KIND[doctype.DESIGN])
            # 预评价项目、算不出预算的文件不写篇幅行
            self.assertEqual(new.design_budget_line(root, doctype.DESIGN, 'x.md'), '')
        self.assertIn('<!-- 公式 编号=', new.DESIGN_KIND[doctype.ANNEX])
        self.assertIn('采购技术协议应满足', common.ROLE_DESIGN)
        self.assertIn('接触限值', common.ROLE_DESIGN)

    def test_设计专篇审稿对R10_R11照报(self):
        txt = ''.join(review.HOW_DESIGN.values())
        self.assertIn('R10', txt)
        self.assertIn('R11', txt)
        self.assertNotIn('只是“可以写得更好”的不报', txt)
        self.assertNotIn('预期结论都是“符合”', txt)


class T99_真文件没动(unittest.TestCase):
    def test_项目登记和定稿项目正文没动(self):
        self.assertTrue(_snap, '测试开头没拍到快照')
        self.assertEqual(_snapshot(), _snap)
        self.assertEqual(OUTSIDE, [])
