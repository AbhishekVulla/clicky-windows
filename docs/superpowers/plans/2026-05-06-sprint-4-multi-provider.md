# Sprint 4 — Multi-Provider Settings UX + Privacy Framing + ElevenLabs TTS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current 3-flat-required-keys settings dialog with a 3-category dropdown UX (LLM / STT / TTS), each with progressive-disclosure provider selection + key field, fix the existing HTML-rendering bug visible in the dialog, add a one-line privacy framing, and ship `ElevenLabsTTS` as the second TTS provider so the dropdown demonstrates provider switching with an audibly different voice.

**Architecture:** Each TTS provider is a subclass of `tts.TTS` ABC with the existing `speak`/`speak_sentence`/`stop` contract. `ElevenLabsTTS` mirrors `CartesiaSonicTTS` Option B prefetch+playback two-thread architecture verbatim, with three deliberate divergences: (a) `_generate_response` calls `client.text_to_speech.stream(...)` returning an iterator directly (true streaming, no body fetch), (b) `_play_response` converts each int16 PCM chunk to float32 inline, (c) `stop()` is 5-pronged because elevenlabs SDK exposes no `response.close()`. A new `tts.create_tts_client(provider, api_key)` factory routes by string. A new `config.resolve_setting(name, default)` helper extends the keyring-persistence pattern from `resolve_api_key` to non-secret config (`LLM_PROVIDER`, `STT_PROVIDER`, `TTS_PROVIDER`) so bundled-EXE startup doesn't silently fall back to env-only defaults (per Sprint 3.6 dotenv-trap lesson). Settings dialog restructures from a 3-tuple flat list to a `_PROVIDER_CATEGORIES` data model whose render+save+swap behaviors are driven by table data, not hardcoded fields.

**Tech Stack:** Python 3.13, PyQt6 (`QComboBox`, `QLineEdit`, `QPushButton`, `QFormLayout`, `QDesktopServices`+`QUrl` for URL opening), `keyring` (Windows Credential Manager DPAPI), `elevenlabs>=2.0` (NEW — for `ElevenLabsTTS`), `numpy` (existing — int16→float32 buffer conversion), `sounddevice` (existing — `OutputStream` per-provider sample rate), `pytest`+`pytest-mock` (DI mock test fixtures).

**Source of truth for the strategic narrative:** `C:\Users\Abhis\.claude\plans\streamed-tumbling-sunbeam.md` → `# Sprint 4 (REVISED 2026-05-06)` section. That doc captures the locked USER decisions (1 provider per category, Anthropic-only LLM, lean privacy line, no tray submenu, Sprint 4 ships dropdown+ElevenLabs together, Deepgram parked) plus the ElevenLabs SDK research findings. This plan converts that narrative into executable TDD tasks.

**Load-bearing constraints (never break):**
- 223/223 pytest tests stay green; new tests for new code; sprint exit at ~238/238
- Only `pyqtSignal` crosses thread boundaries — PyQt6 is not thread-safe
- `overlay.hide_for_capture()` fires BEFORE every `mss.grab()` (feedback-loop prevention; unaffected by this sprint but protected)
- `pynput.Listener(suppress=False)` observe-only — never `suppress=True` (globally destructive)
- `config.resolve_api_key` env→keyring with one-shot migration is the established pattern; `resolve_setting` mirrors it for non-secret config
- `CartesiaSonicTTS` is NOT modified — its `speak`/`speak_sentence`/`stop` contract is the shape `ElevenLabsTTS` must match
- Tray menu stays at exactly 4 items: Settings... / Open Knowledge Folder / Open Memory Folder / Quit Clicky. NO TTS Provider submenu (single source of truth = Settings dialog)
- API keys persist to keyring service `"clicky-windows"` (existing constant); ElevenLabs key uses env-var name `ELEVENLABS_API_KEY` and the same keyring slot pattern
- Conventional-commit prefix (`feat:` for new features, `fix:` for bugfixes, `test:` only when test-only changes); NO `Co-Authored-By: Claude` trailer (USER global rule)
- DI factory pattern via `client_factory` / `player_factory` constructor params — required for unit-testable subclass

---

## File Structure Map

| File | Responsibility | Change type |
|---|---|---|
| `config.py` | `resolve_setting(name, default)` helper + provider/voice/model/rate constants | Modified (Tasks 1, 2) |
| `tts.py` | `ElevenLabsTTS` subclass mirroring `CartesiaSonicTTS` Option B arch + `create_tts_client` factory | Modified (Tasks 3, 4) |
| `app.py` | Main-block factory dispatch on `TTS_PROVIDER` | Modified (Task 5) |
| `settings_dialog.py` | `_PROVIDER_CATEGORIES` data model + 3-row dropdown layout + dropdown change handler + Save persistence + privacy line + "Get key →" buttons | Modified (Tasks 6, 7, 8, 9) |
| `requirements.txt` | Add `elevenlabs>=2.0` | Modified (Task 10) |
| `clicky.spec` | Add `elevenlabs` to `hiddenimports=[...]` | Modified (Task 10) |
| `tests/test_config_keyring.py` | New tests for `resolve_setting` (env path, keyring path, default, env→keyring migration) | Modified (Task 1) |
| `tests/test_tts.py` | New tests: ElevenLabsTTS speak/speak_sentence/stop + factory dispatch | Modified (Tasks 3, 4) |
| `tests/test_app.py` | New tests: factory dispatch on TTS_PROVIDER → right subclass | Modified (Task 5) |
| `tests/test_settings_dialog.py` | New tests: `_PROVIDER_CATEGORIES` shape, dropdown render, dropdown change handler, Save persistence, "Get key →" click | Modified (Tasks 6-9) |

---

## Test Fixture Convention (READ before writing any test)

Verified against `tests/test_tts.py`, `tests/test_app.py`, `tests/test_settings_dialog.py`, `tests/test_config_keyring.py` as of 2026-05-06.

**The shipping pattern is helper methods inside the test class** using pytest-mock's `mocker` fixture. There are no module-level `_with_mocks` fixtures.

### Canonical `tests/test_tts.py` pattern (class `TestCartesiaSonicTTSSpeak`)

```python
class TestElevenLabsTTSSpeak:
    def _make_tts(self, chunks=None):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        fake_client = MagicMock(name="fake_elevenlabs_client")
        # ElevenLabs streaming returns Iterator[bytes] directly:
        fake_client.text_to_speech.stream.return_value = iter(
            chunks if chunks is not None else [b"\x00\x00" * 8, b"\x00\x00" * 8]
        )
        fake_play = MagicMock(name="fake_play")

        def client_factory(*, api_key):
            return fake_client

        def player_factory(*, sample_rate):
            return fake_play, None

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )
        return tts_obj, fake_client, fake_play
```

### Canonical `tests/test_settings_dialog.py` pattern

QApplication needed. Use the existing session fixture in `tests/test_tray.py`:

```python
@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app

class TestSettingsDialogProviderDropdowns:
    def test_dropdown_renders_correct_options(self, qapp, mocker):
        # Mock keyring to avoid touching real Credential Manager
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        # Inspect dlg._dropdowns["TTS"] etc. — internal state, not user clicks
        ...
```

### Canonical `tests/test_config_keyring.py` pattern (existing `fake_keyring` fixture)

```python
@pytest.fixture
def fake_keyring(monkeypatch):
    store: dict[tuple[str, str], str] = {}
    def fake_get(service, name): return store.get((service, name))
    def fake_set(service, name, value): store[(service, name)] = value
    import config
    monkeypatch.setattr(config.keyring, "get_password", fake_get)
    monkeypatch.setattr(config.keyring, "set_password", fake_set)
    yield store
```

Tests pre-populate `fake_keyring` to simulate "key already in keyring" scenarios, or assert against it after a save.

---

## Task 1: `config.resolve_setting` helper (env→keyring with default)

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\config.py` (add new function near `resolve_api_key`, around line 60)
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_config_keyring.py` (append a `TestResolveSetting` class)

**Why:** `TTS_PROVIDER`, `LLM_PROVIDER`, `STT_PROVIDER` are non-secret config that nonetheless need keyring persistence per the Sprint 3.6 bundled-EXE dotenv-trap lesson. Env-only would silently fall back to defaults in the bundled EXE. `resolve_setting` mirrors `resolve_api_key`'s env→keyring resolution path but adds a `default` parameter (since these constants always have a sensible default, unlike API keys which must be entered).

- [ ] **Step 1.1: Write the failing test class**

Append to `tests/test_config_keyring.py`:

```python
class TestResolveSetting:
    """resolve_setting is a sibling to resolve_api_key for non-secret
    config. Same env→keyring semantics, plus a default fallback when
    neither env nor keyring has a value (since settings always have a
    sensible default, unlike API keys which require explicit entry)."""

    def test_returns_env_value_when_present(self, monkeypatch, fake_keyring):
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"

    def test_migrates_env_to_keyring_on_resolve(self, monkeypatch, fake_keyring):
        """When env is present, the value MUST also land in keyring so the
        user can later delete .env without losing the choice."""
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
        from config import resolve_setting, KEYRING_SERVICE
        resolve_setting("TTS_PROVIDER", default="cartesia")
        assert fake_keyring[(KEYRING_SERVICE, "TTS_PROVIDER")] == "elevenlabs"

    def test_falls_back_to_keyring_when_env_absent(self, monkeypatch, fake_keyring):
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        from config import resolve_setting, KEYRING_SERVICE
        fake_keyring[(KEYRING_SERVICE, "TTS_PROVIDER")] = "elevenlabs"
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"

    def test_returns_default_when_neither_source_has_value(self, monkeypatch, fake_keyring):
        """First-launch state: no env, empty keyring → default. Distinct from
        resolve_api_key which returns None (settings always have a default)."""
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "cartesia"

    def test_keyring_failures_do_not_block_env_path(self, monkeypatch):
        """Keyring backend errors swallowed — env value still returned + default
        still works as final fallback."""
        monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")

        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated keyring failure")

        import config
        monkeypatch.setattr(config.keyring, "set_password", boom)
        from config import resolve_setting
        assert resolve_setting("TTS_PROVIDER", default="cartesia") == "elevenlabs"
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_config_keyring.py::TestResolveSetting -v`
Expected: 5 FAIL with `ImportError: cannot import name 'resolve_setting' from 'config'`

