"""Offline checks for the footage track: retrieval, graphics, composites.

Split out of selftest.py rather than added to it. Two Claude Code sessions work
on this repo at once - one on the drawn track, one on this - and a single
thousand-line test file is the one place their work is guaranteed to collide.
The tracks share `textkit` and `audio` and little else; their checks need share
nothing at all.

Run through selftest.py, which owns the Suite and prints the summary.
"""
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import audio as audio_mod
import config
import layout as layout_mod
import styles as styles_mod
import textkit

ROOT = Path(__file__).resolve().parent.parent


def run(suite, lay):
    """Add every footage-track check to `suite`."""
    # --- footage track (pure functions only, no network) -------------------
    import footage as footage_mod
    import footage_render as fr_mod
    import footage_build as fb_mod

    beats = ["第一句。", "第二句。", "第三句。"]
    plans = footage_mod._validate_queries(
        {"beats": [
            {"id": 1, "needs": "a rotary telephone on a desk", "en": "A phone.",
             "queries": ["rotary telephone", "x" * 80, "", "old phone dial"]},
            # id 2 missing entirely; id 3 has a description but no queries
            {"id": 3, "needs": "rain on a window", "queries": []},
        ]}, beats)
    suite.check("footage: one plan per beat", len(plans) == 3, f"{len(plans)}")
    # A provider named in FOOTAGE_PROVIDERS but missing its credential does
    # not fail loudly - Pexels answers some networks without a key and 401s
    # others - so a build reports "covered 0/N beats" with no hint that half
    # the funnel was never connected.
    import os as _os
    _saved = _os.environ.get("PEXELS_API_KEY")
    try:
        _os.environ["PEXELS_API_KEY"] = ""
        blind = footage_mod.unusable_providers("pexels+commons")
        _os.environ["PEXELS_API_KEY"] = "a-key"
        keyed = footage_mod.unusable_providers("pexels+commons")
    finally:
        if _saved is None:
            _os.environ.pop("PEXELS_API_KEY", None)
        else:
            _os.environ["PEXELS_API_KEY"] = _saved
    # "covered 0/0 beats (0%)" reads as a total failure; it is the opposite.
    all_made = [{"chosen": None, "graphic": {"kind": "counter"}},
                {"chosen": None, "composite": {"layout": "card"}}]
    suite.check("footage: a video of only made shots has no coverage gap",
                footage_mod.coverage(all_made)["beats"] == 0
                and not footage_mod.coverage(all_made)["gaps"],
                "0 beats searched, 0 gaps")

    suite.check("footage: a provider with no credential says so",
                len(blind) == 1 and "PEXELS_API_KEY" in blind[0] and not keyed,
                blind[0][:48] if blind else "said nothing")

    suite.check("footage: over-long and empty queries dropped",
                plans[0]["queries"] == ["rotary telephone", "old phone dial"],
                str(plans[0]["queries"]))
    # The bug this pins: resolving `needs` after using it as the fallback left
    # a skipped beat with zero queries, so it was never searched at all.
    suite.check("footage: a skipped beat still gets a query",
                bool(plans[1]["queries"]) and plans[1]["fallback"],
                str(plans[1]["queries"]))
    suite.check("footage: a described beat falls back to its description",
                plans[2]["queries"] == ["rain on a window"])
    suite.check("footage: a missing translation is survivable",
                plans[0]["en"] == "A phone." and plans[2]["en"] == "")

    pexels = footage_mod._normalise_pexels({
        "id": 42, "duration": 12, "image": "http://t/x.jpg", "url": "http://p/42",
        "user": {"name": "A", "url": "http://u/a"},
        "video_files": [{"width": 640, "height": 360, "link": "s"},
                        {"width": 3840, "height": 2160, "link": "xl"},
                        {"width": 1920, "height": 1080, "link": "hd"}]})
    suite.check("footage: picks 1080p over the 4K and the proxy",
                pexels["download_url"] == "hd", str(pexels["download_url"]))

    results = [
        {"id": 1, "chosen": {"provider": "p", "id": "1", "credit": "A",
                             "page_url": "u1"}, "fallback": False},
        {"id": 2, "chosen": None, "fallback": True},
        {"id": 3, "chosen": None, "fallback": False},
    ]
    cov = footage_mod.coverage(results)
    suite.check("footage: coverage splits gaps by cause",
                cov["unplanned"] == [2] and cov["searched"] == [3],
                f"unplanned={cov['unplanned']} searched={cov['searched']}")
    suite.check("footage: credits list only used clips",
                footage_mod.credits(results) == ["A - u1"])

    # Two 5 s shots with a 0.5 s dissolve run 9.5 s, and the single transition
    # starts at 4.5 s. Verified against a real assemble: ffprobe said 9.500000.
    suite.check("footage: dissolve arithmetic",
                fr_mod.total_duration([5, 5], 0.5) == 9.5
                and fr_mod.xfade_offsets([5, 5], 0.5) == [4.5])
    suite.check("footage: dissolve arithmetic over many shots",
                fr_mod.xfade_offsets([5, 5, 5], 0.5) == [4.5, 9.0]
                and fr_mod.total_duration([5, 5, 5], 0.5) == 14.0)
    suite.check("footage: one shot needs no transition",
                fr_mod.xfade_offsets([5], 0.5) == []
                and fr_mod.total_duration([5], 0.5) == 5)
    # A short clip is one shot: locate must not spend vision calls on it. This
    # runs offline precisely because it must short-circuit before any I/O.
    suite.check("footage: a short source is cut from the head, not probed",
                fr_mod.locate({"duration": 12.0}, "anything", 5.0, src=None)
                == fr_mod.HEAD_TRIM)
    # Latin wrapping. The caption wrapper is character-based, which is right
    # for CJK and broke English words apart: "distant places." came out as
    # "distant plac" / "es." in a finished video. The drawn track never saw it
    # because it only renders Chinese.
    long_en = ("More than a hundred years ago, people first used the telephone "
               "to send voices to distant places, which was a remarkable thing.")
    en_lines = textkit.wrap(long_en, 34, 900)
    # Rejoining with single spaces has to reproduce the original. A line simply
    # ending in a letter is normal English, so that is not the tell; a word cut
    # in half is, and it shows up here as a space appearing inside a word.
    suite.check("footage: Latin text never wraps mid-word",
                len(en_lines) > 1 and " ".join(en_lines) == long_en,
                " | ".join(ln[-14:] for ln in en_lines))
    # ...and the CJK behaviour it exists for is untouched.
    zh_lines = textkit.wrap("那时候的电话要靠人工接线，接线员坐在一整面墙的插孔前面。", 62, 700)
    suite.check("footage: CJK wrapping still breaks between characters",
                len(zh_lines) > 1 and all(zh_lines))

    # Caption stacking. A two-line Chinese beat used to push the English line
    # off the bottom of the frame - the second Chinese line landed on top of
    # it, and the English itself was clipped at the frame edge. The position is
    # computed from the block's real height, so both cases must sit clear of
    # the floor and neither may touch the frame edge.
    _lay = layout_mod.Layout("landscape")
    _look = styles_mod.look()
    floor = int(_lay.height * fr_mod.CAPTION_FLOOR)
    for label, zh in (("one-line", "一百多年前，人们第一次用电话把声音送到了远方。"),
                      ("two-line",
                       "那时候的电话要靠人工接线，接线员坐在一整面墙的插孔前面。")):
        layer = fr_mod._stacked_caption(
            _lay, _look, zh,
            "Back then, telephones required manual switchboards, with "
            "operators sitting in front of a wall of jack plugs.")
        alpha = np.asarray(layer)[:, :, 3]
        rows = np.where(alpha.max(axis=1) > 8)[0]
        suite.check(f"footage: {label} caption sits clear of the frame edge",
                    len(rows) and rows.max() <= floor,
                    f"ink ends at {rows.max() if len(rows) else '-'}, floor {floor}")

    # --- motion graphics ---------------------------------------------------
    import motion as motion_mod

    # The rule the whole feature rests on: a number reaches the screen only if
    # the narration said it. These are finance videos, and a chart is read as
    # data whatever the voiceover claims.
    beat_with = "中国的储蓄率高达45%，德国只有28%。"
    beat_without = "物价涨得比工资快得多。"
    ok, _ = footage_mod.validate_graphic(
        {"kind": "bar_chart", "unit": "%",
         "items": [{"label": "中国", "value": 45}, {"label": "德国", "value": 28}]},
        beat_with)
    suite.check("graphics: values stated in the narration are kept", bool(ok))
    bad, why = footage_mod.validate_graphic(
        {"kind": "bar_chart", "unit": "%",
         "items": [{"label": "中国", "value": 45}, {"label": "日本", "value": 33}]},
        beat_with)
    suite.check("graphics: an invented figure is refused", bad is None,
                why[0] if why else "")
    cmp_spec, notes = footage_mod.validate_graphic(
        {"kind": "comparison", "left": {"label": "工资", "value": 100},
         "right": {"label": "物价", "value": 140}}, beat_without)
    suite.check("graphics: ungrounded comparison keeps shape, drops numbers",
                cmp_spec and cmp_spec.get("show_values") is False and notes)
    suite.check("graphics: Chinese numerals are understood",
                footage_mod.numbers_in("年通胀率是百分之七点二。") >= {7.2}
                and footage_mod.numbers_in("大概三十年前") >= {30.0})
    # "百分之" is a unit marker; its 百 used to parse as the number 100, so
    # every beat mentioning a percentage silently grounded an invented 100.
    suite.check("graphics: a percent marker is not the number 100",
                100.0 not in footage_mod.numbers_in("年通胀率是百分之七点二。")
                and footage_mod.validate_graphic(
                    {"kind": "counter", "value": 100, "unit": "%"},
                    "年通胀率是百分之七点二。")[0] is None)
    suite.check("graphics: an unknown kind is refused",
                footage_mod.validate_graphic({"kind": "pie"}, beat_with)[0] is None)
    # A word is not a quantity: 老百姓 holds 百 and 千万不要 means "never", and
    # read as numbers they grounded an invented 100 and 10,000,000.
    suite.check("graphics: a word that contains a numeral grounds nothing",
                not footage_mod.numbers_in("老百姓的日子不好过")
                and not footage_mod.numbers_in("千万不要这样做")
                and footage_mod.validate_graphic(
                    {"kind": "counter", "value": 100, "unit": "%"},
                    "老百姓的日子不好过。")[0] is None)
    suite.check("graphics: years, 亿 and 万 are read at the size they were said",
                footage_mod.numbers_in("二零零八年金融危机") == {2008.0}
                and {14.0, 1.4e9} <= footage_mod.numbers_in("全国有十四亿人")
                and {100.0, 1e14} <= footage_mod.numbers_in("GDP超过一百万亿元")
                and {3.5, 35000.0} <= footage_mod.numbers_in("人口3.5万"),
                "二零零八 is 2008, 十四亿 is 14 and 1.4e9")

    # Nothing may be drawn into the subtitle band. A chart that puts its
    # category labels at 0.85 of frame height is drawing them under the
    # narration, and it looks like a subtitle bug rather than a layout one.
    floor = int(1080 * motion_mod.SAFE_BOTTOM)
    samples = {
        "bar_chart": {"kind": "bar_chart", "title": "储蓄率", "unit": "%",
                      "items": [{"label": "中国", "value": 45},
                                {"label": "德国", "value": 28}]},
        "line_chart": {"kind": "line_chart", "title": "通胀",
                       "x_labels": ["2015", "2024"],
                       "series": [{"label": "CPI", "points": [2.0, 7.2, 3.8]}]},
        "counter": {"kind": "counter", "value": 7.2, "unit": "%",
                    "caption": "年通胀率"},
        "flow": {"kind": "flow", "title": "钱的流动",
                 "nodes": ["家庭", "银行", "企业"], "caption": "存款变成贷款"},
        "comparison": {"kind": "comparison", "title": "工资与物价",
                       "left": {"label": "工资", "value": 100},
                       "right": {"label": "物价", "value": 140}},
    }
    # Ink is found by differencing against the bare backdrop, not by looking
    # for pixels unlike the most common value. The backdrop is a gradient, so
    # every row differs from the modal colour and the old method reported the
    # whole frame as ink the moment the flat fill was replaced.
    bare = np.asarray(motion_mod.backdrop((1920, 1080),
                                          motion_mod.palette("vintage")["bg"])
                      .convert("L")).astype(int)
    for kind, spec in samples.items():
        # drift=0 so the frame and the reference backdrop share a texture
        # phase and gradient; with the crop offset in play every cross reads
        # as content and the whole frame looks like ink. This checks LAYOUT.
        frame_img = motion_mod.frame(spec, (1920, 1080), 0.9, "vintage", drift=0)
        arr = np.asarray(frame_img.convert("L")).astype(int)
        rows_ink = np.where(np.abs(arr - bare).max(axis=1) > 25)[0]
        low = int(rows_ink.max()) if len(rows_ink) else 0
        suite.check(f"graphics: {kind} stays out of the subtitle band",
                    low <= floor, f"ink ends {low}, floor {floor}")
    # ...and the drift is checked as arithmetic, because it is: the layout
    # floor must sit at least DRIFT pixels above the guarantee, or the move
    # carries content into the subtitle band at one end of its travel.
    suite.check("graphics: the layout floor reserves the drift",
                int(1080 * motion_mod.layout_floor(1080)) + motion_mod.DRIFT
                <= int(1080 * motion_mod.SAFE_BOTTOM),
                f"floor {int(1080 * motion_mod.layout_floor(1080))} + "
                f"drift {motion_mod.DRIFT} <= {int(1080 * motion_mod.SAFE_BOTTOM)}")

    # --- composites --------------------------------------------------------
    import composite as comp_mod

    good, _ = footage_mod.validate_composite(
        {"layout": "card", "headline": "再生化学纤维", "sub": "regenerated fibre",
         "subject": "a bundle of white synthetic fibre", "note": "原料"})
    suite.check("composite: a full spec survives validation",
                good and good["layout"] == "card" and good["note"] == "原料")
    suite.check("composite: a spec with no subject is refused",
                footage_mod.validate_composite({"headline": "音障"})[0] is None)
    suite.check("composite: an unknown layout falls back",
                footage_mod.validate_composite(
                    {"layout": "diagonal", "headline": "x",
                     "subject": "y"})[0]["layout"] == "subject_left")
    trimmed, trim_notes = footage_mod.validate_composite(
        {"headline": "这是一个非常非常长的标题应该被裁剪掉", "subject": "y"})
    suite.check("composite: an over-long headline is trimmed and reported",
                len(trimmed["headline"]) == 16 and trim_notes)

    # Layout, with a synthetic cut-out so nothing is generated. Every layer
    # must stay above the subtitle band, and the sub-line must not land on the
    # headline - the same fixed-offset mistake that hit the bilingual caption
    # and the opening hook before it.
    art = Image.new("RGBA", (900, 900), (0, 0, 0, 0))
    ImageDraw.Draw(art).ellipse([80, 140, 820, 760], fill=(200, 205, 212, 255))
    bare_c = np.asarray(motion_mod.backdrop(
        (1920, 1080), motion_mod.palette("clean")["bg"]).convert("L")).astype(int)
    for layout in comp_mod.LAYOUTS:
        spec = {"layout": layout, "headline": "再生化学纤维",
                "sub": "regenerated fibre", "subject": "x", "note": "原料"}
        img = comp_mod.frame(spec, (1920, 1080), 0.95, "clean",
                             subject_image=art, drift=0)
        arr = np.asarray(img.convert("L")).astype(int)
        rows_ink = np.where(np.abs(arr - bare_c).max(axis=1) > 25)[0]
        low = int(rows_ink.max()) if len(rows_ink) else 0
        suite.check(f"composite: {layout} stays out of the subtitle band",
                    low <= floor, f"ink ends {low}, floor {floor}")
    suite.check("composite: text blocks report their own bottom",
                comp_mod._draw_text(
                    Image.new("RGBA", (600, 400), (0, 0, 0, 0)), (300, 100),
                    "两行的标题会换行", 60, (255, 255, 255, 255), "mm", 240) > 100)

    # --- the frame is never empty at a cut ---------------------------------
    # Sampling every frame of a build found 0.6-8.5% of the frame covered at
    # each of seven hard cuts, climbing over the next second: every layer
    # faded up from zero alpha, so the cut landed on nothing. These check the
    # FIRST frame, which is the one the cut lands on.
    def covered(img, ground):
        arr = np.asarray(img.convert("L")).astype(int)
        return float((np.abs(arr - ground) > 25).mean())

    first = comp_mod.frame(
        {"layout": "subject_left", "headline": "再生化学纤维",
         "sub": "regenerated fibre", "subject": "x", "note": "原料"},
        (1920, 1080), 0.0, "clean", subject_image=art, drift=0)
    suite.check("composite: the first frame after a cut is already composed",
                covered(first, bare_c) > 0.05,
                f"{covered(first, bare_c):.1%} covered at t=0")
    # A chart is different from a composite here, and the distinction matters.
    # Bars SHOULD grow - that is the animation, and demanding a fixed share of
    # the finished ink at t=0 would force the data to be pre-drawn, which is
    # the opposite of right. What must be there when the cut lands is the
    # scaffold: title, axis, gridlines, category labels. A frame where every
    # layer ramps from zero alpha scores exactly 0.0% here, so the floor only
    # has to be clear of nothing at all.
    for kind, spec in samples.items():
        img0 = motion_mod.frame(spec, (1920, 1080), 0.0, "vintage", drift=0)
        img1 = motion_mod.frame(spec, (1920, 1080), 1.0, "vintage", drift=0)
        at_cut, done = covered(img0, bare), covered(img1, bare)
        suite.check(f"graphics: {kind} draws its scaffold before the data",
                    at_cut > 0.004,
                    f"{at_cut:.1%} at the cut, {at_cut / done:.0%} of finished")

    # A composite whose subject never arrived used to reserve half the frame
    # for it anyway and print the headline in the other half, which is worse
    # than the flat photo the module replaced.
    void = comp_mod.frame(
        {"layout": "subject_left", "headline": "再生化学纤维",
         "sub": "regenerated fibre"}, (1920, 1080), 1.0, "clean", drift=0)
    left_half = np.asarray(void.convert("L")).astype(int)[:, :960]
    suite.check("composite: with no subject the type centres instead",
                float((np.abs(left_half - bare_c[:, :960]) > 25).mean()) > 0.005,
                "the empty half now carries type")

    # The annotation used to be offset from the subject towards the rest of
    # the frame, which is exactly where the headline column is - so it landed
    # on the headline in every one of the four layouts. It is now drawn over
    # the subject deliberately, so the test isolates the HEADLINE's pixels by
    # differencing two renders rather than treating all ink as type.
    def ink(a, b):
        return np.abs(np.asarray(a.convert("L")).astype(int)
                      - np.asarray(b.convert("L")).astype(int)) > 25

    #
    # The type is isolated against other type of the same measured size, not
    # against no type at all: the centred layout sizes the subject's box from
    # the type block above it, so dropping the headline moves the subject too
    # and the difference would count the subject as type.
    for layout in comp_mod.LAYOUTS:
        base_spec = {"layout": layout, "subject": "x"}
        head_spec = dict(base_spec, headline="再生化学纤维",
                         sub="regenerated fibre")
        other_spec = dict(base_spec, headline="甲乙丙丁戊己",
                          sub="xxxxxxxxxxxxxxxxx")
        def shot(spec):
            return comp_mod.frame(spec, (1920, 1080), 1.0, "clean",
                                  subject_image=art, drift=0)
        with_head = shot(head_spec)
        with_both = shot(dict(head_spec, note="原料"))
        headline_at = ink(with_head, shot(other_spec))
        note_at = ink(with_both, with_head)
        overlap = int((headline_at & note_at).sum())
        suite.check(f"composite: {layout} keeps the note off the headline",
                    overlap < max(1, note_at.sum()) * 0.02,
                    f"{overlap} of {int(note_at.sum())} note pixels on type")

    suite.check("graphics: gridline steps are round numbers",
                [motion_mod._nice_step(v) for v in (45, 7.2, 1200, 0.9)]
                == [10.0, 2.0, 250.0, 0.2])

    # A rejected graphic leaves the beat carrying a `needs` written for that
    # graphic. Sending it to footage asks generation for a picture of a chart -
    # the empty-whiteboard failure arriving through the fallback rather than
    # through the director's first choice.
    suite.check("footage: a chart description is recognised",
                all(footage_mod.describes_a_diagram(s) for s in (
                    "A side-by-side comparison of two upward lines.",
                    "A comparison graphic showing money rising.",
                    "A diagram of the four parts of GDP on a whiteboard.")))
    suite.check("footage: a real scene is not mistaken for one",
                not any(footage_mod.describes_a_diagram(s) for s in (
                    "Close-up of a printing press running new banknotes.",
                    "Wide shot of shoppers at a busy market stall.")))
    # And the declared shot survives the model answering with a chart name.
    named = footage_mod._validate_queries(
        {"beats": [{"id": 1, "shot": "comparison", "needs": "n", "en": "e",
                    "queries": ["q"],
                    "graphic": {"kind": "comparison",
                                "left": {"label": "中国", "value": 45},
                                "right": {"label": "美国", "value": 18}}}]},
        ["中国是45%，美国是18%。"])[0]
    suite.check("footage: a graphic kind in `shot` still means graphic",
                named["shot"] == "graphic" and named["graphic"])

    suite.check("graphics: every kind has a drawer",
                set(motion_mod.KINDS) == set(motion_mod.DRAWERS))

    # The director supplies "unit" inconsistently - "%" on a counter and a bar
    # chart, omitted on the comparison in the same build, so two bars read
    # "53" and "68" under a voiceover saying 百分之五十三. Recover it from how
    # the figure was spoken rather than asking again.
    said = footage_mod.numbers_with_units(
        "在中国是百分之五十三，在美国是百分之六十八，大概三十年了。")
    suite.check("graphics: a percentage is recognised as one",
                said.get(53.0) == "%" and said.get(68.0) == "%"
                and said.get(30.0) == "", str(said))
    inferred, _ = footage_mod.validate_graphic(
        {"kind": "comparison", "left": {"label": "中国", "value": 53},
         "right": {"label": "美国", "value": 68}},
        "在中国，消费占了百分之五十三。在美国，这个数字是百分之六十八。")
    suite.check("graphics: a missing % unit is recovered",
                inferred and inferred.get("unit") == "%")
    plain, _ = footage_mod.validate_graphic(
        {"kind": "counter", "value": 30}, "大概三十年前的事。")
    suite.check("graphics: a plain count is not made a percentage",
                plain and not plain.get("unit"))

    # A breakdown with no stated split shows equal parts rather than inventing
    # proportions; the parts are what the narration said, the split is not.
    parts, part_notes = footage_mod.validate_graphic(
        {"kind": "breakdown", "title": "GDP组成",
         "items": [{"label": "消费"}, {"label": "投资"},
                   {"label": "政府支出"}, {"label": "净出口"}]},
        "它主要由四个部分组成：消费、投资、政府支出，还有净出口。")
    suite.check("graphics: an unstated split becomes equal segments",
                parts and len(parts["items"]) == 4
                and parts.get("show_values") is False)

    # The hook is an accent, not the subtitle in a bigger font. A beat that
    # needs two lines at hook size is refused - and when it was not, the
    # translation printed straight across the hook's own second line.
    _lay2 = layout_mod.Layout("landscape")
    with tempfile.TemporaryDirectory() as tmp:
        short = fr_mod.hook_png("你一定听过这个声音", "You must have heard this.",
                                _lay2, styles_mod.look(), Path(tmp) / "h.png")
        long_one = fr_mod.hook_png(
            "每年新闻里都会说，我们的GDP又增长了百分之五。",
            "Every year the news says our GDP grew by five percent.",
            _lay2, styles_mod.look(), Path(tmp) / "h2.png")
        suite.check("footage: a short hook renders, a long one is refused",
                    short is not None and long_one is None)

    suite.check("footage: generated fill is photographic and unlettered",
                "photograph" in fr_mod.fill_prompt("a desk", "vintage")
                and "no text" in fr_mod.fill_prompt("a desk", "vintage"))

    # The invariant that keeps subtitles on their shots: a caption must start
    # exactly when its shot starts, and a shot starts exactly where its xfade
    # transition is placed. Computed by two different functions; if they ever
    # disagree the video does not fail, the back half just drifts out of sync.
    ds = [4.2, 5.8, 3.9, 6.1]
    spans = fb_mod.caption_spans(
        [{"beat": f"b{i}", "en": ""} for i in range(len(ds))], ds, dissolve=0.5)
    offsets = fr_mod.xfade_offsets(ds, 0.5)
    starts = [round(s[0], 6) for s in spans]
    suite.check("footage: captions start where their shots start",
                [round(o, 6) for o in offsets] == starts[1:],
                f"captions {starts[1:]} vs xfade {[round(o, 3) for o in offsets]}")
    suite.check("footage: the last caption ends inside the video",
                spans[-1][1] <= fr_mod.total_duration(ds, 0.5) + 1e-6)
    # The check that was here asserted each caption ends inside its own shot,
    # which was true while consecutive captions still overlapped by 0.2 s and
    # the finished video showed every subtitle doubled through the transition.
    # The invariant that actually matters is between neighbours.
    suite.check("footage: consecutive captions never overlap",
                all(a[1] <= b[0] + 1e-9 for a, b in zip(spans, spans[1:])),
                "; ".join(f"{a[1]:.3f}>{b[0]:.3f}"
                          for a, b in zip(spans, spans[1:]) if a[1] > b[0] + 1e-9))
    dissolved = fb_mod.caption_spans(
        [{"beat": f"b{i}", "en": ""} for i in range(len(ds))], ds, dissolve=0.5)
    suite.check("footage: and not when cross-fading either",
                all(a[1] <= b[0] + 1e-9
                    for a, b in zip(dissolved, dissolved[1:])))
    suite.check("footage: loudness target sits in the reference band",
                -18.7 <= audio_mod.TARGET_LUFS <= -16.4,
                f"{audio_mod.TARGET_LUFS} LUFS")

    # Real ffmpeg, because this failure only exists in the finished file. Two
    # sources with different pixel aspect ratios - which is what a 384x288
    # archival upscale next to a generated still actually looks like - joined
    # fine under xfade and made `concat` refuse the whole graph once
    # transitions became hard cuts. prepare() must hand back square pixels.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        cache = work / "clips"
        cache.mkdir()
        made = []
        for i, sar in enumerate(("16/15", "1/1")):
            src = cache / f"pexels_sar{i}.mp4"
            subprocess.run(
                [config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                 "-i", "testsrc=size=320x240:rate=15:duration=1",
                 "-vf", f"setsar={sar}", "-c:v", "libx264", "-t", "1",
                 "-pix_fmt", "yuv420p", str(src)], check=True)
            clip = {"provider": "pexels", "id": f"sar{i}", "duration": 1.0,
                    "download_url": "unused - already in the cache"}
            made.append(fr_mod.prepare(clip, work / f"s{i}.mp4", seconds=0.4,
                                       size=(320, 180), fps=15, grade="none",
                                       cache_dir=cache))
        sars = []
        for path in made:
            out = subprocess.run(
                [config.FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=sample_aspect_ratio",
                 "-of", "csv=p=0", str(path)], capture_output=True, text=True)
            sars.append(out.stdout.strip())
        suite.check("footage: prepare normalises pixel aspect ratio",
                    all(s in ("1:1", "") for s in sars), f"got {sars}")
        try:
            fr_mod.assemble(made, [0.4, 0.4], work / "joined.mp4", dissolve=0.0)
            joined = (work / "joined.mp4").exists()
            detail = ""
        except RuntimeError as exc:
            joined, detail = False, str(exc)[-90:]
        suite.check("footage: mismatched sources still hard-cut together",
                    joined, detail)

        # The chrome rides over the assembled video so it covers every shot
        # kind. Its progress rule is checked by MEASURING the filled fraction
        # at three moments, because the first implementation used drawbox with
        # `w=iw*t/D`, which exits 0, writes a file and draws the rule at full
        # width on every frame - in this ffmpeg the size expressions are not
        # re-evaluated per frame, and nothing said so.
        lay = layout_mod.Layout("landscape")
        chrome = fr_mod.chrome_png("通货膨胀", lay, "clean", work / "chrome.png")
        shots = []
        for i in range(2):
            shots.append(motion_mod.render(
                {"kind": "counter", "value": 7.2 + i, "unit": "%"},
                work / f"c{i}.mp4", seconds=1.0, size=lay.size, grade="none"))
        fr_mod.assemble(shots, [1.0, 1.0], work / "chromed.mp4", dissolve=0.0,
                        chrome=chrome, grade="clean")
        accent = np.array(motion_mod.palette("clean")["accent"])
        row = int(lay.size[1] * motion_mod.RULE_Y)
        filled = []
        for moment in (0.25, 1.0, 1.75):
            still = work / f"r{moment}.png"
            subprocess.run(
                [config.FFMPEG, "-y", "-v", "error", "-ss", str(moment),
                 "-i", str(work / "chromed.mp4"), "-frames:v", "1", str(still)],
                capture_output=True)
            pixels = np.asarray(Image.open(still).convert("RGB")).astype(int)
            near = (np.abs(pixels[row:row + 3] - accent).sum(axis=2) < 90)
            filled.append(float(near.any(axis=0).mean()))
        suite.check("footage: the progress rule actually advances",
                    filled[0] < 0.25 < filled[1] < 0.85 < filled[2],
                    "  ".join(f"{f:.0%}" for f in filled))

    # Speed has to reach the footage itself, and this is the one check that can
    # tell the difference. A shot cut shorter and a shot played faster are the
    # same length on the timeline and look identical in every report; the only
    # place they differ is in what is on screen partway through. So: a source
    # that is one second of red and then three of blue, cut to a two-second
    # shot at 1.0x and at 2.0x, sampled at the same moment in each. At 1.0x
    # that moment is still inside the red; at 2.0x the shot has already reached
    # the blue, because it is playing the source at twice the rate rather than
    # taking half as much of it.
    #
    # Narration at 1.5x over footage at 1.0x is the mismatch the setting exists
    # to remove, and it would pass every other check in this file.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        cache = work / "clips"
        cache.mkdir()
        src = cache / "pexels_retime.mp4"
        subprocess.run(
            [config.FFMPEG, "-y", "-v", "error",
             "-f", "lavfi", "-i", "color=c=red:s=64x64:r=15:d=1",
             "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=15:d=3",
             "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
             "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
             str(src)], check=True)
        clip = {"provider": "pexels", "id": "retime", "duration": 4.0,
                "download_url": "unused - already in the cache"}

        def shot_at(speed, moment=0.75):
            out = work / f"speed_{speed:.0f}.mp4"
            fr_mod.prepare(clip, out, seconds=2.0, size=(64, 64), fps=15,
                           grade="none", head_trim=0.0, cache_dir=cache,
                           speed=speed)
            still = work / f"speed_{speed:.0f}.png"
            subprocess.run(
                [config.FFMPEG, "-y", "-v", "error", "-ss", str(moment),
                 "-i", str(out), "-frames:v", "1", str(still)], check=True)
            pixels = np.asarray(Image.open(still).convert("RGB")).astype(int)
            length = subprocess.run(
                [config.FFPROBE, "-v", "error", "-show_entries",
                 "format=duration", "-of", "csv=p=0", str(out)],
                capture_output=True, text=True).stdout.strip()
            return float(pixels[..., 0].mean() - pixels[..., 2].mean()), float(length)

        warm_slow, len_slow = shot_at(1.0)
        warm_fast, len_fast = shot_at(2.0)
        suite.check("footage: speed plays the clip faster, not just shorter",
                    warm_slow > 20 > -20 > warm_fast,
                    f"r-b {warm_slow:+.0f} at 1.0x, {warm_fast:+.0f} at 2.0x")
        suite.check("footage: and the shot is still the length it was given",
                    abs(len_slow - 2.0) < 0.15 and abs(len_fast - 2.0) < 0.15,
                    f"{len_slow:.2f}s and {len_fast:.2f}s")

    # The rest of the timeline moves with it. Caption spans are cut from the
    # shot durations, so at 1.5x they have to land 1.5x earlier - a caption
    # left on the 1.0x clock is a subtitle for the shot before it.
    beats = [{"beat": "第一句", "en": "one"}, {"beat": "第二句", "en": "two"}]
    slow_spans = fb_mod.caption_spans(beats, [4.0, 4.0])
    fast_spans = fb_mod.caption_spans(
        [dict(b) for b in beats], [4.0 / 1.5, 4.0 / 1.5],
        gap=fb_mod.paced(fb_mod.CAPTION_GAP, 1.5))
    suite.check("footage: captions ride the same clock as the shots",
                all(abs(f[0] * 1.5 - s[0]) < 1e-6
                    and abs(f[1] * 1.5 - s[1]) < 1e-6
                    for s, f in zip(slow_spans, fast_spans)),
                f"ends {slow_spans[0][1]:.2f}s -> {fast_spans[0][1]:.2f}s")

    _placement(suite)
    _graphics_detail(suite)
    _caption_bands(suite)
    _voice_and_draft(suite)


