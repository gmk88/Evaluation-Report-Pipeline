#!/bin/bash
# 双击启动评价报告流水线（Mac）。第一次会准备运行环境（要几分钟），以后几秒就开。
# 运行环境放在 ~/.report-pipeline/venv，不放进流水线文件夹（文件夹要拷给同事）。
# 平时双击桌面上的“预评价编制工作台”就行，它不开这个窗口；第一次用（要准备运行环境）或者出了错，它才转到这里。
# 以前桌面上放的是指向这个文件的替身（符号链接），从替身打开时先解开链接，找到真正的流水线文件夹。

pause_exit() { read -r -p "按回车键关闭这个窗口…" _; exit 1; }

SELF="${BASH_SOURCE[0]:-$0}"
while [ -L "$SELF" ]; do
  LINK_DIR="$(cd -P "$(dirname "$SELF")" && pwd)"
  SELF="$(readlink "$SELF")"
  case "$SELF" in /*) ;; *) SELF="$LINK_DIR/$SELF" ;; esac
done
HERE="$(cd -P "$(dirname "$SELF")" 2>/dev/null && pwd)"
SELF="$HERE/$(basename "$SELF")"
cd "$HERE" 2>/dev/null && [ -d app ] || { echo "找不到流水线文件夹（${HERE}）。"; pause_exit; }

DATA="$HOME/.report-pipeline"
VENV="$DATA/venv"
PY="$VENV/bin/python"
PKGS="anthropic==2.3.0 lxml==6.1.2 python-docx==1.2.0 pymupdf==1.28.2 pillow==12.3.0"
MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"
CHECK='import anthropic, lxml, docx, pymupdf, PIL'

fail() {
  echo ""
  echo "【没有启动】$1"
  [ -n "$2" ] && echo "$2"
  echo ""
  pause_exit
}

# 在桌面放“预评价编制工作台”小程序：有自己的图标，名字不带后缀，双击在后台打开网页、不开这个窗口。
# 做法在 app/desktop/make_mac_app.sh：第一次建；流水线文件夹搬了地方就跟着重建；用户自己删了就不再建；
# 以前放的“启动流水线.command”替身顺手删掉。出错也不影响启动。
make_shortcut() {
  /bin/bash "$HERE/app/desktop/make_mac_app.sh" "$@"
  return 0
}

# 测试用：只建桌面图标，别的都不做（再加 --force：删过也重新建）
if [ "$1" = "--shortcut-only" ]; then
  shift
  make_shortcut "$@"
  exit 0
fi

mkdir -p "$DATA" || fail "第 1 步：建不了设置文件夹 ${DATA}。"
make_shortcut      # 放在准备环境之前：第一次准备环境失败时，桌面上也已经有图标，修好网络后双击它重试

if ! "$PY" -c "$CHECK" >/dev/null 2>&1; then
  echo "第一次使用（或运行环境不完整），正在准备运行环境，要几分钟，请不要关这个窗口……"
  if ! "$PY" -c 'import sys' >/dev/null 2>&1; then
    # 没有环境，或者环境坏了（比如电脑上的 Python 升级过）：重新建
    SYS_PY=""
    for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 "$HOME/homebrew/bin/python3" python3; do
      if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
        SYS_PY="$c"
        break
      fi
    done
    [ -n "$SYS_PY" ] || fail "第 2 步：这台 Mac 上没有能用的 Python 3（要 3.10 或更新的版本）。" \
      "请到 https://www.python.org/downloads/ 下载安装 Python，装好后再双击这个图标。"
    echo "用 $SYS_PY 建运行环境……"
    "$SYS_PY" -m venv --clear "$VENV" || fail "第 3 步：建运行环境失败（${VENV}）。" \
      "可以把这个窗口截图发给管理员。"
  fi
  if ! "$PY" -m pip --version >/dev/null 2>&1; then
    "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || fail "第 4 步：运行环境里没有安装工具 pip，也装不上。" \
      "可以把这个窗口截图发给管理员。"
  fi
  echo "正在安装需要的组件（先试清华镜像）……"
  if ! "$PY" -m pip install --disable-pip-version-check --timeout 60 -i "$MIRROR" $PKGS; then
    echo ""
    echo "清华镜像没装成，改用官方源再试一次……"
    "$PY" -m pip install --disable-pip-version-check --timeout 60 $PKGS || fail "第 5 步：安装组件失败。" \
      "请检查这台电脑能不能上网，再双击这个图标重试。还不行就把这个窗口截图发给管理员。"
  fi
  "$PY" -c "$CHECK" || fail "第 6 步：组件装好了，但是用不了。" "可以把这个窗口截图发给管理员。"
  echo "运行环境准备好了。"
fi

# 先在这个窗口里试一下能不能载入程序，出错时能看到原因
"$PY" -c 'import app.server' || fail "第 7 步：流水线程序载入失败。" "可以把这个窗口截图发给管理员。"

echo "正在启动……"
# 服务另开一个会话在后台运行：关掉这个窗口时终端不会问“要不要终止进程”，关掉浏览器服务也照样跑，任务不中断。
# 服务自己写 server.log；已经有服务在跑时，新起的这个只打开那个网页就退出。
PID="$("$PY" -c 'import subprocess, sys
log = open(sys.argv[1], "ab")
p = subprocess.Popen([sys.executable, "-m", "app.server", "--open"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
print(p.pid)' "$DATA/server.log")"
[ -n "$PID" ] || fail "第 8 步：网页服务没有起来。最近的记录：" "$(tail -n 15 "$DATA/server.log" 2>/dev/null)"
OK=""
for _ in $(seq 1 40); do
  sleep 0.5
  if grep -q "\"pid\": $PID[,}]" "$DATA/server.json" 2>/dev/null; then
    OK=1
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    # 进程已经退出：已有服务在跑时就是这样（它打开那个网页后就退出了），确认一下
    if "$PY" -m app.server --check >/dev/null 2>&1; then
      OK=1
      break
    fi
    fail "第 8 步：网页服务没有起来。最近的记录：" "$(tail -n 15 "$DATA/server.log" 2>/dev/null)"
  fi
done
[ -n "$OK" ] || fail "第 8 步：等了 20 秒，网页服务还没起来。最近的记录：" "$(tail -n 15 "$DATA/server.log" 2>/dev/null)"

echo ""
echo "网页已打开，这个窗口可以关掉。"
echo "（浏览器关掉后，再双击桌面上的“预评价编制工作台”就能回到网页；服务一直在后台运行。）"
