# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — SoundCloud PlayBot backend (one-folder)."""
import os, site

# Find the Lib/site-packages entry (not the Python root)
sp = next((p for p in site.getsitepackages() if 'site-packages' in p), site.getsitepackages()[-1])

# ── Native binaries ───────────────────────────────────────────────────────────
extra_binaries = []
curl_libs = os.path.join(sp, '~url_cffi.libs')
if os.path.isdir(curl_libs):
    for f in os.listdir(curl_libs):
        if f.endswith('.dll'):
            extra_binaries.append((os.path.join(curl_libs, f), '.'))

# ── Data files ────────────────────────────────────────────────────────────────
extra_datas = [
    (os.path.join(sp, 'curl_cffi'), 'curl_cffi'),
]
faker_pkg = os.path.join(sp, 'faker')
if os.path.isdir(faker_pkg):
    extra_datas.append((faker_pkg, 'faker'))

a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=extra_binaries,
    datas=extra_datas,
    hiddenimports=[
        # FastAPI / Uvicorn
        'fastapi', 'fastapi.middleware', 'fastapi.middleware.cors', 'fastapi.staticfiles',
        'uvicorn', 'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan', 'uvicorn.lifespan.on',
        'starlette', 'starlette.responses', 'starlette.routing',
        'starlette.middleware', 'starlette.websockets',
        'anyio._backends._asyncio',
        # Pydantic
        'pydantic', 'pydantic_core',
        # curl_cffi
        'curl_cffi', 'curl_cffi.requests', 'curl_cffi.curl', 'curl_cffi.const',
        'curl_cffi.aio', 'curl_cffi._wrapper', '_cffi_backend', 'cffi',
        # Selenium / UC
        'selenium', 'selenium.webdriver', 'selenium.webdriver.common',
        'selenium.webdriver.chrome', 'undetected_chromedriver',
        # Other
        'psutil', 'faker', 'faker.providers', 'websockets',
        'websockets.legacy', 'websockets.legacy.server',
        'winreg', 'twocaptcha',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'pandas', 'PIL', 'IPython', 'notebook', 'pytest'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='server',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='server',
)
