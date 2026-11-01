"""出稿：检查为 0 → 生成 Word → 导出 PDF 两轮、回填目录页码、逐页核对 → 发布到 输出/（scripts/report.py 出稿）。"""
import datetime
import re
import time
from pathlib import Path

from .. import tools


def publish(job, root):
    s = job.step('出稿：Word → PDF 两轮导出 → 逐页核对 → 发布', '出稿')
    s.update(状态='进行', 开始=time.time())
    rc, out = tools.report('出稿', root, timeout=3600)
    pages = re.search(r'共 (\d+) 页', out)
    ok = rc == 0 and '核对通过' in out
    for ln in out.split('\n'):
        if ln.strip() and re.search(r'通过|页|错误|出错|失败|发布|字体|插图|→', ln):
            job.info(ln.strip()[:200])
    res = {'通过': ok, '页数': int(pages.group(1)) if pages else None}
    if not ok:
        res['问题'] = '\n'.join(out.strip().split('\n')[-12:])
    s.update(状态='完成' if ok else '失败', 说明=('共 %s 页' % res['页数']) if ok else '没通过')
    return res


def note(root, title, lines):
    """在 输出/交稿说明.md 末尾追加一节。"""
    p = Path(root) / '输出' / '交稿说明.md'
    old = tools.read(p) if p.exists() else '# 交稿说明\n'
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
    body = '\n'.join(['', '## %s（%s）' % (title, stamp), ''] + ['- ' + x for x in lines if x])
    tools.write(p, old.rstrip('\n') + '\n' + body + '\n')
    return p


def job_publish(job):
    root = job.project
    c = tools.check(root)
    if c['错误']:
        raise RuntimeError('检查有错误 %d 条，不出稿（先审稿或改稿把错误清掉）' % c['错误'])
    res = publish(job, root)
    if res['通过']:
        note(root, '出稿', ['PDF 共 %s 页，目录页码逐页核对通过' % res['页数'], '检查：错误 0、提示 %d、待办 %d' % (c['提示'], c['待办'])])
    return res
