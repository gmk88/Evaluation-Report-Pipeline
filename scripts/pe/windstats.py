"""项目附近国家气象站的风向频率：读 data/风向站点.json（scripts/dev/build_wind_stations.py 从国家气象科学数据中心
逐时数据统计出来的各站各年 16 方位＋静风次数），按项目坐标找最近的站，算近三年全年、夏季风向频率，写成正文的风频表
（表头同 windrose.find_table 认的那样，画风频玫瑰图直接用）。"""
import json
import math
import re
from pathlib import Path

from .windrose import DIRS

DATA = Path(__file__).resolve().parent / 'data' / '风向站点.json'
RADIUS = 100          # km：最近的站超过这么远就不拿来代替，报缺资料
FULL = 0.8            # 一年有效记录不到 80% 的不算一年
SITE = ('场址', '厂址', '站址', '项目', '中心', '建设地点')
NOT_SITE = ('气象站', '气象台', '测风塔', '雷达', '参证站', '观测站')
DMS = r'(\d{2,3})\s*[°º]\s*(?:(\d{1,2}(?:\.\d+)?)\s*[\'′’‘]\s*)?(?:(\d{1,2}(?:\.\d+)?)\s*["″”“]+)?'


def load(path=DATA):
    if not Path(path).is_file():
        raise FileNotFoundError('没有气象站风向数据（%s）' % path)
    return json.loads(Path(path).read_text(encoding='utf-8'))


def km(a, b):
    """两点（纬度, 经度）的大圆距离。"""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _deg(m, at=1):
    """度分秒 → 度；at 是“度”所在的组号。"""
    return float(m.group(at)) + float(m.group(at + 1) or 0) / 60 + float(m.group(at + 2) or 0) / 3600


def site_coords(text):
    """资料或正文里写的场址（厂址）坐标 (纬度, 经度)；只认前面写了场址、中心之类、没写测风塔和气象站的那一处。"""
    t = re.sub(r'\s*\n\s*', '', text)
    for m in re.finditer(r'(东经|北纬)\s*' + DMS, t):
        before = t[max(0, m.start() - 40):m.start()]
        if (not any(w in before for w in SITE) or not re.search(r'坐标|位于', before[-12:])
                or any(w in before[-25:] for w in NOT_SITE)):        # “场址中心距14161#（东经…”是测风塔的
            continue
        other = '北纬' if m.group(1) == '东经' else '东经'
        n = re.search(other + r'\s*' + DMS, t[m.end():m.end() + 40])
        if n:
            lon, lat = (_deg(m, 2), _deg(n)) if m.group(1) == '东经' else (_deg(n), _deg(m, 2))
            if 15 < lat < 55 and 70 < lon < 140:
                return lat, lon
    return None


def nearest(lat, lon, data=None, radius=RADIUS):
    """最近的、有三个整年记录的站：(区站号, 站, 距离 km, 用哪几年)。范围内没有返回 None。"""
    data = data or load()
    best = None
    for sid, st in data['站'].items():
        if not st.get('坐标'):
            continue
        years = sorted(y for y, v in st['年'].items() if sum(v['全年']) >= FULL * (366 if int(y) % 4 == 0 else 365) * 24)
        if len(years) < 3:
            continue
        d = km((lat, lon), st['坐标'][:2])
        if d <= radius and (best is None or d < best[2]):
            best = (sid, st, d, years[-3:])
    return best


def _pct(counts):
    """次数 → 百分数（两位小数），合计正好 100.00（最大余数法）。"""
    total = sum(counts) or 1
    raw = [c * 10000 / total for c in counts]
    out = [int(x) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)[:10000 - sum(out)]:
        out[i] += 1
    return [x / 100 for x in out]


def freq(st, years):
    """{'全年': [16 方位…, 静风], '夏季': [...]}，百分数。"""
    out = {}
    for k in ('全年', '夏季'):
        out[k] = _pct([sum(st['年'][y][k][i] for y in years) for i in range(17)])
    return out


def table_md(key, title, fr, note):
    """正文里的风频表（两栏并排，同200MW、新材料表2.1-3）。"""
    a, s = fr['全年'], fr['夏季']
    rows = ['%s  %s' % (key, title), '<!-- 表格 列宽=1300,1400,1450,1300,1400,1450 字号=21 对齐=中,中,中,中,中,中 -->',
            '| 风向 | 全年（%） | 夏季（%） | 风向 | 全年（%） | 夏季（%） |', '|---|---|---|---|---|---|']
    for i in range(8):
        rows.append('| %s | %.2f | %.2f | %s | %.2f | %.2f |' % (DIRS[i], a[i], s[i], DIRS[i + 8], a[i + 8], s[i + 8]))
    rows.append('| C（静风） | %.2f | %.2f | 合计 | 100.00 | 100.00 |' % (a[16], s[16]))
    return rows + ['', '<!-- 段落 字号=五号 -->', note]


def top(fr, k):
    """最多风向和频率：('SE', 15.5)；并列的用“、”连起来。"""
    v = fr[k][:16]
    m = max(v)
    return '、'.join(DIRS[i] for i in range(16) if v[i] == m), m