def _placement(suite):
    """The composite's note goes beside the subject, not across it."""
    import composite as comp_mod

    # A standing figure, cut out tight: a head on a body, most of the height
    # and a third of the width. The note used to sit at a fixed fraction of
    # the subject's box - a third of the way down, just inside its edge -
    # which on a figure like this is the face.
    figure = Image.new("RGBA", (300, 900), (0, 0, 0, 0))
    pen = ImageDraw.Draw(figure)
    pen.ellipse([90, 10, 210, 150], fill=(40, 44, 60, 255))
    pen.rounded_rectangle([30, 150, 270, 890], radius=40, fill=(40, 44, 60, 255))
    # On the paper grade, whose light ground a dark figure stands out from.
    for layout in ("subject_left", "subject_right", "card"):
        spec = {"layout": layout, "headline": "偷懒的代价", "subject": "x"}
        bare = comp_mod.frame(spec, (1920, 1080), 1.0, "vintage",
                              subject_image=figure, drift=0)
        noted = comp_mod.frame(dict(spec, note="被开除"), (1920, 1080), 1.0,
                               "vintage", subject_image=figure, drift=0)
        note = np.abs(np.asarray(noted.convert("L")).astype(int)
                      - np.asarray(bare.convert("L")).astype(int)) > 25
        # The subject's own pixels: the figure is far darker than anything
        # else on the frame except the headline, which it never shares a
        # column with.
        subject = np.asarray(bare.convert("L")).astype(int) < 70
        ground = comp_mod.frame({"layout": layout, "subject": "x"},
                                (1920, 1080), 1.0, "vintage", drift=0)
        subject &= np.asarray(ground.convert("L")).astype(int) > 150
        on = int((note & subject).sum())
        suite.check(f"composite: {layout} sets its note beside the subject",
                    note.sum() > 500 and on < note.sum() * 0.08,
                    f"{on} of {int(note.sum())} note pixels on the figure")

    # And it points at it: the leader ends on the subject.
    art = comp_mod._fit(figure, 972, 700)
    spot = comp_mod._note_spot(art, (100, 120), (40, 110, 1010, 850),
                               (190, 80), 67, 1)
    inside = spot is not None and art.getchannel("A").getpixel(
        (spot[1][0] - 100, spot[1][1] - 120)) > 0
    suite.check("composite: the note's leader lands on the subject", inside,
                f"{spot}")
    suite.check("composite: a subject with nothing solid places no note",
                comp_mod._note_spot(Image.new("RGBA", (200, 200)), (0, 0),
                                    (0, 0, 400, 400), (100, 40), 40, 1)
                is None)


