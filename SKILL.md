---
name: cartoon-econ-video
description: "把一段口播文案自动做成知识科普成片（MP4）。内置多种画风（比奇堡海绵宝宝为默认，另有水彩动漫、黏土定格、赛博霓虹、复古欧式插画、复古公路海报、极简几何商务；画风名单与默认项由 casts/styles.json 统一配置）。固定背景板 + AI生成的透明底角色/道具素材分层合成，逐镜演绎文案，镜头间溶解转场，底部白字黑边字幕，AI书法标题开场与金句收尾。用户只需给出文案、确认画风、选择横屏或竖屏，其余（分镜编排、素材生成与抠图、AI配音、渲染、混音、出片、质检）全自动完成。当用户说『把这段文案做成视频』『做个海绵宝宝讲XX的视频』『地瓜经济那种风格』『自动剪一条科普短视频』并附上文案时使用。只需一把 Volcengine Ark Agent Plan 的 API key（导演、生图、配音共用）。"
---

# 卡通科普视频自动制作

用户给一段口播文案，你还回一条成片 MP4。

**这个技能的绝大部分判断已经写进代码了。你的工作是照流程跑，把 `verify` 报出来的问题按对照表修掉，不是自己发挥。**

---

## 一、铁律

前七条是从参考成片逐帧量出来的（数据在 `references/reference-findings.md`）。违反任何一条，成片就不像了。**代码已经强制执行了这些规则，不要绕过去。**

| # | 规则 | 量到的证据 |
|---|---|---|
| 1 | 背景**全片一张、逐帧不动** | 无角色区域帧间差 0.7–1.4 灰阶 = 压缩噪声 |
| 2 | 一镜之内画面**完全静止** | 相邻帧差 0.001–0.05，持续数秒 |
| 3 | 换镜只用**整镜溶解**，约 0.5s，所有元素一起淡 | 实测 0.65s，元素不单独入场 |
| 4 | 角色/道具**没有投影** | 参考片里一个都没有 |
| 5 | 实心物体**站在地面上**，只有板/图/表可以悬空 | — |
| 6 | 字幕白字黑边，中心线在画面高 90.3%，字号 0.032×宽 | 1080p 下 62px @ y=975 |
| 7 | **旁白文字 = 用户原文，一个字都不能改** | 见下面「绝不要做」第 1 条 |
| 8 | **动手前先问画风/横竖屏/时长/音色** | 生图和配音要花钱，问一轮比返工便宜 |
| 9 | **交的是剪映工程，MP4 只是预览** | 自动排版没有人眼好，留一步人工微调 |
| 10 | **前景要「演」出文案的意思，不是把相关的人和物摆在一起** | 「拿到工资」不是一个人加一袋钱同框，是有人**递**、有人**接**、并且高兴；没有合适姿势时导演会自己要一个 |

---

## 二、标准流程

### Step 0 — 检查环境

```bash
python scripts/build.py --check
```

全 `[ok]` 才继续（ffmpeg / ffprobe / Ark / 配音 / 画风文件 / python 包）。任何一行 `[MISS]`，按它给的提示补，不要往下走。

装完或改过代码之后，再跑一次离线自检（**不花钱、不联网**）：

```bash
python scripts/selftest.py
```

二十多项离线检查（含画风注册表与全部画风文件校验），覆盖分句、时长模型、抠图、构图修复、渲染、混音、质检。跑不过就是装的有问题，不要往下走。构建失败但不确定是流水线的问题还是接口的问题时，也先跑这个。

### Step 1 — 先问清楚，再动手（**不许跳过**）

**用户给了文案之后，什么都别先生成。** 先用 `AskUserQuestion` 一次性问清下面四件事。
生图和配音都是花钱的，问一轮比返工一轮便宜得多。

一次问四个问题（`AskUserQuestion` 支持多问题）：

