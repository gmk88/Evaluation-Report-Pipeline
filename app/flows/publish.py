"""出稿：检查为 0 → 生成 Word → 导出 PDF 两轮、回填目录页码、逐页核对 → 发布到 输出/（scripts/report.py 出稿）。"""
import datetime
import re
import time
from pathlib import Path

from .. import gaps, tools
from ..tools import doctype as DT
from . import figures as FG


def _fresh_pdf(vol, since):
    """这一卷 输出/ 里 since 以后出的 PDF（没有返回 None）。"""
    d = Path(vol) / '输出'
    pdfs = [x for x in d.glob('*.pdf') if x.stat().st_mtime >= since] if d.is_dir() else []
    return max(pdfs, key=lambda x: x.stat().st_mtime) if pdfs else None


def _publish_annex(job, root, since):
    """设计专篇：主报告出稿后接着出附件册。scripts/report.py 出稿时已经接着出了的（附件册 输出/ 里有新 PDF）不再出。
    返回 (通过, 页数, 说明)；没有附件册返回 None。"""
    annex = DT.annex_root(root)
    if not annex:
        return None
    if _fresh_pdf(annex, since):
        return True, None, '附件册已随主报告出稿'
    FG.refresh_wind(job, annex)
    s = job.step('出稿：附件册 Word → PDF 两轮导出 → 逐页核对 → 发布', '出稿')
    s.update(状态='进行', 开始=time.time())
    rc, out = tools.report('出稿', annex, timeout=3600)
    ok = rc == 0 and '核对通过' in out
    pages = re.search(r'共 (\d+) 页', out)
    for ln in out.split('\n'):
        if ln.strip() and re.search(r'通过|页|错误|出错|失败|发布|字体|插图|→', ln):
            job.info('附件册：' + ln.strip()[:200])
    s.update(状态='完成' if ok else '失败', 说明=('共 %s 页' % pages.group(1)) if ok and pages else '没通过')
    return ok, int(pages.group(1)) if pages else None, '' if ok else '\n'.join(out.strip().split('\n')[-12:])


def publish(job, root):
    FG.refresh_wind(job, root)          # 风频表有了或改了：出稿前把风频玫瑰图画上、重画
    s = job.step('出稿：Word → PDF 两轮导出 → 逐页核对 → 发布', '出稿')
    since = time.time() - 1
    s.update(状态='进行', 开始=time.time())
    rc, out = tools.report('出稿', root, timeout=3600)
    pages = re.search(r'共 (\d+) 页', out)
    ok = rc == 0 and '核对通过' in out
    for ln in out.split('\n'):
        if ln.strip() and re.search(r'通过|页|错误|出错|失败|发布|字体|插图|→', ln):
            job.info(ln.strip()[:200])
    res = {'通过': ok, '页数': int(pages.group(1)) if pages else None}
    ann = _publish_annex(job, root, since) if ok else None
    if ann:
        res['附件册'] = {'通过': ann[0], '页数': ann[1], '说明': ann[2]}
        if not ann[0]:
            ok = res['通过'] = False
            out += '\n附件册没通过：\n' + ann[2]
    if ok:
        chk = tools.check(root)
        refresh_head(root, res['页数'], chk)
        res['资料欠缺'] = len(gaps.write(root, chk)['要补'])
    if not ok:
        res['问题'] = '\n'.join(out.strip().split('\n')[-12:])
    s.update(状态='完成' if ok else '失败', 说明=('共 %s 页' % res['页数']) if ok else '没通过')
    return res


def refresh_head(root, pages, chk):
    """出稿通过后，把 输出/交稿说明.md 开头“## 报告”里的 PDF、检查两行换成现在的（不然开头还写着没出 PDF、有错误）。"""
    p = Path(root) / '输出' / '交稿说明.md'
    if not p.exists():
        return
    text = tools.read(p)
    m = re.search(r'^## 报告\n.*?(?=^## |\Z)', text, re.M | re.S)
    if not m:
        return
    head = re.sub(r'^- PDF：.*$', '- PDF：共 %s 页，目录页码逐页核对通过' % pages, m.group(0), flags=re.M)
    head = re.sub(r'^- 检查：.*$', '- 检查：错误 %d、提示 %d、待办 %d' % (chk['错误'], chk['提示'], chk['待办']), head, flags=re.M)
    if head != m.group(0):
        tools.write(p, text[:m.start()] + head + text[m.end():])


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
    for vol in DT.volumes(root)[1:]:        # 设计专篇：附件册有错误也不出
        c2 = tools.check(vol)
        c = dict(c, **{k: c[k] + c2[k] for k in ('错误', '提示', '待办')})
    if c['错误']:
        raise RuntimeError('检查有错误 %d 条，不出稿（先审稿或改稿把错误清掉）' % c['错误'])
    res = publish(job, root)
    if res['通过']:
        note(root, '出稿', ['PDF 共 %s 页，目录页码逐页核对通过' % res['页数'], '检查：错误 0、提示 %d、待办 %d' % (c['提示'], c['待办']),
                          '还缺 %d 项资料，见同一文件夹的“资料欠缺清单”' % res['资料欠缺'] if res.get('资料欠缺') else ''])
    return res
