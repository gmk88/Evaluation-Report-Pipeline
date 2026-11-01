"""各流程的入口：任务类别 → 函数(job)。按需导入，某个流程出问题不影响别的。"""
import importlib
import time

KINDS = {'新报告': ('new', 'job_new'), '审稿': ('review', 'job_review'), '改稿': ('revise', 'job_revise'),
         '出稿': ('publish', 'job_publish'), '事实库': ('facts', 'job_facts'), '提炼标准': ('distill', 'job_distill'),
         '自测': (None, None)}


def selftest(job):
    """不调模型的假任务，给网页和排队测试用。参数：秒（默认 3）、失败（为真时最后报错）。"""
    n = int(job.params.get('秒', 3))
    job.stage('自测')
    for i in range(n):
        job.check_cancel()
        s = job.step('第%d步' % (i + 1))
        s.update(状态='进行', 开始=time.time())
        time.sleep(1)
        s.update(状态='完成')
        job.info('第%d步完成' % (i + 1))
    if job.params.get('失败'):
        raise RuntimeError('按要求失败')
    return {'步数': n}


def get(kind):
    if kind == '自测':
        return selftest
    if kind not in KINDS:
        raise KeyError('没有这个任务：%s' % kind)
    mod, fn = KINDS[kind]
    return getattr(importlib.import_module('.' + mod, __name__), fn)
