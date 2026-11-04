# 印尼语 ASR 准确度评测 · 执行计划（交接 CodeBuddy）

> 编写时间：2026-11-02 14:20 · 编写者：Claude（WorkBuddy 会话）
> 本文是**可执行交接文档**：现状已实测、命令已验证。CodeBuddy 读完后从 §3 的 T0 开始按顺序做，不要重新设计框架。
> 相关文档：`data/asr-eval/README.md`（分层定义 + GT 规则）、`data/asr-eval/TODO.md`（任务勾选）、`docs/会议转写系统需求设计.md` §5.8（原始优化动作清单与验收口径）。

---

## 0. 一句话目标

把"印尼语真实录音准确率 60–80%"这个**主观估计**变成**可复现、分层、带口径说明的数字**，并用 A/B 量化 §5.8 的优化动作；在拿不到印尼语母语者的情况下，先把**不依赖人力**的部分（约 80%）做完。

**最终交付**：一份能对客户讲的报告 —— 干净语音多少、广播多少、现场录音多少、哪些是物理限制、做了哪些优化各自值多少。

---

## 1. 现状快照（2026-11-02 实测，勿重复劳动）

### 1.1 基线数字

| 层 | 语料 | 参考词数 | WER | 口径 | 状态 |
|---|---|---|---|---|---|
| L0 | `dialog_id`（旧 speakr TTS 对话） | 105 | **0.00%** | 逐条 | ✅ |
| L0 | `meeting_id`（本机 say TTS，会议场景 31 句） | 214 | **0.00%** | 逐条 | ✅ |
| L0 | `news_id`（本机 say TTS，新闻腔 16 句） | 137 | **1.46%** | 逐条 | ⚠️ 2 处 `dana→dene`，TTS 韵律问题，非模型缺陷 |
| L1 | FLEURS id_id test，124 条真人朗读 | 2380 | **5.08%** | **逐条（头条口径）** | ✅ 优于公开基线 ≈7.8% |
| L1 | 同 2380 词拼成 18 条 90s 长轨 | 2380 | 7.23% | 拼接 | ⚠️ 拼接代价 +2.15pp |
| L1b | CNN/CNBC Indonesia 播报 4×120s | — | 无 GT | — | ⏸ |
| L2 | 印尼员工录制 | — | — | — | ⏸ 空目录，等录制 |
| L3 | LionAir 01/02 全量 | — | 无 GT | — | ⏸ 仅音频 + 旧 ASR 参考文本 |

### 1.2 被测系统与已验证能力

```
ASR 服务：http://<ASR_HOST>:9010/asr   （whisperx-asr-service，CUDA，serve_mode=simple）
健康检查：GET /health → {"status":"healthy","device":"cuda","loaded_models":["large-v3"]}
```

- **`/asr` 可用参数**（openapi 实测，勿臆造）：`task, language, initial_prompt, hotwords, word_timestamps, output_format, output, model, num_speakers, min_speakers, max_speakers, diarize, enable_diarization, return_speaker_embeddings`
- **模型**：`large-v3`（默认）、`large-v3-turbo`、`turbo`、`distil-*` 等均可用。**`large-v3-turbo` 已实测可跑通**（news_id 137 词，与 large-v3 文本 100% 一致）
- **`maleo-ai`（印尼语微调版）不在该服务上** → §5.8 动作 #4 的模型对比目前只能做 `large-v3 vs large-v3-turbo`；maleo 需另部署（暂不列入本计划，见 T4 备注）
- **服务未暴露 `condition_on_previous_text` / `vad_filter`** → 想关掉"续写上文的重复循环"，客户端只能靠**分段转写**模拟，或找服务端部署者改环境变量（不阻塞）
- 返回结构：顶层 `text` 是 **segment 列表**（不是字符串）；`words`（含 `score`）嵌在每个 segment 内，另有顶层 `word_segments`

### 1.3 已就绪的目录与脚本

