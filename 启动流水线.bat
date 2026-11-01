@echo off
chcp 65001 >nul
setlocal
rem 双击启动评价报告流水线（Windows）。第一次会准备运行环境，要几分钟，以后几秒就开。
rem 运行环境放在 %LOCALAPPDATA%\report-pipeline\venv，不放进流水线文件夹，文件夹要拷给同事。
rem 这个文件要存成 UTF-8 无 BOM、CRLF 换行。提示文字中英文都写：个别电脑的命令窗口显示不了中文时还能看懂。

rem 先记下这个文件的完整路径，建桌面快捷方式要用（从桌面快捷方式打开时 %~dp0 也是流水线文件夹）
set "RP_SELF=%~f0"
cd /d "%~dp0" 2>nul
if errorlevel 1 pushd "%~dp0"
if errorlevel 1 (set "MSG=找不到流水线文件夹。 Pipeline folder not found." & goto fail)

set PYTHONUTF8=1
set "DATA=%LOCALAPPDATA%\report-pipeline"
set "VENV=%DATA%\venv"
set "PY=%VENV%\Scripts\python.exe"
set "PYW=%VENV%\Scripts\pythonw.exe"
set "PKGS=anthropic==2.3.0 lxml==6.1.2 python-docx==1.2.0 pymupdf==1.28.2 pillow==12.3.0"
set "MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple"
set "MSG="
set "MSG2="

rem 测试用：只建桌面快捷方式，别的都不做（测试时设 RP_DESKTOP 指到临时文件夹，不碰真的桌面）
if /i "%~1"=="--shortcut-only" (
  call :make_shortcut
  exit /b 0
)

if not exist "%DATA%" mkdir "%DATA%" >nul 2>&1
if not exist "%DATA%" (set "MSG=第 1 步：建不了设置文件夹 Step 1: cannot create %DATA%" & goto fail)
rem 放在准备环境之前：第一次准备环境失败时，桌面上也已经有图标，修好网络后双击它重试
call :make_shortcut

"%PY%" -c "import anthropic, lxml, docx, pymupdf, PIL" >nul 2>&1
if not errorlevel 1 goto ready

echo 第一次使用，或运行环境不完整，正在准备运行环境，要几分钟，请不要关这个窗口……
echo First run: setting up, this takes a few minutes. Please keep this window open.
"%PY%" -c "import sys" >nul 2>&1
if not errorlevel 1 goto have_venv

rem 没有环境，或者环境坏了（比如电脑上的 Python 卸载、升级过）：找 Python 重新建
call :find_python
if not defined SYS_PY call :install_python
if not defined SYS_PY (
  set "MSG=第 2 步：这台电脑上没有能用的 Python 3，自动安装也没有成功。 Step 2: Python 3 not found."
  set "MSG2=请到 https://www.python.org/downloads/ 下载安装 Python 3.13，安装时勾选 Add python.exe to PATH，装好后再双击这个图标。"
  goto fail
)
echo 用 %SYS_PY% 建运行环境…… Creating environment with %SYS_PY%
%SYS_PY% -m venv --clear "%VENV%"
if errorlevel 1 (set "MSG=第 3 步：建运行环境失败 Step 3: venv failed: %VENV%" & set "MSG2=可以把这个窗口截图发给管理员。" & goto fail)

:have_venv
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 "%PY%" -m ensurepip --upgrade >nul 2>&1
"%PY%" -m pip --version >nul 2>&1
if errorlevel 1 (set "MSG=第 4 步：运行环境里没有安装工具 pip，也装不上。 Step 4: pip missing." & set "MSG2=可以把这个窗口截图发给管理员。" & goto fail)
echo 正在安装需要的组件，先试清华镜像…… Installing packages (Tsinghua mirror first)
"%PY%" -m pip install --disable-pip-version-check --timeout 60 -i %MIRROR% %PKGS%
if not errorlevel 1 goto check_pkgs
echo.
echo 清华镜像没装成，改用官方源再试一次…… Mirror failed, trying the official index
"%PY%" -m pip install --disable-pip-version-check --timeout 60 %PKGS%
if errorlevel 1 (set "MSG=第 5 步：安装组件失败。 Step 5: package install failed." & set "MSG2=请检查这台电脑能不能上网，再双击这个图标重试。还不行就把这个窗口截图发给管理员。" & goto fail)

:check_pkgs
"%PY%" -c "import anthropic, lxml, docx, pymupdf, PIL"
if errorlevel 1 (set "MSG=第 6 步：组件装好了，但是用不了。 Step 6: packages installed but not usable." & set "MSG2=可以把这个窗口截图发给管理员。" & goto fail)
echo 运行环境准备好了。 Environment ready.

:ready
rem 先在这个窗口里试一下能不能载入程序，出错时能看到原因
"%PY%" -c "import app.server"
if errorlevel 1 (set "MSG=第 7 步：流水线程序载入失败。 Step 7: program failed to load." & set "MSG2=可以把这个窗口截图发给管理员。" & goto fail)
if not exist "%PYW%" (set "MSG=第 8 步：运行环境里缺少 pythonw.exe。 Step 8: pythonw.exe missing." & set "MSG2=可以把这个窗口截图发给管理员。" & goto fail)

