# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Clicky Windows.

Cribbed from JaySmith502/clicky-win/clicky-py/clicky.spec (verified
via gh api 2026-05-04). Differences vs JaySmith's spec:
  - Entry point is ``app.py`` at repo root (not ``clicky/__main__.py``).
  - Qt binding is PyQt6, not PySide6 — replaced ``collect_data_files``
    target + all hidden-import names.
  - Dropped ``qasync`` (we don't use async-Qt bridge).
  - Added ``anthropic``, ``openai``, ``cartesia``, ``assemblyai``
    explicit hidden imports for SDK dependencies.
  - Output bundle named ``Clicky`` (so ``dist/Clicky/Clicky.exe``).

Build:
    py -3.13 -m PyInstaller clicky.spec --noconfirm

Output: ``dist/Clicky/`` containing ``Clicky.exe`` plus all bundled
DLLs/Python stdlib/site-packages. Inno Setup wraps this folder into
``Clicky-Windows-Setup.exe`` (see ``installer/clicky.iss``).

Build tooling installed via pip:
    pip install pyinstaller>=6.20

Inno Setup (separate install — not a Python dep):
    https://jrsoftware.org/isdl.php  (free, ~3MB)
"""
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs


# Qt 6 plugins required at runtime — the platform shim DLL (windows.dll
# under plugins/platforms/) is what makes PyQt6 actually render on
# Windows. Without it, the app crashes at QApplication construction.
pyqt6_data = collect_data_files(
    "PyQt6",
    includes=[
        "Qt6/plugins/platforms/**",
        "Qt6/plugins/imageformats/**",
        "Qt6/plugins/multimedia/**",
        "Qt6/plugins/styles/**",
    ],
)
pyqt6_libs = collect_dynamic_libs("PyQt6")


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=pyqt6_libs,
    datas=pyqt6_data + [
        # Tray icon — referenced by tray.py at runtime via Path-relative
        # lookup. Without this entry, the .ico is missing from the
        # bundle and the tray icon shows blank.
        ("assets/clicky_tray.ico", "assets"),
    ],
    hiddenimports=[
        # Qt 6 sub-modules — PyInstaller's hook misses some by default.
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtMultimedia",
        # Audio I/O
        "sounddevice",
        "numpy",
        # Hotkey + mouse — pynput's platform-specific shims
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        # Screen capture
        "mss.windows",
        # SDK deps — explicit so PyInstaller doesn't miss them
        "anthropic",
        "openai",
        "cartesia",
        "elevenlabs",  # Sprint 4 — opt-in alternative TTS
        "assemblyai",
        # HTTP / networking deps used transitively by the SDKs
        "websockets",
        "httpx",
        "httpx._transports.default",
        # Image processing
        "PIL",
        "PIL.Image",
        # Keyring — Windows Credential Manager backend is loaded
        # dynamically via entry_points; PyInstaller's hook can miss it.
        "keyring",
        "keyring.backends",
        "keyring.backends.Windows",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "pytest",
        "pytest_mock",
        # Other Qt bindings — including any of these by accident bloats
        # the bundle and can cause runtime symbol clashes.
        "PySide6",
        "PyQt5",
        "PySide2",
        # Heavy ML / scientific stack pulled in transitively (likely via
        # optional deps in some package's deep dep graph) but NEVER used
        # by Clicky's runtime — we route vision via the Anthropic HTTP
        # SDK, audio via streaming HTTP/WebSocket, and screen capture via
        # mss. No tensors, no JIT, no dataframes. First build was 1.1GB;
        # excluding these drops it ~60% to ~440MB.
        "torch",          # 315MB — PyTorch
        "torchvision",
        "torchaudio",
        "llvmlite",       # 102MB — LLVM bindings (numba transitive)
        "numba",          # JIT — not used
        "pyarrow",        # 76MB — Apache Arrow
        "av",             # 65MB — PyAV / FFmpeg bindings
        "scipy",          # 53MB — scientific computing
        "onnxruntime",    # 32MB — ONNX inference
        "pandas",         # 17MB — dataframes
        # Dev / interactive tooling — never used at runtime
        "IPython",
        "ipykernel",
        "jedi",
        "parso",
        "jupyter",
        "jupyter_client",
        "notebook",
        "matplotlib",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Clicky",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed app — no console window flash on launch
    icon="assets/clicky_tray.ico",  # embedded as Windows resource in
                                    # the EXE — used by taskbar,
                                    # Alt-Tab, Start Menu shortcut,
                                    # Apps & features uninstall list.
                                    # Multi-res .ico (16/32/48/64/128/256)
                                    # so Windows picks native size for
                                    # each surface (no blur).
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Clicky",
)