```
data/asr-eval/
├── README.md                                 分层定义 + GT 归一化规则（必读）
├── TODO.md                                   任务勾选（本计划的影子清单）
├── gen_l0_corpus.py                          L0 TTS 生成（say -v Damayanti）
├── build_l1b.py                              L1b 抓取/切片/转写
├── scripts/eval_asr.py                       ★ 主评测脚本（jiwer WER/CER + 数字归一化 + 循环检测 + leaderboard）
├── scripts/eval_fleurs_per_utt.py            L1 逐条口径（依赖 /tmp/fleurs16，易失）
├── corpus/L0-tts/        3 条 wav + gt + hyp.json
├── corpus/L1-read/       18 条拼接轨 + utt/（124 条逐条 hyp 缓存）+ fleurs_id_test.tsv
├── corpus/L1b-broadcast/ 4 条 wav + hyp.json + meta.json（GT 缺）
├── corpus/L2-lionair/    空
├── corpus/L3-stress/     4 条 *.ref.txt + AUDIO-INVENTORY.txt（音频本体未拷入）
└── results/              leaderboard.md + 每轮 json + 基线报告 html
```

常用命令：

```bash
cd data/asr-eval
python3 scripts/eval_asr.py --pair <gt> <hyp> --layer L2 --corpus lion02a --model large-v3 --config "xxx" --tag baseline
python3 scripts/eval_asr.py --batch --layer all --model large-v3 --config "xxx" --tag baseline
python3 scripts/eval_fleurs_per_utt.py --tag per-utt          # L1 头条数字
```

---

## 2. 硬约束与已知坑（改代码前必读）

1. **本机网络**：`curl` 一律加 `--noproxy '*'`（WorkBuddy 会注入 HTTP_PROXY，不加会走隧道拿 502）。YouTube / huggingface.co 直连不通；**hf-mirror.com、cnnindonesia.com、cnbcindonesia.com、<ASR_HOST> 可达**。
2. **旧 `data/Lion Air给的培训视频/asr_json*/*.json` 的 `words` 是空数组**（当时没开 `word_timestamps`），拿不到词级置信度，**不要复用做置信度分析**。
3. **`02.txt` / `02-2.txt` 不是人工 GT**，是 ASR 自己的输出（自己跟自己比 WER=0 是无意义的）。已改存为 `corpus/L3-stress/*.ref.txt`，只作交叉参考。
4. **数字必须两侧归一化**：`delapan puluh persen` ↔ `80%`、`1.000` ↔ `1000`。不做会让 WER 虚高 3–6pp（L0 首轮全部误差都来自此）。`eval_asr.py` 已实现，别改坏。
5. **长音频拼接的 WER ≠ 逐条 WER**（5.08% vs 7.23%，差 2.15pp）。报告数字必须注明口径，头条用逐条。
6. **置信度不是准确率代理**：L1（WER 5.08%）均置信度 0.675，L1b 广播 0.68–0.76 反而更高但更不准。只用于**同域内排序抽样**。
7. **`data/Lion Air给的培训视频/` 下文件是只读（`r--r--r--`）**，不要试图原地改写；转码输出到 `corpus/L3-stress/audio/`。
8. **`/tmp/fleurs16`（per-utt 脚本依赖）会被系统清理**。若 `eval_fleurs_per_utt.py` 报缺文件，按 T1 的重建步骤恢复。
9. **silver GT 与人工 GT 不能混进同一行 leaderboard**：silver 结果 `--tag silver`，报告里必须标注一致率。
10. **`results/leaderboard.md` 只追加不删**，除非是调试产生的脏行（历史上有过 4 条脏行已清）。

---

## 3. 任务清单

> 依赖标注：`→` 表示前置。每任务都有 **DoD**，不达标不算完成。工作量等级 S（<半天）/ M（半天–1天）/ L（>1天，多为算力或人力等待）。

### T0 [P0][S] 把 ASR 调用封成可复用脚本 `scripts/asr_client.py`

**为什么**：现在每条命令都是手写 curl + 手工拼 JSON 结构，T3/T4/T5/T6 会重复几十次，出错率高。

**步骤**
1. 新建 `data/asr-eval/scripts/asr_client.py`，函数：
   - `transcribe(wav, *, model="large-v3", language="id", hotwords=None, diarize=False, word_timestamps=True, initial_prompt=None, timeout=1800) -> dict`
   - 内部 `subprocess.run(["curl","--noproxy","*","-sS","-m",str(timeout),"-X","POST",ASR, "-F", f"audio_file=@{p}", ...])`（**必须带 `--noproxy *`**）
   - `text_of(resp) -> str`：`" ".join(s["text"] for s in resp["text"])`
   - `words_of(resp) -> list`：先取各 segment 的 `words`，为空再回退顶层 `word_segments`
   - `conf_stats(words) -> {n, mean, p10, low_ratio}`
   - `preprocess(src, dst, noise_reduce=True, target_sr=16000)`：ffmpeg `afftdn` + `loudnorm` + 单声道 16k
   - `cut(src, dst, start, duration)`、`split(src, outdir, chunk_s)`
   - 失败重试 2 次（指数退避），HTTP != 200 抛异常并打印响应体前 200 字