| 问什么 | 选项 | 默认（放第一个，标 Recommended） |
|---|---|---|
| **画风 / 班底** | 用 `python scripts/new_project.py --list-styles` 拿到全部画风（比奇堡 / 水彩动漫 / 黏土定格 / 赛博霓虹 / 复古欧式插画 / 复古公路海报 / 极简几何商务）/「新建一套画风」 | 比奇堡 |
| **横屏还是竖屏** | 「横屏 1920×1080（B站/YouTube）」**默认**/「竖屏 1080×1920（抖音/小红书）」 | **横屏**（默认项在 `casts/styles.json` 的 `default_orientation`） |
| **成片时长** | 「顺其自然（按文案长度）」/「目标 60s」/「目标 90s」/「目标 3 分钟」 | 顺其自然 |
| **旁白音色** | 「温厚男声」/「知性女声」/「明快女声」 | 温厚男声 |

补充说明：

- **时长不是随便定的。** 文案多长，视频就多长。选了目标时长，语速会在 0.85×–1.20× 之间自动拟合；超出这个范围就拟合不了，Step 2 会直接告诉你差多少字。**这时要回来问用户：是改文案，还是接受实际时长。不要自己删改文案。**
- **画风的名单和默认项由 `casts/styles.json` 统一管理**（画风总配置）。比奇堡自带素材库；其余画风第一次用要先建素材库（下一步说明）。**要提前告诉用户这一步的量。**
- **画风选「新建一套」时**：复制 `casts/_template.json`（里面的 `_hint_*` 写清了每个字段该怎么填、坑在哪）成 `casts/<key>.json`，填好后先 `python scripts/build.py --check` 验一遍，再 `python scripts/build_library.py <key> --plates` 一次性把素材生成好。想要中文名就在 `casts/styles.json` 的 `styles` 里加一条 `{"<key>": {"label": "中文名", "note": "一句话"}}`。**要提前告诉用户这一步的量**：每张图约 20 秒，一套 30-60 张就是 10-20 分钟左右。素材只生成这一次，之后所有视频复用。
- 用户已经明确说过的（比如「做成竖屏的」），就不用再问，但要在 Step 2 的确认摘要里复述一遍。

### Step 2 — 写 project 文件，把预估摆给用户看

把文案存成 `examples/<名字>.txt`（**一句一行，原样保存**），然后：

```bash
python scripts/new_project.py \
    --name my_video --title "什么是效率工资" \
    --script examples/my_video.txt \
    --orientation landscape --target 90 --voice male
```

它不联网，只做三件事：**校验画风文件**、**预估成片时长**、**算出要生成多少张图**。输出长这样：

```
predicted result
  script      466 characters
  shots       15
  narration   79.9s
  cards        6.6s
  total       86.5s at 1.00x speed

what this will generate
  sprites     up to 5 new image(s); 32 already in the library
  background  reuse the cached plate
  narration   15 clips
```

**把这段贴给用户，等确认再往下。** 退出码 2 表示目标时长做不到，原因和差多少字都写在输出里。

时长预估是拿 45 条真实配音拟合出来的（`duration = 0.161×字数 + 0.484`），实测误差 0.3 秒，可以直接报给用户。

### Step 3 — 出分镜预览，给用户确认

```bash
python scripts/build.py projects/my_video.json --preview
```

**必须把 `out/my_video/preview.jpg` 发给用户看过再往下。** 这一步花几分钟，能省掉一次全量返工——三个真实缺陷都是在这张图上第一次被发现的。

### Step 4 — 出片（会自动质检）

```bash
python scripts/build.py projects/my_video.json
```

八个阶段跑完后**自动跑一遍 verify**，打印 12 项检查。全 PASS 才算完。

最后一个阶段（draft）会导出**剪映可编辑工程**，并当场跟渲染结果逐镜比对；
对不上会直接报错，不会把一个错位的工程丢给用户。

### Step 5 — 有 FAIL 就查对照表修，然后重跑

```bash
python scripts/build.py projects/my_video.json --from storyboard
```

改 `plan.json` 后重跑 `--from storyboard` 不会重新调模型、不会重新配音，很便宜。

### Step 6 — 交付

**主交付物是剪映工程，不是 MP4。**

```bash
python scripts/draft.py out/my_video --install
```

`--install` 会直接写进剪映的草稿目录，用户打开剪映就能看到。没装剪映或找不到
目录时，工程在 `out/my_video/jianying/`，让用户自己拷过去。

交付时要说清三件事：

