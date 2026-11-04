# MeetingAI · 会议录音转写与摘要系统

> 私有化部署的会议音频处理系统：上传录音 → 说话人分离转写 → 中文纪要 → 敏感词检测与告警。
> 支持中文 / 英文 / 印尼语会议，**音频与转写结果全部留在自有服务器**，唯一外发的是「转写文本 → LLM」。

---

## 1. 能做什么

| 能力 | 说明 | 状态 |
|---|---|---|
| 会议转写 | 浏览器拖拽上传 mp3/wav/m4a/mp4，自动转码、排队、转写；大文件分片 | 已上线 |
| 说话人分离 | pyannote 聚类 + 声纹档案，逐句标注「谁说了什么」，可改名并全库生效 | 已上线（相似音色仍会分错，见 §7） |
| 会议纪要 | LLM 生成摘要、议题、行动项；界面语言可设为中文，原文保留 | 已上线 |
| 导出归档 | Markdown / TXT / SRT / DOCX（转写 + 摘要 + 笔记一并成文，模板可自定义） | 已上线 |
| 敏感词检测 | 转写完成后自动扫描，三级分级，自动打标签、写备注、追加总结清单、播放页高亮 | 已上线（独立服务） |
| 词库管理 | CSV 词表热加载，改完即生效；支持历史会议全量重扫 | 已上线 |
| 数据主权 | 音频 / 转写 / 摘要全在自有服务器卷内；LLM 可换自建 vLLM 端点 | 已满足 |

### 平台自带能力（无需开发）

- 声纹档案：给说话人改名即积累声纹，新录音自动比对；每人 20 个样本、差异过大自动拒收
- 发言统计：每位说话人的时长占比、发言轮次、词数、WPM 与静音占比
- 音转文双向联动：点句跳播、播放跟随高亮、自动滚动；气泡 / 列表两种视图
- 事件提取：自动识别会议与截止日，导出 ICS 日历
- 批量操作：全选删除 / 打标 / 重处理；录音合并上传；视频直通 ASR
- 目录监听：指定 watched 目录，放入音频即自动转写（可对接网盘/会议系统导出）
- 团队协作：群组、群组标签共享、三级权限、安全公开链接、私有笔记
- 企业能力：OIDC SSO、S3 对象存储、Incognito 模式（处理完即删）、自动导出

**实测基线**（详见 §4）：

- 干净朗读（FLEURS 印尼语 124 条）WER **5.08%**，CER 1.77%
- 合成噪声集（744 条，0/5/10 dB，两类噪声）WER **19.88%**，CER 11.54%
- 12 秒音频端到端 ASR **1.4–1.5 s**（RTF ≈ 0.12，单线程）
- 1 小时会议约 **10–20 分钟**出全文 + 中文摘要（RTX 3080，含说话人分离）

## 2. 系统架构

```
                    +----------------+
                    |     浏览器      |  HTTP(S) :8899
                    +--------+-------+
                             |
                             v
+----------------------- 应用服务器 -----------------------+
|  speakr（Flask + Vue，单容器）                           |
|  · Web UI：上传 / 转写编辑器 / 摘要 / 播放器 / 导出       |
|  · 任务队列：queued -> transcribing -> summarizing -> done|
|  · SQLite（录音、段落、摘要、标签、声纹）                 |
|  · 音频卷（上传即转 mp3 128k，1h 约 55MB）                |
|  · Webhook 发射器（HMAC 签名 + 指数退避）                 |
+--------+----------------------------------+--------------+
         | POST /asr（内网 :9000）           | OpenAI 兼容
         v                                  v
+---------------- GPU 算力机 ----------------+   +------ LLM ------+
| whisperx-asr-service（FastAPI）             |   | DeepSeek / 自建  |
|   VAD -> faster-whisper large-v3            |   | · 摘要与行动项    |
|   -> wav2vec2 词级对齐 -> pyannote 分离      |   | · 对话问答        |
|   模型缓存 5-6GB，/tmp 音频即用即删           |   | · 敏感词精判      |
+---------------------------------------------+   +------------------+

        可选：sensitive-filter（FastAPI + SQLite，:8898）
        <- webhook 事件；-> 回写标签 / 备注 / 总结
```

### 组件职责

