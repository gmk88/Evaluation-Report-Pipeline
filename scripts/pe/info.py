"""项目信息.md 与 检查规则.md 的读写（白话格式，不用编程语法）。

项目信息.md：
    - 项目名称：……            （“键：值”，一行一项；封面名称可写多行，每行一行封面字）
    ## 评价人员                （表格：类别｜姓名｜职称｜专业｜证书号）
    ## 附件                    （“1. 名称”，一行一个，顺序即编号；可写“1. 名称（文件：附件/xx.pdf）”）
检查规则.md：
    ## 禁用 / ## 必备 / ## 提示 / ## 正则禁用   下面每行“- 文字”
"""
import re
from pathlib import Path

LIST_KEYS = ('封面名称',)
MAIN_KEYS = ('项目名称', '封面名称', '报告编号', '报告日期', '建设单位', '评价单位', '法定代表人')
PEOPLE_COLS = ('类别', '姓名', '职称', '专业', '证书号')


def _strip_comments(text):
    return re.sub(r'<!--.*?-->', '', text, flags=re.S)


def read_info(path):
    text = _strip_comments(Path(path).read_text(encoding='utf-8'))
    info = {'评价人员': [], '附件': []}
    section = None
    for raw in text.split('\n'):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^#{1,3}\s*(.+?)\s*$', line)
        if m:
            t = m.group(1)
            section = 'people' if '人员' in t else ('att' if '附件' in t else None)
            continue
        if section == 'people' and line.startswith('|'):
            cells = [c.strip() for c in line.strip('|').split('|')]
            if re.match(r'^:?-{2,}', cells[0]) or cells[0] in ('类别', '人员类别'):
                continue
            cells += [''] * (5 - len(cells))
            info['评价人员'].append(dict(zip(PEOPLE_COLS, cells[:5])))
            continue
        if section == 'att':
            m = re.match(r'^(?:\d+[.．、]|[-*])\s*(.+)$', line)
            if m:
                name = m.group(1).strip()
                f = re.search(r'[（(]文件[：:]\s*(.+?)[)）]\s*$', name)
                item = {'名称': name[:f.start()].strip() if f else name}
                if f:
                    item['文件'] = f.group(1).strip()
                info['附件'].append(item)
            continue
        m = re.match(r'^[-*]?\s*([^：:|]{1,20}?)\s*[：:]\s*(.*)$', line)
        if m:
            k, v = m.group(1).strip(), m.group(2).strip()
            if k in LIST_KEYS:
                info.setdefault(k, []).append(v)
            else:
                info[k] = v
    return info


def write_info(path, info):
    L = ['# 项目信息', '',
         '<!-- 封面、声明、人员表、页眉页脚、附件清单都从这里取。改完重新生成即可。',
         '     【待填】要换成实际内容；封面名称一行写一行封面字（可写多行）。 -->', '']
    for k in MAIN_KEYS:
        v = info.get(k) or '【待填】'
        for x in (v if isinstance(v, list) else [v]):
            L.append('- %s：%s' % (k, x))
    for k, v in info.items():
        if k not in MAIN_KEYS and not isinstance(v, (list, dict)):
            L.append('- %s：%s' % (k, v))
    L += ['', '## 评价人员', '', '<!-- 同一类别的人连续写，人员表会自动合并“人员类别”格 -->', '',
          '| 类别 | 姓名 | 职称 | 专业 | 证书号 |', '|---|---|---|---|---|']
    for p in info.get('评价人员', []) or [{}]:
        L.append('| ' + ' | '.join(p.get(c, '【待填】') or ' ' for c in PEOPLE_COLS) + ' |')
    L += ['', '## 附件', '', '<!-- 一行一个，顺序就是编号（附件1、附件2…）。可在末尾写（文件：附件/xx.pdf）便于合订 -->', '']
    for i, a in enumerate(info.get('附件', []), 1):
        L.append('%d. %s%s' % (i, a.get('名称', ''), '（文件：%s）' % a['文件'] if a.get('文件') else ''))
    Path(path).write_text('\n'.join(L) + '\n', encoding='utf-8')


def read_rules(path):
    rules = {'禁用': [], '必备': [], '提示': [], '正则禁用': [], '豁免': []}
    if not Path(path).exists():
        return rules
    text = _strip_comments(Path(path).read_text(encoding='utf-8'))
    cur = None
    for raw in text.split('\n'):
        line = raw.strip()
        m = re.match(r'^#{1,3}\s*(.+?)\s*$', line)
        if m:
            t = m.group(1)
            cur = next((k for k in ('豁免', '正则禁用', '禁用', '必备', '提示') if k in t), None)
            continue
        m = re.match(r'^[-*]\s+(.+)$', line)
        if m and cur:
            item = m.group(1).strip()
            if len(item) >= 2 and item[0] == item[-1] == '`':
                item = item[1:-1]
            rules[cur].append(item)
    return rules


def merge_common(rules, path):
    """把流水线的 标准/通用检查规则.md 并进项目规则。项目 检查规则.md 的“## 豁免”里列的项不并，
    写“全部通用规则”则一项都不并。返回并进来的项（检查时据此标“通用规则”）。"""
    skip = set(rules.get('豁免', []))
    added = set()
    if '全部通用规则' in skip or not Path(path).exists():
        return added
    common = read_rules(path)
    for k in ('禁用', '正则禁用', '提示'):
        for w in common.get(k, []):
            if w not in skip and w not in rules[k]:
                rules[k].append(w)
                added.add(w)
    return added
