# -*- mode: python ; coding: utf-8 -*-
import os

ROOT = os.path.abspath(os.getcwd())

a = Analysis(
    [os.path.join(ROOT, 'src', 'aacc', '__main__.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[],
    datas=[(os.path.join(ROOT, 'src', 'aacc', 'styles.qss'), 'aacc')],
    hiddenimports=[
        'aacc.adapters',
        'PySide6.QtWebView',
    ],
    hookspath=[os.path.join(ROOT, 'hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['mypy', 'pytest', 'Quartz'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AACC',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AACC',
)
