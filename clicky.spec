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
import glob
import os

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)


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

# v0.4.1: collect_all the local-provider stack so the frozen EXE includes their
# native libs + data files, not just the Python modules. v0.4.0 listed these in
# hiddenimports but PyInstaller did not recurse into faster-whisper's own imports,
# so `av` (PyAV, the audio decoder) was missing and local STT crashed on launch.
_local_datas, _local_bins, _local_hidden = [], [], []
for _pkg in (
    "faster_whisper", "ctranslate2", "onnxruntime",
    "kokoro_onnx", "soundfile", "tokenizers",
    # Kokoro TTS grapheme->phoneme: espeakng_loader ships espeak-ng.dll +
    # ~15MB espeak-ng-data; phonemizer-fork drives it. Both resolve their
    # paths via __file__ so collect_all (in-package data) bundles them safely.
    "espeakng_loader", "phonemizer",
    # phonemizer-fork imports its `segments` backend at module load (even
    # though Kokoro only uses espeak), which drags in segments -> csvw ->
    # jsonschema. Each needs its bundled data or the frozen import crashes.
    # Verified end-to-end in a frozen test EXE (synth + transcribe). The
    # rfc3987_syntax/lark URI checker that jsonschema *optionally* pulls is
    # NOT needed and is excluded below so jsonschema skips it cleanly.
    "segments", "csvw", "language_tags",
    "jsonschema", "jsonschema_specifications", "referencing",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        _local_datas += _d
        _local_bins += _b
        _local_hidden += _h
    except Exception:
        pass  # not installed in this build env; skip

# av (PyAV) needs special handling: collect_all misclassifies its .pyd as datas
# (returns 0 binaries) and its ffmpeg DLLs live in a sibling `av.libs` dir
# (delvewheel layout). collect_submodules forces the .pyd to bundle as proper
# extensions; the av.libs DLLs go in as binaries. This combo was verified
# importable inside a frozen test EXE before shipping (v0.4.0 crashed without it).
_local_hidden += collect_submodules("av")
try:
    import av as _av
    _av_libs = os.path.dirname(_av.__file__) + ".libs"
    if os.path.isdir(_av_libs):
        _local_bins += [(_f, "av.libs") for _f in glob.glob(os.path.join(_av_libs, "*.dll"))]
except Exception:
    pass


a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=pyqt6_libs + _local_bins,
    datas=pyqt6_data + _local_datas + [
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
        # Local offline providers (opt-in) — faster-whisper STT + Kokoro TTS.
        # Lazy-imported at runtime; bundled so one installer carries both the
        # cloud (default) and local lanes. Model weights download on first use.
        "faster_whisper",
        "ctranslate2",
        "onnxruntime",
        "kokoro_onnx",
        "soundfile",
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
        *_local_hidden,  # v0.4.1: submodules pulled by collect_all for the local stack
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
        # av is NO LONGER excluded — faster-whisper (local STT) imports PyAV.
        "scipy",          # 53MB — scientific computing
        # onnxruntime is NO LONGER excluded — Kokoro local TTS requires it.
        "pandas",         # 17MB — dataframes
        # jsonschema's OPTIONAL URI-format checkers. Kokoro/phonemizer never
        # use them; excluding lets jsonschema skip them so we don't bundle
        # rfc3987_syntax's .lark grammar. Verified safe in a frozen test EXE.
        "rfc3987_syntax",
        "rfc3987",
        "lark",
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