def _graphics_detail(suite):
    import motion as motion_mod

    # Record what the drawers print instead of reading it back off pixels.
    printed = []
    real_text = motion_mod._text

    def recording(draw, xy, body, size, fill, anchor="mm", bold=True):
        printed.append((xy, body, size))
        return real_text(draw, xy, body, size, fill, anchor, bold)

    motion_mod._text = recording
    try:
        spec = {"kind": "breakdown", "unit": "%", "show_values": True,
                "items": [{"label": "消费", "value": 55},
                          {"label": "投资", "value": 42},
                          {"label": "净出口", "value": 3}]}
        motion_mod.frame(spec, (1920, 1080), 1.0, "vintage", drift=0)
        bodies = [body for _, body, _ in printed]
        suite.check("graphics: a figure too wide for its segment joins the label",
                    "净出口 3%" in bodies and "3%" not in bodies
                    and "55%" in bodies, str(bodies))

        printed.clear()
        motion_mod.frame({"kind": "flow", "nodes": ["家庭", "银行", "企业"]},
                         (1080, 1920), 1.0, "vintage", drift=0)
        spots = {body: xy for xy, body, _ in printed}
        column = [spots.get(n) for n in ("家庭", "银行", "企业")]
        suite.check("graphics: a portrait flow runs top to bottom",
                    all(column) and len({round(x) for x, _ in column}) == 1
                    and column[0][1] < column[1][1] < column[2][1],
                    str(column))
    finally:
        motion_mod._text = real_text

    size, lines = motion_mod._fitted_label("中央银行的基础货币投放", 52, 300, 2)
    suite.check("graphics: a long node label fits its box",
                len(lines) <= 2 and all(
                    textkit.advance(line, size, True) <= 300 for line in lines),
                f"{lines} at {size}px")

    # The tokens keep moving once the chart has built: two moments in the
    # hold differ along the arrows, and nowhere else.
    spec = {"kind": "flow", "nodes": ["家庭", "银行", "企业"]}
    a = np.asarray(motion_mod.frame(spec, (1920, 1080), 0.80, "vintage",
                                    drift=0).convert("L")).astype(int)
    b = np.asarray(motion_mod.frame(spec, (1920, 1080), 0.86, "vintage",
                                    drift=0).convert("L")).astype(int)
    rows = np.where(np.abs(a - b).max(axis=1) > 25)[0]
    suite.check("graphics: a flow's tokens move after it has built",
                len(rows) > 0 and rows.max() - rows.min() < 60,
                f"{len(rows)} rows changed")

    # Nothing in a portrait graphic reaches the caption band, which sits
    # higher in portrait to stay clear of the phone app's controls.
    bare = np.asarray(motion_mod.backdrop(
        (1080, 1920), motion_mod.palette("vintage")["bg"]).convert("L")).astype(int)
    floor = int(1920 * motion_mod.safe_bottom(1080, 1920))
    samples = {
        "bar_chart": {"kind": "bar_chart", "title": "储蓄率", "unit": "%",
                      "items": [{"label": "中国", "value": 45},
                                {"label": "德国", "value": 28}]},
        "flow": {"kind": "flow", "title": "钱的流动", "caption": "存款变成贷款",
                 "nodes": ["家庭", "银行", "企业", "工厂"]},
        "breakdown": {"kind": "breakdown", "title": "GDP", "unit": "%",
                      "show_values": True,
                      "items": [{"label": "消费", "value": 53},
                                {"label": "投资", "value": 43},
                                {"label": "净出口", "value": 4}]},
        "comparison": {"kind": "comparison", "title": "工资与物价",
                       "left": {"label": "工资", "value": 100},
                       "right": {"label": "物价", "value": 140}},
    }
    for kind, spec in samples.items():
        arr = np.asarray(motion_mod.frame(spec, (1080, 1920), 0.9, "vintage",
                                          drift=0).convert("L")).astype(int)
        inked = np.where(np.abs(arr - bare).max(axis=1) > 25)[0]
        low = int(inked.max()) if len(inked) else 0
        suite.check(f"graphics: portrait {kind} stays above the captions",
                    low <= floor, f"ink ends {low}, floor {floor}")