| 组件 | 职责 | 不负责 |
|---|---|---|
| speakr | Web 入口、用户与权限、任务编排、音频存储、摘要编排、导出、webhook、声纹档案 | 不做 ASR 本身 |
| whisperx-asr-service | 转写、说话人分离、词级时间戳；OpenAI 兼容接口 | 无鉴权、无业务逻辑、不落盘 |
| LLM（外接） | 摘要、问答、敏感词精判、翻译 | 不接触音频 |
| sensitive-filter | 敏感词检测、分级、回写、告警看板 | 不存储转写正文 |

### 主链路

1. 用户上传 → speakr 转码 mp3、落卷、建任务（SQLite）
2. speakr 调 ASR：`POST /asr`，参数 `audio_file / language / diarize / word_timestamps`
3. GPU 机执行 WhisperX：VAD 切分 → large-v3 转写 → 词级对齐（印尼语用 wav2vec2 印尼语模型）→ pyannote 分离
4. 返回 `segments[{speaker,start,end,text,words[{word,score}]}]`
5. speakr 入库 → 触发 LLM 摘要（Preferred Language 可设中文）
6. 前端展示（音转文联动）、导出、可选 webhook 通知

### 存储与生命周期

| 数据 | 位置 | 生命周期 |
|---|---|---|
| 音频 | 应用服务器 `./uploads` 卷 | 默认 90 天自动删除（只删音频） |
| 转写 / 摘要 / 标签 / 声纹 | speakr SQLite `./instance` 卷 | 永久保留 |
| ASR / 对齐 / 分离模型 | GPU 机模型卷 | 常驻（5-6 GB） |
| GPU 机临时音频 | 容器 `/tmp` | 用完即删（finally 清理） |

### 安全设计

1. **数据主权**：唯一出网的是转写文本 → LLM；可切自建 vLLM 完全不出网。
2. **最小暴露面**：ASR 服务无鉴权，只允许应用服务器内网访问，禁止暴露公网；Web 端公网访问必须 HTTPS 反代。
3. **Webhook 完整性**：HMAC-SHA256 签名 + 时间窗 ±300s + 失败指数退避 + 连续失败自动暂停 + SSRF 白名单。
4. **账号与权限**：内置用户/共享/三级权限，可接 OIDC SSO；`Protected tag` 防误删。

## 3. 敏感词检测

speakr 原生没有任何敏感词能力，可用的"画布"只有四个：**标签、备注、总结正文、前端 DOM**。本系统把四者都用上，并保持原文不可变。

### 两种实现

| | Part A `speakr-sensitive-lite` | Part B `speakr-sensitive-filter` |
|---|---|---|
| 形态 | 单文件 Python + 文本词表 | FastAPI + SQLite 独立容器 |
| 触发 | 轮询（默认 60s） | Webhook（秒级） |
| 检测 | 纯词表匹配 | 词表粗筛 + LLM 精判（可选） |
| 回写 | 标签 + 备注 | 标签 + 备注 + L1 置顶 + 总结清单 |
| 可靠性 | 轮询自愈 | 验签、幂等、退避重试、自动暂停 |
| 适用 | 快速验证 / 极小规模 | 生产 |

两者并行维护：Part A 保持零依赖可直接跑；Part B 接管触发层与告警。

### 检测链路（Part B）

```
speakr --webhook(transcription.completed)--> sensitive-filter
    1. 验签（V2: HMAC-SHA256("{t}."+body)，±300s）
    2. Delivery-Id 幂等 -> 立即 202
    3. GET transcript（逐句 segments）
    4. 词表粗筛（变体 + 白名单）-> 候选句
    5. LLM 精判（只送候选句 ±1 句，严格 JSON）
    6. 落库 job / alert / delivery / scan
    7. 回写：SW-L1/L2/L3 标签、备注告警块、
       L1 is_highlighted、总结清单块
    8. 告警：L1/L2 即时，L3 每日汇总；/alerts 看板
```

`recording.summary.completed` 事件用于处理"总结生成晚于转写"的时序问题：总结后到则补追加，先到则延迟重试（30s 退避），用户手动重新生成也会自动重追加。

### 分级与呈现

| 级别 | 含义 | 标签 | 呈现 |
|---|---|---|---|
| L1 | 违禁 | SW-L1（红） | 左侧粗竖条 + 图钉角标 + 关键词下划线 + 录音置顶 |
| L2 | 警示 | SW-L2（橙） | 同上（不置顶） |
| L3 | 关注 | SW-L3（黄） | 同上 |

