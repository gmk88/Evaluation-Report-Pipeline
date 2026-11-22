#!/bin/bash
# 登记评价报告流水线（用户 2026-10-31 同意）：
#   ① ~/.claude/skills/report-pipeline 链接到本文件夹（全局 skill）
#   ② ~/.claude/settings.json 的 permissions.additionalDirectories 加上本文件夹（Claude 可读目录）
# 只加不删，可重复运行；改 settings.json 前先备份。
F="/Users/a1234/Desktop/评价报告流水线"
mkdir -p ~/.claude/skills
if [ -e ~/.claude/skills/report-pipeline ]; then
  echo "① skill 早已登记"
else
  ln -s "$F" ~/.claude/skills/report-pipeline && echo "① skill 已登记"
fi
[ -f ~/.claude/settings.json ] && cp -p ~/.claude/settings.json ~/.claude/settings.json.登记前备份
python3 - "$F" <<'EOF'
import json, os, stat, sys
F = sys.argv[1]
p = os.path.expanduser('~/.claude/settings.json')
d = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
mode = stat.S_IMODE(os.stat(p).st_mode) if os.path.exists(p) else 0o600
dirs = d.setdefault('permissions', {}).setdefault('additionalDirectories', [])
if F in dirs:
    print('② 可读目录早已登记')
else:
    dirs.append(F)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)
        fh.write('\n')
    os.chmod(tmp, mode)
    os.replace(tmp, p)
    print('② 可读目录已登记')
EOF
echo "完成。以后新开的对话自动生效。这个窗口可以关掉。"
