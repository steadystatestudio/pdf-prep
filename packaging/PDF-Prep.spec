from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / 'scripts'))
from linux_dependencies import omit_system_libraries
a = Analysis(
    [str(root / 'scripts/launcher.py')], pathex=[str(root / 'src')],
    binaries=collect_dynamic_libs('pypdfium2_raw'),
    datas=collect_data_files('pypdfium2') + collect_data_files('pypdfium2_raw'),
    hiddenimports=['pypdfium2_raw', 'pdf_prep.smoke'],
    hookspath=[str(root / 'packaging/hooks')],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.QtWebEngineQuick', 'PySide6.QtQml', 'PySide6.QtQuick',
              'PySide6.QtPdf', 'PySide6.QtNetwork', 'tkinter', 'pytest', 'reportlab'],
    noarchive=False,
)
if sys.platform == 'linux':
    omit_system_libraries(a, root / 'build/LINUX-SYSTEM-DEPENDENCIES.json')
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='PDF-Prep',
          console=False, debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='PDF-Prep')
