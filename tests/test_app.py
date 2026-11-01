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
from app.flows import common, distill, facts, new, review, revise, supplement  # noqa: E402

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
            self.calls.append({'标签': label, 'schema': kw.get('schema'), '内容': content})
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
        self.assertEqual([x for x in FAKE.labels() if not x.startswith('清零')], ['审 ' + f.name])
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


class T99_真文件没动(unittest.TestCase):
    def test_项目登记和定稿项目正文没动(self):
        self.assertTrue(_snap, '测试开头没拍到快照')
        self.assertEqual(_snapshot(), _snap)
        self.assertEqual(OUTSIDE, [])