2. 加 `if __name__ == "__main__"` 自测：对 `corpus/L0-tts/news_id.wav` 跑一次，打印词数与前 80 字。

**产出**：`scripts/asr_client.py`
**DoD**：自测输出 137 词、开头 `Selamat pagi, ini adalah berita pagi dari Jakarta.`；`from asr_client import transcribe` 在 `scripts/` 下可导入。

---

### T1 [P0][S] 评测脚本补 `[inaudible]` 剔除 + 归一化开关 + **回归验证**

**为什么**：README 规则 2 写了 `[inaudible]` / `[unclear: xxx]` 约定，但代码没实现；L1b/L3 人工 GT 一到位就会踩到。同时需要开关做敏感性分析。

**步骤**
1. `scripts/eval_asr.py` 的 `normalize()` 增加：
   - 剔除 `[inaudible]`、`(inaudible)`、`[噪声]` 等整段标记（两侧都剔）
   - `[unclear: kata]` → 只保留 `kata`
   - 新增参数 `remove_fillers`（默认 False，填充词表已有 `FILLERS`）
2. CLI 增加：`--no-numbers`（关闭数字归一化，做敏感性分析）、`--remove-fillers`、`--drop-inaudible`（默认 True）
3. **回归**：用默认参数重跑三条 L0 + L1 逐条，数字必须与 §1.1 完全一致（0.00% / 0.00% / 1.46% / 5.08%）。若不一致，说明新增逻辑污染了原有归一化，必须修正。
4. 重建 `/tmp/fleurs16`（若已丢）：从 `corpus/L1-read/fleurs_id_test.tsv` 对应的 FLEURS 原始 wav 重新 `ffmpeg -ac 1 -ar 16000 -c:a pcm_s16le` 转换；把这段写成 `scripts/prepare_fleurs.py` 以便复现。

**产出**：改好的 `eval_asr.py`、`scripts/prepare_fleurs.py`
**DoD**：`--help` 出现 3 个新开关；回归四行数字**逐位一致**；对含 `[inaudible]` 的假数据能正确剔除（自测 3 例）。

---

### T2 [P0][S] 支持更多 hyp 输入格式（SRT / VTT / speakr 导出）

**为什么**：后续改动 speakr 侧链路时，导出的是 SRT/格式化 md，不该再写一次性转换脚本。

**步骤**：扩展 `load_text()`：
- `.srt` / `.vtt`：去掉序号行、`-->` 时间行、`WEBVTT` 头，只留文本
- `*_formatted.md`（speakr 导出）：去 markdown 标记、去 `**SPEAKER_xx:**` / `**名字:**` 前缀、去 `#` 标题
- `.txt` / `.json` 行为不变（json 仍按 `text=[segment...]` 或 `segments` 兜底）

**产出**：`eval_asr.py` 的 `load_text`
**DoD**：用现有 L1b 的一条 hyp.json 手动转成 srt 再评测，WER 与直接喂 json 相同（±1e-9）。

---

### T3 [P0][M] L3 LionAir 全量诊断（无 GT 也能出结论）

**为什么**：LionAir 是客户真实现场，也是"人耳都听不清"的压力场景。GT 拿不到，但**重复循环、幻觉、置信度分布、词密度**这些指标不需要 GT，先出"风险清单"。

**步骤**
1. 转码（原文件只读，输出到 corpus）：
   ```bash
   cd data/asr-eval && mkdir -p corpus/L3-stress/audio
   ffmpeg -nostdin -hide_banner -loglevel error -y \
     -i "/Users/aaronliu/Workspaces/MeetingAI/data/Lion Air给的培训视频/01-20m.mp4" \
     -vn -ac 1 -ar 16000 -c:a pcm_s16le corpus/L3-stress/audio/lion01_full.wav
   # 02 同理：02-6m.mp4 → lion02_full.wav
   ```