1. 时长、分辨率，以及 `out/my_video/my_video.mp4` 是**预览**，不是最终稿；
2. 工程里轨道是分开的（`背景` / `图层N` / `配音` / `字幕`），可以逐个调；
3. **每个元素是整画布大小的透明图层**，所以选中框比人物大一圈——拖动和缩放都
   正常，只是手柄在画布边上。这是为了绕开剪映 `scale` 语义没有文档这件事，
   见 `scripts/draft.py` 开头。

素材是**绝对路径**引用的：工程文件夹和它的 `materials/` 要一起移动，否则重导。

### 退出码（可以直接用来卡流程）

| 码 | 意思 | 该怎么办 |
|---|---|---|
| 0 | 成片完成，12 项质检全过 | 交付 |
| 1 | 质检有 FAIL | 查下面的对照表 |
| 2 | 文案/project/画风文件有问题 | 按提示改输入，**不要改流水线** |
| 3 | 接口拒绝了请求 | 跑 `--check`，看 `references/api-notes.md` |
| 130 | 被中断 | 直接重跑，会从上一个完成的阶段接着走 |

---

## 三、verify 失败对照表

**照这张表修，不要自己想别的办法。**

| FAIL 项 | 真实原因 | 怎么修 |
|---|---|---|
| `background is static`（p75 > 3） | 有东西在动。多半是有人给背景加了视差/气泡，或者背景图被逐镜换了 | 检查有没有人改过 `render.py`。背景必须是 `background.png` 一张到底 |
| `shot layout` 报 overlap | 两个素材撞在一起 | 跑 `--from storyboard`，自动排开。还报就手动改 `plan.json` 里的 `x` |
| `shot layout` 报 runs off the side | 素材太大或 x 太靠边 | 改 `plan.json`：调小 `h`，或把 `x` 拉回 0.2–0.8 |
| `sprite cutouts` 报 kept the whole frame | 生图没给出品红底，抠图失败 | 删掉 `casts/<cast>/sprites/<那个文件>.png` 重跑；还不行就改 cast 里那条 prop 描述 |
| `audio is not silent` FAIL | 配音全没生成 | 跑 `--check` 看 TTS 那行；音色必须是 `_uranus_` 系列 |
| `duration` 对不上 | storyboard 和成片不一致，通常是手改过 storyboard.json | 删掉 `storyboard.json`，`--from storyboard` 重生成 |
| `subtitle timing` 报 overlapping | 手改过 srt 或 plan 的时间 | 删 `storyboard.json` 重生成 |

---

## 四、绝不要做

1. **不要改用户的文案。** 一个字都不要。导演模型只负责选素材，分句是代码做的（`plan.split_script`）。曾经出过事：39 字的文案被模型扩写成 112 字，凭空加了「珊迪做了个实验」这种原文没有的情节。现在代码不给模型碰文字的机会，**不要把这个权限还回去**。
2. **不要加动效。** 不要呼吸微动、不要气泡层、不要背景视差、不要运镜、不要元素逐个滑入弹出、不要角色投影。参考片里一个都没有，加了只会更不像，还慢。
3. **不要让图像模型写中文。** 标签、气泡、卡片正文全部用 PIL 画。唯一例外是标题书法字，而那条路径**带视觉模型校验**：读回来对不上就自动退回 PIL 画的版本。
4. **不要为了省事跳过 `--preview`。** 用户没看过构图就出片，返工代价大得多。
5. **不要手改 `storyboard.json`。** 它是从 `plan.json` + 配音时长生成的，改了下次就被覆盖。要改就改 `plan.json`。
6. **不要为了换题材去改代码。** 换 cast 文件。
7. **不要跳过 verify**（`--no-verify` 只在调试时用）。

---

## 五、想调效果，改哪里

**观感相关的一切都在 `casts/styles.json`，不用改 Python。** 字幕大小/位置、镜头
松紧、标签配色、转场时长、构图安全边距、抠图力度，原来都硬编码在
`layout.py` / `render.py` / `checks.py` / `matting.py` 里，现在全在 `look` 段。
只想改某一个画风就在该画风条目里写 `look`，只写要改的那几项，其余自动继承。