- **备注块**：写入命中词、级别、说话人、时间点、原句，带机器可读 JSON（`<!-- sensitive-filter:data ... -->`），重扫只替换自己的块。
- **总结清单**：在总结末尾追加"敏感词涉及语句清单"（marker 块幂等替换；零命中时删除残留块）。
- **前端高亮**：`widget.js` 在文本节点上包 `<mark>`，不拼 innerHTML，不破坏 Vue 状态；数据双通道（检测服务 `/hits` 优先，speakr notes 回退）。

### 词表

CSV，整份替换、热加载，支持简式 `L1,词` 与全 schema：

```csv
term,lang,level,category,variants,regex,enabled
武器,zh,L1,violence,,,1
suap,id,L2,bribery,menyuap penyuapan disuap,,1
komplain,id,L3,complaint,komplainnya dikomplain,,1
```

- 印尼语用「词根 + meN-/ber-/-kan/-nya 变体」+ LLM 兜底词形与错字。
- 英文按词边界匹配（防 `class` 命中 `ass`），中文 NFKC 归一。
- 白名单抑制误报（"风险评估" ≠ "风险"）。

### 对外接口（节选）

| 接口 | 方法 | 用途 |
|---|---|---|
| `/hooks/speakr` | POST | Webhook 接收（验签 → 幂等 → 入队 → 202） |
| `/hits?recording_id=N` | GET | 命中明细（高亮数据源） |
| `/wordlist` | GET / PUT | 下载 / 整份替换词表（校验 + 原子替换 + 热加载） |
| `/scan/{rid}` · `/rescan-all` | POST | 单条重扫 / 全量重扫（异步，幂等） |
| `/alerts` | GET | 告警看板（Basic Auth，可关） |
| `/healthz` | GET | 词表数 / LLM 开关 / worker 状态 |

调用 speakr 侧需要 `read + write` scope 的 API Token；多账号部署时按录音归属自动切换可用 token，并为每个账号单独注册 webhook（secret 汇总到 `WEBHOOK_SECRETS`）。

### 边界

- 检测上限 = 转写上限：转错字就会漏检，核心词应同时进入 ASR hotwords（当前部署的 ASR 服务会忽略该参数，见 §7）。
- 备注只有 token 所属用户可见；跨用户提示依赖标签与总结。
- 纯前端高亮不落库，导出文件不带高亮。

## 4. 印尼语 ASR 评测

### 分层语料

| 层 | 语料 | 规模 | 参考词 | 状态 |
|---|---|---|---|---|
| L0 | TTS 合成（会议/新闻腔） | 3 条 | 456 | 回归基线 |
| L1 | FLEURS id_id test，真人朗读 | 124 条 | 2380 | 头条口径（逐条） |
| L1b | 印尼新闻播报片段 | 4 x 120s | 待人工 GT | 暂停 |
| L2 | 印尼员工近讲录制 | — | — | 待录制 |
| L3 | 客户现场远场录音 | 2 段 | 无 GT | 只出风险指标 |

### 基线（whisper large-v3）

| 口径 | WER | CER | 说明 |
|---|---|---|---|
| L0 会议 TTS | 0.00% | 0.00% | 管线正确性 |
| L0 新闻 TTS | 1.46% | 0.48% | 2 处 TTS 韵律问题 |
| **L1 逐条（124 条）** | **5.08%** | **1.77%** | 优于公开基线 ≈7.8% |
| L1 拼接长轨（18 x 90s） | 7.23% | 3.35% | 边界吞句，+2.15pp |
| 噪声集 744 条 | 19.88% | 11.54% | room / reverb x 0/5/10 dB |

### 噪声集与引擎对比

744 条 = 124 句 x 2 类噪声（现场底噪 room / 合成混响 reverb）x 3 档 SNR（0/5/10 dB），GT 复用 FLEURS 原文。

