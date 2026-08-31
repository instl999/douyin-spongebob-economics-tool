"""Configuration: credentials, endpoints and model ids.

Values come from the environment, then from a .env file next to the skill root,
then from the defaults here. Real environment variables always win, so a single
run can be overridden with `export` without editing anything.

The endpoint facts below were verified against the live service; see
references/api-notes.md for the probe results behind each one.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    """Read KEY=VALUE lines from .env without overriding real env vars."""
    for candidate in (ROOT / ".env", Path.cwd() / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def _env(name, default=""):
    return os.environ.get(name, default).strip()


# --- Ark (Agent Plan) ------------------------------------------------------
# An Agent Plan key is routed through /api/plan/v3. The pay-as-you-go path
# /api/v3 rejects the same key with 401 AuthenticationError.
ARK_API_KEY = _env("ARK_API_KEY")
ARK_BASE_URL = _env("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3")

# seedream-5-0-lite is on the plan; seedream-4-0-* returns UnsupportedModel.
# The plan also enforces a minimum of 3,686,400 output pixels (2K).
ARK_IMAGE_MODEL = _env("ARK_IMAGE_MODEL", "doubao-seedream-5-0-lite")
ARK_TEXT_MODEL = _env("ARK_TEXT_MODEL", "doubao-seed-2.0-lite")

# --- Speech ---------------------------------------------------------------
# Agent Plan authenticates speech with the same ark- key, sent as X-Api-Key.
# The older openspeech scheme (X-Api-App-Key + X-Api-Access-Key from the speech
# console) fails on this endpoint with 45000010 "grant not found", which reads
# like a missing entitlement and is really the wrong header.
TTS_KEY = _env("VOLC_TTS_KEY") or ARK_API_KEY
TTS_RESOURCE_ID = _env("VOLC_TTS_RESOURCE_ID", "seed-tts-2.0")
TTS_URL = _env(
    "VOLC_TTS_URL",
    "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional",
)
# seed-tts-2.0 speaks through the _uranus_ voice family; a _moon_ voice from
# the earlier models is rejected as mismatched with the resource id.
TTS_SPEAKER = _env("VOLC_TTS_SPEAKER", "zh_male_yuanboxiaoshu_uranus_bigtts")


# --- Local tools ----------------------------------------------------------
FFMPEG = _env("FFMPEG_BIN", "ffmpeg")
FFPROBE = _env("FFPROBE_BIN", "ffprobe")


def have_ark():
    return bool(ARK_API_KEY)


def have_tts():
    return bool(TTS_KEY)


def describe():
    """One-line-per-capability readiness report, used by `build.py --check`."""
    rows = [
        ("Ark  (director + images)", have_ark(),
         "set ARK_API_KEY" if not have_ark() else ARK_BASE_URL),
        ("TTS  (narration)", have_tts(),
         "set ARK_API_KEY" if not have_tts()
         else f"{TTS_RESOURCE_ID} / {TTS_SPEAKER}"),
    ]
    return rows
