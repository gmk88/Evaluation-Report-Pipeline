#!/bin/bash
# 在桌面放一个正常的 Mac 小程序“预评价编制工作台”：有自己的图标，名字不带后缀，双击在后台打开流水线网页。
# 小程序本体是 mac_app.applescript，图标是 icon.icns，双击后跑 mac_launch.sh。
# “启动流水线.command”每次启动都调这个：第一次建；流水线文件夹搬了地方就跟着重建；用户自己删了就不再建。
# 以前放的“启动流水线.command”替身（指向本流水线的符号链接）顺手删掉，别的同名文件不动。
# 用法：/bin/bash app/desktop/make_mac_app.sh [--force]    --force：删过也重新建
# 测试时设 RP_DESKTOP 指到临时文件夹：不碰真的桌面，不记那一笔，也不登记到系统里。
# 退出码：0 建好了或不用动，3 桌面上有同名的别的东西（没动它），1 出错。

NAME="预评价编制工作台"
ID="local.report-pipeline.workbench"
SRC="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -P "$SRC/../.." && pwd)"
DATA="$HOME/.report-pipeline"
DESK="${RP_DESKTOP:-$HOME/Desktop}"
APP="$DESK/$NAME.app"
MARK="$DATA/desktop_app.done"
[ -n "$RP_DESKTOP" ] && MARK=""
FORCE=""
[ "$1" = "--force" ] && FORCE=1
PB=/usr/libexec/PlistBuddy

[ -d "$DESK" ] || exit 0

OLD="$DESK/启动流水线.command"
if [ -L "$OLD" ] && [ "$(readlink "$OLD")" = "$ROOT/启动流水线.command" ]; then
  rm -f "$OLD" && echo "桌面上原来的“启动流水线.command”替身已删掉，换成“${NAME}”。"
fi

if [ -e "$APP" ] || [ -L "$APP" ]; then
  if [ -L "$APP" ] || [ "$("$PB" -c 'Print :CFBundleIdentifier' "$APP/Contents/Info.plist" 2>/dev/null)" != "$ID" ]; then
    echo "桌面上已经有一个“${NAME}”，不是流水线建的，没有动它。"
    exit 3
  fi
  if [ -z "$FORCE" ] && [ "$(cat "$APP/Contents/Resources/pipeline_path" 2>/dev/null)" = "$ROOT" ]; then
    exit 0
  fi
elif [ -z "$FORCE" ] && [ -n "$MARK" ] && [ -e "$MARK" ]; then
  exit 0      # 建过，是用户自己删掉的：不再建
fi

# 先在临时文件夹里做好，再整个换到桌面上
BUILD="$(mktemp -d "${TMPDIR:-/tmp}/rp_app.XXXXXX")" || { echo "建桌面图标失败：建不了临时文件夹。"; exit 1; }
trap 'rm -rf "$BUILD"' EXIT
NEW="$BUILD/$NAME.app"
R="$NEW/Contents/Resources"
P="$NEW/Contents/Info.plist"
/usr/bin/osacompile -s -o "$NEW" "$SRC/mac_app.applescript" >/dev/null 2>&1 || { echo "建桌面图标失败：编译小程序出错。"; exit 1; }
cp "$SRC/icon.icns" "$R/applet.icns" && printf '%s' "$ROOT" > "$R/pipeline_path" ||
  { echo "建桌面图标失败：拷不进文件。"; exit 1; }
for kv in "CFBundleIdentifier string $ID" "CFBundleName string $NAME" "CFBundleDisplayName string $NAME" \
          "CFBundleShortVersionString string 1.0" "LSUIElement bool true"; do
  k="${kv%% *}" rest="${kv#* }"
  t="${rest%% *}" v="${rest#* }"
  "$PB" -c "Delete :$k" "$P" >/dev/null 2>&1
  "$PB" -c "Add :$k $t $v" "$P" >/dev/null 2>&1 || { echo "建桌面图标失败：写不进程序信息（${k}）。"; exit 1; }
done
# 改过里面的文件，重新签名（本机自签，不联网），不然系统认为程序被改坏了
/usr/bin/codesign --force --sign - "$NEW" >/dev/null 2>&1 || { echo "建桌面图标失败：签名出错。"; exit 1; }

if [ -e "$APP" ]; then
  mv "$APP" "$BUILD/旧.app" || { echo "建桌面图标失败：桌面上旧的“${NAME}”挪不动。"; exit 1; }
fi
if ! mv "$NEW" "$APP"; then
  [ -e "$BUILD/旧.app" ] && mv "$BUILD/旧.app" "$APP"
  echo "建桌面图标失败：放不到桌面上（${DESK}）。"
  exit 1
fi
if [ -z "$RP_DESKTOP" ]; then
  # 让访达马上认出新图标和名字
  LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  [ -x "$LSREG" ] && "$LSREG" -f "$APP" >/dev/null 2>&1
  touch "$APP"
fi
[ -n "$MARK" ] && mkdir -p "$DATA" 2>/dev/null && : > "$MARK" 2>/dev/null
echo "已在桌面放了“${NAME}”图标，以后双击它就能打开。"
exit 0
