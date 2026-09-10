"""Footage retrieval: the beat needs a picture, the library has ten thousand.

The illustrated pipeline answers "what should this shot look like" by drawing
it. This module answers the same question by *finding* it, which is how the
live-action reference videos are actually built - macro fibre cinematography,
colourised archival street footage, a factory aisle shot on a real floor. None
of that is generated. All of it is licensed and retrieved.

Retrieval is a three-stage funnel, and each stage exists because the one before
it is not good enough alone:

  1. queries   the model turns a Chinese beat into English search queries.
               Stock libraries index in English, and - the part that actually
               matters - they index *what is visible*, not what is meant. As a
               translation, the beat about a bought ticket retrieves nothing
               usable. As a picture it is "person walking through heavy rain
               with umbrella, unhappy expression", and that retrieves the shot.

  2. search    the provider returns candidates per query. Cheap, cached on disk
               so a rerun costs nothing and a failed build is free to retry.

  3. rank      a vision model looks at each candidate's thumbnail and scores it
               against what the beat needs. This is the stage that separates
               "the top hit for a keyword" from "the right clip". Measured: the
               word "ticket" retrieved a traffic-citation bodycam video for a
               beat about a concert ticket stub, and only something that looks
               at the frame throws that out.

The model never writes or edits narration here either, for the reason recorded
at the top of plan.py. It is asked to describe pictures, nothing else.

Known limit: a thumbnail shows composition and subject, not motion, and it says
WHAT a source contains, never WHERE. See `footage_render.locate`.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import console  # noqa: F401  UTF-8 stdout; see console.py

import ark

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache" / "footage"

# Pexels answers unauthenticated requests from some networks, which is useful
# for a first run and is not something to build on: a first call returned 200
# with real results and the next returned 401. A free key from pexels.com/api
# removes the ambiguity - set PEXELS_API_KEY in .env.
PEXELS_SEARCH = "https://api.pexels.com/videos/search"

# What a candidate must clear before it is worth a vision call. Vision is the
# expensive stage, so the cheap filters run first: an 800x450 clip cannot fill
# a 1080p frame, and a clip shorter than its beat has to be looped or slowed.
MIN_WIDTH = 1280
MIN_SCORE = 3          # out of 5; below this we would rather generate the shot
GOOD_ENOUGH = 4        # stop looking; search already returned in relevance order
CANDIDATES_PER_BEAT = 8

USAGE = {"searches": 0, "vision_calls": 0, "thumbs": 0}


class FootageError(RuntimeError):
    pass


# --- plumbing --------------------------------------------------------------

def _cache_key(*parts):
    import hashlib
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _cached_json(key, produce):
    """Disk-memoise a JSON-returning call. Retrieval is idempotent and slow."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            path.unlink()          # a truncated write, not a real cache hit
    value = produce()
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def _get(url, headers=None, timeout=30, retries=3):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers=headers or {"User-Agent": "cartoon-econ-video/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last = FootageError(f"HTTP {exc.code} from {url.split('?')[0]}")
            if exc.code not in (408, 429, 500, 502, 503, 504):
                raise last
        except Exception as exc:
            last = FootageError(f"{type(exc).__name__}: {exc}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise last or FootageError(f"no attempt was made for {url.split('?')[0]}")


# --- stage 2: search -------------------------------------------------------
#
# Two providers, because the reference videos use two registers and no single
# library covers both. Pexels is modern B-roll - a factory floor, a person in
# the rain - and needs a free key. Commons is archival and public domain, which
# is where the 1950s street scenes and the rotary telephones actually live, and
# needs no key at all. Measured on the same three queries, Commons returned
# four usable 1950s telephone films and exactly one hit for "rain umbrella
# street", and that hit was a McKinley funeral cortege. It is a complement, not
# a substitute.

def search_pexels(query, orientation="landscape", min_seconds=4, per_page=10):
    """Return normalised candidate dicts for one query."""
    params = {
        "query": query,
        "orientation": orientation,
        "per_page": per_page,
        "min_duration": max(1, int(min_seconds)),
    }
    url = PEXELS_SEARCH + "?" + urllib.parse.urlencode(params)
    key = _cache_key("pexels", query, orientation, min_seconds, per_page)

    def fetch():
        headers = {"User-Agent": "cartoon-econ-video/1.0"}
        api_key = os.environ.get("PEXELS_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = api_key
        USAGE["searches"] += 1
        return json.loads(_get(url, headers=headers).decode("utf-8"))

    try:
        data = _cached_json(key, fetch)
    except FootageError as exc:
        if "401" in str(exc):
            raise FootageError(
                "Pexels rejected the request (401). It answers some networks "
                "without a key, but not reliably - get a free key at "
                "https://www.pexels.com/api/ and set PEXELS_API_KEY in .env."
            ) from exc
        raise
    clips = [_normalise_pexels(v) for v in data.get("videos", [])]
    return [c for c in clips if c]


def _normalise_pexels(video):
    """Pick the best file that is not larger than we need.

    video_files is unordered and mixes resolutions; taking [0] gets a 640x360
    proxy about as often as not. We want the smallest file that still covers
    1080p, because a 4K download costs minutes and gets scaled straight back
    down by the renderer.
    """
    files = [f for f in video.get("video_files", []) if f.get("width")]
    usable = [f for f in files if f["width"] >= MIN_WIDTH] or files
    usable.sort(key=lambda f: f["width"])
    best = next((f for f in usable if f["width"] >= 1920), usable[-1] if usable else None)
    if not best:
        return None
    user = video.get("user") or {}
    return {
        "provider": "pexels",
        "id": str(video.get("id")),
        "width": best.get("width"),
        "height": best.get("height"),
        "duration": float(video.get("duration") or 0),
        "download_url": best.get("link"),
        "thumb_url": video.get("image"),
        "page_url": video.get("url"),
        "credit": user.get("name") or "",
        "credit_url": user.get("url") or "",
        "license": "Pexels License",
        "low_res": (best.get("width") or 0) < MIN_WIDTH,
    }


COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Commons asks for a descriptive User-Agent and throttles anonymous clients
# that do not send one.
COMMONS_UA = "cartoon-econ-video/1.0 (stock footage retrieval)"
# Twelve back-to-back searches - four beats at three queries each - was enough
# to earn a 429, and the retry backoff then burned the rest of the run. One
# request every 0.6 s stays under it. Cache hits do not pay this.
COMMONS_MIN_INTERVAL = 0.6
_commons_last_call = 0.0


def _commons_throttle():
    global _commons_last_call
    wait = COMMONS_MIN_INTERVAL - (time.monotonic() - _commons_last_call)
    if wait > 0:
        time.sleep(wait)
    _commons_last_call = time.monotonic()


def search_commons(query, orientation="landscape", min_seconds=4, per_page=10):
    """Keyless archival search against Wikimedia Commons.

    Commons has no orientation filter, so that argument is honoured by
    discarding the wrong shape after the fact rather than before. Resolution is
    deliberately *not* filtered: the good archival material here is 640x480 by
    nature, and upscaling a soft telecine under a grain-and-vignette grade is
    what the reference videos are visibly doing. Low-resolution clips are
    flagged instead, so the renderer can decide to treat or letterbox them.

    Recall is literal and largely not in English - much of the best footage is
    titled in German or Dutch, so a sensible query can return nothing while an
    oddly specific one hits. That is why the director writes three queries per
    beat, narrow to broad.
    """
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:video {query}", "gsrnamespace": "6",
        "gsrlimit": str(per_page),
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
        "iiurlwidth": "640",
    }
    url = COMMONS_API + "?" + urllib.parse.urlencode(params)
    key = _cache_key("commons", query, per_page)

    def fetch():
        _commons_throttle()
        USAGE["searches"] += 1
        return json.loads(_get(url, headers={"User-Agent": COMMONS_UA}).decode("utf-8"))

    data = _cached_json(key, fetch)
    pages = ((data.get("query") or {}).get("pages") or {}).values()
    clips = [_normalise_commons(p) for p in pages]
    clips = [c for c in clips if c and c["duration"] >= min_seconds]
    if orientation == "landscape":
        clips = [c for c in clips if c["width"] >= c["height"]]
    elif orientation == "portrait":
        clips = [c for c in clips if c["height"] > c["width"]]
    return clips


def _normalise_commons(page):
    info = (page.get("imageinfo") or [{}])[0]
    if not info.get("url") or not info.get("thumburl"):
        return None
    meta = info.get("extmetadata") or {}

    def field(name):
        return re.sub(r"<[^>]+>", "", str((meta.get(name) or {}).get("value") or "")).strip()

    width, height = info.get("width") or 0, info.get("height") or 0
    return {
        "provider": "commons",
        "id": str(page.get("pageid")),
        "width": width,
        "height": height,
        "duration": float(info.get("duration") or 0),
        "download_url": info["url"],
        "thumb_url": info["thumburl"],
        "page_url": info.get("descriptionurl", ""),
        # Artist is free-form wikitext and is sometimes a whole credits
        # paragraph; keep it to something that fits on an attribution line.
        "credit": (field("Artist") or "Wikimedia Commons")[:60],
        "credit_url": info.get("descriptionurl", ""),
        "license": field("LicenseShortName") or "see file page",
        "low_res": width < MIN_WIDTH,
    }


PROVIDERS = {"pexels": search_pexels, "commons": search_commons}


# --- stage 1: queries ------------------------------------------------------

QUERY_SYSTEM = """You plan the visuals for a documentary-style explainer video
on a finance and economics channel.

FIRST, for every beat, choose exactly one "shot". This is a required field and
the most important decision you make. (It is called "shot", not "kind", because
a graphic spec has its own "kind" inside it - do not confuse the two.)

  "composite"  the beat NAMES or DEFINES a thing - a term, an object, a
               concept being introduced. This is the DEFAULT for explanatory
               beats and the reference videos' most common shot: the subject
               cut out on a designed backdrop with the term set large beside
               it. 「音障」 beside a jet. 「比值」 beside a dial.
  "graphic"    the beat carries a quantity, share, rate, trend, comparison, or
               money moving between parties. There is no footage of a savings
               rate.
  "footage"    the beat describes a SCENE or an ACTION that exists in the
               world - people doing something, a place, a process happening.

If a beat could be two of these, prefer composite over footage, and graphic
over both when a number is spoken. A bare full-frame photograph under a
caption is the weakest shot available; do not default to it.

THEN fill in the fields that kind needs.

For "composite":
  {"layout":"subject_left|subject_right|subject_center|card",
   "headline":"音障",              // the term, narration's language, <=16 chars
   "sub":"sound barrier",         // ENGLISH gloss beneath it, optional
   "subject":"a fighter jet ...", // ENGLISH image prompt for the cut-out
   "note":"后掠翼"}                // optional annotation label, narration language
  Vary the layout between consecutive composites.

For "graphic", one of:
  {"kind":"counter","value":7.2,"unit":"%","caption":"..."}   one rate or figure
  {"kind":"bar_chart","title":"...","unit":"%","highlight":"...",
   "items":[{"label":"...","value":45},...]}                  2-6 things compared
  {"kind":"line_chart","title":"...","x_labels":[...],
   "series":[{"label":"...","points":[...]}]}                 a trend over time
  {"kind":"comparison","title":"...",
   "left":{"label":"...","value":100},"right":{...}}          exactly two things
  {"kind":"flow","title":"...","nodes":["家庭","银行","企业"],
   "caption":"..."}                                           money or goods moving
  {"kind":"breakdown","title":"...","unit":"%",
   "items":[{"label":"消费","value":53},...]}                  parts of one whole

NEVER INVENT A NUMBER. Use a figure only if that exact figure is spoken in the
beat. If the beat says prices rose faster than wages without saying by how
much, use a "flow" or a "comparison" and let the shape carry it - do not supply
plausible-looking values. Fabricated figures in a chart are read as data.

ALL TEXT INSIDE A GRAPHIC OR COMPOSITE IS IN THE NARRATION'S OWN LANGUAGE -
titles, captions, bar labels, node names, headlines. A Chinese script gets
家庭 / 银行 / 企业 and 中国 / 美国, never "Households" or "United States". The
exceptions are "sub", which is deliberately an English gloss, and "needs",
"queries" and "subject", which are English because image and stock libraries
index in English.

Every beat, whatever its kind, also needs:
- "needs": one English sentence describing what is on screen. Describe what is
  VISIBLE, never what is meant - libraries index objects, people and actions,
  not concepts.
- "en": a plain English translation of the narration beat, for the second
  subtitle line. Translate the sentence; do not describe the picture.
- "queries": 3 English search queries, 2-5 words, most specific to most
  generic. Only "footage" beats use them, but always supply them so a rejected
  composite can fall back.

Rules that apply throughout:
- THE FOOTAGE IS REAL. The narration may use cartoon characters, invented
  places or brand names. None exist on film. Recast every one as an ordinary
  real-world person, object or place. Never put a fictional name in "needs",
  "subject" or a query.
- Do not film the words. A beat naming a term is not a shot of the term printed
  in a textbook; it is the thing itself.
- NEVER ask footage or a generated still for a diagram, chart, graph,
  whiteboard, flowchart, labelled boxes, or written words. Image models cannot
  draw them: asked for "a diagram of the four parts of GDP" one returned a
  whiteboard with four empty boxes. Those beats are "graphic" or "composite".
- VARY THE FRAMING across consecutive shots - wide, medium, close-up, unusual
  angle. Five shots of the same object is a failure even when each matches its
  beat. State the framing inside "needs".
- Avoid stock cliches: no handshakes, no lightbulbs, no man pointing at a
  whiteboard, no rising arrow graphs, no diverse team high-fiving.
- Never rewrite the narration. You are not given it to edit.

Return JSON only:
{"beats":[{"id":1,"shot":"composite","needs":"...","en":"...",
           "queries":["...","...","..."],
           "composite":{...} or "graphic":{...}}]}
"""

QUERY_TEMPLATE = """Narration beats, in order. Return exactly {count} entries \
with these ids.

{beats}

"needs" is one English sentence describing the shot that should be on screen -
subject, action, setting. It is what a human researcher would be told to go
and find. Keep it visual and specific.

"en" is a plain English translation of the narration beat itself, for the
second subtitle line. Translate the sentence; do not describe the picture, and
do not add anything the Chinese does not say. All three reference videos carry
this second line, so every beat needs one."""


CJK = re.compile(r"[一-鿿㐀-䶿]")


def _graphic_strings(spec):
    """Every human-readable string in a graphic spec, with its location."""
    if not spec:
        return []
    found = []
    for key in ("title", "caption", "highlight"):
        if spec.get(key):
            found.append((("key", key), str(spec[key])))
    for i, item in enumerate(spec.get("items") or []):
        if item.get("label"):
            found.append((("item", i), str(item["label"])))
    for side in ("left", "right"):
        if isinstance(spec.get(side), dict) and spec[side].get("label"):
            found.append((("side", side), str(spec[side]["label"])))
    for i, node in enumerate(spec.get("nodes") or []):
        found.append((("node", i), str(node)))
    for i, s in enumerate(spec.get("series") or []):
        if s.get("label"):
            found.append((("series", i), str(s["label"])))
    return found


def _put_graphic_string(spec, where, value):
    kind, key = where
    if kind == "key":
        spec[key] = value
    elif kind == "item":
        spec["items"][key]["label"] = value
    elif kind == "side":
        spec[key]["label"] = value
    elif kind == "node":
        spec["nodes"][key] = value
    elif kind == "series":
        spec["series"][key]["label"] = value


def _foreign(text, script_is_cjk):
    """True when this label is in the wrong language for the narration."""
    if not script_is_cjk:
        return False
    body = str(text)
    # Pure ASCII words. Years, percentages and acronym-only labels like "GDP"
    # are left alone: they read the same in a Chinese chart.
    if CJK.search(body):
        return False
    letters = [c for c in body if c.isalpha()]
    if not letters:
        return False
    return not (body.isupper() and len(body) <= 5)


def repair_graphic_language(plans, model=None):
    """Translate graphic labels that came back in the wrong language.

    The prompt asks for graphic text in the narration's language and the model
    complies inconsistently - one build came back entirely in Chinese, the next
    had English titles over Chinese bar labels in the same video. Asking again
    is not a fix; checking is. Everything that needs translating goes in one
    call, so this costs at most one request per build and usually none.
    """
    script_is_cjk = any(CJK.search(p["beat"]) for p in plans)
    jobs = []
    for plan_entry in plans:
        spec = plan_entry.get("graphic")
        for where, text in _graphic_strings(spec):
            if _foreign(text, script_is_cjk):
                jobs.append((spec, where, text))
    if not jobs:
        return 0

    wanted = sorted({text for _s, _w, text in jobs})
    try:
        reply = ark.chat_json(
            [{"role": "system", "content":
              "You translate short chart labels into the target language. "
              "Keep them short enough to fit under a bar - a few characters. "
              "Keep widely-used acronyms (GDP, CPI, GNP) as they are. "
              'Return JSON only: {"translations":{"source":"translation"}}'},
             {"role": "user", "content":
              "Target language: the language of this narration - "
              + plans[0]["beat"][:60]
              + chr(10) + chr(10) + "Labels:" + chr(10)
              + chr(10).join(f"- {w}" for w in wanted)}],
            model=model, temperature=0.0, max_tokens=1200)
    except Exception:
        return 0                      # wrong-language labels beat no labels
    pairs = (reply or {}).get("translations") if isinstance(reply, dict) else None
    table = {str(k): str(v).strip() for k, v in (pairs or {}).items()
             if str(v).strip()}
    fixed = 0
    for spec, where, text in jobs:
        if table.get(text):
            _put_graphic_string(spec, where, _label(table[text]))
            fixed += 1
    return fixed


# Words that mean the shot description is describing a CHART rather than a
# scene. A beat whose graphic was rejected still carries the `needs` written
# for that graphic, and sending it to image generation asks for a picture of a
# chart - which is how "a whiteboard with four empty boxes" got into a build.
DIAGRAM_WORDS = (
    "chart", "graph", "diagram", "infographic", "whiteboard", "flowchart",
    "flow chart", "axis", "axes", "plot", "pie ", "bar chart",
    "data visualization", "data visualisation", "side-by-side comparison",
    "comparison graphic", "labelled boxes", "labeled boxes",
)


def describes_a_diagram(needs):
    body = (needs or "").lower()
    return any(word in body for word in DIAGRAM_WORDS)


def repair_diagram_needs(plans, model=None):
    """Rewrite footage shot descriptions that describe a chart.

    When a declared graphic is refused - an invented figure, a comparison whose
    values came back as empty strings - the beat falls through to footage
    carrying the `needs` that was written for the chart. Nothing downstream
    notices: retrieval searches for "a side-by-side comparison of two upward
    lines" and generation cheerfully renders a picture of a chart. This is the
    same failure as the empty-whiteboard shots, arriving through the fallback
    rather than through the director's first choice.

    One batched call, and only for the beats that need it.
    """
    jobs = [p for p in plans
            if not p.get("graphic") and not p.get("composite")
            and describes_a_diagram(p.get("needs"))]
    if not jobs:
        return 0
    listing = chr(10).join(
        f'{i + 1}. beat: {p["beat"]}{chr(10)}   current: {p["needs"]}'
        for i, p in enumerate(jobs))
    try:
        reply = ark.chat_json(
            [{"role": "system", "content":
              "Each shot description below asks for a picture of a chart or "
              "diagram. Image and stock libraries cannot supply those. Rewrite "
              "each as a REAL-WORLD scene that stands for the same idea - "
              "people, objects, places, actions - in one English sentence. "
              "State the framing (wide, medium, close-up). Never mention a "
              "chart, graph, diagram, axis or any written words. "
              'Return JSON only: {"needs":{"1":"...","2":"..."}}'},
             {"role": "user", "content": listing}],
            model=model, temperature=0.3, max_tokens=1600)
    except Exception:
        return 0
    table = (reply or {}).get("needs") if isinstance(reply, dict) else None
    fixed = 0
    for i, plan_entry in enumerate(jobs, 1):
        replacement = str((table or {}).get(str(i), "")).strip()
        if replacement and not describes_a_diagram(replacement):
            plan_entry["needs"] = replacement[:200]
            fixed += 1
    return fixed


def queries_for_beats(beats, model=None, temperature=0.4):
    """Ask the model for a shot description, a translation and queries per beat."""
    listing = "\n".join(f"{i}. {text}" for i, text in enumerate(beats, 1))
    prompt = QUERY_TEMPLATE.format(count=len(beats), beats=listing)
    data = ark.chat_json(
        [{"role": "system", "content": QUERY_SYSTEM},
         {"role": "user", "content": prompt}],
        model=model, temperature=temperature, max_tokens=8000)
    plans = _validate_queries(data, beats)
    repair_graphic_language(plans, model=model)
    repair_diagram_needs(plans, model=model)
    return plans


def _validate_queries(data, beats):
    """Trust nothing: fill gaps, clamp counts, never return fewer than beats.

    Same posture as plan.validate - a director that returns eight entries for
    ten beats should cost two fallbacks, not the run.
    """
    raw = {}
    for entry in (data or {}).get("beats", []) or []:
        try:
            raw[int(entry.get("id"))] = entry
        except (TypeError, ValueError):
            continue
    out = []
    for i, text in enumerate(beats, 1):
        entry = raw.get(i) or {}
        queries = [str(q).strip() for q in (entry.get("queries") or []) if str(q).strip()]
        queries = [q for q in queries if len(q) < 60][:3]
        # Resolve `needs` BEFORE falling back to it. Getting this the other way
        # round meant a beat the director skipped entirely came out with an
        # empty query list and was never searched at all - it went straight to
        # NO MATCH with nothing considered, which reads like "no such footage
        # exists" when the truth is "nobody looked".
        needs = str(entry.get("needs") or "").strip() or text
        missing = not queries
        if missing:
            queries = [needs[:60]]
        out.append({"id": i, "beat": text, "needs": needs, "queries": queries,
                    # Second subtitle line. Empty is survivable - the caption
                    # renderer just draws the Chinese alone - so a missing
                    # translation must never cost the shot.
                    "en": str(entry.get("en") or "").strip(),
                    # True when the director gave us nothing usable for this
                    # beat. The query above is a stab in the dark - very likely
                    # the untranslated narration - so callers should re-ask for
                    # these ids rather than trust the result.
                    "fallback": missing})
        # Honour the declared shot where it is valid. The point of asking for
        # an explicit choice was to stop composite being an optional field the
        # model could skip - silently overriding that choice here would put us
        # straight back to inferring it from whatever spec happened to appear.
        shot = str(entry.get("shot") or "").strip().lower()
        # The model sometimes answers with the graphic's own kind here -
        # "counter", "comparison" - because the two fields were both called
        # "kind" once. Treat any chart name as a declaration of "graphic".
        import motion as _motion
        if shot in _motion.KINDS:
            shot = "graphic"
        graphic, notes = validate_graphic(entry.get("graphic"), text)
        comp, comp_notes = validate_composite(entry.get("composite"))
        if shot == "footage":
            graphic, comp = None, None
        elif shot == "composite" and comp:
            graphic = None
        elif shot == "graphic" and graphic:
            comp = None
        elif graphic:
            # No usable declaration: a figure on screen beats a name.
            comp = None
        out[-1]["graphic"] = graphic
        out[-1]["composite"] = comp
        out[-1]["shot"] = ("graphic" if graphic else
                           "composite" if comp else "footage")
        out[-1]["graphic_notes"] = notes + comp_notes
    return out


# --- graphics ---------------------------------------------------------------
#
# Some beats are better drawn than filmed - a savings rate, money moving from
# households through a bank to firms, one quantity outgrowing another. There is
# no footage of any of that.
#
# The rule that governs this whole section: **a number may appear on screen
# only if it appears in the narration.** These are finance and economics
# videos. A chart is read as data whatever the voiceover says, so a model that
# helpfully supplies "45%" for a beat that never mentioned 45 has fabricated a
# statistic and published it in a format that looks authoritative. Grounding is
# checked here rather than asked for in the prompt, because a prompt is a
# request and this needs to be a guarantee.
#
# Where values are not grounded the graphic is not necessarily discarded: a
# comparison can still show one bar taller than another with the numbers
# suppressed, which is what the narration actually said.

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def numbers_with_units(text):
    """Every number the narration states, mapped to the unit it was said in.

    The director supplies "unit" inconsistently - it gave "%" for a counter and
    a bar chart and omitted it on the comparison in the same build, so two bars
    read "53" and "68" in a video whose voiceover said 百分之五十三. The unit is
    recoverable from how the figure was spoken, so recover it rather than ask
    again.
    """
    units = {}
    body = text or ""
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(%|percent)?", body):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        units[value] = "%" if match.group(2) else units.get(value, "")
    # 百分之X / 百分点 mark a percentage; the marker itself is not a number.
    for match in re.finditer(r"百分(?:之|点)\s*([零〇一二两三四五六七八九十百千万点]+)", body):
        value = _cn_number(match.group(1))
        if value is not None:
            units[value] = "%"
    stripped = body.replace("百分之", "%").replace("百分点", "%")
    for run in re.findall(r"[零〇一二两三四五六七八九十百千万点]+", stripped):
        value = _cn_number(run)
        if value is not None:
            units.setdefault(value, "")
    return units


def numbers_in(text):
    """Every number the narration states, Arabic or Chinese.

    Deliberately generous: a false positive here only permits a number the
    narration plausibly contains, while a false negative silently downgrades a
    legitimate chart. Handles "7.2", "45%", "百分之七点二", "三十" and "两千".
    """
    found = set()
    for raw in re.findall(r"\d+(?:\.\d+)?", text or ""):
        try:
            found.add(float(raw))
        except ValueError:
            continue
    # "百分之" and "百分点" are unit markers, not quantities, and the 百 in them
    # otherwise parses as the number 100 - so every beat mentioning a
    # percentage would silently ground an invented value of 100.
    body = (text or "").replace("百分之", "%").replace("百分点", "%")
    # Chinese numerals, including the decimal form used after 百分之.
    for run in re.findall(r"[零〇一二两三四五六七八九十百千万点]+", body):
        value = _cn_number(run)
        if value is not None:
            found.add(value)
    return found


def _cn_number(run):
    """Parse a Chinese numeral run. None when it is not a number."""
    if "点" in run:
        whole, _, frac = run.partition("点")
        head = _cn_number(whole) if whole else 0
        if head is None or not frac:
            return None
        digits = "".join(str(CN_DIGITS[c]) for c in frac if c in CN_DIGITS)
        if not digits or len(digits) != len(frac):
            return None
        return float(f"{int(head)}.{digits}")
    total, section, seen = 0, 0, False
    for ch in run:
        if ch in CN_DIGITS:
            section = CN_DIGITS[ch]
            seen = True
        elif ch == "十":
            section = (section or 1) * 10
            total, section, seen = total + section, 0, True
        elif ch in "百千":
            unit = 100 if ch == "百" else 1000
            total, section, seen = total + (section or 1) * unit, 0, True
        elif ch == "万":
            total = (total + section) * 10000
            section, seen = 0, True
        else:
            return None
    return float(total + section) if seen else None


def _label(text, limit=14):
    """Trim a label to something that fits under a bar, without cutting a word.

    A hard slice produced "United State" from "United States" - a label that is
    not shorter so much as wrong. Latin text is cut back to a word boundary and
    marked; CJK has no such boundary and is cut where it must be.
    """
    body = str(text or "").strip()
    if len(body) <= limit:
        return body
    head = body[:limit]
    if head[-1].isascii() and head[-1].isalnum() and " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip(" ,.") + "…"


def _grounded(value, allowed, tolerance=0.05):
    try:
        value = abs(float(value))
    except (TypeError, ValueError):
        return False
    return any(abs(value - a) <= max(tolerance, abs(a) * 0.01) for a in allowed)


def validate_composite(spec):
    """Return a safe composite spec, or None.

    A composite is a designed frame: backdrop, a cut-out subject, a large
    headline and an optional annotation. It is the reference videos' default
    shot for naming or defining a thing, and the layer this track was missing.

    `headline` and `note` are in the narration's language; `sub` is the English
    line the references set underneath - 「音障」 over "sound barrier" - and
    `subject` is an English image prompt, like `needs`. So the language repair
    must leave `sub` and `subject` alone, which is why they are not in
    `_graphic_strings`.
    """
    import composite as composite_mod

    if not isinstance(spec, dict):
        return None, []
    headline = str(spec.get("headline") or "").strip()
    subject = str(spec.get("subject") or "").strip()
    if not headline or not subject:
        return None, ["a composite needs both a headline and a subject"]
    notes = []
    layout = str(spec.get("layout") or "").strip()
    if layout not in composite_mod.LAYOUTS:
        layout = "subject_left"
    out = {"layout": layout, "headline": headline[:16], "subject": subject[:180]}
    if len(headline) > 16:
        notes.append(f"headline trimmed to 16 characters: {headline[:16]!r}")
    for key, limit in (("sub", 40), ("note", 10)):
        value = str(spec.get(key) or "").strip()
        if value:
            out[key] = value[:limit]
    return out, notes


def validate_graphic(spec, beat_text, kinds=None):
    """Return a safe graphic spec, or None if it cannot be made safe.

    Returns (spec, notes). `notes` records anything that was changed, so a
    build can say out loud that it suppressed values rather than doing it
    quietly.
    """
    import motion

    kinds = kinds or motion.KINDS
    if not isinstance(spec, dict):
        return None, []
    kind = str(spec.get("kind") or "").strip()
    if kind not in kinds:
        return None, [f"unknown kind {kind!r}"]

    units = numbers_with_units(beat_text)
    allowed = set(units)
    notes: list = []
    out: dict = {"kind": kind}
    for key in ("title", "caption", "unit", "highlight"):
        if spec.get(key):
            out[key] = str(spec[key])[:48]

    def clean_items(raw, limit):
        items = []
        for entry in (raw or [])[:limit]:
            if not isinstance(entry, dict):
                continue
            try:
                value = float(entry.get("value"))
            except (TypeError, ValueError):
                continue
            items.append({"label": _label(entry.get("label")), "value": value})
        return items

    if kind == "bar_chart":
        items = clean_items(spec.get("items"), 6)
        if len(items) < 2:
            return None, ["a bar chart needs at least two bars"]
        if not all(_grounded(i["value"], allowed) for i in items):
            return None, ["bar values are not stated in the narration"]
        out["items"] = items
        # A highlight that matches no bar silently colours nothing, which looks
        # identical to not asking for one. Drop it and say so.
        if out.get("highlight") and not any(
                i["label"] == out["highlight"] for i in items):
            notes.append(f"highlight {out['highlight']!r} matches no bar; ignored")
            out.pop("highlight")

    elif kind == "line_chart":
        series = []
        for s in (spec.get("series") or [])[:2]:
            pts = []
            for v in (s.get("points") or [])[:12]:
                try:
                    pts.append(float(v))
                except (TypeError, ValueError):
                    pass
            if len(pts) >= 3:
                series.append({"label": str(s.get("label", ""))[:14], "points": pts})
        if not series:
            return None, ["a line chart needs a series of at least three points"]
        # A trend line is a shape, not a table: it carries no printed values, so
        # it only has to be grounded when its endpoints are stated.
        out["series"] = series
        labels = [str(x)[:8] for x in (spec.get("x_labels") or [])[:12]]
        if labels:
            out["x_labels"] = labels

    elif kind == "counter":
        try:
            value = float(spec.get("value"))
        except (TypeError, ValueError):
            return None, ["counter has no numeric value"]
        if not _grounded(value, allowed):
            return None, [f"counter value {value} is not stated in the narration"]
        out["value"] = value

    elif kind == "breakdown":
        items = []
        for entry in (spec.get("items") or [])[:6]:
            if not isinstance(entry, dict) or not str(entry.get("label", "")).strip():
                continue
            try:
                value = float(entry.get("value"))
            except (TypeError, ValueError):
                value = None
            items.append({"label": _label(entry.get("label")), "value": value})
        if len(items) < 2:
            return None, ["a breakdown needs at least two parts"]
        stated = [i for i in items if i["value"] is not None]
        if stated and not all(_grounded(i["value"], allowed) for i in stated):
            # Losing the split is fine; the parts are the point. Inventing the
            # split is not.
            for i in items:
                i["value"] = None
            notes.append("breakdown proportions are not in the narration; "
                         "showing the parts as equal segments")
        out["items"] = [{"label": i["label"], "value": i["value"] or 0}
                        for i in items]
        out["show_values"] = bool(stated) and not notes

    elif kind == "flow":
        nodes = [_label(n) for n in (spec.get("nodes") or [])[:4] if str(n).strip()]
        if len(nodes) < 2:
            return None, ["a flow needs at least two nodes"]
        out["nodes"] = nodes

    elif kind == "comparison":
        pair = []
        for side in ("left", "right"):
            entry = spec.get(side)
            if not isinstance(entry, dict):
                return None, [f"comparison is missing its {side} side"]
            try:
                pair.append({"label": _label(entry.get("label")),
                             "value": float(entry.get("value"))})
            except (TypeError, ValueError):
                return None, [f"comparison {side} has no numeric value"]
        out["left"], out["right"] = pair
        if not all(_grounded(p["value"], allowed) for p in pair):
            # Keep the shape, drop the claim: the bars still show which is
            # larger, which is what an ungrounded beat actually said.
            out["unit"] = ""
            out["show_values"] = False
            notes.append("comparison values are not in the narration; "
                         "drawing the shape without the numbers")

    # If every figure on screen was spoken as a percentage and no unit was
    # given, it is a percentage. Inferring beats printing a bare "53" under a
    # voiceover that said 百分之五十三.
    if not out.get("unit"):
        shown = [i["value"] for i in out.get("items", []) if i.get("value")]
        shown += [out[s]["value"] for s in ("left", "right") if s in out]
        if "value" in out:
            shown.append(out["value"])
        if shown and all(units.get(abs(float(v))) == "%" for v in shown):
            out["unit"] = "%"

    return out, notes


# --- stage 3: rank ---------------------------------------------------------

RANK_QUESTION = """This is a still from a stock video clip.

The clip is a candidate for a shot that needs to show:
{needs}

Score how well this clip fits, 0 to 5:
5 = exactly this subject and action
4 = this subject, slightly different action or setting
3 = related and usable as illustration
2 = loosely related, would read as filler
1 = wrong subject
0 = a stock cliche (whiteboard pointing, handshake, lightbulb, arrow graph,
    team high-five), a watermark, text overlay, or an unusable frame

Answer with exactly one line: SCORE|reason in under 12 words"""


def _thumb(url, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(url)}.jpg"
    if not path.exists():
        path.write_bytes(_get(url, timeout=45))
        USAGE["thumbs"] += 1
    return path


def score_image(path, needs, model=None):
    """Score one local image against a shot brief. Not cached - callers do it.

    Shared by candidate ranking (which looks at a thumbnail) and by window
    location (which looks at frames sampled from inside a long source), so both
    judge against exactly the same rubric.
    """
    USAGE["vision_calls"] += 1
    try:
        reply = ark.read_image_text(
            path, RANK_QUESTION.format(needs=needs), model=model, max_tokens=60)
    except Exception as exc:
        return {"score": 0, "why": f"vision call failed: {exc}"}
    match = re.search(r"([0-5])\s*\|?\s*(.*)", reply.strip())
    if not match:
        return {"score": 0, "why": f"unparsed: {reply[:60]}"}
    return {"score": int(match.group(1)), "why": match.group(2).strip()[:80]}


def score_clip(clip, needs, cache_dir=None, model=None):
    """Look at the clip's thumbnail and score it against what the beat needs.

    This scores ONE frame. For a short single-shot stock clip that frame speaks
    for the whole thing; for a long archival film it does not, and the shot the
    thumbnail shows may be a minute away from where a naive cut would land.
    `footage_render.locate` is what closes that gap - do not treat a high score
    here as knowing *where* in the source the wanted shot is.
    """
    cache_dir = cache_dir or (CACHE / "thumbs")
    key = _cache_key("score", clip.get("id"), needs)

    def judge():
        try:
            thumb = _thumb(clip["thumb_url"], cache_dir)
        except FootageError as exc:
            return {"score": 0, "why": f"thumbnail unavailable: {exc}"}
        return score_image(thumb, needs, model=model)

    return _cached_json(key, judge)


# --- the funnel ------------------------------------------------------------

def unusable_providers(provider):
    """Configured providers that cannot actually run, and why.

    A provider named in FOOTAGE_PROVIDERS but missing its credential does not
    fail loudly: Pexels answers some networks without a key and 401s others,
    and either way the build just reports "retrieval covered 0/N beats" with no
    hint that half the funnel was never connected. Saying so once, up front, is
    the difference between a tuning problem and a five-minute mystery.
    """
    reasons = []
    for name in [n.strip() for n in str(provider).split("+") if n.strip()]:
        if name == "pexels" and not os.environ.get("PEXELS_API_KEY", "").strip():
            reasons.append(
                "pexels has no PEXELS_API_KEY; it answers some networks "
                "without one and refuses others. A key is free at "
                "https://www.pexels.com/api/")
    return reasons


def select_footage(beats, durations=None, orientation="landscape",
                   provider="pexels", model=None,
                   candidates_per_beat=CANDIDATES_PER_BEAT,
                   min_score=MIN_SCORE, progress=None):
    """Run all three stages and return one chosen clip per beat.

    Clips are deduplicated across the whole video: the same shot appearing at
    0:30 and 2:10 is the single most obvious tell that a video was assembled by
    a machine, and it is free to avoid here by walking the ranked list.
    """
    names = [n.strip() for n in str(provider).split("+") if n.strip()]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise FootageError(f"unknown provider(s) {unknown}; have {sorted(PROVIDERS)}")
    searches = [(n, PROVIDERS[n]) for n in names]
    durations = durations or [5.0] * len(beats)

    plans = queries_for_beats(beats, model=model)
    used = set()
    results = []

    for plan_entry, want_seconds in zip(plans, durations):
        # A beat the director marked as a graphic is not searched at all. There
        # is no footage of a savings rate, and searching for it spends real
        # money on candidates that can only lose to the chart.
        if plan_entry.get("graphic") or plan_entry.get("composite"):
            results.append({
                "id": plan_entry["id"], "beat": plan_entry["beat"],
                "needs": plan_entry["needs"], "en": plan_entry.get("en", ""),
                "queries": [], "fallback": False, "want_seconds": want_seconds,
                "chosen": None, "considered": 0, "runners_up": [],
                "graphic": plan_entry.get("graphic"),
                "composite": plan_entry.get("composite"),
                "graphic_notes": plan_entry.get("graphic_notes", []),
            })
            if progress:
                made = ("graphic " + plan_entry["graphic"]["kind"]
                        if plan_entry.get("graphic")
                        else "composite " + plan_entry["composite"]["layout"])
                progress(f"  beat {plan_entry['id']:>2}  {made}")
            continue
        pool, seen = [], set()
        for query in plan_entry["queries"]:
            for name, search in searches:
                try:
                    hits = search(query, orientation=orientation,
                                  min_seconds=max(3, int(want_seconds)),
                                  per_page=candidates_per_beat)
                except FootageError as exc:
                    if progress:
                        progress(f"  {name} failed for {query!r}: {exc}")
                    continue
                for clip in hits:
                    tag = (clip["provider"], clip["id"])
                    if tag not in seen:
                        seen.add(tag)
                        pool.append(clip)

        # Vision is the expensive stage, so spend as little of it as possible:
        # skip clips already used elsewhere (they cannot be chosen anyway), and
        # stop as soon as something scores GOOD_ENOUGH, since search returns in
        # relevance order and the rest are unlikely to beat it. Scoring all
        # sixteen candidates for every beat costs ~960 vision calls on a
        # five-minute video and buys almost nothing.
        scored = []
        for clip in pool[: candidates_per_beat * 2]:
            if (clip["provider"], clip["id"]) in used:
                continue
            verdict = score_clip(clip, plan_entry["needs"], model=model)
            scored.append({**clip, **verdict})
            if verdict["score"] >= GOOD_ENOUGH:
                break
        # Prefer score, then a clip long enough to cover the beat without a
        # loop, then the higher resolution.
        scored.sort(key=lambda c: (c["score"], c["duration"] >= want_seconds,
                                   c["width"]), reverse=True)

        chosen = next((c for c in scored if c["score"] >= min_score), None)
        if chosen:
            used.add((chosen["provider"], chosen["id"]))
        results.append({
            "id": plan_entry["id"],
            "beat": plan_entry["beat"],
            "needs": plan_entry["needs"],
            "en": plan_entry.get("en", ""),
            "queries": plan_entry["queries"],
            "fallback": plan_entry.get("fallback", False),
            "want_seconds": want_seconds,
            "chosen": chosen,
            "considered": len(scored),
            "graphic": None,
            "composite": None,
            "graphic_notes": plan_entry.get("graphic_notes", []),
            "runners_up": [
                {"provider": c["provider"], "id": c["id"], "score": c["score"],
                 "why": c["why"], "page_url": c["page_url"]}
                for c in scored[:3]
                if not chosen or (c["provider"], c["id"]) != (chosen["provider"], chosen["id"])
            ],
        })
        if progress:
            mark = f"score {chosen['score']}" if chosen else "NO MATCH"
            progress(f"  beat {plan_entry['id']:>2}  {mark:<9} "
                     f"({len(scored)} considered)  {plan_entry['needs'][:52]}")
    return results


def coverage(results):
    """How much of the video retrieval actually found a shot for.

    Gaps are split by cause, because the two need opposite responses. A
    "searched" gap means the library genuinely has nothing for this beat and
    the shot should be generated instead. An "unplanned" gap means the director
    never produced queries for it, and the fix is to ask again.
    """
    # Graphic beats are not gaps. They were never searched, and counting them
    # as retrieval failures makes coverage look far worse than it is.
    made = lambda r: r.get("graphic") or r.get("composite")
    total = len([r for r in results if not made(r)])
    hit = sum(1 for r in results if r["chosen"])
    gaps = [r for r in results if not r["chosen"] and not made(r)]
    return {"beats": total, "matched": hit,
            "ratio": (hit / total) if total else 0.0,
            "gaps": [r["id"] for r in gaps],
            "unplanned": [r["id"] for r in gaps if r.get("fallback")],
            "searched": [r["id"] for r in gaps if not r.get("fallback")]}


def credits(results):
    """Attribution lines for every clip actually used."""
    seen, lines = set(), []
    for r in results:
        c = r.get("chosen")
        if not c or c["id"] in seen:
            continue
        seen.add(c["id"])
        lines.append(f"{c['credit']} - {c['page_url']}")
    return lines


# --- CLI -------------------------------------------------------------------

def _main():
    import argparse
    import config
    import plan as plan_mod

    ap = argparse.ArgumentParser(description="Search and rank stock footage for a script.")
    ap.add_argument("--script", required=True, help="path to the narration text file")
    ap.add_argument("--orientation", default="landscape",
                    choices=["landscape", "portrait"])
    ap.add_argument("--shot-seconds", type=float, default=5.0)
    ap.add_argument("--limit", type=int, default=0, help="only the first N beats")
    ap.add_argument("--provider", default="",
                    help="pexels, commons, or pexels+commons "
                         "(default: FOOTAGE_PROVIDERS)")
    ap.add_argument("--out", default="", help="write the result JSON here")
    args = ap.parse_args()
    provider = args.provider or config.FOOTAGE_PROVIDERS

    script = Path(args.script).read_text(encoding="utf-8-sig")
    beats = plan_mod.split_script(script, args.shot_seconds)
    if args.limit:
        beats = beats[: args.limit]
    print(f"{len(beats)} beats from {args.script} via {provider}")

    try:
        results = select_footage(beats, orientation=args.orientation,
                                 provider=provider, progress=print)
    except ark.ArkError as exc:
        # Stage 1 and stage 3 both need the model. Searching is free and
        # keyless on Commons, so it is worth saying which half is blocked
        # rather than printing a traceback over an ordinary quota reset.
        print(f"\nthe director/ranker is unavailable: {exc}")
        print("search itself needs no key on Commons - only the query and "
              "ranking stages are blocked.")
        return 1
    stats = coverage(results)
    print(f"\nmatched {stats['matched']}/{stats['beats']} ({stats['ratio']:.0%})")
    if stats["searched"]:
        print(f"  no footage exists for beats {stats['searched']} - generate these")
    if stats["unplanned"]:
        print(f"  director gave no queries for beats {stats['unplanned']} - re-ask")
    print(f"api: {USAGE['searches']} searches, {USAGE['vision_calls']} vision calls, "
          f"{USAGE['thumbs']} thumbnails")
    if args.out:
        Path(args.out).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main() or 0)
