# Volcengine endpoints, as verified

Everything here was probed against the live service. Where a wrong answer is
easy to reach, the wrong answer and its error are recorded too, because the
error messages are not self-explanatory.

## Ark, Agent Plan

| | |
|---|---|
| Base URL | `https://ark.cn-beijing.volces.com/api/plan/v3` |
| Auth | `Authorization: Bearer ark-…` |
| Text model | `doubao-seed-2.0-lite` (resolves to `doubao-seed-2-0-lite-260215`) |
| Image model | `doubao-seedream-5-0-lite` |

**The base path is the thing that catches people.** An Agent Plan key on the
pay-as-you-go path `/api/v3` returns:

```
401 {"error":{"code":"AuthenticationError",
     "message":"The API key or AK/SK in the request is missing or invalid."}}
```

which reads like a bad key and is not. Note that this is a *different* message
from the one a genuinely unknown key gets on the same path
(`"The API key doesn't exist."`) — that difference is the quickest way to tell
"wrong URL" from "wrong key".

### Images

- Minimum output size is **3,686,400 pixels**. Anything smaller returns
  `InvalidParameter … image size must be at least 3686400 pixels`. Use
  `2560x1440` for landscape, `1440x2560` for portrait.
- `doubao-seedream-4-0-*` is **not** on the plan:
  `404 UnsupportedModel … does not support the agent plan feature`.
- Output is **JPEG**. There is no alpha channel, and `transparent_background`,
  `background: "transparent"` and similar parameters are accepted and silently
  ignored — the result is still a JPEG. Cutouts have to be done locally, which
  is why sprites are generated on a chroma background; see `matting.py`.
- About 19–24 s per 2K image.

### Text

`doubao-seed-*` are reasoning models and think at length by default. One
director call ran **over nine minutes** and spent most of its completion tokens
on reasoning that never reached the storyboard. Passing

```json
"thinking": {"type": "disabled"}
```

cuts a short call from 4.0 s to 0.7 s and produces the same storyboard quality
for this task. `ark.chat()` disables it unless a caller opts in.

## Speech — on the plan, behind a different header

`seed-tts-2.0` at
`https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional` works with
**the same ark- key**, sent as `X-Api-Key`. No separate speech account is
needed.

```
Content-Type:        application/json
X-Api-Key:           ark-…                 # the Agent Plan key
X-Api-Resource-Id:   seed-tts-2.0          # ASR: volc.seedasr.sauc.duration
X-Api-Connect-Id:    <uuid>
```

This is worth stating loudly, because the older openspeech scheme —
`X-Api-App-Key` plus `X-Api-Access-Key`, issued separately from
console.volcengine.com/speech/app — is what almost all material describes, and
on this endpoint **every** combination of it returns:

```
401 {"header":{"code":45000010,
     "message":"load grant: requested grant not found in SaaS storage"}}
```

That reads like a missing entitlement and sends you off to buy a speech
package. It is simply the wrong header. Swapping to `X-Api-Key` returns 200
immediately on the same key.

Sequence of errors while feeling your way in with the *old* scheme, none of
which point at the real problem:

| Sent | Message |
|---|---|
| no `X-Api-Resource-Id` | `get resource id empty` |
| no `X-Api-App-Key` | `app key not found in header or query` |
| no `X-Api-Access-Key` | `no token or access_key was found from the header or query` |
| all three, ark key | `45000010 load grant: requested grant not found` |

Also 404, for completeness: `/api/plan/v3/audio/speech` and
`/api/v3/audio/speech`. There is no OpenAI-shaped speech route on Ark.

### Voices must match the resource

`seed-tts-2.0` speaks through the **`_uranus_`** voice family. A `_moon_` voice
from the earlier bigtts models is rejected with:

```
{"code":55000000,"message":"resource ID is mismatched with speaker related resource"}
```

Verified working:

| Speaker | Voice |
|---|---|
| `zh_male_yuanboxiaoshu_uranus_bigtts` | warm male narrator — the default |
| `zh_female_gaolengyujie_uranus_bigtts` | cool female |
| `zh_female_shuangkuaisisi_uranus_bigtts` | brisk female |

Not every `_uranus_` name exists: `zh_female_wanwanxiaohe_uranus_bigtts` and
`zh_male_beijingxiaoye_uranus_bigtts` both come back mismatched.

### Request and reply

```json
{"user": {"uid": "..."},
 "req_params": {
   "text": "…",
   "speaker": "zh_male_yuanboxiaoshu_uranus_bigtts",
   "audio_params": {"format": "mp3", "sample_rate": 24000,
                    "enable_timestamp": true, "speech_rate": -10}}}
```

`speech_rate` is an integer percentage offset in [-50, 100].

The reply is newline-delimited JSON — **not** SSE, though `tts.py` tolerates
both — one object per chunk:

```json
{"code": 0, "message": "", "data": "<base64 mp3 slice>"}
{"code": 0, "message": "", "sentence": {"phonemes": [], "text": "…", "words": []}}
{"code": 20000000, "message": "OK"}
```

`20000000` is the terminal marker, not an error. The `sentence.words` array is
present in the schema but comes back **empty** for this resource, so word-level
caption timing is not available; `tts.py` reads it anyway in case that changes.

Roughly 4.2s of audio per 20 Chinese characters, mp3 at ~64 kbps.

### Other speech endpoints on the plan

| | |
|---|---|
| TTS, bidirectional stream | `wss://openspeech.bytedance.com/api/v3/plan/tts/bidirection` |
| TTS, unidirectional stream | `wss://openspeech.bytedance.com/api/v3/plan/tts/unidirectional/stream` |
| ASR, bidirectional | `wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async` |
| ASR, unidirectional | `wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_nostream` |

The pipeline uses the plain HTTP route: narration is generated per shot ahead
of the render, so there is nothing to stream to.