2. 转写（`word_timestamps=true`、`diarize=false`）：先整段试；若 413/超时，改用已有的 `mp3_under5mb/01_audio_part0{0,1,2}.mp3` 分别转写后拼接文本（**分别评测，不要拼 wav**，避免拼接效应污染结论）。
3. 统计并写入 `corpus/L3-stress/lion0X.diag.json`：总词数、时长、**词/秒**（正常印尼语 2.0–3.0；<1.2 说明大段丢失）、重复循环段数（复用 `detect_repetitions`）、`conf<0.5` 词占比、`conf<0.7` 词占比、最长连续低置信区间。
4. 生成 **`corpus/L3-stress/lowconf_sample.md`**：把 `conf<0.55` 且**连续 ≥2 词**的片段导出为清单（序号 / 起止时间 / ASR 文本 / 平均置信度），**不超过 60 条**，按置信度升序。这是给印尼语同事定点核对的**最省人力入口**。
5. 在 `results/leaderboard.md` 追加一行 `L3 | lion0X_full | n/a（无 GT）`，其余指标写进 json。

**产出**：`audio/lion01_full.wav`、`lion0X.hyp.json`、`lion0X.diag.json`、`lowconf_sample.md`
**DoD**：两份 hyp 的 `words` **非空**（这是与旧数据的关键区别）；diag 五类指标齐全；抽样清单 ≤60 条且带时间戳；给出 3 条结论（例如"重复循环集中在 X 分钟""词密度 0.9/秒，缺失约 XX% 内容"）。

---

### T4 [P1][L] 优化动作 A/B（可观测指标版，不依赖 GT）

**为什么**：§5.8 列的 6 个动作，在 GT 到位前也能用"循环段数 / 词密度 / 低置信比 / 与 baseline 文本差异率"排序性价比。

**变体（在 lion01 + lion02 上各跑一遍）**

| # | 变体 | 做法 |
|---|---|---|
| A | baseline | 原 16k wav，`large-v3`，默认参数 |
| B | 预处理 | `afftdn=nf=-25` + `loudnorm=I=-16:TP=-1.5:LRA=11` + 16k mono |
| C | 分段 | 5 分钟切片分别转写，文本按时序拼接（**分别评测**，不拼 wav） |
| D | 模型 | `large-v3-turbo`，其余同 A |

**指标**（每个变体一行）：总词数、词/秒、循环段数、`conf<0.5` 占比、相对 A 的文本差异率（用 `jiwer.process_words(A_text, X_text).wer`）、耗时。

**步骤**：用 T0 的 `asr_client.py` 写 `scripts/ab_optimize.py`，结果落 `results/optim_compare_<YYYYMMDD>.json` + `results/optim_compare_<YYYYMMDD>.md`（表格 + 3 行结论）。

**备注 / 边界**
- `maleo-ai` 未部署，模型对比只能到 turbo。若 Aaron 后续要求 maleo，单开任务（需 GPU 机器 + faster-whisper 权重）。
- `condition_on_previous_text=false` 服务未暴露，C 变体是**客户端近似**；若结论显示循环主要来自续写，再提工单让服务端加环境变量。
- hotwords 的量化放到 T6，不在这里混做（避免变量交叉）。

**产出**：`scripts/ab_optimize.py`、`results/optim_compare_*.{json,md}`
**DoD**：4 变体 × 2 音频 = 8 组齐全；表格能直接回答"哪个动作最值"；明确写出"哪些指标不能替代 WER"。

---

### T5 [P1][M] 银标 GT（只对 L1b 广播；L3 只出分歧清单）

**为什么**：L1b 是相对干净的广播，双模型交叉的银标**可信度够**；LionAir 太差，银标会自欺，所以 L3 **不产 WER**，只产"两模型分歧 + 低置信"两份清单给人工。

**方法（L1b 4 条）**
1. 用 `large-v3` 与 `large-v3-turbo` 各转一次（已有 large-v3 的 hyp.json，补 turbo）。
2. 词级对齐（`jiwer.process_words(v3_text, turbo_text)`），生成银标：
   - 对齐一致 → 采信该词
   - 替换/插入/删除 → 写 `[diff: v3词|turbo词]`（删除侧写 `[gap: 词]`）
