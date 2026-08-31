"""Volcengine Ark client: text (the director) and image generation.

Both endpoints live under the Agent Plan base url. Everything here retries on
the transient failures the service actually produces - 429, 5xx and read
timeouts - and raises ArkError with the server's own message otherwise, because
those messages are specific enough to act on (UnsupportedModel, InvalidParameter
on `size`, and so on).
"""
import json
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

import config

RETRYABLE = {408, 429, 500, 502, 503, 504}

# What this run has spent. Images dominate the bill and the wall clock, and a
# build that quietly regenerates a library because a prompt changed by one word
# is worth noticing before the invoice does it for you.
USAGE = {"images": 0, "text_calls": 0, "vision_calls": 0, "seconds": 0.0}


def reset_usage():
    for key in USAGE:
        USAGE[key] = 0 if key != "seconds" else 0.0


class ArkError(RuntimeError):
    pass


def _request(path, body, timeout=300, retries=4):
    if not config.ARK_API_KEY:
        raise ArkError("ARK_API_KEY is not set - copy .env.example to .env and fill it in")
    url = config.ARK_BASE_URL.rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {config.ARK_API_KEY}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                message = json.loads(raw)["error"]["message"]
            except Exception:
                message = raw[:400] or f"HTTP {exc.code}"
            last = ArkError(f"{exc.code} {message}")
            if exc.code not in RETRYABLE:
                raise last
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = ArkError(f"{type(exc).__name__}: {exc}")
        if attempt < retries:
            time.sleep(min(2 ** attempt + random.random(), 20))
    raise last


# --- text -----------------------------------------------------------------

def chat(messages, model=None, temperature=0.7, max_tokens=8192,
         json_object=False, thinking=False):
    """Return the assistant's message content as a string.

    doubao-seed-* are reasoning models and think at length by default: a single
    director call took over nine minutes and spent most of its tokens on
    reasoning that never reaches the storyboard. Thinking is therefore off
    unless a caller asks for it.
    """
    body = {
        "model": model or config.ARK_TEXT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": "enabled" if thinking else "disabled"},
    }
    if json_object:
        body["response_format"] = {"type": "json_object"}
    data = _request("/chat/completions", body, timeout=300)
    return data["choices"][0]["message"]["content"]


def chat_json(messages, model=None, temperature=0.7, max_tokens=8192,
              retries=2, thinking=False):
    """chat() that insists on parseable JSON, retrying on malformed output."""
    for attempt in range(retries + 1):
        text = chat(messages, model=model, temperature=temperature,
                    max_tokens=max_tokens, json_object=True, thinking=thinking)
        try:
            return json.loads(_strip_fence(text))
        except json.JSONDecodeError as exc:
            if attempt == retries:
                raise ArkError(f"model did not return valid JSON: {exc}\n{text[:800]}")
            messages = messages + [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content": f"That was not valid JSON ({exc}). "
                                            "Return the corrected JSON object only."},
            ]


def _strip_fence(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def read_image_text(image_path, question, model=None, max_tokens=120):
    """Ask the vision model what text is in an image.

    Used to check that generated lettering actually says what it should.
    doubao-seed-2.0-lite is multimodal on the plan; the dedicated vision models
    are not.
    """
    import base64
    data = base64.b64encode(Path(image_path).read_bytes()).decode()
    suffix = Path(image_path).suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return chat([{"role": "user", "content": [
        {"type": "text", "text": question},
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{data}"}}]}],
        model=model, temperature=0.0, max_tokens=max_tokens).strip()


# --- images ---------------------------------------------------------------

# The plan requires >= 3,686,400 pixels. These are the two shapes we ever need.
SIZE_LANDSCAPE = "2560x1440"
SIZE_PORTRAIT = "1440x2560"
SIZE_SQUARE = "1920x1920"


def generate_image(prompt, out_path, size=SIZE_LANDSCAPE, seed=None,
                   reference_images=None, timeout=300):
    """Generate one image and save it to out_path. Returns the Path."""
    body = {
        "model": config.ARK_IMAGE_MODEL,
        "prompt": prompt,
        "size": size,
        "response_format": "url",
        "watermark": False,
    }
    if seed is not None:
        body["seed"] = int(seed)
    if reference_images:
        # Ark accepts a single url/data-uri or a list, depending on model.
        body["image"] = reference_images if len(reference_images) > 1 else reference_images[0]
    data = _request("/images/generations", body, timeout=timeout)
    url = data["data"][0]["url"]
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _download(url, out_path)
    return out_path


def _download(url, out_path, retries=3):
    last = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                out_path.write_bytes(resp.read())
            return
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise ArkError(f"failed to download generated image: {last}")
