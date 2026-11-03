# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：产出 dist/Banner/Banner.exe（目录版）。

用法（仓库根目录）：
    python -m PyInstaller Banner.spec --noconfirm --clean

精确分辨率横幅图 banner_{宽}x{高}.png 是本机产物，仓库里不一定有：
存在才打包，缺失时程序运行时会按屏幕分辨率自动生成。

v1.5 起打包控制中心（gui.py），它依赖 CustomTkinter：需要一并收集其
主题 json / 字体等资源；可选的根目录 theme.json 若存在也会一起打包。
"""

import os

from PyInstaller.utils.hooks import collect_data_files

_datas = [
    (name, '.')
    for name in ('banner.png', 'banner_2560x1440.png', 'tray.ico', 'theme.json')
    if os.path.exists(name)
]
# CustomTkinter 运行所需的主题 json、字体等数据文件（v1.5 控制中心）
_datas += collect_data_files('customtkinter')

a = Analysis(
    ['banner.pyw'],
    pathex=['.'],
    binaries=[],
    datas=_datas,
    hiddenimports=['make_banner', 'gui'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Banner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon='tray.ico',
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Banner',
)
