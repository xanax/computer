"""Text-to-speech using voices already installed on the machine.

This is the zero-configuration TTS path: no API key, no network call. It is used
when ``audio.tts_provider`` is ``native`` so assistant replies can be spoken on a
machine that has no provider account.

Backends are probed in order of preference:

* Windows SAPI (``System.Speech``) -- reached through WSL interop when the server
  runs in WSL. A single PowerShell worker is kept alive and fed one JSON request
  per line, because process start-up costs about a second while synthesis itself
  is much faster than real time.
* macOS ``say``.
* Linux ``espeak-ng`` / ``espeak``.

All backends return WAV bytes, which is what the audio router caches and what the
browser plays back.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# Windows PowerShell 5.1 ships with the .NET Framework that System.Speech lives
# in, so it is preferred over PowerShell 7.
_POWERSHELL_CANDIDATES = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/Program Files/PowerShell/7/pwsh.exe",
)

_SAY_CANDIDATES = ("/usr/bin/say", "/bin/say")
_ESPEAK_CANDIDATES = ("espeak-ng", "espeak")

# Replaced with the real output path when a command-line engine is invoked.
OUT_PLACEHOLDER = "{out}"

# Kept as one script so the persistent worker and the one-shot fallback produce
# identical audio settings. Text arrives base64 encoded to avoid any dependence on
# the console code page.
_SAPI_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    22050,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$dir = Join-Path $env:TEMP 'cptr-tts'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Write-Output ('READY ' + $dir)
while ($true) {
    $line = [Console]::In.ReadLine()
    if ($null -eq $line) { break }
    if ($line.Trim().Length -eq 0) { continue }
    try {
        $req = ConvertFrom-Json $line
        $text = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String($req.text))
        $out = Join-Path $dir ($req.id + '.wav')
        if ($req.voice) {
            try { $synth.SelectVoice($req.voice) } catch { }
        }
        $synth.SetOutputToWaveFile($out, $fmt)
        $synth.Speak($text)
        $synth.SetOutputToNull()
        Write-Output ('OK ' + $req.id)
    } catch {
        $msg = [System.Convert]::ToBase64String(
            [System.Text.Encoding]::UTF8.GetBytes($_.Exception.Message))
        Write-Output ('ERR ' + $msg)
    }
}
"""