| 想改什么 | 改哪里 |
|---|---|
| **字幕大小 / 位置** | `casts/styles.json` → `look.frame.<横竖屏>.subtitle_size` / `subtitle_y` |
| **镜头松紧、标签配色、转场时长** | `casts/styles.json` → `look.framing` / `look.label_tones` / `look.timing` |
| **构图安全边距、抠图力度** | `casts/styles.json` → `look.safe_zones` / `look.matting`（改之前先看注释） |
| **只给某一个画风改上面任意一项** | 该画风条目里加 `"look": { ... }`，只写要改的那几项 |
| **画风总配置（名单 / 默认画风 / 中文名 / 隐藏）** | `casts/styles.json` —— 换默认画风只改这一个 |
| project 用哪套画风 | project 的 `cast` 字段写画风 key（如 `"clay"`）或 cast 文件路径；不写就用 `styles.json` 的 `default` |
| 谁出场、什么姿势、有什么道具 | `casts/<cast>.json` 的 `characters` / `props` |
| 画风 | `casts/<cast>.json` 的 `style` |
| 背景（**最影响观感**：地平线位置决定角色站得稳不稳） | `casts/<cast>.json` 的 `background.prompt`，说清地平线在哪、两边留空 |
| 哪些道具可以悬空 | `casts/<cast>.json` 的 `hanging` 列表 |
| 哪些道具挡在角色前面 | `casts/<cast>.json` 的 `foreground` 列表 |
| 哪些道具是「可以往上写字」的板面 | `casts/<cast>.json` 的 `writable` 列表；落在上面的标签会自动吸附到板中央，落在角色身上的会被挪开 |
| 标签用哪个色 | 标签的 `tone`：`good` / `bad` / `money` / 不写就是深灰；**具体颜色**在 `styles.json` 的 `look.label_tones` |
| 精确的前后遮挡 | 元素上写 `z`（数字，小的在后），只在三层默认排序不够用时才写 |
| 某一镜的构图 | `out/<name>/plan.json` 的 `x` / `y` / `h` / `framing` |
| **某一镜「没演出文案的意思」** | 看 `plan.json` 里那一镜的 `beat`：`action` 错了是理解错了，`action` 对但选的姿势不对是选角错了。改 `elements`，或在 `casts/<cast>.json` 的 `poses` 里补一个真正在做这个动作的姿势 |
| **导演自己加的姿势** | `casts/<cast>/learned_poses.json`；每条视频最多加 8 个，画一次以后所有视频复用。删掉就是下次再问一遍 |
| **镜头运动（推镜）** | `casts/styles.json` → `look.motion`；**只在剪映工程里有，MP4 预览是静止的**。默认只推特写、5%、上限 8%。这是唯一没法在本地验证的功能，第一次打开工程要专门看一眼，不对就 `enabled: false` |
| **音效** | `look.sound.cues`：每个「时刻」给一组音效，每次挑**最久没用过**的那个，所以不会一个声音响七遍。音效由 `python scripts/gen_sfx.py` 合成，共 25 个 |
| **某个情绪配错了音** | 音效是按导演写的 `beat.emotion` 选的（`sfx.py` 的 `EMOTION_WORDS` 是中英文关键词表）。配错了先看 `plan.json` 里那一镜的 emotion 写了什么 |
| **字幕动画** | `look.text.animation`，只在剪映工程里生效 |
| **「递东西 / 互动」演不出来** | 两张分开的素材永远碰不到一起。导演会要一张**双人素材** `duo_<甲>_<乙>_<动作>.png`，两个人画在同一张图里，每条视频最多 4 张。出来解剖有问题（多手、断肢）就删掉那张 PNG 重跑一次，自己看哪张好 —— 模型自己挑不准，实测它会挑中有断肢的那张 |
| 把某一镜放到别的场景（办公室/码头/店里） | 加一个 `{"type":"panel", ...}`，是一块画在所有人后面的色块，参考片就是这么在同一张背景上做出室内场景的 |
| 镜头松紧 | 每镜的 `framing`：`wide` / `medium` / `close`；**倍数**在 `styles.json` 的 `look.framing` |
| 每镜多长 | project 的 `shot_seconds`（默认 5.0） |
| 成片时长 | project 的 `target_seconds`；语速在 0.85×–1.20× 内自动拟合，超出范围会报出来 |
| 溶解快慢 | project 的 `dissolve`；不写就用 `styles.json` 的 `look.timing.dissolve` |
| 音色 / 语速 | project 的 `voice`（音色必须 `_uranus_` 系列） |
| 重新生成某个素材 | 删 `casts/<cast>/sprites/<name>.png` 再跑 |