def _caption_bands(suite):
    import footage_build as fb_mod
    import footage_render as fr_mod
    import motion as motion_mod

    def block(lay, zh, en):
        """Top and bottom of a caption block as set, stroke included."""
        where = fr_mod.caption_layout(lay, zh, en)
        edges = []
        for lines, px, centre in (where["zh"], where["en"]):
            lh = textkit.line_height(px, True)
            total = lh * len(lines) + int(px * 0.20) * (len(lines) - 1)
            pad = max(2, round(px * 0.085)) + 6
            edges += [centre - total // 2 - pad, centre - total // 2 + total + pad]
        return min(edges), max(edges)

    english = ("So once you understand where the money goes, you understand "
               "why prices keep rising.")
    for orientation in ("landscape", "portrait"):
        lay = layout_mod.Layout(orientation)
        top, bottom = block(lay, "所以理解钱的流向。", english)
        graphics = motion_mod.safe_bottom(lay.width, lay.height) * lay.height
        suite.check(f"footage: {orientation} captions start below the graphics",
                    top >= graphics, f"caption top {top}, graphics end {graphics:.0f}")
        suite.check(f"footage: {orientation} captions end above the app's controls",
                    bottom <= fr_mod.caption_floor(lay) * lay.height + 1,
                    f"caption bottom {bottom}, floor "
                    f"{fr_mod.caption_floor(lay) * lay.height:.0f}")

    # A long beat is shown a line at a time, as every reference frame shows
    # it, and its English stays up across all of its lines.
    lay = layout_mod.Layout("landscape")
    beat = ("在整个经济里，消费占百分之五十三，投资占百分之四十三，"
            "净出口只占百分之四，政府支出占剩下的部分。")
    spans = fb_mod.caption_spans(
        [{"beat": beat, "en": "The parts of GDP."},
         {"beat": "就是这样。", "en": "That's it."}], [9.0, 2.0],
        gap=0.12, lay=lay)
    first = [s for s in spans if s[4] == "The parts of GDP."]
    suite.check("footage: a long beat is captioned a line at a time",
                len(first) > 1 and all(
                    len(textkit.wrap(s[2], lay.subtitle_font_px(),
                                     lay.subtitle_max_px)) == 1 for s in first),
                " | ".join(s[2] for s in first))
    suite.check("footage: its lines run back to back and end on the gap",
                all(abs(a[1] - b[0]) < 1e-9 for a, b in zip(first, first[1:]))
                and abs(first[0][0]) < 1e-9 and abs(first[-1][1] - 8.88) < 1e-6,
                f"{[(round(s[0], 2), round(s[1], 2)) for s in first]}")
    paused = fb_mod.caption_spans(
        [{"beat": beat, "en": ""}], [9.0], gap=0.12, lay=lay,
        speech=[{"speech": [0.2, 8.6], "pauses": [[5.2, 5.5]]}])
    # By length alone the second line would start at 4.94 s; the voice pauses
    # from 5.2 to 5.5, so it starts just before the voice resumes.
    suite.check("footage: a line changes where the voice pauses",
                len(paused) == 2 and abs(paused[1][0] - (5.5 - 0.06)) < 1e-6,
                f"{[round(s[0], 2) for s in paused]}")


def _voice_and_draft(suite):
    """The last beat's missing tail, and a whole offline build with its draft."""
    import config as config_mod
    import footage as footage_mod
    import footage_build as fb_mod
    import footage_draft as fd_mod
    import footage_render as fr_mod
    import tts as tts_mod

    def tone(text, out_path, speaker=None, speed=1.0, emotion=None, timeout=180):
        """A stand-in voice: a quiet tone as long as the line would be."""
        seconds = tts_mod.estimate_duration(text, speed)
        out = Path(out_path).with_suffix(".wav")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([config_mod.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"sine=frequency=220:sample_rate=24000:"
                              f"duration={seconds:.3f}",
                        "-af", "volume=0.3", str(out)], check=True)
        return {"path": out, "duration": tts_mod.probe_duration(out),
                "words": [], "degraded": False}

    def plans(beats, durations=None, **_):
        """What retrieval would return, without asking anyone: one beat that
        wanted footage and found none, then a chart."""
        out = []
        for i, beat in enumerate(beats):
            out.append({"id": i + 1, "beat": beat, "en": f"Line {i + 1}.",
                        "needs": "a quiet street", "chosen": None,
                        "graphic": None if i == 0 else {
                            "kind": "counter", "value": 7.2, "unit": "%"},
                        "composite": None, "graphic_notes": []})
        return out

    def plain(needs, target, seconds, size, grade, move_index=0):
        subprocess.run([config_mod.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"color=c=0x556677:s={size[0]}x{size[1]}:r=30:"
                              f"d={seconds:.3f}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target)],
                       check=True)
        return target

    saved = (footage_mod.select_footage, fr_mod.fill_shot, tts_mod.synth,
             config_mod.ARK_API_KEY, config_mod.TTS_KEY)
    try:
        # Nothing here may reach a paid service, whatever the environment has.
        config_mod.ARK_API_KEY, config_mod.TTS_KEY = "", ""
        tts_mod.synth = tone
        with tempfile.TemporaryDirectory() as tmp:
            pieces = fb_mod.stage_voice(["第一句。", "第二句。"], Path(tmp),
                                        speed=1.5)
            index = fb_mod.voice_index(Path(tmp))
            tail = fb_mod.paced(fb_mod.TAIL_PAD, 1.5)
            suite.check("footage: the last beat carries no tail",
                        abs(pieces[0][1] - index["1"]["duration"] - tail) < 1e-6
                        and abs(pieces[1][1] - index["2"]["duration"]) < 1e-6,
                        f"{pieces[0][1]:.2f}s and {pieces[1][1]:.2f}s")
            suite.check("footage: the voice stage measures where speech is",
                        index["1"].get("speech") is not None, str(index["1"]))

        footage_mod.select_footage = plans
        fr_mod.fill_shot = plain
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            script = work / "draftcheck.txt"
            script.write_text("物价一直在涨。\n去年的通胀率，达到了百分之七点二，"
                              "这是十年来最高的一次，比前年高出不少。",
                              encoding="utf-8")
            final, failures = fb_mod.build(
                script, work / "out", grade="vintage", provider="commons",
                hook="物价一直在涨", draft=True, draft_here=True)
            draft_dir = work / "out" / "jianying" / "draftcheck"
            rows = (fd_mod.check(work / "out", draft_dir)
                    if (draft_dir / "draft_content.json").exists() else [])
            suite.check("footage: an offline build writes an editable draft",
                        failures == 0 and rows and all(ok for _, ok, _ in rows),
                        "; ".join(f"{n}: {d}" for n, ok, d in rows if not ok)
                        or f"{len(rows)} draft checks")
            content = json.loads((draft_dir / "draft_content.json").read_text(
                encoding="utf-8")) if draft_dir.exists() else {"tracks": []}
            names = {t["name"]: len(t["segments"]) for t in content["tracks"]}
            suite.check("footage: the draft carries the hook and the rule",
                        names.get(fd_mod.HOOK) == 1 and names.get(fd_mod.RULE) == 1,
                        str(names))
            # A draft that does not match its timeline must fail the check -
            # a check that cannot fail is not one.
            if draft_dir.exists():
                moved = json.loads(json.dumps(content))
                picture = next(t for t in moved["tracks"]
                               if t["name"] == fd_mod.PICTURE)
                picture["segments"][-1]["target_timerange"]["start"] += 200000
                (draft_dir / "draft_content.json").write_text(
                    json.dumps(moved), encoding="utf-8")
                broken = dict((n, ok) for n, ok, _ in
                              fd_mod.check(work / "out", draft_dir))
                suite.check("footage: the draft check notices a moved shot",
                            broken.get("draft: every shot on its boundary") is False)
    finally:
        (footage_mod.select_footage, fr_mod.fill_shot, tts_mod.synth,
         config_mod.ARK_API_KEY, config_mod.TTS_KEY) = saved
