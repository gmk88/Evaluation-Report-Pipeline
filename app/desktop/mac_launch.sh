#!/bin/bash
# 双击桌面上的“预评价编制工作台”时，小程序跑这个（Mac）：不开命令窗口，在后台起网页服务、打开浏览器。
# 已有服务在跑时只打开那个网页。运行环境还没准备好（第一次用）或者哪一步出错，就改用命令窗口
# 跑“启动流水线.command”：那边一步一步显示进度，出错时写明第几步、为什么。
# 起服务的做法和“启动流水线.command”第 8 步一样，改一边要看看另一边。
# 最后输出一行告诉小程序结果：pid=<号>（服务是这次起的）、running（已经在跑，打开了网页）、terminal（转给了命令窗口）。
# 测试用：设 RP_NO_TERMINAL=1 不真开命令窗口；设 BROWSER=/usr/bin/true 不真开浏览器。

ROOT="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)"
cd "$ROOT" 2>/dev/null && [ -d app ] || { echo "找不到流水线文件夹（${ROOT}）。" >&2; exit 2; }

# 从桌面图标打开时，环境变量是系统给的最少一套：补上 Homebrew 的位置（转 PDF 要用里面的工具）和中文环境
PATH="/usr/bin:/bin:/usr/sbin:/sbin"
[ -x /usr/libexec/path_helper ] && eval "$(/usr/libexec/path_helper -s)"
for d in /usr/local/bin /opt/homebrew/bin "$HOME/homebrew/bin"; do
  [ -d "$d" ] && case ":$PATH:" in *":$d:"*) ;; *) PATH="$d:$PATH" ;; esac
done
export PATH
[ -n "$LANG" ] || export LANG=zh_CN.UTF-8

DATA="$HOME/.report-pipeline"
PY="$DATA/venv/bin/python"
CHECK='import anthropic, lxml, docx, pymupdf, PIL'
mkdir -p "$DATA" 2>/dev/null
note() { printf '%s 桌面图标：%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$DATA/server.log" 2>/dev/null; }

# 交给命令窗口：运行环境没准备好，或者后台起不来
handoff() {
  note "$1，改用命令窗口启动"
  if [ -z "$RP_NO_TERMINAL" ] && ! open -a Terminal "$ROOT/启动流水线.command"; then
    echo "$1，命令窗口也打不开。请到流水线文件夹里双击“启动流水线.command”。" >&2
    exit 1
  fi
  echo terminal
  exit 0
}

"$PY" -c "$CHECK" >/dev/null 2>&1 || handoff "运行环境还没准备好"
"$PY" -c 'import app.server' >/dev/null 2>&1 || handoff "程序载入失败"

# 服务另开一个会话在后台运行。已经有服务在跑时，新起的这个只打开那个网页就退出。
PID="$("$PY" -c 'import subprocess, sys
log = open(sys.argv[1], "ab")
p = subprocess.Popen([sys.executable, "-m", "app.server", "--open"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
print(p.pid)' "$DATA/server.log" 2>/dev/null)"
[ -n "$PID" ] || handoff "网页服务没有起来"
for _ in $(seq 1 40); do
  sleep 0.5
  if grep -q "\"pid\": $PID[,}]" "$DATA/server.json" 2>/dev/null; then
    echo "pid=$PID"
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    # 进程已经退出：已有服务在跑时就是这样（它打开那个网页后就退出了），确认一下
    if "$PY" -m app.server --check >/dev/null 2>&1; then
      echo running
      exit 0
    fi
    handoff "网页服务没有起来"
  fi
done
handoff "等了 20 秒，网页服务还没起来"
