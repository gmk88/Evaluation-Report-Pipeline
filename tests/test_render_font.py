"""Font substitution should not override an installed Chinese font."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pe import render  # noqa: E402


class FontProfileTests(unittest.TestCase):
    def test_installed_fangsong_keeps_libreoffice_native_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'sample.docx'
            source.touch()

            def fake_convert(command, **kwargs):
                import pymupdf
                output = Path(command[command.index('--outdir') + 1]) / 'sample.pdf'
                with pymupdf.open() as pdf:
                    pdf.new_page()
                    pdf.save(output)

            with patch.object(render, 'has_fangsong', return_value=True), \
                    patch.object(render, '_profile') as profile, \
                    patch.object(render.subprocess, 'run', side_effect=fake_convert):
                output, note = render.to_pdf(source, Path(tmp) / 'output')

            profile.assert_not_called()
            self.assertTrue(output.is_file())
            self.assertIn('使用本机仿宋字体', note)


if __name__ == '__main__':
    unittest.main()
