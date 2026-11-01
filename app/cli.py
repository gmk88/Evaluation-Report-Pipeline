"""命令行：不开网页也能跑各流程（试跑、自测用）。任务记录和网页上的一样，存在 任务/<号>/，网页上也看得到。

在流水线文件夹里运行：python -m app.cli 命令 …
  测连接
  检查   项目文件夹 [--章 07 05]
  审稿   项目文件夹 [--章 07,05] [--只看] [--重点 文字] [--出稿]
  改稿   "用户原话" [--项目 文件夹 …（不写＝全部）] [--不出稿] [--日期 2026-11-02]
  新报告 新项目文件夹 --方法 风险评估法 [--资料 文件或文件夹 …] [--名称 …] [--简称 …] [--编号 …] [--日期 …]
         [--不审稿] [--不出稿] [--只写 07 …]
  出稿   项目文件夹
  事实库 项目文件夹
  补资料 项目文件夹 --资料 文件或文件夹 … [--说明 文字]   （只列变化，不改正文）
  按新资料改 项目文件夹 [--采用 1 3 …（不写＝全部）] [--出稿]
  提炼标准 [--只做 口径 各章 补漏]
  自测   [--秒 3] [--失败]
"""
import argparse
import json
import sys
import threading
from pathlib import Path

from . import flows, gaps, jobs, llm, tools


def _parser():
    ap = argparse.ArgumentParser(prog='python -m app.cli', description='评价报告流水线命令行')
    sp = ap.add_subparsers(dest='kind', required=True)
    sp.add_parser('测连接')
    p = sp.add_parser('检查')
    p.add_argument('项目')
    p.add_argument('--章', nargs='*', default=[])
    p = sp.add_parser('审稿')
    p.add_argument('项目')
    p.add_argument('--章', default='')
    p.add_argument('--只看', action='store_true')
    p.add_argument('--重点', default='')
    p.add_argument('--出稿', action='store_true')
    p = sp.add_parser('改稿')
    p.add_argument('意见')
    p.add_argument('--项目', nargs='*', default=[])
    p.add_argument('--不出稿', action='store_true')
    p.add_argument('--日期', default='')
    p = sp.add_parser('新报告')
    p.add_argument('文件夹')
    p.add_argument('--方法', required=True, choices=['类比法', '风险评估法'])
    p.add_argument('--资料', nargs='*', default=[])
    for k in ('--名称', '--简称', '--编号', '--日期'):
        p.add_argument(k, default='')
    p.add_argument('--不审稿', action='store_true')
    p.add_argument('--不出稿', action='store_true')
    p.add_argument('--只写', nargs='*', default=[])
    p.add_argument('--接着做', action='store_true', help='接着做上次没做完的（文件夹是上次建好的；做完的步骤不重做）')
    for k in ('出稿', '事实库', '配图', '资料欠缺'):
        sp.add_parser(k).add_argument('项目')
    p = sp.add_parser('补资料')
    p.add_argument('项目')
    p.add_argument('--资料', nargs='*', default=[])
    p.add_argument('--说明', default='')
    p = sp.add_parser('按新资料改')
    p.add_argument('项目')
    p.add_argument('--采用', nargs='*', type=int, default=None)
    p.add_argument('--出稿', action='store_true')
    sp.add_parser('提炼标准').add_argument('--只做', nargs='*', default=[])
    p = sp.add_parser('自测')
    p.add_argument('--秒', type=int, default=3)
    p.add_argument('--失败', action='store_true')
    return ap


def _job(a):
    """返回 (类别, 标题, 参数, 项目)。"""
    k = a.kind
    if k == '审稿':
        return k, '审稿' + (' 第%s章' % a.章 if a.章 else ''), {'修改': not a.只看, '章': a.章, '重点': a.重点, '出稿': a.出稿}, a.项目
    if k == '改稿':
        p = {'意见': a.意见, '项目': a.项目 or '全部', '出稿': not a.不出稿, '日期': a.日期}
        if a.项目:      # 指定了项目的，开始前就锁上，正忙的项目当场报出来
            p['_锁'] = [str(Path(x).expanduser().resolve()) for x in a.项目 if Path(x).expanduser().is_dir()]
        return k, '改稿：' + a.意见[:20], p, None
    if k == '新报告':
        return k, '新报告', {'文件夹': a.文件夹, '方法': a.方法, '资料': a.资料, '名称': a.名称, '简称': a.简称, '编号': a.编号,
                          '日期': a.日期, '审稿': not a.不审稿, '出稿': not a.不出稿, '只写': a.只写, '接着做': a.接着做}, a.文件夹
    if k in ('出稿', '事实库', '配图'):
        return k, k, {}, a.项目
    if k == '补资料':
        return k, '补充资料', {'资料': [str(Path(x).expanduser().resolve()) for x in a.资料], '说明': a.说明}, a.项目
    if k == '按新资料改':
        return k, k, {'采用': a.采用, '出稿': a.出稿}, a.项目
    if k == '提炼标准':
        return k, k, {'只做': a.只做}, None
    return k, '自测', {'秒': a.秒, '失败': a.失败}, None


def main(argv=None):
    a = _parser().parse_args(argv)
    if a.kind == '测连接':
        ok, msg = llm.ping()
        print(msg)
        return 0 if ok else 1
    if a.kind == '检查':
        c = tools.check(a.项目, a.章)
        print(json.dumps({k: c[k] for k in ('错误', '提示', '待办', '按文件')}, ensure_ascii=False, indent=1)[:6000])
        return 1 if c['错误'] else 0
    if a.kind == '资料欠缺':
        d = gaps.write(a.项目)
        print(json.dumps({k: d[k] for k in ('要补', '补了更准', '可以不补')}, ensure_ascii=False, indent=1)[:6000])
        print('已写 %s' % (Path(a.项目) / '输出' / gaps.FILE))
        return 0
    kind, title, params, project = _job(a)
    params['_回显'] = True
    job = jobs.Job(kind, title, params, project)
    if project:
        job.project_name = tools.project_name(project)
    print('任务 %s：%s（记录在 %s）' % (job.id, title, job.dir), flush=True)
    t = threading.Thread(target=jobs.run, args=(job, flows.get(kind)), daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(0.5)
    except KeyboardInterrupt:
        job.cancelled = True
        print('正在取消……', flush=True)
        t.join()
    u = job.usage
    print('== %s：%s' % (job.state, job.error or '没有错误'))
    print('用量：调用 %d 次，输入 %d、输出 %d token，按官方价约 %.2f 美元' % (u['次数'], u['输入'], u['输出'], u['美元']))
    print('结果：' + json.dumps(job.result, ensure_ascii=False, indent=1, default=str)[:4000])
    return 0 if job.state == '完成' else 1


if __name__ == '__main__':
    sys.exit(main())
