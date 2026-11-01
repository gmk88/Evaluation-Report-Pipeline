"""桌面小程序“预评价编制工作台”自测（只在 Mac 上跑）：建小程序、换掉旧替身、不动别人的同名东西、
运行环境没准备好时转给命令窗口、后台起服务。不碰真的桌面和设置文件夹，不开浏览器、不开命令窗口。

在流水线文件夹里运行：~/.report-pipeline/venv/bin/python -m unittest tests/test_desktop.py -v
"""
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / 'app' / 'desktop'
NAME = '预评价编制工作台'
ID = 'local.report-pipeline.workbench'


@unittest.skipUnless(sys.platform == 'darwin', '桌面小程序只有 Mac 有')
class DesktopAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='rp_desktop_test_')).resolve()
        self.desk = self.tmp / '桌面'
        self.home = self.tmp / 'home'
        self.desk.mkdir()
        self.home.mkdir()
        self.app = self.desk / (NAME + '.app')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def sh(self, script, *args, home=False):
        env = dict(os.environ, RP_DESKTOP=str(self.desk), RP_NO_TERMINAL='1', BROWSER='/usr/bin/true')
        if home:
            env['HOME'] = str(self.home)       # 设置文件夹也换到临时文件夹
        return subprocess.run(['/bin/bash', str(script), *args], env=env, capture_output=True, text=True,
                              timeout=120)

    def test_build_and_rebuild(self):
        r = self.sh(D / 'make_mac_app.sh')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        info = plistlib.loads((self.app / 'Contents' / 'Info.plist').read_bytes())
        self.assertEqual((info['CFBundleIdentifier'], info['CFBundleName']), (ID, NAME))
        self.assertIs(info['LSUIElement'], True)
        res = self.app / 'Contents' / 'Resources'
        self.assertEqual((res / 'applet.icns').read_bytes(), (D / 'icon.icns').read_bytes())
        self.assertEqual((res / 'pipeline_path').read_text(encoding='utf-8'), str(ROOT))
        v = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(self.app)],
                           capture_output=True, text=True)
        self.assertEqual(v.returncode, 0, v.stderr)
        # 再跑一次：流水线文件夹没动，不重建
        before = (res / 'pipeline_path').stat().st_mtime_ns
        r = self.sh(D / 'make_mac_app.sh')
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ''))
        self.assertEqual((res / 'pipeline_path').stat().st_mtime_ns, before)
        # 流水线文件夹搬了地方（小程序里记的路径对不上）：从“启动流水线.command”进来也会重建
        (res / 'pipeline_path').write_text('/别处/评价报告流水线', encoding='utf-8')
        r = self.sh(ROOT / '启动流水线.command', '--shortcut-only')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((res / 'pipeline_path').read_text(encoding='utf-8'), str(ROOT))

    def test_old_symlink_replaced(self):
        old = self.desk / '启动流水线.command'
        old.symlink_to(ROOT / '启动流水线.command')
        r = self.sh(D / 'make_mac_app.sh')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.lexists(old))
        self.assertTrue(self.app.is_dir())

    def test_others_untouched(self):
        old = self.desk / '启动流水线.command'
        old.symlink_to(self.tmp / '别的流水线' / '启动流水线.command')    # 指向别处的同名替身
        (self.app / 'Contents').mkdir(parents=True)                       # 别人的同名小程序
        (self.app / '别人的.txt').write_text('x', encoding='utf-8')
        r = self.sh(D / 'make_mac_app.sh')
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertTrue(os.path.lexists(old))
        self.assertTrue((self.app / '别人的.txt').exists())

    def test_launch_hands_off_without_env(self):
        r = self.sh(D / 'mac_launch.sh', home=True)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, 'terminal'), r.stderr)
        log = (self.home / '.report-pipeline' / 'server.log').read_text(encoding='utf-8')
        self.assertIn('运行环境还没准备好', log)

    def test_launch_starts_server(self):
        if sys.prefix == sys.base_prefix:
            self.skipTest('要用运行环境里的 Python 跑：~/.report-pipeline/venv/bin/python')
        data = self.home / '.report-pipeline'
        data.mkdir()
        (data / 'venv').symlink_to(sys.prefix)
        r = self.sh(D / 'mac_launch.sh', home=True)
        out = r.stdout.strip()
        self.assertTrue(out.startswith('pid='), out + r.stderr)
        pid = int(out[4:])
        try:
            self.assertEqual(json.loads((data / 'server.json').read_text(encoding='utf-8'))['pid'], pid)
            # 再双击一次：服务已经在跑，只打开网页，不起第二个
            r = self.sh(D / 'mac_launch.sh', home=True)
            self.assertEqual(r.stdout.strip(), 'running', r.stderr)
        finally:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)


if __name__ == '__main__':
    unittest.main()
