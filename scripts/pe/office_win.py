"""Windows：用本机的 WPS 或 Microsoft Word 导出 PDF、把 .doc 等旧格式另存成 .docx。

经 PowerShell 调 COM 自动化，不装第三方包。先试 WPS（KWps.Application），再试 Word（Word.Application）；
环境变量 PE_OFFICE 可以指定，如 PE_OFFICE=Word.Application。只在 Windows 上用，Mac 走 wps.py 的 WPS 命令行。
"""
import os
import subprocess
import tempfile
from pathlib import Path

PROGIDS = ('KWps.Application', 'Word.Application')
PDF, DOCX = 17, 12          # wdExportFormatPDF（也是 wdFormatPDF）；wdFormatXMLDocument

# 只用 ASCII 写，免得 PowerShell 按本机代码页读错。用户已经开着 Word 时 COM 可能接到同一个进程，
# 所以只在里面没有别的文档时才退出。
SCRIPT = r'''
param([string]$Src, [string]$Dst, [int]$Kind, [string]$Progs)
$ErrorActionPreference = 'Stop'
$app = $null; $used = ''
foreach ($p in $Progs.Split(',')) {
  try { $app = New-Object -ComObject $p; $used = $p; break } catch { }
}
if ($app -eq $null) { [Console]::Out.WriteLine('NOAPP'); exit 3 }
$code = 0
try {
  try { $app.Visible = $false } catch { }
  try { $app.DisplayAlerts = 0 } catch { }
  $doc = $app.Documents.Open($Src, $false, $true)
  try {
    if ($Kind -eq 17) {
      try { $doc.ExportAsFixedFormat($Dst, 17) } catch { try { $doc.SaveAs2($Dst, 17) } catch { $doc.SaveAs($Dst, 17) } }
    } else {
      try { $doc.SaveAs2($Dst, $Kind) } catch { $doc.SaveAs($Dst, $Kind) }
    }
  } finally {
    $doc.Close($false)
  }
  [Console]::Out.WriteLine('OK ' + $used)
} catch {
  [Console]::Out.WriteLine('ERR ' + $_.Exception.Message)
  $code = 2
} finally {
  try { if ($app.Documents.Count -eq 0) { $app.Quit() } } catch { }
  try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) } catch { }
}
exit $code
'''


class OfficeError(RuntimeError):
    pass


def _decode(b):
    return b.decode('mbcs' if os.name == 'nt' else 'utf-8', 'replace')


def _run(src, dst, kind, timeout):
    src, dst = Path(src).resolve(), Path(dst).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    progs = os.environ.get('PE_OFFICE') or ','.join(PROGIDS)
    with tempfile.TemporaryDirectory(prefix='pe_office_') as d:
        ps1 = Path(d) / 'office.ps1'
        ps1.write_text(SCRIPT, encoding='utf-8-sig')
        cmd = ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(ps1),
               '-Src', str(src), '-Dst', str(dst), '-Kind', str(kind), '-Progs', progs]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise OfficeError('WPS／Word 导出超时（%d 分钟）：%s' % (timeout // 60, src.name))
        except FileNotFoundError:
            raise OfficeError('这台电脑上找不到 PowerShell，没法调 WPS／Word 导出')
    out = (_decode(r.stdout) + _decode(r.stderr)).strip()
    if 'NOAPP' in out:
        raise OfficeError('这台电脑上没找到 WPS 或 Microsoft Word（装好任意一个再出稿）')
    if r.returncode or not dst.is_file():
        raise OfficeError('WPS／Word 导出失败（退出码 %s）：%s' % (r.returncode, out[-400:]))
    return out.split()[-1] if out else ''


def export_pdf(src, dst, timeout=900):
    """Word 文件导出 PDF，返回用的是哪个程序（ProgID）。"""
    used = _run(src, dst, PDF, timeout)
    if Path(dst).stat().st_size < 1000:
        raise OfficeError('导出的 PDF 不到 1 KB：%s' % dst)
    return used


def to_docx(src, dst, timeout=300):
    """.doc、.rtf、.wps 等另存成 .docx（资料转文字用）。"""
    return _run(src, dst, DOCX, timeout)
