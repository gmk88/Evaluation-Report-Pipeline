"""程序设置：中转地址、密钥、模型、并发数。

设置存在本机用户目录（Mac：~/.report-pipeline/，Windows：%LOCALAPPDATA%\\report-pipeline\\），
不放进流水线文件夹：文件夹要拷给同事，密钥不能跟着走。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # 流水线文件夹
SCRIPTS = ROOT / 'scripts'
REPORT = SCRIPTS / 'report.py'
STD = ROOT / '标准'
REGISTER = ROOT / '项目登记.md'
IS_WIN = sys.platform == 'win32'
IS_MAC = sys.platform == 'darwin'


def _data_dir():
    if IS_WIN:
        d = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'report-pipeline'
    else:
        d = Path.home() / '.report-pipeline'
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA = _data_dir()
CONFIG = DATA / 'config.json'
DEFAULTS = {
    '协议': 'anthropic',  # anthropic＝Anthropic Messages；openai＝OpenAI Chat Completions（DeepSeek、CodeBuddy 等）
    '模型': 'claude-opus-5-5',
    '并发': 4,            # 同时调用模型的个数
    '缓存': False,        # 提示缓存；本机用的中转读不到缓存，开了反而多算写入费
    '兜底': True,         # 模型拒答时由服务端换模型重做（fallbacks: default）
    '走系统代理': False,  # 调模型走不走这台电脑的系统代理（代理软件）；默认直连，代理线路断了不受影响
    '思考摘要': True,     # 模型思考时照常发回摘要，连接不长时间没动静（中转空着一百来秒会掐线）；中转不认这个参数时关掉
    '项目位置': str(Path.home() / 'Desktop' / '评价报告项目'),
}


def load():
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
    return cfg


def save(changes):
    cfg = load()
    for k, v in changes.items():
        if k == '密钥' and not v:
            continue                      # 设置页没填密钥＝不改
        cfg[k] = v
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    # 存着密钥：临时文件一建出来就只许自己读写，写好再换上（原先先按默认权限写、再改权限，中间别的用户能读到）
    tmp = CONFIG.with_name(CONFIG.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps(cfg, ensure_ascii=False, indent=1))
    try:
        os.chmod(tmp, 0o600)      # 临时文件原来就在、权限宽的，也收紧
    except OSError:
        pass
    os.replace(tmp, CONFIG)
    return cfg


def _claude_settings():
    p = Path.home() / '.claude' / 'settings.json'
    try:
        return json.loads(p.read_text(encoding='utf-8')).get('env', {})
    except (OSError, ValueError):
        return {}


def credentials(cfg=None):
    """返回 (地址, 密钥, 来源)。顺序：本程序设置 → 环境变量 → 本机 Claude Code 的 settings.json。
    OpenAI 兼容协议也认 OPENAI_API_KEY / OPENAI_BASE_URL，没设再用 ANTHROPIC_* 那套。"""
    cfg = cfg or load()
    if cfg.get('密钥'):
        return cfg.get('地址') or None, cfg['密钥'], '本程序设置'
    openai = cfg.get('协议') == 'openai'
    if openai:
        key = os.environ.get('OPENAI_API_KEY')
        if key:
            return cfg.get('地址') or os.environ.get('OPENAI_BASE_URL'), key, '环境变量'
    key = os.environ.get('ANTHROPIC_AUTH_TOKEN') or os.environ.get('ANTHROPIC_API_KEY')
    if key:
        return cfg.get('地址') or os.environ.get('ANTHROPIC_BASE_URL'), key, '环境变量'
    env = _claude_settings()
    key = env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY')
    if key:
        return cfg.get('地址') or env.get('ANTHROPIC_BASE_URL'), key, '本机 Claude Code 设置'
    return cfg.get('地址') or None, None, '没有设置'


def public(cfg=None):
    """给网页看的设置：密钥只说有没有，不回传。"""
    cfg = dict(cfg or load())
    base, key, src = credentials(cfg)
    cfg.pop('密钥', None)
    cfg.update(地址生效=base or '官方接口', 密钥来源=src, 有密钥=bool(key), 系统='Windows' if IS_WIN else ('Mac' if IS_MAC else sys.platform))
    return cfg
