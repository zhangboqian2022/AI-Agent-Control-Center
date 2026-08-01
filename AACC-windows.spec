# -*- mode: python ; coding: utf-8 -*-
import os
import sys

ROOT = os.path.abspath(os.getcwd())
PYWIN32_SYSTEM32 = os.path.join(
    sys.prefix,
    'Lib',
    'site-packages',
    'pywin32_system32',
)
PYWINTYPES_DLL_NAME = (
    f'pywintypes{sys.version_info.major}{sys.version_info.minor}.dll'
)

a = Analysis(
    [os.path.join(ROOT, 'src', 'aacc', '__main__.py')],
    pathex=[os.path.join(ROOT, 'src')],
    binaries=[(os.path.join(PYWIN32_SYSTEM32, PYWINTYPES_DLL_NAME), '.')],
    datas=[(os.path.join(ROOT, 'src', 'aacc', 'styles.qss'), 'aacc')],
    hiddenimports=[
        'aacc.adapters',
        'aacc.opencode_edge_cdp',
        'aacc.opencode_edge_session',
        'aacc.windows_broker',
        'websocket',
        'win32api',
        'win32con',
        'win32event',
        'win32security',
        'ntsecuritycon',
        'pywintypes',
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
    disable_windowed_traceback=True,
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