---

## 六、代码是怎么防呆的

改代码前先理解这些，它们都是踩过坑加上去的：

- **文案不经过模型。** `plan.split_script` 按标点切、按字数打包，模型只回「每一镜用哪些素材」。
- **素材名会校验。** 模型编的 `patrick_happy.png` 会自动落到同一角色的另一个姿势；完全对不上的丢掉并报出来。
- **坐标会夹紧。** 越界的拉回来；实心道具无论模型写了什么锚点都放回地面。
- **一镜之内同一个角色只能出现一次。**
- **构图碰撞会自动排开。** 素材尺寸只有 PNG 落地后才知道，所以这一步在 storyboard 阶段做（`checks.py`）。
- **镜头景别不会连着三个一样**，避免看起来像 PPT。
- **标题书法字会被视觉模型读回来核对**，对不上就退回 PIL 画。
- **素材只生成一次**，存在 cast 下，所有视频复用同一批 PNG——角色因此不可能漂移。背景板同理，全系列共用一张。
- **文案没标点也不会炸**。没有句号的长句会在逗号处切开，再不行就硬切——不然 200 字会变成一个 33 秒的镜头，字幕铺满 8 行把角色全盖住。
- **空镜会自动补**。这一镜没选到任何素材，就沿用上一镜的；只有布景没有角色（比如一个吧台加一排顾客），会自动补一个角色进去。只有图表的镜头不算空镜，参考片里就有。
- **素材文件丢了不会中断渲染**，跳过那一个元素并报出来——这一步在花完钱之后，不该因为一个 PNG 全盘重来。
- **构图修复会自己迭代到稳定**，最多三轮，不会「修完还是报同一个问题」。
- **中途被杀不会留下半个文件**。渲染、混音、封装都先写 `.partial` 再改名，中断了就等于没发生。缓存的渲染在复用前还会被 ffprobe 验一遍，坏了就自己重渲。
- **输入错误不会甩堆栈**。文案空的、文件找不到、JSON 写坏了，都会给一句人话加一个退出码。
- **配音会重试**。实测 32 镜的片子里有 2 次 SSL 闪断，不重试就是两个镜头没声音。失败的镜头在下次运行时会自动重试，不会被「已缓存」跳过。
- **`--from` 会把下游的缓存删掉**。曾经 `--from storyboard` 会复用旧的 `video_mute.mp4`，改了 plan 却渲染出一模一样的片子。
- **镜头景别会自动配平**，close 太少会补到约五分之一——一部片子从头到尾没有一个近景会显得很平。
- **收尾金句太长会自动截到最后一个分句**，卡片是一句话，不是一段话。

---

## 七、凭据

**一把 key 全搞定**：`ARK_API_KEY`（console.volcengine.com/ark），导演、生图、配音都用它。

两个坑，报错都指向别处：

- Agent Plan 走 `/api/plan/v3`，**不是** `/api/v3`。后者返回 401，看起来像 key 废了，其实是路径错了。
- 语音用 `X-Api-Key` 这一个 header。老的 openspeech 方案（App Key + Access Key）在这个端点一律 `45000010 grant not found`，看起来像没开通，其实只是 header 用错了。**不需要**单独开通语音服务。

| 项 | 值 |
|---|---|
| 生图 | `doubao-seedream-5-0-lite`（`seedream-4-0-*` 不在套餐内），最低 2K |
| 导演/视觉 | `doubao-seed-2.0-lite`，必须关 thinking，否则一次调用能跑九分钟 |
| 配音 | `X-Api-Resource-Id: seed-tts-2.0` |
| 音色 | 必须 `_uranus_` 系列；`_moon_` 会报 speaker 与 resource 不匹配 |

已验证音色：`zh_male_yuanboxiaoshu_uranus_bigtts`（温厚男声，默认）、`zh_female_gaolengyujie_uranus_bigtts`、`zh_female_shuangkuaisisi_uranus_bigtts`。