3. **一致率** = `1 - (S+D+I)/v3词数`，写进 meta。**低于 85% 的片段标记为"银标不可信"，不参与任何 WER**。
4. 产出 `corpus/L1b-broadcast/*.silver.gt.txt` + `*.silver.meta.json`（含 model_a/model_b/一致率/生成时间）。
5. 用银标跑一次 WER（`--tag silver`），报告里写清"这是上界，因为两模型同错不会被发现"。

**已在 L0 验证的前提**：large-v3 与 turbo 在干净 TTS 上文本 100% 一致（news_id，137 词，WER 均 1.46%）→ 分歧确实指向难点，而不是模型随机性。**在 L1b 上要重新验证一次一致率**。

**产出**：4 份 silver GT + meta + 一行 silver leaderboard + L3 的 `divergence.md`
**DoD**：4 条一致率都算出来；<85% 的条目被明确排除；L3 分歧清单与 `lowconf_sample.md` 合并成**一份给印尼同事的工作包**（见 T8）。

---

### T6 [P1][M] 术语表 + hotwords 命中率

**为什么**：§5.8 动作 #3 唯一能量化的抓手；LionAir 有官方 PPT，术语可以白拿。

**步骤**
1. 从 `data/Lion Air给的培训视频/Lion Air Audio AI Solution(2).pptx` 抽文本（`pip install python-pptx`，遍历所有 shape 的 text_frame + notes），落到 `corpus/terminology/_raw_pptx.txt`。
2. 人工/半自动筛出**印尼语术语 ≥40 条**（航空：`penerbangan, awak kabut, landasan, keterlambatan, bagasi, kursi, jadwal, maskapai, tarif, bagasi kabin…`；业务：`kebijakan, prosedur, pelatihan, evaluasi, kepatuhan…`），写 `corpus/terminology/terminology_id.tsv`（列：`term / en_gloss / source / count_in_l3`）。
3. **命中率**定义（写进 README）：`术语在 ASR 输出中以正确拼写出现的次数 ÷ 术语在参考文本（silver GT 或 PPT 稿）中出现的次数`，按 term 加权与不加权各算一份。
4. hotwords 实验：L1b 4 条 + L3 1 条，`hotwords="、"` 分隔？**先用服务实际接受的分隔符试**（单一术语 → 逗号 → 换行，以 200 且文本有变化为准），A/B 跑 on/off，比较命中率与 WER（L1b 用 silver GT）。

**产出**：`terminology_id.tsv`、`results/hotwords_ab_*.md`
**DoD**：术语 ≥40 条且带英文释义；hotwords 分隔符格式**实测确认并记录**；A/B 有明确结论（命中率提升 X pp，WER 变化 Y pp）。

---

### T7 [P2][M] 报告 v2（对外可读）

**为什么**：现有 `results/印尼语评测基线报告_20261102.html` 只有 L0/L1。客户真正要的是"现场到底行不行"。

**步骤**：新建 `results/印尼语评测报告_v2_<YYYYMMDD>.html`，沿用现有报告的样式（自包含、内联 CSS、无外链），章节：
1. **结论先行**：三层数字并列（干净 5.08% / 广播 X% / 现场 LionAir 无 GT 但词密度仅 Y/秒）+ 一句"现场差距的主因是音频质量，不是模型语言能力"
2. 评测方法与分层表（含口径标注：逐条 vs 拼接）
3. L0/L1 基线 + 交叉验证（公开 FLEURS large-v3 ≈7.8%）
4. L3 诊断：循环、词密度、低置信分布图（纯 CSS/SVG 柱状，不引外部库）
5. 优化 A/B 结果（T4 表）
6. 风险与边界：远场/重叠/印尼英夹杂；silver GT 的局限；L2 待补
7. 下一步（人工 GT 需求 + 采集端建议：近讲/全向麦）

**DoD**：单文件可离线打开；手机宽度可读；所有数字能在 `results/` 里追到来源 json；**不出现没有出处的数字**。

---

### T8 [P2][S] 人工 GT 工作包（`handoff/`，与 T3/T5 并行）

**为什么**：人力是唯一硬阻塞，把"要人做什么"降到最低摩擦，才算真推进。