_SAPI_ONESHOT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    22050,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($env:CPTR_TTS_TEXT))
if ($env:CPTR_TTS_VOICE) {
    try { $synth.SelectVoice($env:CPTR_TTS_VOICE) } catch { }
}
$synth.SetOutputToWaveFile($env:CPTR_TTS_OUT, $fmt)
$synth.Speak($text)
$synth.SetOutputToNull()
"""


class NativeTtsError(RuntimeError):
    """Native speech synthesis failed and the caller should report it."""


def _encoded_command(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode()


def _powershell_exe() -> str | None:
    for candidate in _POWERSHELL_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def _win_to_wsl(path: str) -> Path | None:
    """Map ``C:\\dir\\file.wav`` to ``/mnt/c/dir/file.wav``. Returns None if not absolute."""
    cleaned = path.strip().strip('"')
    if len(cleaned) < 3 or cleaned[1] != ":":
        return None
    return Path(f"/mnt/{cleaned[0].lower()}{cleaned[2:].replace(chr(92), '/')}")


_backend_cache: tuple[str | None, str | None] | None = None


def available_backend() -> tuple[str | None, str | None]:
    """Return ``(backend, executable)`` for the first usable native TTS engine."""
    global _backend_cache
    if _backend_cache is None:
        exe = _powershell_exe()
        if exe:
            _backend_cache = ("windows-sapi", exe)
        elif (say := shutil.which("say")) or any(os.path.exists(p) for p in _SAY_CANDIDATES):
            _backend_cache = ("macos-say", say or "/usr/bin/say")
        elif (espeak := shutil.which("espeak-ng")) or (espeak := shutil.which("espeak")):
            _backend_cache = ("espeak", espeak)
        else:
            _backend_cache = (None, None)
        logger.info("[native-tts] backend=%s exe=%s", _backend_cache[0], _backend_cache[1])
    return _backend_cache


def is_available() -> bool:
    return available_backend()[0] is not None


class _SapiWorker:
    """A single long-lived PowerShell process that synthesizes WAV files on demand."""

    def __init__(self, exe: str) -> None:
        self._exe = exe
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._output_dir: Path | None = None

    async def _start(self) -> None:
        proc = await asyncio.create_subprocess_exec(
            self._exe,
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            _encoded_command(_SAPI_SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        ready = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
        text = ready.decode("utf-8", "replace").strip()
        if not text.startswith("READY "):
            proc.kill()
            raise NativeTtsError(f"native speech engine did not start: {text or 'no output'}")
        output_dir = _win_to_wsl(text[len("READY ") :])
        if output_dir is None:
            proc.kill()
            raise NativeTtsError("native speech engine reported an unusable output directory")
        self._proc = proc
        self._output_dir = output_dir
        logger.info("[native-tts] windows worker ready, output dir %s", output_dir)

    async def _ensure_started(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            await self._start()

    async def synthesize(self, text: str, voice: str | None) -> bytes:
        async with self._lock:
            await self._ensure_started()
            proc = self._proc
            assert proc is not None and proc.stdin is not None and proc.stdout is not None
            assert self._output_dir is not None

            request_id = uuid.uuid4().hex
            payload = json.dumps(
                {
                    "id": request_id,
                    "text": base64.b64encode(text.encode("utf-8")).decode(),
                    "voice": voice or "",
                }
            )
            try:
                proc.stdin.write((payload + "\n").encode("utf-8"))
                await proc.stdin.drain()
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=300)
            except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError) as exc:
                # The worker died mid-request. Drop it so the next call restarts cleanly.
                await self._stop()
                raise NativeTtsError(f"native speech engine failed: {exc}") from exc

            reply = line.decode("utf-8", "replace").strip()
            audio_path = self._output_dir / f"{request_id}.wav"
            if reply.startswith("OK "):
                try:
                    data = await asyncio.to_thread(audio_path.read_bytes)
                except OSError as exc:
                    raise NativeTtsError(f"native speech output missing: {exc}") from exc
                finally:
                    audio_path.unlink(missing_ok=True)
                if not data:
                    raise NativeTtsError("native speech engine returned empty audio")
                return data
            if reply.startswith("ERR "):
                try:
                    detail = base64.b64decode(reply[4:]).decode("utf-8", "replace")
                except Exception:
                    detail = reply
                raise NativeTtsError(detail.strip() or "native speech synthesis failed")
            await self._stop()
            raise NativeTtsError(f"unexpected native speech reply: {reply or 'none'}")

    async def _stop(self) -> None:
        proc, self._proc = self._proc, None
        self._output_dir = None
        if proc is None:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass


_worker: _SapiWorker | None = None
_worker_lock = asyncio.Lock()


async def _synthesize_windows(exe: str, text: str, voice: str | None) -> bytes:
    global _worker
    async with _worker_lock:
        if _worker is None:
            _worker = _SapiWorker(exe)
    try:
        return await _worker.synthesize(text, voice)
    except NativeTtsError as exc:
        logger.warning("[native-tts] persistent worker failed (%s), retrying once", exc)
    # One retry, then a one-shot process, so a wedged worker does not lose the reply.
    try:
        return await _worker.synthesize(text, voice)
    except NativeTtsError:
        return await _synthesize_windows_oneshot(exe, text, voice)


async def _synthesize_windows_oneshot(exe: str, text: str, voice: str | None) -> bytes:
    tmp_dir = Path(tempfile.mkdtemp(prefix="cptr-tts-"))
    out = tmp_dir / "speech.wav"
    win_out = _wsl_to_win(str(out))
    if win_out is None:
        raise NativeTtsError("native speech output path is not reachable from Windows")
    env = {
        **os.environ,
        "CPTR_TTS_TEXT": base64.b64encode(text.encode("utf-8")).decode(),
        "CPTR_TTS_VOICE": voice or "",
        "CPTR_TTS_OUT": win_out,
    }
    proc = await asyncio.create_subprocess_exec(
        exe,
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        _encoded_command(_SAPI_ONESHOT),
        env=env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    try:
        data = out.read_bytes()
    except OSError as exc:
        detail = (stderr or b"").decode("utf-8", "replace").strip()
        raise NativeTtsError(detail or f"native speech synthesis failed: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    if not data:
        raise NativeTtsError("native speech engine returned empty audio")
    return data


def _wsl_to_win(path: str) -> str | None:
    if path.startswith("/mnt/") and len(path) > 6:
        drive = path[5]
        rest = path[6:].replace("/", "\\")
        return f"{drive.upper()}:{rest}"
    return None


async def _run_to_wav(exe: str, args: list[str]) -> bytes:
    """Run a command-line speech engine that writes WAV to the ``OUT_PLACEHOLDER`` arg."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="cptr-tts-"))
    out = tmp_dir / "speech.wav"
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            *[str(out) if a == OUT_PLACEHOLDER else a for a in args],
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            detail = (stderr or b"").decode("utf-8", "replace").strip()
            raise NativeTtsError(detail or "native speech synthesis failed")
        data = out.read_bytes()
        if not data:
            raise NativeTtsError("native speech engine returned empty audio")
        return data
    except FileNotFoundError as exc:
        raise NativeTtsError(f"native speech engine not found: {exc}") from exc
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# Names of the remote provider's hosted voices. The UI stores whichever voice was
# last used, so a machine switching from the API provider to this one would
# otherwise hand "alloy" to a local engine, where it is at best ignored and at
# worst a hard error. Treat those names as "no preference" and use the system
# default voice instead.
_PROVIDER_VOICE_NAMES = frozenset(
    {
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
    }
)


def _local_voice_name(voice: str | None) -> str | None:
    voice = (voice or "").strip()
    if not voice or voice.lower() in _PROVIDER_VOICE_NAMES:
        return None
    return voice


async def synthesize(text: str, voice: str | None = None) -> bytes:
    """Render ``text`` to WAV bytes with the machine's own voices."""
    text = (text or "").strip()
    if not text:
        raise NativeTtsError("native speech needs non-empty text")

    voice = _local_voice_name(voice)
    backend, exe = available_backend()
    if backend is None or not exe:
        raise NativeTtsError("no native speech engine is available on this machine")

    if backend == "windows-sapi":
        return await _synthesize_windows(exe, text, voice)
    if backend == "macos-say":
        args: list[str] = ["--data-format=LEI16@22050", "-o", OUT_PLACEHOLDER]
        if voice:
            args += ["-v", voice]
        args.append(text)
        return await _run_to_wav(exe, args)
    # espeak-ng / espeak take -w <file> and the spoken text last.
    espeak_args: list[str] = ["-w", OUT_PLACEHOLDER]
    if voice:
        espeak_args += ["-v", voice]
    espeak_args.append(text)
    return await _run_to_wav(exe, espeak_args)