- [ ] **Step 1.3: Implement `resolve_setting` in config.py**

Add immediately after `resolve_api_key` (around line 60 of `config.py`):

```python
def resolve_setting(name: str, default: str) -> str:
    """Resolve a non-secret setting by name with env→keyring→default fallback.

    Sibling to ``resolve_api_key`` for config knobs (TTS_PROVIDER,
    LLM_PROVIDER, STT_PROVIDER, etc.) that need keyring persistence so
    bundled-EXE startup doesn't silently fall back to defaults when the
    user's `.env` doesn't load (cwd is install dir, not repo root — see
    DECISIONS.md 2026-05-05 Sprint 3.6).

    Differs from resolve_api_key in that it always returns a string —
    callers pass the right default for the setting (e.g. "cartesia" for
    TTS_PROVIDER) rather than handling None.

    Failures in keyring (locked vault, no backend) are swallowed in both
    directions: env path always returns successfully even if keyring write
    fails; keyring read errors fall through to the default.
    """
    env_value = os.getenv(name)
    if env_value:
        try:
            keyring.set_password(KEYRING_SERVICE, name, env_value)
        except Exception:
            pass
        return env_value
    try:
        stored = keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        stored = None
    return stored if stored else default
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `py -3.13 -m pytest tests/test_config_keyring.py::TestResolveSetting -v`
Expected: 5 PASS in <1s

- [ ] **Step 1.5: Run full test suite to confirm no regression**

Run: `py -3.13 -m pytest -q`
Expected: 228/228 passed (was 223 + 5 new)

- [ ] **Step 1.6: Commit**

```bash
git add config.py tests/test_config_keyring.py
git commit -m "feat(config): add resolve_setting helper for env-keyring non-secret persistence"
```

---

## Task 2: `config.py` — provider + ElevenLabs constants

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\config.py` (append constants near the existing Cartesia constants block, around line 200)

**Why:** Lock the new constants (`LLM_PROVIDER`, `STT_PROVIDER`, `TTS_PROVIDER`, plus all `ELEVENLABS_*`) into the module namespace so downstream files (`tts.py`, `settings_dialog.py`, `app.py`) can import them. Tests-light because constants are data, not behavior.

- [ ] **Step 2.1: Add provider constants in config.py**

Append after the `CARTESIA_OUTPUT_SAMPLE_RATE` constant (around line 228):

```python
# ── Provider selection (which subclass app.py constructs at startup) ────────

LLM_PROVIDER: str = resolve_setting("LLM_PROVIDER", default="anthropic")
"""Which AIClient subclass to construct. Sprint 4 ships only "anthropic"
in the dropdown; GeminiClient infrastructure stays in ai.py for opt-in
via env override (MODEL_ID=google/...) but is not user-selectable in the
settings dialog. See DECISIONS.md 2026-04-19 (late-evening) for the
empirical A/B that rejected Gemini on coordinate accuracy."""

STT_PROVIDER: str = resolve_setting("STT_PROVIDER", default="assemblyai")
"""Which STT subclass to construct. Sprint 4 ships only "assemblyai".
Deepgram is parked for post-launch."""

TTS_PROVIDER: str = resolve_setting("TTS_PROVIDER", default="cartesia")
"""Which TTS subclass to construct. Sprint 4 ships "cartesia" (default)
and "elevenlabs" (opt-in). User switches via Settings dialog dropdown."""


# ── ElevenLabs TTS (opt-in alternative to Cartesia) ─────────────────────────

ELEVENLABS_API_KEY: str | None = resolve_api_key("ELEVENLABS_API_KEY")
"""Optional. Required only when TTS_PROVIDER='elevenlabs'. 10k chars/month
free tier at https://elevenlabs.io/app/sign-up — no credit card."""

ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5")
"""ElevenLabs Flash v2.5 — ~75ms model TTFB. ElevenLabs officially
recommends Flash over Turbo v2.5 for low-latency voice agents."""

ELEVENLABS_VOICE_ID: str = os.getenv(
    "ELEVENLABS_VOICE_ID",
    "21m00Tcm4TlvDq8ikWAM",  # Rachel — American female, conversational
)
"""ElevenLabs voice ID for the buddy persona. Default Rachel matches
Cartesia "Brooke - Big Sister" warmth (conversational adult female).
Catalog: https://elevenlabs.io/app/voice-library."""

ELEVENLABS_OUTPUT_SAMPLE_RATE: int = int(
    os.getenv("ELEVENLABS_OUTPUT_SAMPLE_RATE", "22050")
)
"""ElevenLabs PCM sample rate. Defaulted to 22050 because 44.1kHz PCM
requires Pro tier. ElevenLabs PCM is int16 (NOT float32 like Cartesia),
so playback path converts inline: np.frombuffer(chunk, np.int16).astype(
np.float32) / 32768.0."""
```

- [ ] **Step 2.2: Verify imports + module loads cleanly**

Run: `py -3.13 -c "import config; print(config.TTS_PROVIDER, config.ELEVENLABS_VOICE_ID, config.ELEVENLABS_OUTPUT_SAMPLE_RATE)"`
Expected output: `cartesia 21m00Tcm4TlvDq8ikWAM 22050`

- [ ] **Step 2.3: Run full test suite to confirm no regression**

