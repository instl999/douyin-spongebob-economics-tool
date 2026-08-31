"""Volcengine speech synthesis (seed-tts-2.0), on the Agent Plan key.

Agent Plan authenticates speech with a single `X-Api-Key` header holding the
same ark- key the director and image models use. This is worth stating plainly
because the older openspeech scheme - `X-Api-App-Key` plus `X-Api-Access-Key`,
issued separately from the speech console - is what most material describes,
and on this endpoint it fails with `45000010 grant not found` no matter which
combination you try. That error looks like a missing entitlement and is really
a wrong header.

Voices must match the resource: `seed-tts-2.0` speaks through the `_uranus_`
voice family. A `_moon_` voice from the earlier bigtts models is rejected with
`55000000 resource ID is mismatched with speaker related resource`.

The reply is newline-delimited JSON, one object per chunk, each carrying a
slice of base64 mp3, and terminated by an object with code 20000000.

When no key is configured, synth() writes silence of an estimated length and
reports degraded=True, so a full run still completes and every downstream stage
can be exercised.
"""
import base64
import json
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import config

MIN_ESTIMATE = 1.2

DONE_CODE = 20000000

USAGE = {"clips": 0, "characters": 0, "retries": 0, "seconds": 0.0}


def reset_usage():
    for key in USAGE:
        USAGE[key] = 0 if key != "seconds" else 0.0


class TTSError(RuntimeError):
    pass


def estimate_duration(text, speed=1.0):
    """Length this narration would come back as, without synthesising it.

    Shares the fitted model in timing.py rather than carrying a second, worse
    guess: two estimators drift apart and then the plan and the render disagree
    about how long the video is.
    """
    import timing
    return max(MIN_ESTIMATE, timing.clip_seconds(text, speed))


def synth(text, out_path, speaker=None, speed=1.0, emotion=None, timeout=180):
    """Synthesize `text` to out_path (.mp3).

    Returns {"path", "duration", "words", "degraded"}. `words` is a list of
    {"word", "start", "end"} in seconds when the service supplies them, which
    it currently does not for this resource - the field comes back empty.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not config.have_tts():
        return _silent(text, out_path)

    audio_params = {"format": "mp3", "sample_rate": 24000,
                    "enable_timestamp": True}
    if speed and abs(float(speed) - 1.0) > 1e-6:
        audio_params["speech_rate"] = _rate(speed)
    if emotion:
        audio_params["emotion"] = emotion

    body = {
        "user": {"uid": "cartoon-video-pipeline"},
        "req_params": {
            "text": text,
            "speaker": speaker or config.TTS_SPEAKER,
            "audio_params": audio_params,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": config.TTS_KEY,
        "X-Api-Resource-Id": config.TTS_RESOURCE_ID,
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }
    import time as _time
    started = _time.time()
    audio, words = _post_with_retries(body, headers, timeout)
    USAGE["clips"] += 1
    USAGE["characters"] += len(text)
    USAGE["seconds"] += _time.time() - started
    if not audio:
        raise TTSError("service returned no audio")
    out_path.write_bytes(audio)
    return {"path": out_path, "duration": probe_duration(out_path),
            "words": words, "degraded": False}


# Retried the way ark.py retries. Measured on a 32-shot run, two calls died
# with `SSL: UNEXPECTED_EOF_WHILE_READING` - a transient network fault, not a
# rejection - and both shots came out silent. At six percent per call, most
# long videos would ship with holes in the narration.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4


def _post_with_retries(body, headers, timeout):
    import random
    import time
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(MAX_ATTEMPTS):
        # A fresh request id per attempt; the service dedupes on it.
        headers = dict(headers, **{"X-Api-Connect-Id": str(uuid.uuid4())})
        try:
            request = urllib.request.Request(
                config.TTS_URL, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return _consume(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            last = TTSError(f"{exc.code} {detail}")
            if exc.code not in RETRYABLE_STATUS:
                raise last from None          # a rejection will not improve
        except TTSError as exc:
            # A code in the body - a bad speaker, a mismatched resource. Also
            # permanent; retrying just spends the quota again.
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = TTSError(f"{type(exc).__name__}: {exc}")
        if attempt < MAX_ATTEMPTS - 1:
            USAGE["retries"] += 1
            time.sleep(min(2 ** attempt + random.random(), 12))
    raise last


def _rate(speed):
    """speech_rate is an integer percentage offset in [-50, 100]."""
    return max(-50, min(100, int(round((float(speed) - 1.0) * 100))))


def _consume(raw):
    """Collect mp3 bytes and any word timings out of the NDJSON reply."""
    chunks, words = [], []
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith(b"data:"):          # tolerate SSE framing
            line = line[5:].strip()
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = obj.get("code", (obj.get("header") or {}).get("code", 0))
        if code not in (0, None, DONE_CODE):
            message = obj.get("message") or (obj.get("header") or {}).get("message", "")
            raise TTSError(f"code {code}: {message}")
        payload = obj.get("data")
        if isinstance(payload, str) and payload:
            chunks.append(base64.b64decode(payload))
        words.extend(_words(obj.get("sentence")))
    return b"".join(chunks), words


def _words(sentence):
    """Pull word timing out of the sentence object, when it carries any."""
    if not isinstance(sentence, dict):
        return []
    out = []
    for w in sentence.get("words") or []:
        text = w.get("word") or w.get("text") or ""
        start, end = w.get("start_time", w.get("start")), w.get("end_time", w.get("end"))
        if not text or start is None or end is None:
            continue
        # Timings arrive in milliseconds on every deployment seen so far, but
        # the field is undocumented, so infer the unit rather than assume it.
        scale = 1000.0 if max(float(start), float(end)) > 1000 else 1.0
        out.append({"word": text, "start": float(start) / scale,
                    "end": float(end) / scale})
    return out


def _silent(text, out_path):
    """No credentials: a silent track of the estimated length keeps timing sane."""
    duration = estimate_duration(text)
    out_path = out_path.with_suffix(".wav")
    subprocess.run(
        [config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "anullsrc=r=24000:cl=mono", "-t", f"{duration:.3f}", str(out_path)],
        check=True)
    return {"path": out_path, "duration": duration, "words": [], "degraded": True}


def probe_duration(path):
    result = subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