**步骤**：建 `handoff/`：
1. `README.md`（给印尼同事，印尼语 + 中文对照）：目标、`[inaudible]` / `[unclear: x]` 规则、提交格式（纯 txt，UTF-8）、不要用 AI 代转写。
2. `gt_task_l1b/`：4 条 120s wav + 空白 `.gt.txt` 模板 + 对应 ASR 输出（**仅供参照，禁止直接粘贴**）。
3. `gt_task_l3/`：从 `lowconf_sample.md` 导出 20 段 × 20–30s 切片（`ffmpeg -ss -t`），带序号与时间戳。
4. `record_task_l2/`：
   - **L2a 朗读稿**（3 段，每段 200–250 词，印尼语，主题=航空培训/会议开场）→ **GT 白送**（就是稿子本身），用来测"近讲声学上限"
   - **L2b 自由对话**（要求：2 人、近讲 30–60cm、安静房间、手机录音原始格式保留、每段 2–3 分钟、共 ≥10 分钟）→ **需人工转写**，但这是最贴近真实会议的数据
5. 记录采集元信息模板：设备、距离、环境、说话人数、时长。

**产出**：`handoff/` 全目录
**DoD**：印尼同事不看其他文档、只打开 `handoff/README.md` 就能开始干活；L2a 朗读稿 3 段齐全（GT 免费）。

---

### T9 [P3][阻塞] 人工 GT 到位后的收口

按顺序：① L2a 录制回来 → 转写评测（`--layer L2`）② L1b 人工 GT 替换 silver → 重跑 WER ③ L3 可辨认片段（≥30s）切出归 L2 评测 ④ `--batch --layer all` 全量刷新 ⑤ 更新 `README.md` 基线表 + `TODO.md` + 报告 v2。

**DoD**：L2 WER 落在 §5.8 验收口径（≤25%）或有明确归因；README 表格全绿/全有数；报告有"人耳可辨认段落"这一层。

---

## 4. 执行顺序

```
T0 ──┬─> T1 ──> T2
     ├─> T3 ──┬─> T4 ──┐
     │        └─> T5 ──┼─> T6 ─> T7
     └────────────┬────┘
                  └─> T8 （随时可做，不占算力）
T9 等外部人力
```

**建议节奏**：T0→T1→T3 一次做完（这条链是"先让框架可信"，回归不通过不要往下走）；然后 T4/T5 并行；T6 看 T4 结论决定是否值得做；T7 最后写；T8 任何时候插。

---

## 5. 判定阈值（不要自创口径）

| 项 | 阈值 | 出处 |
|---|---|---|
| L0 WER | ≤ 2% | 本计划 §1 |
| L1 WER（逐条） | ≤ 10% | README |
| L2 WER | ≤ 25% | §5.8 验收口径 |
| L3 | 只记录，**不设门槛** | 客户风险清单 |
| 重复循环段 | = 0 | §5.8 |
| 银标可用性 | 双模型一致率 ≥ 85%，否则弃用该段 | T5 |
| 词密度 | 2.0–3.0 词/秒正常；<1.2 视为大段丢失 | T3 |

---

## 6. 回写规范（每完成一个任务都要做）

1. `data/asr-eval/results/leaderboard.md`：追加行，**不改历史行**（脏行除外）
2. `data/asr-eval/README.md`：新语料/新口径/新坑 → 补进对应小节
3. `data/asr-eval/TODO.md`：勾选
4. `.workbuddy/memory/YYYY-MM-DD.md`：追加 3–8 行（结论 + 坑 + 数字），**只写有跨会话价值的内容**

---

## 7. 明确不要做

- 不要用 `02.txt` / `*.ref.txt` / 旧 `asr_json_id/*.json` 当 GT
- 不要把银标 GT 的 WER 和人工 GT 的 WER 混进同一行 leaderboard 或同一张对比表而不标注
- 不要用拼接 wav 的 WER 当头条数字（拼接只用于管线检视）
- 不要把置信度当准确率写进报告
- 不要为了"数字好看"调归一化规则；任何规则变更必须两侧同时改 + 重跑回归
- 不要删除或改写 `data/Lion Air给的培训视频/` 下的原始文件（只读）
- 不要引入需要联网外部库的 HTML（报告要能离线打开）

---

## 8. 一句话交接

**先信任框架（T0–T2 回归必须过），再把没 GT 也能测的测完（T3–T4），然后用双模型银标把 L1b 填上（T5），最后把给人力的活压缩成开箱即用的工作包（T8）。L2 的人工录制是唯一真正的天花板，其余都能自己跑。**

---

## 9. 执行修正（2026-11-02 下午，CodeBuddy 实测）

