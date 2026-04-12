"""Per-interaction debug logging for Clicky Windows.

Each PTT interaction gets its own folder under ~/.clicky-windows/debug/
containing a timestamped log file + the screenshot Claude saw.

Usage:
    session = DebugSession.start("EXCEL.EXE", "Book1 - Excel")
    session.log("PRESS received")
    session.save_screenshot(pil_image)
    session.log(f"Transcript: {text!r}")
    session.log(f"Coordinate: claude={coord} -> physical={phys}")
    session.close()
"""
from __future__ import annotations

import time
from pathlib import Path

from config import MEMORY_DIR


_DEBUG_DIR = Path(MEMORY_DIR).parent / "debug"


class DebugSession:
    """One debug session per PTT interaction."""

    def __init__(self, folder: Path, log_file):
        self._folder = folder
        self._log_file = log_file
        self._t0 = time.time()

    @classmethod
    def start(cls, app_name: str, window_title: str) -> DebugSession:
        ts = time.strftime("%Y-%m-%d_%H-%M-%S")
        safe_app = app_name.replace("/", "_").replace("\\", "_")
        folder = _DEBUG_DIR / f"{ts}_{safe_app}"
        folder.mkdir(parents=True, exist_ok=True)
        log_path = folder / "interaction.log"
        f = open(log_path, "w", encoding="utf-8")
        session = cls(folder, f)
        session.log(f"APP: {app_name}")
        session.log(f"WINDOW: {window_title}")
        return session

    def log(self, msg: str) -> None:
        elapsed_ms = (time.time() - self._t0) * 1000
        line = f"[+{elapsed_ms:.0f}ms] {msg}\n"
        try:
            self._log_file.write(line)
            self._log_file.flush()
        except Exception:
            pass

    def save_screenshot(
        self,
        pil_image,
        filename: str = "screenshot.jpg",
        coordinate: tuple[int, int] | None = None,
    ) -> None:
        """Save screenshot, optionally drawing a red marker at Claude's coordinate."""
        try:
            img = pil_image.copy()
            if coordinate:
                from PIL import ImageDraw
                draw = ImageDraw.Draw(img)
                x, y = coordinate
                r = 12
                draw.ellipse([(x - r, y - r), (x + r, y + r)], outline="red", width=3)
                draw.line([(x - r, y), (x + r, y)], fill="red", width=2)
                draw.line([(x, y - r), (x, y + r)], fill="red", width=2)
            path = self._folder / filename
            img.save(str(path), "JPEG", quality=85)
            self.log(f"Screenshot saved: {path}" + (f" (marker at {coordinate})" if coordinate else ""))
        except Exception as exc:
            self.log(f"Screenshot save FAILED: {exc}")

    def close(self) -> None:
        try:
            self._log_file.close()
        except Exception:
            pass