没有 key 也能跑完：时长退化为按字数估算，成片没旁白，每个降级的阶段都会报出来。完整接口事实见 `references/api-notes.md`。

---

## 八、常用命令

| 目的 | 命令 |
|---|---|
| 检查环境（接口侧） | `python scripts/build.py --check` |
| 离线自检（装完/改完必跑） | `python scripts/selftest.py` |
| 列出全部画风 | `python scripts/new_project.py --list-styles` |
| 建 project + 看预估（**先做**） | `python scripts/new_project.py --name … --title … --script … --cast <画风key> --orientation …` |
| 分镜预览（**必做**） | `python scripts/build.py <proj> --preview` |
| 出片（含自动质检） | `python scripts/build.py <proj>` |
| 单独质检 | `python scripts/verify.py <proj> --verbose` |
| 改了 plan 后重出片 | `python scripts/build.py <proj> --from storyboard` |
| 只建素材库 | `python scripts/build.py <proj> --stop-after assets` |
| 单张抠图 | `python scripts/matting.py in.jpg out.png` |
| **查每一镜有没有演出文案**（花钱，每镜一次视觉调用） | `python scripts/critique.py out/<name>` |
| 重新生成音效库 | `python scripts/gen_sfx.py` |
| 导出剪映工程（**交付物**） | `python scripts/draft.py out/<name> --install` |
| 单独校验剪映工程 | `python scripts/check_draft.py out/<name>` |
| 老 plan 套用新规则（不调模型） | `python scripts/migrate_plan.py out/<name>/plan.json <画风key或cast路径>` |
| 新画风：一次性生成整套素材 | `python scripts/build_library.py <画风key> --plates` |

## 文件

```
SKILL.md                     本文件
README.md                    环境搭建与设计说明
.env.example                 凭据模板（复制成 .env）
casts/
  styles.json                画风总配置：默认画风、每个画风的中文名与说明
  _template.json             新画风模板（_hint_* 里写了每个字段怎么填）
  bikini_bottom.json         比奇堡：海绵宝宝风，5 角色 60 素材（默认画风）
  watercolor_anime.json      水彩动漫：手绘水彩动画电影质感，3 角色
  clay.json                  黏土定格：橡皮泥定格动画质感，3 角色
  neon_cyberpunk.json        赛博霓虹：霓虹雨夜都市，3 角色
  retro_editorial.json       复古欧式插画：雨夜街角咖啡馆，3 角色
  retro_pulp.json            复古公路海报：70 年代丝网印刷海报，3 角色
  flat_geo.json              极简几何商务：扁平几何色块剪影风，3 角色
projects/*.json              一条视频一个文件
examples/*.txt               口播文案
scripts/
  styles.py                  画风注册表：解析画风 key / 路径、默认画风、校验
  new_project.py             建 project 文件 + 校验画风 + 预估时长与用量（不联网）
  timing.py                  时长预估与目标时长拟合（拟合自 45 条真实配音）
  build.py                   主入口，七阶段编排，收尾自动质检
  verify.py                   12 项自动质检，退出码可用来卡流程
  plan.py                    分句（代码做）+ 导演选素材（模型做）+ 校验
  assets.py                  素材库：生成 → 抠图 → 缓存；标题书法字 + 视觉校验
  matting.py                 品红抠图 + 边缘去色 + 自动裁切
  render.py                  渲染：固定底板、整镜溶解、景别缩放、字幕
  textkit.py                 字体、断行、描边字、标签、气泡、卡片
  layout.py                  横竖屏几何（stage 相对坐标）
  ark.py                     Ark：导演文本 + 生图 + 视觉读图
  tts.py                     火山语音合成
  audio.py                   旁白拼接、配乐混音、SRT
  preview.py                 分镜联系表
  migrate_plan.py            把旧 plan 重新过一遍校验（新规则生效，不花钱）
  build_library.py           一次性生成某个 cast 的全部素材
  selftest.py                 离线自检（含画风注册表校验），不联网不花钱
  checks.py                  构图碰撞/越界检测与自动修复
references/
  reference-findings.md      参考片逐帧测量结果（铁律的出处）
  storyboard-schema.md       plan.json / storyboard.json 字段说明
  api-notes.md               已验证的接口事实
```