Run: `py -3.13 -m pytest -q`
Expected: 228/228 passed (no new tests; constants don't need tests)

- [ ] **Step 2.4: Commit**

```bash
git add config.py
git commit -m "feat(config): add provider constants + ElevenLabs settings (voice/model/rate)"
```

---

## Task 3: `ElevenLabsTTS` subclass — full implementation

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tts.py` (append `ElevenLabsTTS` class after the existing `CartesiaSonicTTS` class definition, before the `__main__` block)
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_tts.py` (append `TestElevenLabsTTSSpeak`, `TestElevenLabsTTSSentenceQueue`, `TestElevenLabsTTSStop` classes after existing Cartesia test classes)

**Why:** The whole point of Sprint 4. Mirror `CartesiaSonicTTS` Option B prefetch+playback two-thread architecture verbatim, with three deliberate divergences captured in three separate tests. ~120 LOC.

**Architecture mirror checklist (verify against `tts.py:CartesiaSonicTTS`):**
- `__init__`: `api_key`, `voice_id`, `model_id`, `sample_rate`, `client_factory`, `player_factory` — same signature shape
- `_sentence_queue`, `_prefetch_queue` (maxsize=1), `_epoch`, `_cancel_event` — same instance attrs
- `_prefetch_thread` + `_playback_thread` started in `__init__` — same lifecycle
- `_prefetch_worker`, `_playback_worker` — same loop shape, same `_SHUTDOWN_SENTINEL` handling, same epoch comparison in playback
- `speak(text)`, `speak_sentence(sentence)` — same public API
- `stop()` — 5-pronged not 6 (no `response.close()`)
- `_generate_response(text)` — calls `client.text_to_speech.stream(...)` (returns `Iterator[bytes]` directly)
- `_play_response(text, response, cancel)` — iterates response (it IS the iterator), int16→float32 conversion, plays via sounddevice

- [ ] **Step 3.1: Write the failing tests for ElevenLabsTTS basic API**

Append to `tests/test_tts.py`:

```python
# --- ElevenLabs TTS (Sprint 4) -------------------------------------------------

class TestElevenLabsTTSSpeak:
    """Mirrors TestCartesiaSonicTTSSpeak — same speak/stop/cancel
    semantics. Differences: stream() returns Iterator[bytes] directly
    (no .iter_bytes()); chunks are int16 PCM, converted to float32 in
    the playback loop."""

    def _make_tts(self, chunks=None):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        fake_client = MagicMock(name="fake_elevenlabs_client")
        # ElevenLabs streaming method: client.text_to_speech.stream(...)
        # returns an Iterator[bytes] directly (true streaming, no body fetch)
        fake_client.text_to_speech.stream.return_value = iter(
            chunks if chunks is not None
            else [b"\x00\x00" * 8, b"\x00\x00" * 8]  # int16 zeros
        )
        fake_play = MagicMock(name="fake_play")

        def client_factory(*, api_key):
            return fake_client

        def player_factory(*, sample_rate):
            return fake_play, None

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )
        return tts_obj, fake_client, fake_play

    def test_speak_dispatches_to_background_thread_non_blocking(self):
        import time
        tts_obj, fake_client, fake_play = self._make_tts()

        t0 = time.perf_counter()
        tts_obj.speak("hello")
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 50

        if tts_obj._current_thread:
            tts_obj._current_thread.join(timeout=5)

        fake_client.text_to_speech.stream.assert_called_once()
        call_kwargs = fake_client.text_to_speech.stream.call_args.kwargs
        assert call_kwargs["text"] == "hello"
        assert call_kwargs["model_id"] == "eleven_flash_v2_5"
        assert call_kwargs["voice_id"] == "21m00Tcm4TlvDq8ikWAM"
        assert call_kwargs["output_format"] == "pcm_22050"
        assert fake_play.call_count >= 1

    def test_speak_empty_string_skips_thread(self):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        client_factory = MagicMock(name="client_factory")
        player_factory = MagicMock(name="player_factory")
        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak("")
        tts_obj.speak("   \t\n")

        assert tts_obj._current_thread is None
        client_factory.assert_not_called()
        player_factory.assert_not_called()

    def test_play_response_converts_int16_to_float32(self):
        """ElevenLabs PCM chunks are int16 little-endian. The playback
        loop must convert each chunk to float32 in [-1, 1] range before
        passing to sounddevice (which expects float32 per OutputStream
        config). This is the load-bearing divergence from Cartesia which
        emits float32 directly."""
        import struct
        import threading
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS
        import numpy as np

        # Build a chunk of 4 int16 samples at +0.5 amplitude.
        max_int16 = 32767
        amplitude = int(0.5 * max_int16)
        chunk_bytes = struct.pack("<hhhh", amplitude, -amplitude, amplitude, 0)

        fake_client = MagicMock()
        fake_client.text_to_speech.stream.return_value = iter([chunk_bytes])
        captured_samples = []
        def fake_play(samples):
            captured_samples.append(samples.copy())

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: fake_client,
            player_factory=lambda *, sample_rate: (fake_play, None),
        )

        cancel = threading.Event()
        response = tts_obj._generate_response("hi")
        tts_obj._play_response("hi", response, cancel)

        assert len(captured_samples) == 1
        arr = captured_samples[0]
        assert arr.dtype == np.float32
        # 0.5 amplitude after divide by 32768 ≈ 0.4999... — assert close
        assert abs(float(arr[0]) - 0.5) < 0.001
        assert abs(float(arr[1]) + 0.5) < 0.001
        assert abs(float(arr[3])) < 0.001
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_tts.py::TestElevenLabsTTSSpeak -v`
Expected: 3 FAIL with `ImportError: cannot import name 'ElevenLabsTTS' from 'tts'`

- [ ] **Step 3.3: Write the failing tests for sentence queue + prefetch**

Append to `tests/test_tts.py`:

```python
class TestElevenLabsTTSSentenceQueue:
    """Mirrors TestCartesiaSonicTTSSpeak::test_speak_sentence_queues_and_plays_sequentially.
    Multiple speak_sentence calls play sequentially via the prefetch+playback
    two-thread architecture (Option B), NOT cancelling each other."""

    def test_speak_sentence_queues_and_plays_sequentially(self):
        import time as _t
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        played_count = [0]

        def fake_play(samples):
            played_count[0] += 1

        def client_factory(*, api_key):
            client = MagicMock(name="multi-sentence-elevenlabs-client")

            def gen_iterator(**kwargs):
                # Each stream() call must return a fresh iterator
                return iter([b"\x00\x00" * 8])

            client.text_to_speech.stream.side_effect = gen_iterator
            return client

        def player_factory(*, sample_rate):
            return fake_play, None

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("first sentence.")
        tts_obj.speak_sentence("second sentence.")
        tts_obj.speak_sentence("third sentence.")

        for _ in range(100):
            if played_count[0] >= 3:
                break
            _t.sleep(0.02)

        assert played_count[0] >= 3, (
            f"Expected >=3 sentences played, got {played_count[0]} — "
            "queue worker may not be consuming sequentially"
        )
```

- [ ] **Step 3.4: Write the failing tests for stop() 5-pronged kill**

Append to `tests/test_tts.py`:

```python
class TestElevenLabsTTSStop:
    """5-pronged kill (NOT 6 — no response.close, since elevenlabs SDK
    doesn't expose one). Order: epoch++ → drain sentence queue → drain
    prefetch queue → cancel event → sounddevice abort."""

    def test_stop_drains_pending_sentences(self):
        import time as _t
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        def client_factory(*, api_key):
            client = MagicMock()

            def slow_iter(**kwargs):
                def _gen():
                    for _ in range(10):
                        _t.sleep(0.05)
                        yield b"\x00\x00" * 8

                return _gen()

            client.text_to_speech.stream.side_effect = slow_iter
            return client

        def player_factory(*, sample_rate):
            return MagicMock(), None

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=client_factory,
            player_factory=player_factory,
        )

        tts_obj.speak_sentence("pending-1.")
        tts_obj.speak_sentence("pending-2.")
        tts_obj.speak_sentence("pending-3.")

        _t.sleep(0.02)

        tts_obj.stop()
        assert tts_obj._sentence_queue.empty(), (
            "stop() must drain queued sentences"
        )

    def test_stop_sets_cancel_event_and_bumps_epoch(self):
        from unittest.mock import MagicMock
        from tts import ElevenLabsTTS

        tts_obj = ElevenLabsTTS(
            api_key="test-key",
            client_factory=lambda *, api_key: MagicMock(),
            player_factory=lambda *, sample_rate: (MagicMock(), None),
        )
        old_epoch = tts_obj._epoch
        old_cancel = tts_obj._cancel_event
        tts_obj.stop()
        assert tts_obj._epoch == old_epoch + 1
        assert old_cancel.is_set()
```

- [ ] **Step 3.5: Run tests to verify they all fail**

Run: `py -3.13 -m pytest tests/test_tts.py -k "ElevenLabs" -v`
Expected: 5 FAIL with `ImportError: cannot import name 'ElevenLabsTTS' from 'tts'`

- [ ] **Step 3.6: Implement `ElevenLabsTTS` in tts.py**

Read the existing `CartesiaSonicTTS` class definition in `tts.py` (lines ~96-447) for the shape to mirror. Then append after the closing of `CartesiaSonicTTS` (around line 447):

```python
# --- ElevenLabsTTS (Sprint 4 — opt-in alternative to Cartesia) ---------------

class ElevenLabsTTS(TTS):
    """ElevenLabs Flash v2.5 streaming TTS as an opt-in alternative to
    Cartesia. Mirrors CartesiaSonicTTS Option B prefetch+playback
    architecture with three deliberate divergences:

    1. ``_generate_response`` calls ``client.text_to_speech.stream(...)``
       which returns an ``Iterator[bytes]`` DIRECTLY (true streaming, no
       body pre-fetch). Cartesia's ``generate(...)`` blocks for the full
       body before returning a response with ``.iter_bytes()``.
    2. ``_play_response`` converts each int16 PCM chunk to float32 inline:
       ``samples = np.frombuffer(chunk, np.int16).astype(np.float32) / 32768.0``.
       Cartesia emits float32 directly so no conversion needed.
    3. ``stop()`` is 5-pronged (not 6): no ``response.close()`` — the
       elevenlabs SDK doesn't expose one. Cancellation = break the for
       loop via cancel event. Python GC closes the underlying httpx
       connection. Functionally equivalent kill latency to Cartesia's
       6-pronged stop because the cancel event check fires once per chunk
       and chunks arrive at <50ms intervals.

    Default sample rate is 22050 (NOT 44.1k) because ElevenLabs free tier
    doesn't include 44.1kHz PCM (Pro tier feature). Each TTS subclass owns
    its own sample_rate — sounddevice OutputStream is constructed
    per-instance via ``_build_player``.
    """

    def __init__(
        self,
        api_key: str,
        voice_id: str | None = None,
        model_id: str | None = None,
        sample_rate: int | None = None,
        client_factory: Callable | None = None,
        player_factory: Callable | None = None,
    ) -> None:
        from config import (
            ELEVENLABS_VOICE_ID,
            ELEVENLABS_MODEL_ID,
            ELEVENLABS_OUTPUT_SAMPLE_RATE,
        )
        self.api_key = api_key
        self.voice_id = voice_id or ELEVENLABS_VOICE_ID
        self.model_id = model_id or ELEVENLABS_MODEL_ID
        self.sample_rate = sample_rate or ELEVENLABS_OUTPUT_SAMPLE_RATE
        self._client_factory = client_factory
        self._player_factory = player_factory

        self._cancel_event = threading.Event()
        self._current_thread: threading.Thread | None = None
        self._active_audio_stream = None  # sounddevice stream, aborted by stop()

        # Option B: prefetch+playback two-thread architecture, mirrors
        # CartesiaSonicTTS verbatim except no _active_response (elevenlabs
        # has no response.close()).
        self._sentence_queue: queue.Queue = queue.Queue()
        self._prefetch_queue: queue.Queue = queue.Queue(maxsize=1)
        self._epoch: int = 0
        self._prefetch_thread = threading.Thread(
            target=self._prefetch_worker,
            name="ElevenLabsTTS-prefetch",
            daemon=True,
        )
        self._playback_thread = threading.Thread(
            target=self._playback_worker,
            name="ElevenLabsTTS-playback",
            daemon=True,
        )
        self._prefetch_thread.start()
        self._playback_thread.start()

    def speak(self, text: str) -> None:
        """One-shot speak path. Cancels any in-progress playback."""
        if not text or not text.strip():
            return
        self._cancel_event.set()
        old = self._current_thread
        if old and old.is_alive():
            old.join(timeout=0.5)
        self._cancel_event = threading.Event()
        cancel = self._cancel_event
        self._current_thread = threading.Thread(
            target=self._do_speak,
            args=(text, cancel),
            name=f"ElevenLabsTTS-speak-{id(text)}",
            daemon=True,
        )
        self._current_thread.start()

    def speak_sentence(self, sentence: str) -> None:
        if not sentence or not sentence.strip():
            return
        self._sentence_queue.put(sentence)

    def _prefetch_worker(self) -> None:
        while True:
            sentence = self._sentence_queue.get()
            if sentence is _SHUTDOWN_SENTINEL:
                break
            my_epoch = self._epoch
            try:
                response = self._generate_response(sentence)
            except Exception as exc:
                print(f"[tts] elevenlabs prefetch error for {sentence!r}: {exc}", flush=True)
                response = None
            try:
                self._prefetch_queue.put((my_epoch, sentence, response))
            finally:
                self._sentence_queue.task_done()

    def _playback_worker(self) -> None:
        while True:
            item = self._prefetch_queue.get()
            if item is _SHUTDOWN_SENTINEL:
                break
            my_epoch, sentence, response = item
            if my_epoch != self._epoch or response is None:
                # Stale or failed — skip without playing. No response.close()
                # to call (elevenlabs SDK iterator has no explicit close).
                continue
            try:
                cancel = threading.Event()
                self._cancel_event = cancel
                self._play_response(sentence, response, cancel)
            except Exception as exc:
                print(f"[tts] elevenlabs playback error for {sentence!r}: {exc}", flush=True)

    def stop(self) -> None:
        """5-pronged kill (no response.close vs Cartesia's 6-pronged):
        1. Bump _epoch — any in-flight prefetch becomes stale at playback time
        2. Drain _sentence_queue — pending sentences never start
        3. Drain _prefetch_queue — prefetched iterators dropped
        4. Set cancel event — currently-playing sentence's loop exits
        5. Abort sounddevice stream — stops audio output mid-sample
        """
        self._epoch += 1

        while not self._sentence_queue.empty():
            try:
                self._sentence_queue.get_nowait()
                self._sentence_queue.task_done()
            except queue.Empty:
                break

        while not self._prefetch_queue.empty():
            try:
                self._prefetch_queue.get_nowait()
                # No response.close — elevenlabs iterator has no explicit close.
                # Python GC will close the underlying httpx connection.
            except queue.Empty:
                break

        self._cancel_event.set()
        stream = self._active_audio_stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def _generate_response(self, text: str):
        """Call ElevenLabs streaming endpoint. Returns Iterator[bytes]
        directly — TRUE streaming, no body pre-fetch.
        """
        client = self._build_client()
        return client.text_to_speech.stream(
            text=text,
            voice_id=self.voice_id,
            model_id=self.model_id,
            output_format=f"pcm_{self.sample_rate}",
        )

    def _play_response(self, text: str, response, cancel: threading.Event) -> None:
        """Iterate the int16 PCM chunk stream, convert to float32 inline,
        play via sounddevice. Sets _active_audio_stream so stop() can abort.
        """
        if cancel.is_set():
            return

        import time as _t
        _tts_start = _t.time()
        print(f"[tts] elevenlabs _play_response START: {len(text)} chars", flush=True)
        audio_stream = None
        try:
            play, audio_stream = self._build_player()
            self._active_audio_stream = audio_stream
            for chunk in response:
                if cancel.is_set():
                    return
                if not chunk:
                    continue
                # int16 → float32 in [-1, 1]
                samples = (
                    np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
                if samples.size == 0:
                    continue
                play(samples)
        except Exception as exc:
            if cancel.is_set():
                return
            raise RuntimeError(
                "ElevenLabs TTS playback failed. Diagnostic checklist:\n"
                "  1. Is ELEVENLABS_API_KEY set + valid?\n"
                "  2. Is your free-tier quota exhausted? (10k chars/month)\n"
                "  3. Is your internet connection up?\n"
                "  4. Is ElevenLabs up? (https://status.elevenlabs.io)\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            self._active_audio_stream = None
            duration_ms = (_t.time() - _tts_start) * 1000
            cancelled = cancel.is_set()
            print(f"[tts] elevenlabs _play_response END: {duration_ms:.0f}ms, cancelled={cancelled}", flush=True)
            if audio_stream is not None:
                try:
                    audio_stream.abort()
                    audio_stream.close()
                except Exception:
                    pass

    def _do_speak(self, text: str, cancel: threading.Event) -> None:
        """One-shot speak path: get the iterator + play. Used by speak().
        speak_sentence uses the prefetch+playback workers instead.
        """
        if cancel.is_set():
            return
        try:
            response = self._generate_response(text)
        except Exception as exc:
            if cancel.is_set():
                return
            raise RuntimeError(
                "ElevenLabs TTS request failed. Diagnostic checklist:\n"
                "  1. Is ELEVENLABS_API_KEY set in keyring or .env?\n"
                "  2. Is your free-tier quota exhausted?\n"
                "  3. Is your internet connection up?\n"
                f"Underlying error: {type(exc).__name__}: {exc}"
            ) from exc
        self._play_response(text, response, cancel)

    def _build_client(self):
        if self._client_factory is not None:
            return self._client_factory(api_key=self.api_key)
        from elevenlabs import ElevenLabs
        return ElevenLabs(api_key=self.api_key)

    def _build_player(self):
        if self._player_factory is not None:
            return self._player_factory(sample_rate=self.sample_rate)
        import sounddevice as sd

        stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
        )
        stream.start()

        def _play(samples: np.ndarray) -> None:
            stream.write(samples)

        return _play, stream
```

- [ ] **Step 3.7: Run tests to verify they pass**

Run: `py -3.13 -m pytest tests/test_tts.py -k "ElevenLabs" -v`
Expected: 5 PASS in <3s

- [ ] **Step 3.8: Run full test suite to confirm no regression**

Run: `py -3.13 -m pytest -q`
Expected: 233/233 passed (was 228 + 5 new)

- [ ] **Step 3.9: Commit**

```bash
git add tts.py tests/test_tts.py
git commit -m "feat(tts): add ElevenLabsTTS subclass mirroring Cartesia Option B architecture"
```

---

## Task 4: `tts.create_tts_client` factory

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tts.py` (append factory function after `ElevenLabsTTS` class)
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_tts.py` (append `TestCreateTTSClient` class)

**Why:** `app.py` main block needs a single dispatch point on `TTS_PROVIDER` string. Mirrors `ai.create_ai_client(model_id, api_key)` factory pattern. ~15 LOC.

- [ ] **Step 4.1: Write the failing factory tests**

Append to `tests/test_tts.py`:

```python
class TestCreateTTSClient:
    """Tests for tts.create_tts_client factory — routes provider string
    to right TTS subclass."""

    def test_routes_cartesia_to_cartesia_sonic_tts(self, mocker):
        from tts import create_tts_client, CartesiaSonicTTS
        mocker.patch("tts.Cartesia")  # don't construct real SDK
        client = create_tts_client(provider="cartesia", api_key="test-key")
        assert isinstance(client, CartesiaSonicTTS)

    def test_routes_elevenlabs_to_elevenlabs_tts(self, mocker):
        from tts import create_tts_client, ElevenLabsTTS
        mocker.patch("tts.ElevenLabs", create=True)
        client = create_tts_client(provider="elevenlabs", api_key="test-key")
        assert isinstance(client, ElevenLabsTTS)

    def test_unknown_provider_raises_value_error(self):
        from tts import create_tts_client
        with pytest.raises(ValueError) as excinfo:
            create_tts_client(provider="googletts", api_key="x")
        msg = str(excinfo.value)
        assert "googletts" in msg
        assert "cartesia" in msg
        assert "elevenlabs" in msg

    def test_provider_string_is_case_insensitive(self, mocker):
        from tts import create_tts_client, CartesiaSonicTTS
        mocker.patch("tts.Cartesia")
        client = create_tts_client(provider="Cartesia", api_key="x")
        assert isinstance(client, CartesiaSonicTTS)
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_tts.py::TestCreateTTSClient -v`
Expected: 4 FAIL with `ImportError: cannot import name 'create_tts_client'`

- [ ] **Step 4.3: Implement `create_tts_client` in tts.py**

Append after the `ElevenLabsTTS` class (before the `__main__` block):

```python
# --- Factory: route provider string to the right TTS subclass ----------------

def create_tts_client(provider: str, api_key: str) -> TTS:
    """Construct the right TTS subclass based on a provider string.

    Mirrors ai.create_ai_client's factory pattern. Used by app.py main
    block to dispatch on the TTS_PROVIDER constant (resolved from env or
    keyring via config.resolve_setting).

    Args:
        provider: "cartesia" or "elevenlabs". Case-insensitive.
        api_key: provider-specific API key (CARTESIA_API_KEY or
            ELEVENLABS_API_KEY).

    Returns:
        A concrete TTS subclass ready for speak_sentence() / speak() calls.

    Raises:
        ValueError: if provider is not recognized.
    """
    p = provider.lower()
    if p == "cartesia":
        return CartesiaSonicTTS(api_key=api_key)
    if p == "elevenlabs":
        return ElevenLabsTTS(api_key=api_key)
    raise ValueError(
        f"Unsupported TTS provider: {provider!r}. "
        f"Supported: 'cartesia', 'elevenlabs'. To add a new provider, "
        f"subclass TTS in tts.py and extend create_tts_client() with a new branch."
    )
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `py -3.13 -m pytest tests/test_tts.py::TestCreateTTSClient -v`
Expected: 4 PASS

- [ ] **Step 4.5: Run full test suite to confirm no regression**

Run: `py -3.13 -m pytest -q`
Expected: 237/237 passed (was 233 + 4 new)

- [ ] **Step 4.6: Commit**

```bash
git add tts.py tests/test_tts.py
git commit -m "feat(tts): add create_tts_client factory for provider dispatch"
```

---

## Task 5: `app.py` main-block factory dispatch

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\app.py` (main block, around line 856-865)
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_app.py` (append `TestTTSProviderDispatch` class)

**Why:** Replace the hardcoded `CartesiaSonicTTS(api_key=api_cartesia)` instantiation in `__main__` with a `create_tts_client(provider, api_key)` call where provider is resolved from `config.TTS_PROVIDER` and api_key is selected based on provider. ~15 LOC change.

- [ ] **Step 5.1: Write the failing dispatch test**

Append to `tests/test_app.py`:

```python
# --- Sprint 4: TTS factory dispatch -----------------------------------------

class TestTTSProviderDispatch:
    """The main block must construct the right TTS subclass based on
    config.TTS_PROVIDER. This test mocks the factory + verifies it gets
    called with the right (provider, api_key) tuple.

    The main block is gated by ``if __name__ == "__main__"`` so we test
    the factory + key-selection helper directly (a small extracted
    function), not the full main-block flow."""

    def test_resolve_tts_credentials_for_cartesia(self, mocker):
        """Helper returns (provider, api_key) tuple — Cartesia path."""
        mocker.patch("app.resolve_setting", return_value="cartesia")
        mocker.patch("app.resolve_api_key", side_effect=lambda name: {
            "CARTESIA_API_KEY": "sk_car_test",
            "ELEVENLABS_API_KEY": None,
        }[name])
        from app import _resolve_tts_credentials
        provider, api_key = _resolve_tts_credentials()
        assert provider == "cartesia"
        assert api_key == "sk_car_test"

    def test_resolve_tts_credentials_for_elevenlabs(self, mocker):
        """Helper returns (provider, api_key) tuple — ElevenLabs path."""
        mocker.patch("app.resolve_setting", return_value="elevenlabs")
        mocker.patch("app.resolve_api_key", side_effect=lambda name: {
            "CARTESIA_API_KEY": None,
            "ELEVENLABS_API_KEY": "eleven_test",
        }[name])
        from app import _resolve_tts_credentials
        provider, api_key = _resolve_tts_credentials()
        assert provider == "elevenlabs"
        assert api_key == "eleven_test"
```

- [ ] **Step 5.2: Run tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_app.py::TestTTSProviderDispatch -v`
Expected: 2 FAIL with `ImportError: cannot import name '_resolve_tts_credentials'`

- [ ] **Step 5.3: Add the helper + wire main block in app.py**

In `app.py`, near the other module-level helpers (`_log`, `_play_chime_async`), add:

```python
def _resolve_tts_credentials() -> tuple[str, str | None]:
    """Resolve (TTS_PROVIDER, api_key_for_that_provider) at startup.

    Reads TTS_PROVIDER via config.resolve_setting (env→keyring→default)
    then resolves the right API key via config.resolve_api_key based on
    the selected provider.
    """
    from config import resolve_setting, resolve_api_key
    provider = resolve_setting("TTS_PROVIDER", default="cartesia")
    if provider == "elevenlabs":
        api_key = resolve_api_key("ELEVENLABS_API_KEY")
    else:
        api_key = resolve_api_key("CARTESIA_API_KEY")
    return provider, api_key
```

Add the import for `resolve_setting` at the top of app.py (alongside the existing `resolve_api_key` import — note: actually `app.py` doesn't import `resolve_api_key` at module top yet; it does `from config import resolve_api_key` inside `__main__`. Match that pattern):

In the `__main__` block, replace the existing TTS construction:

```python
    # Replace this line (around line 864):
    #     tts_client=CartesiaSonicTTS(api_key=api_cartesia),
    # With:
    tts_provider, tts_api_key = _resolve_tts_credentials()
    if not tts_api_key:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"Clicky needs an API key for {tts_provider.title()} TTS.\n\n"
            "Right-click the tray icon → Settings... to set it.",
            f"{tts_provider.title()} key missing",
            0x40,
        )
        sys.exit(1)
    from tts import create_tts_client
    tts_instance = create_tts_client(provider=tts_provider, api_key=tts_api_key)

    # Then in ClickyApp construction:
    clicky = ClickyApp(
        ai_client=create_ai_client(model_id=MODEL_ID, api_key=api_anthropic),
        stt_client=AssemblyAIStreamingSTT(api_key=api_assemblyai),
        tts_client=tts_instance,
    )
```

Also: import `resolve_setting` next to where `resolve_api_key` is imported in `__main__` (around line 856):

```python
    from config import resolve_api_key, resolve_setting
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `py -3.13 -m pytest tests/test_app.py::TestTTSProviderDispatch -v`
Expected: 2 PASS

- [ ] **Step 5.5: Run full test suite to confirm no regression**

Run: `py -3.13 -m pytest -q`
Expected: 239/239 passed (was 237 + 2 new)

- [ ] **Step 5.6: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(app): wire TTS_PROVIDER factory dispatch in main block"
```

---

## Task 6: `settings_dialog._PROVIDER_CATEGORIES` data model

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\settings_dialog.py` (replace `_KEY_FIELDS` flat list with `_PROVIDER_CATEGORIES` data model + helper class near top of file)
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_settings_dialog.py` (append `TestProviderCategoriesData` class)

**Why:** The data model drives the rendering, the dropdown change handler, and the Save persistence. Lock the shape first, then build UI on top of it. ~40 LOC.

- [ ] **Step 6.1: Write failing tests for the data model**

Append to `tests/test_settings_dialog.py`:

```python
# --- Sprint 4: provider category data model ---------------------------------

class TestProviderCategoriesData:
    """The _PROVIDER_CATEGORIES data drives dialog rendering. Each
    category has: a label, a list of provider options, a default
    provider key, the keyring slot prefix (env-var name root). Each
    provider has: display name, env-var name (= keyring slot), signup URL."""

    def test_three_categories_in_correct_order(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        assert [c.category_key for c in _PROVIDER_CATEGORIES] == ["LLM", "STT", "TTS"]

    def test_llm_category_has_only_anthropic(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        llm = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "LLM")
        assert [p.provider_id for p in llm.providers] == ["anthropic"]
        assert llm.default_index == 0

    def test_stt_category_has_only_assemblyai(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        stt = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "STT")
        assert [p.provider_id for p in stt.providers] == ["assemblyai"]

    def test_tts_category_has_cartesia_and_elevenlabs(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        tts = next(c for c in _PROVIDER_CATEGORIES if c.category_key == "TTS")
        assert [p.provider_id for p in tts.providers] == ["cartesia", "elevenlabs"]
        assert tts.default_index == 0  # Cartesia default

    def test_each_provider_has_env_var_and_signup_url(self):
        from settings_dialog import _PROVIDER_CATEGORIES
        for category in _PROVIDER_CATEGORIES:
            for provider in category.providers:
                assert provider.api_key_env_var.endswith("_API_KEY")
                assert provider.signup_url.startswith("https://")
                assert provider.display_name  # non-empty
```

- [ ] **Step 6.2: Run tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_settings_dialog.py::TestProviderCategoriesData -v`
Expected: 5 FAIL with `ImportError: cannot import name '_PROVIDER_CATEGORIES'`

- [ ] **Step 6.3: Replace `_KEY_FIELDS` with `_PROVIDER_CATEGORIES` data model**

In `settings_dialog.py`, replace lines 44-62 (the existing `_KEY_FIELDS` block) with:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class _Provider:
    """Single provider in a category. ``provider_id`` is the lowercase
    string used as the value of LLM_PROVIDER / STT_PROVIDER / TTS_PROVIDER
    config + the dropdown's data slot. ``api_key_env_var`` is BOTH the
    env-var name AND the keyring slot name (they share namespace by
    convention — see config.resolve_api_key)."""

    provider_id: str            # e.g. "anthropic", "elevenlabs"
    display_name: str           # e.g. "Anthropic", "ElevenLabs"
    api_key_env_var: str        # e.g. "ANTHROPIC_API_KEY"
    signup_url: str


@dataclass(frozen=True)
class _ProviderCategory:
    """A row group in the dialog. ``category_key`` is the prefix of
    the provider-selection config (e.g. "LLM" → LLM_PROVIDER setting)."""

    category_key: str           # "LLM", "STT", "TTS"
    label: str                  # "LLM (vision)", "STT (speech-to-text)", "TTS (text-to-speech)"
    providers: tuple[_Provider, ...]
    default_index: int


_PROVIDER_CATEGORIES: tuple[_ProviderCategory, ...] = (
    _ProviderCategory(
        category_key="LLM",
        label="LLM (vision)",
        providers=(
            _Provider(
                provider_id="anthropic",
                display_name="Anthropic",
                api_key_env_var="ANTHROPIC_API_KEY",
                signup_url="https://console.anthropic.com/settings/keys",
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="STT",
        label="STT (speech-to-text)",
        providers=(
            _Provider(
                provider_id="assemblyai",
                display_name="AssemblyAI",
                api_key_env_var="ASSEMBLYAI_API_KEY",
                signup_url="https://www.assemblyai.com/dashboard/signup",
            ),
        ),
        default_index=0,
    ),
    _ProviderCategory(
        category_key="TTS",
        label="TTS (text-to-speech)",
        providers=(
            _Provider(
                provider_id="cartesia",
                display_name="Cartesia",
                api_key_env_var="CARTESIA_API_KEY",
                signup_url="https://play.cartesia.ai/sign-in",
            ),
            _Provider(
                provider_id="elevenlabs",
                display_name="ElevenLabs",
                api_key_env_var="ELEVENLABS_API_KEY",
                signup_url="https://elevenlabs.io/app/sign-up",
            ),
        ),
        default_index=0,
    ),
)
```

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `py -3.13 -m pytest tests/test_settings_dialog.py::TestProviderCategoriesData -v`
Expected: 5 PASS

- [ ] **Step 6.5: Update `required_keys_present` probe to use the new data model**

In `settings_dialog.py`, replace the existing `required_keys_present` (around line 184) with:

```python
def required_keys_present() -> bool:
    """Probe — does every required-provider's API key resolve?

    Sprint 4: "required" = the currently-SELECTED provider per category
    (resolved via resolve_setting on LLM_PROVIDER / STT_PROVIDER /
    TTS_PROVIDER). The probe is what the launcher uses to decide whether
    to show the modal at start.
    """
    from config import resolve_api_key, resolve_setting

    for category in _PROVIDER_CATEGORIES:
        provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=category.providers[category.default_index].provider_id,
        )
        provider = next(
            (p for p in category.providers if p.provider_id == provider_id),
            category.providers[category.default_index],  # fallback if stored value invalid
        )
        if not resolve_api_key(provider.api_key_env_var):
            return False
    return True
```

- [ ] **Step 6.6: Run all settings_dialog + config tests to confirm no regression**

Run: `py -3.13 -m pytest tests/test_settings_dialog.py tests/test_config_keyring.py -q`
Expected: all green (existing TestRequiredKeysPresent tests need to pass with new logic)

If any existing TestRequiredKeysPresent test fails, the test was using `_KEY_FIELDS` directly — update those tests to use the new `_PROVIDER_CATEGORIES` shape (read the test file to identify offenders). Most tests should pass unchanged because they exercise the env→keyring resolution logic which still works the same way.

- [ ] **Step 6.7: Run full test suite**

Run: `py -3.13 -m pytest -q`
Expected: 244/244 passed (was 239 + 5 new)

- [ ] **Step 6.8: Commit**

```bash
git add settings_dialog.py tests/test_settings_dialog.py
git commit -m "refactor(settings): replace _KEY_FIELDS flat list with _PROVIDER_CATEGORIES data model"
```

---

## Task 7: `settings_dialog.py` — render new dialog layout

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\settings_dialog.py` (replace `_build_ui` method body, around lines 107-154)
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_settings_dialog.py` (append `TestSettingsDialogRender` class)

**Why:** Build the visual layout matching the ASCII mockup in `streamed-tumbling-sunbeam.md`: privacy line + 3 row groups (Provider dropdown + API key field + "Get key →" button) per category + reveal checkbox + Save/Cancel buttons. ~50 LOC.

- [ ] **Step 7.1: Write failing render tests**

Append to `tests/test_settings_dialog.py`:

```python
class TestSettingsDialogRender:
    """Verify the dialog renders the expected widgets in the expected
    structure. Inspects internal state (self._dropdowns, self._key_inputs,
    self._signup_buttons) rather than simulating user clicks — the
    `qapp` fixture provides a QApplication but no event loop runs."""

    def test_dialog_has_privacy_line(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        # Privacy line is the first QLabel after the title/icon — content match
        from PyQt6.QtWidgets import QLabel
        labels = [w for w in dlg.findChildren(QLabel)]
        privacy_texts = [
            l.text() for l in labels
            if "encrypted" in l.text() or "telemetry" in l.text()
        ]
        assert len(privacy_texts) >= 1, "Privacy line not rendered"
        privacy = privacy_texts[0]
        assert "no server" in privacy.lower() or "no telemetry" in privacy.lower()

    def test_dialog_has_three_dropdowns(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        assert set(dlg._dropdowns.keys()) == {"LLM", "STT", "TTS"}

    def test_dialog_has_three_key_inputs(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        assert set(dlg._key_inputs.keys()) == {"LLM", "STT", "TTS"}

    def test_dialog_has_three_signup_buttons(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        assert set(dlg._signup_buttons.keys()) == {"LLM", "STT", "TTS"}

    def test_tts_dropdown_has_two_options(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        tts_dropdown = dlg._dropdowns["TTS"]
        items = [tts_dropdown.itemText(i) for i in range(tts_dropdown.count())]
        assert items == ["Cartesia", "ElevenLabs"]

    def test_llm_dropdown_has_one_option(self, qapp, mocker):
        mocker.patch("settings_dialog.keyring.get_password", return_value=None)
        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        llm_dropdown = dlg._dropdowns["LLM"]
        assert llm_dropdown.count() == 1
        assert llm_dropdown.itemText(0) == "Anthropic"
```

- [ ] **Step 7.2: Run tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_settings_dialog.py::TestSettingsDialogRender -v`
Expected: 6 FAIL with `AttributeError: 'SettingsDialog' object has no attribute '_dropdowns'`

- [ ] **Step 7.3: Replace `_build_ui` with the new layout**

In `settings_dialog.py`:

Update imports (top of file):

```python
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
```

Replace the entire `_build_ui` method (and remove the `_inputs` initialization in `__init__` — replace with the new triple of dicts):

In `__init__`, replace:

```python
        self._inputs: dict[str, QLineEdit] = {}
        self._build_ui()
```

with:

```python
        self._dropdowns: dict[str, QComboBox] = {}
        self._key_inputs: dict[str, QLineEdit] = {}
        self._signup_buttons: dict[str, QPushButton] = {}
        self._build_ui()
```

Replace the `_build_ui` method (around lines 107-154) with:

```python
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        privacy = QLabel(
            "🔒 Stored locally, encrypted via Windows Credential Manager. "
            "No server, no telemetry."
        )
        privacy.setWordWrap(True)
        privacy.setStyleSheet("color: gray; padding-bottom: 4px;")
        outer.addWidget(privacy)

        for category in _PROVIDER_CATEGORIES:
            category_widget = self._build_category_row(category)
            outer.addWidget(category_widget)

        self._reveal = QCheckBox("Show keys in plain text (paste-verify)")
        self._reveal.toggled.connect(self._on_reveal_toggled)
        outer.addWidget(self._reveal)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        outer.addWidget(self._buttons)
        self._update_save_enabled()

    def _build_category_row(self, category: "_ProviderCategory") -> QWidget:
        """Build one (label + dropdown + Get-key + key-field) row group."""
        from config import resolve_setting

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 4, 0, 8)

        label = QLabel(f"<b>{category.label}</b>")
        v.addWidget(label)

        # Resolve currently-selected provider for this category
        selected_provider_id = resolve_setting(
            f"{category.category_key}_PROVIDER",
            default=category.providers[category.default_index].provider_id,
        )
        try:
            selected_index = next(
                i for i, p in enumerate(category.providers)
                if p.provider_id == selected_provider_id
            )
        except StopIteration:
            selected_index = category.default_index

        # Provider dropdown + Get-key button on one horizontal row
        h = QHBoxLayout()
        dropdown = QComboBox()
        for provider in category.providers:
            dropdown.addItem(provider.display_name, provider.provider_id)
        dropdown.setCurrentIndex(selected_index)
        dropdown.currentIndexChanged.connect(
            lambda idx, c=category: self._on_provider_changed(c, idx)
        )
        self._dropdowns[category.category_key] = dropdown
        h.addWidget(dropdown, stretch=1)

        signup_button = QPushButton("Get key →")
        signup_button.clicked.connect(
            lambda _checked=False, c=category: self._on_signup_clicked(c)
        )
        self._signup_buttons[category.category_key] = signup_button
        h.addWidget(signup_button)
        v.addLayout(h)

        # API key field
        key_input = QLineEdit()
        key_input.setEchoMode(QLineEdit.EchoMode.Password)
        key_input.textChanged.connect(self._update_save_enabled)
        self._key_inputs[category.category_key] = key_input
        v.addWidget(key_input)

        # Pre-populate the key field with masked existing value (if any)
        self._refresh_key_field_for_category(category)

        return container

    def _refresh_key_field_for_category(self, category: "_ProviderCategory") -> None:
        """Read the keyring slot for the dropdown's currently-selected
        provider in this category, set the key field's text + placeholder
        accordingly. Called on dialog construction AND on dropdown change."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        existing = keyring.get_password(KEYRING_SERVICE, provider.api_key_env_var) or ""
        key_input = self._key_inputs[category.category_key]
        key_input.setText(existing)
        key_input.setPlaceholderText(
            _mask(existing) if existing else f"paste {provider.api_key_env_var} here"
        )

    def _on_provider_changed(self, category: "_ProviderCategory", _index: int) -> None:
        """Dropdown changed — swap the key field's contents to the newly-
        selected provider's stored key + update the placeholder. Also
        update the Save-enabled state."""
        self._refresh_key_field_for_category(category)
        self._update_save_enabled()

    def _on_signup_clicked(self, category: "_ProviderCategory") -> None:
        """User clicked 'Get key →' button — open the selected provider's
        signup URL in the default browser via QDesktopServices."""
        dropdown = self._dropdowns[category.category_key]
        provider = category.providers[dropdown.currentIndex()]
        QDesktopServices.openUrl(QUrl(provider.signup_url))

    def _on_reveal_toggled(self, checked: bool) -> None:
        mode = (
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        for key_input in self._key_inputs.values():
            key_input.setEchoMode(mode)

    def _update_save_enabled(self) -> None:
        """Save enabled when every category's key field has non-empty content."""
        all_filled = all(
            key_input.text().strip()
            for key_input in self._key_inputs.values()
        )
        self._buttons.button(
            QDialogButtonBox.StandardButton.Save
        ).setEnabled(all_filled)
```

- [ ] **Step 7.4: Run render tests to verify they pass**

Run: `py -3.13 -m pytest tests/test_settings_dialog.py::TestSettingsDialogRender -v`
Expected: 6 PASS

- [ ] **Step 7.5: Run full test suite**

Run: `py -3.13 -m pytest -q`
Expected: all green (sprint exit ~250 tests when all dialog tasks done)

- [ ] **Step 7.6: Commit**

```bash
git add settings_dialog.py tests/test_settings_dialog.py
git commit -m "feat(settings): render dialog with 3 provider-dropdown rows + privacy line + Get key buttons"
```

---

## Task 8: `settings_dialog` — dropdown change handler swaps key field

**Files:**
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_settings_dialog.py` (append `TestSettingsDialogDropdownSwap` class)
- Implementation: already in Task 7's `_on_provider_changed` + `_refresh_key_field_for_category` — this task **verifies** the behavior with deeper tests

**Why:** This is the core UX risk. If the dropdown doesn't correctly rebind the key field's keyring slot, the user could save the Cartesia key into the ELEVENLABS slot. Silent BYOK breakage.

- [ ] **Step 8.1: Write failing tests for dropdown swap behavior**

Append to `tests/test_settings_dialog.py`:

```python
class TestSettingsDialogDropdownSwap:
    """Switching the TTS dropdown from Cartesia → ElevenLabs must:
    (a) update the key field's placeholder to mention ELEVENLABS_API_KEY
    (b) load the existing ElevenLabs key from keyring (if any)
    (c) NOT carry the previously-displayed Cartesia key into the field
    """

    def test_switching_provider_loads_new_providers_existing_key(
        self, qapp, mocker, monkeypatch
    ):
        # Pre-populate keyring with both Cartesia and ElevenLabs keys
        store = {
            ("clicky-windows", "CARTESIA_API_KEY"): "sk_car_existing",
            ("clicky-windows", "ELEVENLABS_API_KEY"): "eleven_existing",
        }
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: store.get((service, name)),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()

        # Initially TTS dropdown selects Cartesia → key field shows that key
        tts_input = dlg._key_inputs["TTS"]
        assert tts_input.text() == "sk_car_existing"

        # Switch dropdown to ElevenLabs (index 1)
        dlg._dropdowns["TTS"].setCurrentIndex(1)

        # Key field now shows the ElevenLabs key
        assert tts_input.text() == "eleven_existing"

    def test_switching_provider_with_no_existing_key_clears_field(
        self, qapp, mocker, monkeypatch
    ):
        store = {
            ("clicky-windows", "CARTESIA_API_KEY"): "sk_car_existing",
            # No ElevenLabs key stored
        }
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: store.get((service, name)),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        tts_input = dlg._key_inputs["TTS"]
        assert tts_input.text() == "sk_car_existing"

        dlg._dropdowns["TTS"].setCurrentIndex(1)

        # No previous ElevenLabs key — field cleared
        assert tts_input.text() == ""
        # Placeholder mentions the new env-var name
        assert "ELEVENLABS_API_KEY" in tts_input.placeholderText()
```

- [ ] **Step 8.2: Run tests to verify they pass**

The implementation was already done in Task 7. The new tests should immediately PASS (assuming Task 7's `_on_provider_changed` + `_refresh_key_field_for_category` work correctly). If a test fails, that's a bug in Task 7's implementation — fix it now.

Run: `py -3.13 -m pytest tests/test_settings_dialog.py::TestSettingsDialogDropdownSwap -v`
Expected: 2 PASS

- [ ] **Step 8.3: Run full test suite**

Run: `py -3.13 -m pytest -q`
Expected: all green

- [ ] **Step 8.4: Commit**

```bash
git add tests/test_settings_dialog.py
git commit -m "test(settings): add dropdown-swap key-field rebind regression tests"
```

---

## Task 9: `settings_dialog._on_save` persists provider + key

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\settings_dialog.py` (replace existing `_on_save`)
- Test: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\tests\test_settings_dialog.py` (append `TestSettingsDialogSave` class)

**Why:** Save must persist BOTH the dropdown selection (e.g. `LLM_PROVIDER=anthropic`) AND the key field contents (e.g. `ANTHROPIC_API_KEY=sk-...`) for each category. ~10 LOC change.

- [ ] **Step 9.1: Write failing tests for Save persistence**

Append to `tests/test_settings_dialog.py`:

```python
class TestSettingsDialogSave:
    """Save persists (a) the selected provider per category as
    {LLM,STT,TTS}_PROVIDER in keyring, AND (b) the API key field's
    contents to that provider's keyring slot."""

    def test_save_persists_provider_selection_to_keyring(
        self, qapp, mocker, monkeypatch
    ):
        saved: dict[tuple[str, str], str] = {}
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: None,
        )
        monkeypatch.setattr(
            "settings_dialog.keyring.set_password",
            lambda service, name, value: saved.update({(service, name): value}),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        # Switch TTS to ElevenLabs and enter a key
        dlg._dropdowns["TTS"].setCurrentIndex(1)
        dlg._key_inputs["LLM"].setText("sk-llm-key")
        dlg._key_inputs["STT"].setText("stt-key")
        dlg._key_inputs["TTS"].setText("eleven-key")

        dlg._on_save()

        assert saved[("clicky-windows", "LLM_PROVIDER")] == "anthropic"
        assert saved[("clicky-windows", "STT_PROVIDER")] == "assemblyai"
        assert saved[("clicky-windows", "TTS_PROVIDER")] == "elevenlabs"
        assert saved[("clicky-windows", "ANTHROPIC_API_KEY")] == "sk-llm-key"
        assert saved[("clicky-windows", "ASSEMBLYAI_API_KEY")] == "stt-key"
        assert saved[("clicky-windows", "ELEVENLABS_API_KEY")] == "eleven-key"

    def test_save_only_persists_to_currently_selected_providers_slot(
        self, qapp, mocker, monkeypatch
    ):
        """If TTS dropdown is on Cartesia, save MUST write to
        CARTESIA_API_KEY, NOT ELEVENLABS_API_KEY."""
        saved: dict[tuple[str, str], str] = {}
        monkeypatch.setattr(
            "settings_dialog.keyring.get_password",
            lambda service, name: None,
        )
        monkeypatch.setattr(
            "settings_dialog.keyring.set_password",
            lambda service, name, value: saved.update({(service, name): value}),
        )

        from settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        # Stay on Cartesia (default index 0)
        dlg._key_inputs["LLM"].setText("a")
        dlg._key_inputs["STT"].setText("a")
        dlg._key_inputs["TTS"].setText("sk_car_value")
        dlg._on_save()

        assert ("clicky-windows", "CARTESIA_API_KEY") in saved
        assert ("clicky-windows", "ELEVENLABS_API_KEY") not in saved
```

- [ ] **Step 9.2: Run tests to verify they fail**

Run: `py -3.13 -m pytest tests/test_settings_dialog.py::TestSettingsDialogSave -v`
Expected: 2 FAIL — current `_on_save` only persists from `self._inputs` (which doesn't exist anymore) and doesn't persist provider selection at all.

- [ ] **Step 9.3: Replace `_on_save` with the new persistence logic**

In `settings_dialog.py`, replace the existing `_on_save` method:

```python
    def _on_save(self) -> None:
        """Persist provider selection + currently-selected provider's key
        for each category to keyring."""
        for category in _PROVIDER_CATEGORIES:
            dropdown = self._dropdowns[category.category_key]
            provider = category.providers[dropdown.currentIndex()]

            # 1. Persist provider selection (e.g. "TTS_PROVIDER" → "elevenlabs")
            keyring.set_password(
                KEYRING_SERVICE,
                f"{category.category_key}_PROVIDER",
                provider.provider_id,
            )

            # 2. Persist the API key for the selected provider
            key_value = self._key_inputs[category.category_key].text().strip()
            if key_value:
                keyring.set_password(
                    KEYRING_SERVICE, provider.api_key_env_var, key_value,
                )
        self.accept()
```

- [ ] **Step 9.4: Run save tests to verify they pass**

Run: `py -3.13 -m pytest tests/test_settings_dialog.py::TestSettingsDialogSave -v`
Expected: 2 PASS

- [ ] **Step 9.5: Run full test suite**

Run: `py -3.13 -m pytest -q`
Expected: all green

- [ ] **Step 9.6: Commit**

```bash
git add settings_dialog.py tests/test_settings_dialog.py
git commit -m "feat(settings): Save persists provider selection + selected provider's key per category"
```

---

## Task 10: `requirements.txt` + `clicky.spec` + smoke verify

**Files:**
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\requirements.txt`
- Modify: `c:\Users\Abhis\OneDrive\Documents\Clicky Windows\clicky.spec`

**Why:** Pull the elevenlabs SDK into the dev env + ensure it bundles into the PyInstaller EXE.

- [ ] **Step 10.1: Add elevenlabs to requirements.txt**

Append to `requirements.txt` (after `cartesia>=1.0.0`):

```
elevenlabs>=2.0           # Streaming TTS (Flash v2.5, ~75ms model TTFB) — opt-in alternative to Cartesia, Sprint 4
```

- [ ] **Step 10.2: Add elevenlabs to clicky.spec hidden imports**

In `clicky.spec`, find the `hiddenimports=[...]` block (around line 55-86) and add `"elevenlabs",` next to the other SDK entries:

```python
        # SDK deps — explicit so PyInstaller doesn't miss them
        "anthropic",
        "openai",
        "cartesia",
        "elevenlabs",  # Sprint 4 — opt-in alternative TTS
        "assemblyai",
```

- [ ] **Step 10.3: Install elevenlabs SDK locally**

Run: `py -3.13 -m pip install -r requirements.txt`
Expected: elevenlabs installs cleanly (typically pulls httpx + websocket-client transitively which are already deps).

- [ ] **Step 10.4: Smoke-verify SDK imports**

Run: `py -3.13 -c "from elevenlabs import ElevenLabs; print('SDK OK', ElevenLabs)"`
Expected: `SDK OK <class 'elevenlabs.client.ElevenLabs'>` (or equivalent)

- [ ] **Step 10.5: Run full test suite to confirm imports OK**

Run: `py -3.13 -m pytest -q`
Expected: all green (no test will fail due to missing elevenlabs module — DI mocks meant tests didn't need real SDK installed; but now that it's installed, `from elevenlabs import ElevenLabs` inside `_build_client` will work for the integration paths)

- [ ] **Step 10.6: Commit**

```bash
git add requirements.txt clicky.spec
git commit -m "chore(deps): add elevenlabs>=2.0 + clicky.spec hidden import"
```

---

## Task 11: Manual dev-mode gate — `py -3.13 -m app`

**No code changes — manual verification.**

- [ ] **Step 11.1: Run app in dev mode**

Run: `py -3.13 -m app`
Expected: Tray icon appears. Modal does NOT appear (assuming you have keys in keyring from prior sessions).

- [ ] **Step 11.2: Open Settings... from tray**

Right-click tray icon → Settings...

Verify the dialog matches the ASCII mockup:
- Privacy line at top: "🔒 Stored locally, encrypted via Windows Credential Manager. No server, no telemetry."
- 3 row groups: LLM (Anthropic dropdown + Get key → button + key field), STT (AssemblyAI), TTS (Cartesia/ElevenLabs)
- "Show keys in plain text" checkbox at bottom
- Save / Cancel buttons

- [ ] **Step 11.3: Test dropdown change behavior on TTS row**

In TTS row dropdown, switch Cartesia → ElevenLabs.
Verify: API key field clears (no existing ElevenLabs key in keyring yet) AND placeholder updates to "paste ELEVENLABS_API_KEY here".

- [ ] **Step 11.4: Test "Get key →" button**

Click "Get key →" next to TTS dropdown (with ElevenLabs selected).
Verify: default browser opens to https://elevenlabs.io/app/sign-up.

- [ ] **Step 11.5: Save with ElevenLabs key**

Paste your ElevenLabs API key in the TTS field. Click Save.
Verify: dialog closes. Tray Settings notification appears: "Settings saved. Restart Clicky for new keys to take effect."

- [ ] **Step 11.6: Restart Clicky + test ElevenLabs voice**

Right-click tray → Quit. Wait. `py -3.13 -m app` again.
Press Ctrl+Alt+Space, ask a question, release.
Verify: voice response is audibly Rachel (ElevenLabs), NOT Brooke (Cartesia). Sample rate is 22050 (slightly lower-fi than Cartesia's 44100 — that's expected on free tier).

- [ ] **Step 11.7: Switch back to Cartesia + verify**

Open Settings, switch TTS dropdown to Cartesia, Save, restart, PTT.
Verify: voice is Brooke again. Roundtrip works in both directions.

If any step fails, STOP and diagnose before proceeding to Task 12. Common failures:
- Dialog doesn't show (keyring auth issue) → check resolve_setting + resolve_api_key
- Dropdown change doesn't update field → check `_on_provider_changed` signal connection
- Save doesn't persist → check `_on_save` keyring writes
- Voice doesn't switch → check main-block `_resolve_tts_credentials` + `create_tts_client` factory dispatch

---

## Task 12: `/review` gate + apply review fixes + bundle + Setup.exe + commit + push

**Files:** All Sprint 4 changes (uncommitted batch).

**Why:** Same Boris #5 gate that caught the T1.1 mock-fidelity issue in Sprint 3.8. Catches Qt API mistakes + ctypes-style gotchas + threading semantics drift.

- [ ] **Step 12.1: Run `/review` (superpowers:code-reviewer) on the diff since `e457905`**

Invoke `superpowers:code-reviewer` agent on the full Sprint 4 diff. Provide:
- Bug context: Sprint 4 = multi-provider settings UX + ElevenLabs TTS subclass
- Files: settings_dialog.py, config.py, tts.py, app.py, requirements.txt, clicky.spec, plus 4 test files
- What to check (high-priority): threading semantics in ElevenLabsTTS (5-pronged stop, epoch-guarded prefetch), int16→float32 buffer math, dropdown signal connection lifetime, Save persistence ordering (provider before key or vice versa? consistent?), keyring race conditions during Save
- Tier the findings into T1 (must fix), T2 (defer), T3 (nit)

- [ ] **Step 12.2: Apply T1 review fixes**

If reviewer flags T1 issues, fix them inline. Re-run pytest to confirm green. Commit each fix as `fix(<scope>): <issue>` per the Sprint 3.8 precedent.

- [ ] **Step 12.3: Rebuild PyInstaller bundle**

Run (background): `rm -rf build/ dist/ && py -3.13 -m PyInstaller clicky.spec --noconfirm --clean`
Expected: ~2-5 min wall-clock. `dist/Clicky/Clicky.exe` (~15 MB) + `dist/Clicky/_internal/` (~270 MB total). Bundle size may grow ~5-10 MB from elevenlabs SDK addition.

- [ ] **Step 12.4: Verify bundle smoke launch**

Run (foreground, briefly): `dist/Clicky/Clicky.exe`
Expected: process stays alive >3 sec (no startup ImportError). If it dies immediately: probably elevenlabs hidden import missing — re-check `clicky.spec`. Right-click tray → Quit cleanly.

- [ ] **Step 12.5: Recompile Inno Setup installer**

Run: `"C:\Users\Abhis\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer/clicky.iss`
Expected: ~1 min. Output `installer/Output/Clicky-Windows-Setup-v0.1.0.exe` ~85-90 MB.

- [ ] **Step 12.6: Final pytest run**

Run: `py -3.13 -m pytest -q`
Expected: all green (~250 tests). Test count must be at or above 238 (target from plan; some tasks may add more depending on review fixes).

- [ ] **Step 12.7: Squash-merge or chain commits + push**

The TDD task structure produced ~10 commits. They can ship as-is (one commit per logical task — matches Path A precedent of one-commit-per-task) OR be squashed into a single `feat(tts): multi-provider settings UX + ElevenLabs subclass` umbrella commit if cleaner history is preferred.

Run: `git log --oneline -n 15` to review the commit chain. Decide squash-or-keep with USER if uncertain.

Run: `git push origin main`
Expected: push succeeds, `git log --oneline -n 1` matches the latest local commit.

- [ ] **Step 12.8: USER manual acceptance gate (post-Setup.exe install)**

Hand to USER: install the new Setup.exe on a test machine (or fresh VM), repeat the Task 11 manual gate steps end-to-end. Specifically:
- Confirm the dropdown UX matches the mockup
- Confirm switching TTS provider + Save + restart actually swaps the audible voice
- Confirm tray menu still has exactly 4 items (no TTS Provider submenu)
- Confirm privacy line is visible and not too loud
- Confirm "Get key →" buttons open the right signup URLs

If acceptance passes, Sprint 4 ships and Sprint 4.7 doc-sync becomes the next pending task.

---

## Summary of Test Count Progression

| After Task | Test count | Source |
|---|---|---|
| Pre-Sprint 4 baseline | 223 | Sprint 3.8 final |
| Task 1 (resolve_setting) | 228 (+5) | TestResolveSetting class |
| Task 2 (constants) | 228 (+0) | constants don't need tests |
| Task 3 (ElevenLabsTTS) | 233 (+5) | TestElevenLabsTTSSpeak/Sentence/Stop |
| Task 4 (factory) | 237 (+4) | TestCreateTTSClient |
| Task 5 (app dispatch) | 239 (+2) | TestTTSProviderDispatch |
| Task 6 (data model) | 244 (+5) | TestProviderCategoriesData |
| Task 7 (render) | 250 (+6) | TestSettingsDialogRender |
| Task 8 (dropdown swap) | 252 (+2) | TestSettingsDialogDropdownSwap |
| Task 9 (save) | 254 (+2) | TestSettingsDialogSave |

Sprint 4 final: ~254/254 tests (target was ~238; came in higher because dropdown UX warranted more granular coverage). Each commit produces a green-test increment.

---

## Locked decisions reference (do NOT re-litigate during execution)

Per USER answers 2026-05-06:
1. ONE provider per category at any time — no saved fallbacks
2. LLM dropdown shows Anthropic only — Gemini parked until benchmark improves
3. Lean privacy line — ONE sentence in dialog, no separate splash, no source-code link until Sprint 6
4. Tray menu stays at 4 items — NO TTS Provider submenu
5. Sprint 4 ships dropdown UX + ElevenLabs together — not staged
6. Deepgram STT parked for post-launch

## Out of scope (do NOT add to plan during execution)

- Deepgram, Gemini-in-dropdown, source-code link in privacy line
- First-launch privacy splash — explicitly rejected per USER lean preference
- Multi-key save (both Cartesia AND ElevenLabs persisted simultaneously)
- `--no-single-instance` flag, cross-platform port guard
- DECISIONS.md ADR / ROADMAP / MEMORY / project_phase1_current_state.md updates — batched into Sprint 4.7 post-merge doc-sync

---

## Self-Review (run before claiming plan complete)

**Spec coverage:** Each USER decision (1-6 above) maps to:
1. Save persists ONE provider per category → Task 9
2. LLM dropdown one option → Task 6 + Task 7
3. Privacy line one sentence → Task 7
4. Tray menu unchanged → covered by NOT modifying tray.py (Task absent — confirms no submenu added)
5. Dropdown + ElevenLabs together → Tasks 3-9 ship in same sprint
6. No Deepgram → not in plan ✓

**Placeholder scan:** Each step contains exact code, exact paths, exact commands, exact expected output. No "TBD", no "implement later", no "similar to Task N" without code repeat.

**Type consistency:** Across tasks:
- `_PROVIDER_CATEGORIES` (Task 6) → consumed by Task 7 (`_build_category_row` iterates), Task 8 (lookup by `category_key`), Task 9 (iterate for save)
- `_dropdowns` / `_key_inputs` / `_signup_buttons` dicts keyed by `category_key` ("LLM"/"STT"/"TTS") — consistent across Tasks 7-9
- `_resolve_tts_credentials()` returns `(str, str | None)` — Task 5 test asserts both element types
- `create_tts_client(provider, api_key)` — Task 4 implements, Task 5 calls in main block

All consistent.

---

## Execution Choice (next step)

Plan complete and saved to `docs/superpowers/plans/2026-05-06-sprint-4-multi-provider.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for the cleaner commit history and the per-task review checkpoints.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review at meaningful milestones (e.g. after Task 5 = "all backend done", after Task 9 = "all UI done", before Task 12 = "ship gate").

Which approach?