> 本节由执行方追加，用于纠正前面被实测推翻的假设。**读 §1.2 / T4 / T5 / T6 时以本节为准。**

### 9.1 关键推翻：`/asr` 只接受 `audio_file`

`scripts/probe_params.py` 实测（news_id 137 词，同一 wav）：

| 传入 | HTTP | 结果 |
|---|---|---|
| `model=tiny` / `small` / `large-v3-turbo` / `definitely-not-a-model` | 200 | 文本与 large-v3 **逐字相同** |
| `language=en` / `zh` | 200 | 同上（输出仍是印尼语） |
| `hotwords=...` / `initial_prompt=...` | 200 | 同上 |

openapi schema 也只声明了 `audio_file` 一个字段 —— 其余字段被 FastAPI 静默丢弃，不报错。

**因此：**
- §1.2 "模型：large-v3 / large-v3-turbo 均可用" → 描述有误，服务端只有一个预加载模型（`loaded_models=["large-v3"]`），换 `model` 无效。
- §1.2 "maleo 不在该服务上" 依然成立，但**换成 turbo 也没用**，模型维度在当前部署上完全不可测。
- T4 变体 D 与 A 是同一结果（实测 diff 0.0%），表里保留只为留证据。
- T5 的"双模型银标"前提崩塌：两路"模型"实际是同一模型，一致率恒为 100%，产出的 GT 就是 ASR 自己的输出 → **T5 判为阻塞**，不产出 silver GT（避免自欺）。
- T6 的 hotwords A/B 不可做；术语表本身仍可做。
- 客户端**唯一**有效旋钮：**音频预处理** 与 **分段**（改变送进去的音频）。

### 9.2 任务状态

| 任务 | 状态 | 产物 |
|---|---|---|
| T0 asr_client | ✅ | `scripts/asr_client.py`（自测 137 词） |
| T1 归一化 + 回归 | ✅ | `[inaudible]` 剔除 + 3 个开关 + `scripts/regress_check.py`（0.00/0.00/1.46/5.08 逐位一致）+ `scripts/prepare_fleurs.py` |
| T2 多格式 hyp | ✅ | `load_text` 支持 srt/vtt/speakr md，WER 差 0.00e+00 |
| T3 L3 诊断 | ✅ | `corpus/L3-stress/{DIAGNOSIS.md,lowconf_sample.md,lion0X.diag.json}` + leaderboard 2 行 |
| T4 A/B | ✅（D 无效） | `scripts/ab_optimize.py`、`results/optim_compare_20261102.{json,md}` |
| T5 银标 GT | ⛔ 阻塞 | 原因见 9.1；不产出假银标 |
| T6 hotwords | ⛔ 阻塞 | 参数被忽略；术语表另行处理 |
| T7 报告 v2 | 待做 | — |
| T8 工作包 | ✅（部分） | `handoff/`（L1b 4 条、L3 20 段、L2 朗读稿 3 篇 + 对话说明） |
| T9 收口 | 等人力 | — |

### 9.3 T3 / T4 结论（与 §3 原文的差异）

- T3 实测：lion01 1203s/1156 词（0.96 词/秒）、循环 5 段（浪费 156 词）、conf<0.5 = 76.7%、中位置信 0.17；lion02 403s/466 词（1.16 词/秒）、循环 1 段、conf<0.5 = 75.5%、中位置信 0.25。整段 37MB 未触发 413（32s 完成），无需分段兜底。
- T3 原文推测"循环来自跨段续写，分段应归零" —— **被 T4 证伪**：分段后循环 5→4，lion02 反而 1→2。循环主要来自音频本身（混响/叠话）。
- T4 预处理（afftdn+loudnorm）是所有变体里唯一显著改变输出的：lion01 词数 1156→1399、conf<0.5 76.7%→54.3%，但同时循环浪费词 156→282、与 baseline 文本差异 84%。**方向未定**，需要人工 GT 才能判断是"补回内容"还是"新增幻觉"。

### 9.4 环境注意

评测依赖装在 `data/asr-eval/.venv`（Python 3.12 + jiwer 4.0）；系统 `python3`（3.14）没有 jiwer。所有命令用 `.venv/bin/python`。`/tmp/fleurs16` 由 `scripts/prepare_fleurs.py` 从 `corpus/L1-read/utt16/` 归档恢复，**只保留 124 条**（全量 test 有 686 条，放进去会悄悄改变 L1 口径）。

