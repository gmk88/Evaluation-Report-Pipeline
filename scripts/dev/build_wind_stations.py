"""把国家气象科学数据中心《全球地面逐小时整合产品》里的中国站逐时风向风速，统计成各站各年的 16 方位＋静风次数，
写进 scripts/pe/data/风向站点.json（几百 KB，随软件带走；原始数据几个 GB，不带）。软件按项目坐标找最近的站用它。
统计口径同马沟 CMA 版风玫瑰图（WorkBuddy 风频玫瑰图_晋城阳城_CMA.py）：缺测（≥999000）不计，质控码只留 0、9；
风速≤0.2 m/s 为静风（只看风速：百色站静风时风向记成缺测，马沟脚本会把这些钟点扔掉）；16 方位按 (度＋11.25)/22.5 分；
夏季为 6、7、8 月。
站名：用 OpenStreetMap 反查站点所在县（区站在市区的取市名），去掉“县、市、旗”加“站”（去掉后只剩一个字的不去，如景县站）。
已有的站名不重查（--重查站名 全部重查）；空着、重名的记“名称待核”，软件不把这种站名写进报告。
用法：python3 scripts/dev/build_wind_stations.py 数据文件夹（含 china_2022.txt 等） [--不查站名] [--重查站名]"""
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'pe' / 'data' / '风向站点.json'
COLS = {27: (3, 12, 13, 21, 22), 25: (3, 11, 12, 19, 20)}   # 字段数 → (月, 风向, 风速, 风向质控, 风速质控)
CALM, SUMMER = 0.2, (6, 7, 8)


def parse(path, stations):
    year = int(re.search(r'(\d{4})', path.name).group(1))
    n = 0
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            p = line.rstrip('\n').replace('"', '').split('\t')
            cols = COLS.get(len(p))
            if not cols:
                continue
            cm, ci, cs, q1, q2 = cols
            try:
                wd, ws, month = float(p[ci]), float(p[cs]), int(p[cm])
            except ValueError:
                continue
            if ws >= 999000 or p[q2] not in ('0', '9'):
                continue
            if ws <= CALM:                    # 静风只看风速：有的站静风时风向记成缺测（百色站），不能当缺测扔掉
                k = 16
            elif wd >= 999000 or p[q1] not in ('0', '9'):
                continue
            else:
                k = int(((wd % 360) + 11.25) % 360 / 22.5)
            st = stations[p[1]]
            st['坐标'] = [float(p[6]), float(p[7]), float(p[8])]
            y = st['年'].setdefault(str(year), {'全年': [0] * 17, '夏季': [0] * 17})
            y['全年'][k] += 1
            if month in SUMMER:
                y['夏季'][k] += 1
            n += 1
    print('%s：%d 条有效记录' % (path.name, n), flush=True)


def _rev(lat, lon, zoom):
    url = ('https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat=%.4f&lon=%.4f&zoom=%d&accept-language=zh-CN'
           % (lat, lon, zoom))
    req = urllib.request.Request(url, headers={'User-Agent': 'report-pipeline/1.0 (internal occupational-health reports)'})
    with urllib.request.urlopen(req, timeout=30) as r:
        a = json.loads(r.read().decode('utf-8')).get('address', {})
    time.sleep(1.1)                                   # OSM 要求每秒不超过 1 次
    return a


def name_of(lat, lon):
    """“百色站”“阳城站”“景县站”：站在县里取县名，站在市辖区取市名（zoom 8 还是区的再往上查一级）。查不到返回 ''。"""
    a = _rev(lat, lon, 10)
    place = a.get('county') or a.get('city') or a.get('state_district') or ''
    for z in (8, 6):
        if not (place.endswith('区') and not place.endswith('地区')):
            break
        b = _rev(lat, lon, z)
        place = next((x for x in (b.get('city'), b.get('state_district')) if x and not x.endswith('区')), place)
    if place.endswith('区') and not place.endswith('地区'):
        return ''                                    # 只查到区名：不猜，留空待核
    stem = re.sub(r'(?:自治县|自治旗|市|县|旗)$', '', place)
    return (stem if len(stem) >= 2 else place) + '站' if place else ''


def mark(out):
    """站名空着或和别的站重名的，记“名称待核”；软件不把这种站名写进报告。"""
    seen = {}
    for v in out['站'].values():
        seen[v['名称']] = seen.get(v['名称'], 0) + 1
    for v in out['站'].values():
        v['名称待核'] = not v['名称'] or seen[v['名称']] > 1


def save(out):
    """先写临时文件再换名：软件同时在读也不会读到写了一半的。"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_name(OUT.name + '.tmp')
    tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(OUT)


def main(argv):
    if not argv:
        print(__doc__.strip().split('\n')[-1])
        return 2
    src, ask, again = Path(argv[0]).expanduser(), '--不查站名' not in argv, '--重查站名' in argv
    old = json.loads(OUT.read_text(encoding='utf-8')) if OUT.exists() else {}
    stations = defaultdict(lambda: {'坐标': None, '年': {}})
    for f in sorted(src.glob('china_*.txt')):
        parse(f, stations)
    out = {'说明': '国家气象科学数据中心《全球地面逐小时整合产品》中国站逐时风向风速统计（scripts/dev/build_wind_stations.py）',
           '静风': '风速≤%.1f m/s' % CALM, '夏季': '6～8 月', '方位': '16 方位（N 起顺时针）＋静风', '站': {}}
    for sid, st in sorted(stations.items()):
        out['站'][sid] = {'名称': old.get('站', {}).get(sid, {}).get('名称', ''), '坐标': st['坐标'], '年': st['年']}
    mark(out)
    save(out)
    todo = [s for s, v in out['站'].items() if again or not v['名称']] if ask else []
    for i, sid in enumerate(todo, 1):
        lat, lon, _ = out['站'][sid]['坐标']
        try:
            out['站'][sid]['名称'] = name_of(lat, lon) if 15 < lat < 55 and 70 < lon < 140 else ''
        except Exception as e:  # noqa: BLE001  网络断了：这个站先空着，下次再查
            print('  %s 站名没查到：%s' % (sid, e), flush=True)
        if i % 20 == 0 or i == len(todo):
            mark(out)
            save(out)
            print('  站名 %d/%d' % (i, len(todo)), flush=True)
    print('写好 %s：%d 个站' % (OUT, len(out['站'])))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