| 引擎 | 干净 WER | 噪声 WER | 噪声 CER | p50 | RTF | 两轮一致性 |
|---|---|---|---|---|---|---|
| whisper large-v3 | 5.08% | 19.88% | 11.54% | 1.46 s | 0.124 | 100% |
| Qwen3-ASR-1.7B | 5.29% | **17.43%** | **9.47%** | 2.33 s | 0.236 | 100% |
| Qwen3-ASR Demo | 3.74% | 15.08% / 19.54% | 8.92% / 13.47% | 1.40 s | 0.126 | 90.7% |

按条件（第 1 轮）：

| 条件 | whisper | Qwen 1.7B | Demo |
|---|---|---|---|
| room 10 / 5 / 0 dB | 6.3 / 7.6 / 11.3 | 5.8 / 7.3 / 8.6 | 4.6 / 4.6 / 6.0 |
| reverb 10 / 5 / 0 dB | 20.1 / 27.1 / 46.9 | 12.8 / 21.9 / 48.2 | 10.5 / 19.6 / 45.3 |

**结论**

1. 干净语音上 whisper 与 Qwen 不可区分（Δ0.21pp，95% CI 含 0）；**噪声混响下 Qwen 领先 5-7pp 且完全可复现**，是换引擎唯一成立的场景。
2. Demo 精度最高但**不可用于生产**：同一服务两轮之间整档退化（混响 0 dB 会把印尼语输出成中文），失败时 HTTP 200、无任何异常信号。
3. 混响比底噪难得多（同 SNR 下 WER 高 2-7 倍）；0 dB 混响三家都崩到 45-48%，不构成选型依据。
4. 错误高度集中：前 10 条难句贡献 37-43% 的全部错误，瓶颈是专名与数字。

### 评测纪律（踩过的坑）

- **逐条 ≠ 拼接**：同一批音频拼成 90s 长轨后 WER 从 5.08% 升到 7.23%，报告必须注明口径，头条用逐条。
- **数字两侧归一化**：`delapan puluh persen` ↔ `80%`、`1.000` ↔ `1000`，不做会虚高 3-6pp。
- **置信度不是准确率代理**：高置信度的广播音频反而更不准，只能用于同域内排序抽样。
- **服务端参数形同虚设**：当前部署的 `/asr` 只接受 `audio_file`，`model / language / hotwords / initial_prompt` 全部被静默忽略（openapi 里虽然声明了）。因此"换模型对比""hotwords 收益"这类实验在当前部署上不成立，客户端真正有效的旋钮只有**音频预处理**和**分段**。
- **长音频与重复循环**：现场远场录音会出现整段重复（单段最长重复 168 次），分段只能轻微缓解，根因在音频本身。

## 5. 关键决策

| # | 决策 | 理由 | 代价 |
|---|---|---|---|
| D1 | 自托管 WhisperX 全管线，而非裸 Whisper 端点 | 裸端点没有说话人分离；WhisperX = VAD + 转写 + 对齐 + pyannote | 需 GPU（≥8GB），pyannote 需 HF token 并同意 3 个 gated 协议 |
| D2 | large-v3 而非 turbo | 印尼语：large-v3 13.67% vs turbo 21.48%（Common Voice 21） | 推理略慢，实测可接受 |
| D3 | speakr lite 镜像 | 比 full 省 3.7GB（full 多的 PyTorch 只用于语义搜索） | 语义搜索暂不可用 |
| D4 | 外接 LLM 而非本地推理 | 摘要质量好、零算力成本；OpenAI 兼容可随时换自建 | 转写文本出内网（可收口） |
| D5 | 印尼语对齐模型走容器内补丁 | 所用 fork 缺 `id` 对齐模型映射，补一行即可恢复词级时间戳 | 容器重建需重打补丁 |
| D6 | 到期只删音频（`audio_only`，90 天） | 1TB ≈ 1800 小时；转写与纪要永久可追溯 | 磁盘需按保留期规划 |
| D7 | 不做实时转写 | 批式管线需要全局音频（VAD/对齐/聚类），实时是另一套架构 | 实时字幕需独立流式组件，不改造本系统 |

**为什么不用 Meetily**：它是桌面端「录音 + 转写」一体工具，转写引擎与音频采集焊死在 Tauri 壳里，没有 Web 服务端、没有 Docker、没有说话人分离（Pro 功能），无法改造成「上传 → 服务端批处理 → 摘要 → 下载」的形态。评估后选择 speakr（Flask + 可插拔 ASR + 原生说话人分离 + 导出 + 权限）作为业务层。