echo 正在启动…… Starting...
rem pythonw 没有窗口，在后台运行：关掉这个窗口、关掉浏览器，服务都照样跑，任务不中断。服务自己写 server.log
start "" "%PYW%" -m app.server --open
set /a N=0
:wait
ping -n 2 127.0.0.1 >nul
"%PY%" -m app.server --check >nul 2>&1
if not errorlevel 1 goto started
set /a N+=1
if %N% lss 20 goto wait
echo.
echo 【没有启动】第 8 步：等了 20 秒，网页服务还没起来。最近的记录：
echo [NOT STARTED] Step 8: the web service did not start within 20 seconds. Recent log:
"%PY%" -c "import sys, pathlib; p = pathlib.Path(sys.argv[1]); print(''.join(p.read_text(encoding='utf-8', errors='replace').splitlines(True)[-15:]) if p.exists() else '没有记录 (no log)')" "%DATA%\server.log"
echo.
echo 可以把这个窗口截图发给管理员。 Please send a screenshot of this window to the administrator.
pause
exit /b 1

:started
echo.
echo 网页已打开，这个窗口可以关掉，10 秒后也会自己关。
echo The web page is open. This window closes by itself in 10 seconds.
echo 浏览器关掉后，再双击桌面上的“预评价编制工作台”就能回到网页；服务一直在后台运行。
ping -n 11 127.0.0.1 >nul
exit /b 0

:fail
echo.
echo 【没有启动】 [NOT STARTED] %MSG%
if defined MSG2 echo %MSG2%
echo.
pause
exit /b 1

rem ---- 第一次运行时在桌面放“预评价编制工作台.lnk”快捷方式：目标是这个文件，起始位置是流水线文件夹，图标是 app\desktop\icon.ico ----
rem 桌面上已有同名的就不动。建过一次就在设置文件夹里记一笔，以后不再查（免得每次启动都多等 PowerShell 一秒）。
rem 桌面位置用 [Environment]::GetFolderPath('Desktop')，桌面被 OneDrive 接管时也对。
rem 测试时设 RP_DESKTOP 指到临时文件夹再加 --shortcut-only 只跑这一段：不碰真的桌面，也不记那一笔。
rem 路径都从环境变量传给 PowerShell，不拼进命令；快捷方式的名字用字符编码拼出“预评价编制工作台”，不受命令窗口编码影响。
rem 存好后读回来核对目标；路径里的中文存坏了（个别英文版 Windows 会这样）就删掉，不留一个打不开的图标。
rem PowerShell 退出码：2 新建了，3 已有同名，0 找不到桌面，1 出错。出错不影响启动。
:make_shortcut
set "RP_MARK=%DATA%\desktop_app.done"
if defined RP_DESKTOP set "RP_MARK="
if defined RP_MARK if exist "%RP_MARK%" exit /b 0
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; try { $d = $env:RP_DESKTOP; if (-not $d) { $d = [Environment]::GetFolderPath('Desktop') }; if (-not $d -or -not (Test-Path -LiteralPath $d -PathType Container)) { exit 0 }; $n = (-join [char[]](0x9884, 0x8BC4, 0x4EF7, 0x7F16, 0x5236, 0x5DE5, 0x4F5C, 0x53F0)) + '.lnk'; $p = Join-Path $d $n; if (Test-Path -LiteralPath $p) { exit 3 }; $w = New-Object -ComObject WScript.Shell; $s = $w.CreateShortcut($p); $s.TargetPath = $env:RP_SELF; $s.WorkingDirectory = [IO.Path]::GetDirectoryName($env:RP_SELF); $ico = Join-Path $s.WorkingDirectory 'app\desktop\icon.ico'; if (Test-Path -LiteralPath $ico) { $s.IconLocation = $ico + ',0' }; $s.Save(); if ($w.CreateShortcut($p).TargetPath -ne $env:RP_SELF) { Remove-Item -LiteralPath $p -Force; exit 1 }; exit 2 } catch { exit 1 }" >nul 2>&1
set "RP_RC=%errorlevel%"
if "%RP_RC%"=="2" echo 已在桌面放了“预评价编制工作台”图标，以后双击它就能打开。 A desktop shortcut was added.
if "%RP_RC%"=="2" goto shortcut_mark
if "%RP_RC%"=="3" goto shortcut_mark
exit /b 0
:shortcut_mark
if not defined RP_MARK exit /b 0
if not exist "%DATA%" mkdir "%DATA%" >nul 2>&1
type nul > "%RP_MARK%" 2>nul
exit /b 0

rem ---- 找 Python：先 py 启动器，再 python，再看常见的安装位置。要真能跑才算（应用商店的 python 假命令跑不起来）----
:find_python
set "SYS_PY="
for %%V in (3.13 3.14 3.12 3.11 3.10 3) do (
  if not defined SYS_PY (
    py -%%V -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "SYS_PY=py -%%V"
  )
)
if defined SYS_PY exit /b 0
python -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "SYS_PY=python"
if defined SYS_PY exit /b 0
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe") do (
  if not defined SYS_PY if exist "%%~P" (
    "%%~P" -c "import sys, venv; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "SYS_PY="%%~P""
  )
)
exit /b 0

rem ---- 没有 Python：用 winget 给当前用户装 Python 3.13（不用管理员权限）----
:install_python
where winget >nul 2>&1
if errorlevel 1 exit /b 0
echo 这台电脑上没有找到能用的 Python，正在用 winget 自动安装 Python 3.13，要几分钟……
echo Python not found. Installing Python 3.13 with winget, this takes a few minutes...
winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
call :find_python
exit /b 0