## 6. 部署

### 前置

- 应用服务器：Docker + Compose v2，2C4G 起
- GPU 机：NVIDIA ≥8GB（推荐 16GB）、驱动 + Container Toolkit
- HuggingFace 账号与 Read token，并**手动同意 3 个 pyannote 协议**（否则 403）；国内可设 `HF_ENDPOINT` 镜像

### 关键配置（示意）

```env
# speakr
ASR_BASE_URL=http://<GPU_HOST>:9000
ASR_DIARIZE=true
ASR_RETURN_SPEAKER_EMBEDDINGS=true
TEXT_MODEL_BASE_URL=https://<llm-endpoint>/v1
TEXT_MODEL_API_KEY=<key>
TEXT_MODEL_NAME=<model>
ENABLE_AUTO_DELETION=true
GLOBAL_RETENTION_DAYS=90
DELETION_MODE=audio_only

# whisperx-asr-service
PRELOAD_MODEL=large-v3
DEVICE=cuda
COMPUTE_TYPE=float16
HF_TOKEN=<token>
```

### 端口

| 端口 | 用途 | 范围 |
|---|---|---|
| 8899 | speakr Web | 局域网；公网需 HTTPS 反代 |
| 9000 | ASR `/asr`、`/health`、`/docs`、`/metrics` | **仅应用服务器内网** |
| 8898 | 敏感词检测服务（可选） | 本机 / 内网 |

### 运维备忘

- 升级用 `docker compose down`（**不要带 `-v`**，否则清空模型卷触发全量重下）。
- 离线环境：`HF_HUB_OFFLINE=1` 必须同时设 `HF_HOME`，否则分离模型加载失败。
- GPU 容器重建后需重新执行印尼语对齐模型补丁。
- 自检脚本：部署包内的 `env_check.sh server|gpu`（只读，不入库）；`GET /health` 的 `loaded_models` 可看模型装载状态。
- 重复循环检测：`重复率 = 1 - 去重词 / 总词`，用于巡检真实录音质量。

## 7. 已知限制与路线

| 问题 | 现状 | 方向 |
|---|---|---|
| 真实远场识别率 | 无人工 GT，只能看风险指标：词密度 0.96-1.16 词/秒、置信度 <0.5 占 75-77%、中位置信 0.17-0.25 | 采集端近讲/全向麦 + 降噪预处理 + 人工 GT 校准 |
| 说话人分离分错 | 聚类阈值偏保守 + 相似音色合并 | 阈值 0.6→0.5 标定；声纹档案随改名积累（中期根治） |
| 重复循环 | 现场音频单段可重复上百次 | 分段仅缓解；根因在音频，需人工 GT 判断预处理是否有效 |
| 敏感词漏检 | 检测上限 = 转写上限 | 核心词进 hotwords（需服务端支持）；LLM 精判兜底词形 |
| 转写原文翻译 | speakr 无自动翻译；WhisperX 只能 X→英文 | webhook 后处理 + LLM 翻译（与敏感词服务同一扩展点） |
| 实时字幕 | 批式架构不支持 | 独立流式 ASR 组件并行部署，不改造主链路 |
| ASR 服务无鉴权 | 依赖内网 ACL | 部署侧加反向代理与 token 校验 |

## 8. 仓库结构

```
MeetingAI/
├── README.md                    # 本文
├── .gitmodules
├── speakr-sensitive-filter/     # Part B：生产版敏感词检测服务
└── speakr-sensitive-lite/       # Part A：极简轮询版（单文件 + 词表 + 用户脚本）
```

- 音频语料、评测工作区与部署脚本（含凭据）**不入库**，`.gitignore` 已排除 `data/`、`deploy/`、`conn/`、`*.pem`、`*.key`。
- 克隆需要拉子模块：

```bash
git clone --recurse-submodules <repo-url>
```

## 9. 术语

| 缩写 | 含义 |
|---|---|
| WER / CER | 词错误率 / 字错误率（编辑距离口径） |
| RTF | Real-Time Factor，处理耗时 ÷ 音频时长 |
| SNR | 信噪比；本项目的噪声集用 0 / 5 / 10 dB 三档 |
| diarization | 说话人分离 |
| hotwords | 偏置词表，用于提升专有名词识别（当前 ASR 部署未生效） |
