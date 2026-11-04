# 会议转写系统 · 部署清单（可重新打包 / 迁移新环境）

> 版本：v1.0 · 编制日期：2026-11-02
> 依据：现网服务器逐项核查（容器、镜像、挂载、环境变量、端口、卷），非文档推测。
> 目标：部署工程师只按本清单即可把同一套系统重建到新机器，**不含已上传的音频文件**。
> 密钥不在本文出现，随包以加密方式单独交付 `.env`。

---

## 1. 系统组成

三个自建容器 + 两个外部依赖：

```
浏览器
  │  http://<host>:8899
  ▼
┌─────────────────────────────┐        ┌──────────────────────────┐
│ speakr (lenovo-speaker:v1)  │        │ sensitive-filter         │
│ 会议上传/转写/总结/分享     │◀───────│ 敏感词检测/标签备注回写  │
│ 8899                        │ webhook│ 127.0.0.1:8898           │
└──────────┬──────────────────┘        └──────────────────────────┘
           │ http://<host>:9010（ASR_BASE_URL）
           ▼
┌─────────────────────────────┐        ┌──────────────────────────┐
│ whisperx-asr-api            │        │ 外部 LLM（qwen，总结/标题）│
│ CUDA large-v3，9000→9010    │        │ 外部 OpenAI（可选，未启用）│
└─────────────────────────────┘        └──────────────────────────┘
```

| 容器名 | 镜像 | 大小 | 作用 | 是否必需 |
|---|---|---|---|---|
| `speakr` | `lenovo-speaker:v1` | 6.76 GB | 主应用（Flask + gunicorn，Lenovo 品牌） | 必需 |
| `sensitive-filter` | `speakr-sensitive-filter:latest` | 145 MB | 敏感词检测服务（FastAPI） | 业务必需 |
| `whisperx-asr-api` | `whisperx-asr-api:v20261027` | 7.27 GB | 自建 ASR（faster-whisper large-v3 + 说话人分离） | 必需 |

> `speakr-sensitive-lite`（Part A 演示版）未部署，不在本清单范围内。

宿主机现状（作为新环境规格下限参考）：Ubuntu 24.04.3、Docker 29.2.1、Compose v5.0.2、32 核 / 309 GB 内存、2× NVIDIA H20（96 GB）。

---

## 2. 端口与网络

| 服务 | 宿主端口 → 容器端口 | 暴露范围 | 说明 |
|---|---|---|---|
| speakr | `8899:8899` | 0.0.0.0（公网） | 用户访问入口，无 TLS，无反代 |
| whisperx | `9010:9000` | 0.0.0.0（公网） | ASR API；建议新环境改为仅内网/加防火墙 |
| whisperx Ray Dashboard | `127.0.0.1:8265:8265` | 仅本机 | `SERVE_MODE=simple` 下不使用 |
| sensitive-filter | `127.0.0.1:8898:8898` | 仅本机 | 浏览器不直连；运维走 SSH 隧道 |

- 三个容器分属两个 compose 网络：`speakr_default`、`whisperx_default`，**互不相通**。
- speakr 调 ASR 走的是宿主机 IP + 发布端口（现网 `ASR_BASE_URL=http://172.21.16.10:9010`）。
  新环境必须改成新宿主机的内网 IP；也可把两个 compose 合并/共享外部网络后改用 `http://whisperx-asr-api:9000`。
- sensitive-filter 调 speakr 走 compose 内服务名：`SPEAKR_URL=http://speakr:8899`（同属 `speakr_default`，无需改）。
- 宿主机 nginx **没有**代理这三个端口，全部为 docker-proxy 直出。

---

## 3. 交付包内容清单

### 3.1 必须随包交付

| 类别 | 源路径（现网） | 体积 | 说明 |
|---|---|---|---|
| 镜像 tar ×3 | `docker save` 导出 | 6.76G + 7.27G + 145M | 见 §5 打包命令 |
| speakr 编排 | `/data/speakr/docker-compose.yml` | 2.8 KB | 含 speakr + sensitive-filter 两个 service |
| speakr 配置 | `/data/speakr/.env` | 34 KB | **含密钥，加密传递**；新环境至少改 3 项（§4.1） |
| 品牌覆盖文件 | `/data/speakr/brand/` | 1.4 MB / 27 个文件 | 全部只读挂载进 speakr 容器 |
| 敏感词服务源码 | `/data/speakr/sensitive-filter/`（排除 `data/`） | 168 KB | 用 `build:` 现场构建，或直接发镜像 |
| 敏感词服务配置 | `sensitive-filter/.env` | 308 B | 含 speakr API Token / Webhook Secret |
| 词表种子 | `sensitive-filter/wordlists/sensitive.csv` | 725 B | 现网以服务器版本为准，见 §6.5 |
| whisperx 编排 | `/data/whisperx/docker-compose.yml` | 4.6 KB | |
| whisperx 配置 | `/data/whisperx/.env` | 198 B | 含 HF_TOKEN |
| ASR 模型缓存（二选一） | 卷 `whisperx_whisperx-cache` | 12 GB | 离线环境必须带；联网环境可首启自动下载（`HF_ENDPOINT=https://hf-mirror.com`） |

### 3.2 明确不打包

| 内容 | 现网路径 | 体积 | 原因 |
|---|---|---|---|
| 已上传音频 | `/data/speakr/uploads/`（`recordings/`、`_staging/`、`_sessions/`） | 76 MB | 按需求排除；新环境从空目录开始 |
| 业务数据库 | `/data/speakr/instance/transcriptions.db`（+`-wal`/`-shm`） | 5 MB | 新环境首次启动自动建库；如需保留用户/历史元数据见 §7 |
| 会话密钥 | `/data/speakr/instance/secret_key` | 64 B | 随库一起迁移才有意义 |
| 敏感词运行库 | `sensitive-filter/data/sensitive.db` | 48 KB | 历史扫描结果，自动重建 |
| 历史打包残留 | `/data/speakr/deploy_data.tar.gz`、`all_data/*.tar` | 7 GB+ | 2026-10-27 旧包，不含敏感词服务，勿用 |

---

## 4. 容器规格明细

### 4.1 speakr（主应用）

| 项 | 值 |
|---|---|
| 镜像 | `lenovo-speaker:v1`（镜像 ID `4618e52ff0cc`，构建于 2026-10-27 16:35） |
| 启动命令 | `docker-entrypoint.sh` + `gunicorn --workers 3 --worker-class gthread --threads 8 --bind 0.0.0.0:8899 --timeout 600 src.app:app` |
| 重启策略 | `unless-stopped` |
| 端口 | `8899:8899` |
| 卷 | `./uploads → /data/uploads`（rw）、`./instance → /data/instance`（rw）、`./brand/** → /app/**`（26 个只读单文件挂载） |
| 数据库 | SQLite `sqlite:////data/instance/transcriptions.db` |
| 额外环境 | `LOG_LEVEL=ERROR`、`HF_ENDPOINT=https://hf-mirror.com`（compose 内写死，覆盖 .env） |

**26 个品牌只读挂载（缺一个就会回退成上游默认页面）**

```
brand/templates/components/header.html        → /app/templates/components/header.html
brand/templates/components/page-header.html   → /app/templates/components/page-header.html
brand/templates/inquire.html                  → /app/templates/inquire.html
brand/templates/login.html                    → /app/templates/login.html
brand/templates/register.html                 → /app/templates/register.html
brand/templates/share.html                    → /app/templates/share.html
brand/templates/account.html                  → /app/templates/account.html
brand/templates/admin.html                    → /app/templates/admin.html
brand/templates/group-admin.html              → /app/templates/group-admin.html
brand/templates/index.html                    → /app/templates/index.html
brand/templates/includes/loading_overlay.html → /app/templates/includes/loading_overlay.html   ← 敏感词气泡 widget 内联在此
brand/templates/includes/organizer_modals.html→ /app/templates/includes/organizer_modals.html
brand/templates/modals/system-audio-help-modal.html → /app/templates/modals/system-audio-help-modal.html
brand/templates/auth/forgot_password.html     → /app/templates/auth/forgot_password.html
brand/templates/auth/check_email.html         → /app/templates/auth/check_email.html
brand/templates/auth/reset_password.html      → /app/templates/auth/reset_password.html
brand/templates/auth/verify_success.html      → /app/templates/auth/verify_success.html
brand/static/img/lenovo.svg                   → /app/static/img/lenovo.svg
brand/static/img/favicon.ico                  → /app/static/img/favicon.ico
brand/static/img/icon-16x16.png               → /app/static/img/icon-16x16.png
brand/static/img/icon-32x32.png               → /app/static/img/icon-32x32.png
brand/static/img/icon-180x180.png             → /app/static/img/icon-180x180.png
brand/static/img/icon-192x192.png             → /app/static/img/icon-192x192.png
brand/static/img/icon-512x512.png             → /app/static/img/icon-512x512.png
brand/static/img/icon-maskable-512x512.png    → /app/static/img/icon-maskable-512x512.png
brand/static/manifest.json                    → /app/static/manifest.json
```

品牌文件与本地 `speakr-brand-kit/`（templates + static）逐文件 MD5 一致，可直接用该仓库产出，无需从服务器反拷。

**环境变量（`/data/speakr/.env` 中生效项，键名全列，值中密钥以 `<REDACTED>` 表示）**

| 分组 | 键 | 现网值 / 新环境要求 |
|---|---|---|
| 文本模型（总结/标题/问答） | `TEXT_MODEL_BASE_URL` | `https://vlm-pgx.sightai.tech:4433/qwen/v1` **外部依赖，需通网或替换** |
| | `TEXT_MODEL_API_KEY` | `<REDACTED>` |
| | `TEXT_MODEL_NAME` | `qwen3.8` |
| | `GPT5_REASONING_EFFORT` / `GPT5_VERBOSITY` | `medium` / `medium` |
| 转写后端 | `ASR_BASE_URL` | `http://172.21.16.10:9010` ← **新环境必须改** |
| | `ASR_TIMEOUT` | `1800` |
| | `ASR_DIARIZE` | `true` |
| | `CHUNK_LIMIT` | `20MB` |
| | `CHUNK_OVERLAP_SECONDS` | `3` |
| OpenAI 备用通道 | `TRANSCRIPTION_BASE_URL` / `TRANSCRIPTION_MODEL` / `TRANSCRIPTION_API_KEY` | `https://api.openai.com/v1` / `gpt-4o-transcribe-diarize` / `<REDACTED>`（**设置了 `ASR_BASE_URL` 即不生效**，可留空） |
| 账号 | `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin` / `admin@example.com` / `<REDACTED>`；**仅首次建库生效** |
| | `ALLOW_REGISTRATION` / `REGISTRATION_ALLOWED_DOMAINS` | `false` / 空 |
| 存储 | `SQLALCHEMY_DATABASE_URI` | `sqlite:////data/instance/transcriptions.db` |
| | `UPLOAD_FOLDER` | `/data/uploads` |
| | `FILE_STORAGE_BACKEND` / `FILE_STORAGE_KEY_PREFIX` | `local` / `recordings` |
| 音频压缩 | `AUDIO_COMPRESS_UPLOADS` / `AUDIO_CODEC` / `AUDIO_BITRATE` | `true` / `mp3` / `128k` |
| 队列 | `MAX_CONCURRENT_UPLOADS` / `JOB_QUEUE_WORKERS` / `SUMMARY_QUEUE_WORKERS` / `JOB_MAX_RETRIES` | `3` / `2` / `2` / `3` |
| Token 上限 | `SUMMARY_MAX_TOKENS` / `CHAT_MAX_TOKENS` / `TITLE_MAX_TOKENS` | `8000` / `5000` / `5000` |
| 功能开关 | `ENABLE_PUBLIC_SHARING` | `true` |
| | `ENABLE_INQUIRE_MODE` / `ENABLE_AUTO_PROCESSING` / `ENABLE_AUTO_EXPORT` / `ENABLE_AUTO_DELETION` / `ENABLE_INTERNAL_SHARING` | 均 `false` |
| | `USERS_CAN_DELETE` / `VIDEO_RETENTION` | `true` / `false` |
| 安全 | `WEBHOOK_INTRANET_HOST_ALLOWLIST` | `^sensitive-filter$` ← **不加则 webhook 指向容器名会被拒** |
| | `LOG_LEVEL`（.env 内） | `"INFO"`（被 compose 的 `ERROR` 覆盖） |

### 4.2 sensitive-filter（敏感词检测）

| 项 | 值 |
|---|---|
| 镜像 | `speakr-sensitive-filter:latest`（ID `6ae3ca1bb774`，2026-10-30 17:56 构建，145 MB） |
| 构建 | `build: ./sensitive-filter`，`Dockerfile`：`python:3.12-slim` + `requirements.txt`（fastapi / uvicorn / httpx） |
| 启动 | `uvicorn app.main:app --host 0.0.0.0 --port ${PORT}`，`PORT=8898` |
| 端口 | `127.0.0.1:8898:8898`（仅本机） |
| 卷 | `./sensitive-filter/data → /app/data`（SQLite 扫描库）、`./sensitive-filter/wordlists → /app/wordlists`（词表，按 mtime 热加载） |
| 网络 | 加入 `speakr_default`，通过服务名 `speakr:8899` 调 API |
| 对外接口 | `POST /hooks/speakr`、`GET /hits`、`GET /widget.js`、`POST /scan/{rid}`、`GET|PUT /wordlist`、`POST /rescan-all`、`GET /alerts`、`GET /healthz` |

**环境变量（`sensitive-filter/.env`）**

| 键 | 说明 |
|---|---|
| `SPEAKR_URL` | `http://speakr:8899` |
| `SPEAKR_TOKEN` | 主账号 API Token，**scope 必须含 read + write + webhooks**；`<REDACTED>` |
| `SPEAKR_TOKENS` | 多账号，逗号分隔（现网 admin + guest 两个 token），`<REDACTED>` |
| `WEBHOOK_SECRET` | 创建 webhook 时一次性返回的 secret，`<REDACTED>` |
| `WEBHOOK_SECRETS` | 多账号各自 webhook 的 secret，逗号分隔，`<REDACTED>` |
| 可选 | `API_URL_PUBLIC`、`LLM_ENABLED`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`ALERT_USER`、`ALERT_PASS`、`NOTIFY_LANG`（现网均未启用） |

### 4.3 whisperx-asr-api（ASR）

| 项 | 值 |
|---|---|
| 镜像 | `whisperx-asr-api:v20261027`（ID `cbabc8d8925b`，7.27 GB） |
| 启动 | `/workspace/entrypoint.sh` |
| 端口 | `9010:9000`、`127.0.0.1:8265:8265` |
| GPU | `deploy.resources.reservations.devices: nvidia, count: all, capabilities [gpu]` |
| `shm_size` | `8g`（Ray / faster-whisper 需要） |
| 卷 | 命名卷 `whisperx-cache → /.cache`（现网 12 GB：faster-whisper-large-v3、多个 wav2vec2 对齐模型、nltk_data） |
| 健康检查 | `GET http://localhost:9000/health`，30s 间隔；现网返回 `{"status":"healthy","device":"cuda","loaded_models":["large-v3"],"serve_mode":"simple"}` |
| 生效环境变量 | `HF_TOKEN=<REDACTED>`、`DEVICE=cuda`、`COMPUTE_TYPE=float16`、`BATCH_SIZE=16`、`PRELOAD_MODEL=large-v3`、`MAX_FILE_SIZE_MB=1000`、`SERVE_MODE=simple` |
| 编排中其余变量 | 全部保留默认（diarization、VAD、Ray 批处理等），改默认需评估 |

---

## 5. 重新打包（在现网 `82.156.1.74` 执行）

> 前提：`/data` 剩余空间 ≥ 30 GB（现网仅剩 46 GB，务必先清理旧包 `deploy_data.tar.gz`、`all_data/*.tar` 共约 14 GB）。

```bash
REL=/data/release-speaker-$(date +%Y%m%d)
mkdir -p $REL/{images,speakr,whisperx,sensitive-filter}

# 1) 镜像
docker save lenovo-speaker:v1              -o $REL/images/lenovo-speaker-v1.tar
docker save whisperx-asr-api:v20261027     -o $REL/images/whisperx-asr-api-v20261027.tar
docker save speakr-sensitive-filter:latest -o $REL/images/speakr-sensitive-filter.tar

# 2) 编排与配置（.env 含密钥，单独加密）
rsync -a /data/speakr/docker-compose.yml /data/speakr/.env $REL/speakr/
rsync -a --exclude data --exclude __pycache__ --exclude .venv --exclude .pytest_cache \
      /data/speakr/sensitive-filter/ $REL/sensitive-filter/
rsync -a /data/speakr/brand/ $REL/speakr/brand/
rsync -a /data/whisperx/docker-compose.yml /data/whisperx/.env $REL/whisperx/

# 3) ASR 模型缓存（离线环境必需；联网环境可跳过）
docker run --rm -v whisperx_whisperx-cache:/c -v $REL:/out alpine \
  tar czf /out/whisperx-cache.tar.gz -C /c .

# 4) 清空运行库、避免把历史数据带过去
rm -rf $REL/sensitive-filter/data

# 5) 校验清单 + 打总包
(cd $REL && find . -type f -printf '%p\t%s\n' | sort > MANIFEST.txt)
tar czf /data/release-speaker-$(date +%Y%m%d).tar.gz -C /data $(basename $REL)
```

打包后核对：镜像 3 个、brand 27 个文件、`MANIFEST.txt` 中**无** `uploads/`、`*.db`、`secret_key`。

---

## 6. 新环境部署步骤

### 6.0 前置条件

- Ubuntu 22.04/24.04，Docker ≥ 24、Docker Compose ≥ v2（现网 v5.0.2）
- 磁盘 ≥ 60 GB（镜像 14 GB + 模型缓存 12 GB + 音频增长），内存 ≥ 16 GB
- 仅 whisperx 所在机器需要：NVIDIA 驱动 + NVIDIA Container Toolkit，GPU 显存 ≥ 16 GB
- 网络：能访问外部 LLM（`vlm-pgx.sightai.tech:4433`）或已替换为内网模型；离线环境跳过模型下载改用缓存包

### 6.1 导入镜像

```bash
tar xzf release-speaker-YYYYMMDD.tar.gz && cd release-speaker-YYYYMMDD
for f in images/*.tar; do docker load -i "$f"; done
docker images | grep -E "lenovo-speaker|whisperx-asr-api|speakr-sensitive-filter"
```

### 6.2 先起 whisperx（GPU）

```bash
cd whisperx
# 若离线：恢复模型缓存
docker volume create whisperx_whisperx-cache
docker run --rm -v whisperx_whisperx-cache:/c -v $PWD:/in alpine \
  tar xzf /in/whisperx-cache.tar.gz -C /c
# 启动
docker compose up -d
curl -s http://127.0.0.1:9010/health
# 期望 {"status":"healthy","device":"cuda","loaded_models":["large-v3"],...}
```

### 6.3 再起 speakr + sensitive-filter

```bash
cd ../speakr
# 必改：ASR 指向新宿主机
sed -i 's|^ASR_BASE_URL=.*|ASR_BASE_URL=http://<新宿主机内网IP>:9010|' .env
# 建议改：管理员初始密码（仅首次建库生效）
sed -i 's|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=<新密码>|' .env
# 确认保留：WEBHOOK_INTRANET_HOST_ALLOWLIST=^sensitive-filter$

mkdir -p uploads instance sensitive-filter/data   # 新环境为空目录
docker compose up -d --build      # sensitive-filter 由 Dockerfile 现场构建
docker compose ps
```

启动顺序必须是 whisperx → speakr：speakr 启动即注册转写任务，ASR 不可达会导致任务失败重试。

### 6.4 建立账号与 Webhook（新库必做）

1. 浏览器打开 `http://<host>:8899`，用 `ADMIN_EMAIL` / `ADMIN_PASSWORD` 登录（首次启动按 .env 建管理员）。
2. 在 speakr「设置 → API Token」为**每个需要检测的账号**各建一个 Token，scope 勾选 `read, write, webhooks`。
   - 多账号：全部 Token 逗号拼接写入 `sensitive-filter/.env` 的 `SPEAKR_TOKENS`，第一个同时填入 `SPEAKR_TOKEN`。
3. 逐账号创建 webhook（**speakr 按录音归属者投递，一个账号一个 webhook**）：

```bash
curl -s -X POST http://<host>:8899/api/v1/webhooks \
  -H "Authorization: Bearer <该账号 token>" -H "Content-Type: application/json" \
  -d '{"name":"sensitive-filter","url":"http://sensitive-filter:8898/hooks/speakr",
       "events":["recording.transcription.completed","recording.summary.completed"],
       "allow_http":true,"enabled":true}'
```

4. 响应里的 `secret` **只返回一次**：主账号写 `WEBHOOK_SECRET`，其余追加到 `WEBHOOK_SECRETS`（逗号分隔）。
5. 验签连通性：`POST /api/v1/webhooks/{id}/test`，sensitive-filter 侧应返回 202。
6. 重建容器使新 .env 生效（`docker compose restart` 不重读 env_file，必须 `up -d`）：

```bash
docker compose up -d sensitive-filter
```

### 6.5 词表

- 交付包内 `wordlists/sensitive.csv` 是**初始种子**（18 行，含表头注释）。
- 现网以服务器上通过 `PUT /wordlist` 维护的版本为准；迁移时如需沿用现网词表，在现网执行
  `curl -s http://127.0.0.1:8898/wordlist` 导出后带回，**不要用本地仓库覆盖**。
- 更新词表后：单条重扫 `POST /scan/{rid}`，全量 `POST /rescan-all`。

### 6.6 前端 widget

`loading_overlay.html` 已内联 widget 脚本（9674 B，随 brand 目录挂载），**无需额外操作**。
若新环境要让浏览器直连 8898（秒级 `/hits` 通道），才需把 8898 改为对外监听并设 `API_URL_PUBLIC`；默认走 notes 回退通道，无需开放。

---

## 7. 可选：保留历史数据

只迁元数据（用户、标签、分享链接、录音记录），**不带音频**会导致历史录音点开为空，一般不建议：

```bash
# 现网停止写入
docker compose stop speakr
# 打包 instance（含 secret_key，二者必须成对迁移）
tar czf instance.tar.gz -C /data/speakr instance/transcriptions.db \
    instance/transcriptions.db-wal instance/secret_key
```

新环境解压到 `speakr/instance/` 后再 `up -d`；此时 `.env` 的 `ADMIN_PASSWORD` 不再生效（密码已在库里）。

---

## 8. 验收清单

| # | 检查项 | 命令 / 判据 |
|---|---|---|
| 1 | 容器全 Up | `docker compose ps`（speakr、sensitive-filter 均 Up） |
| 2 | ASR 健康 | `curl :9010/health` → `healthy` + `loaded_models:["large-v3"]` |
| 3 | 主站可达 | `curl -I http://<host>:8899` → 302（跳登录） |
| 4 | 品牌生效 | 登录页为 Lenovo 标识与配色，非上游默认 |
| 5 | 检测服务健康 | `curl http://127.0.0.1:8898/healthz` |
| 6 | 端到端 | 上传一段 1–2 分钟含敏感词的印尼语/中文音频 → 转写完成 → 自动打标签 + 写备注 + 总结追加清单 → 气泡出现竖条 |
| 7 | 多账号 | 用第二个账号上传同样音频，确认其 webhook 也触发（标签归属该账号） |
| 8 | Webhook 验签 | `POST /api/v1/webhooks/{id}/test` 返回 202，容器日志无验签失败 |
| 9 | 词表热加载 | `PUT /wordlist` 后新录音即时命中 |

---

## 9. 已知坑（迁移时最容易踩）

1. **`ASR_BASE_URL` 写的是旧宿主 IP**：不改成新 IP，转写全部失败，且失败在后台队列里不报页面错误。
2. **`WEBHOOK_INTRANET_HOST_ALLOWLIST` 漏配**：speakr 会拒绝 `http://sensitive-filter:8898` 这个内网 URL，webhook 建不上。
3. **改 `.env` 后要 `docker compose up -d`**：`restart` 不重读 `env_file`。
4. **webhook secret 只返回一次**：没保存只能删除重建，并同步改 `WEBHOOK_SECRET(S)`。
5. **多账号必须各建 webhook**：speakr 按录音归属者投递；只建一个会导致 guest 的录音不被检测。
6. **词表方向**：服务器版本是权威源，`rsync` 本仓库到服务器时必须 `--exclude "wordlists/"`，否则会把线上词表覆盖成种子。
7. **`ADMIN_PASSWORD` 只在首次建库生效**：已有库时改 .env 不会改密码。
8. **8898 仅绑定本机**：调试用 `ssh -N -L 8898:127.0.0.1:8898 <host>`。
9. **`instance/secret_key` 与数据库成对**：只迁库不迁 key 会导致会话/加密字段异常。
10. **whisperx 模型缓存卷名**是 `whisperx_whisperx-cache`（compose 项目名前缀），换项目目录名会创建新空卷并重新下载 12 GB。
11. **磁盘**：现网 `/data` 已用 100%（剩 46 GB），打包前必须清理旧 tar，否则 `docker save` 中途失败留半包。

---

## 10. 附：文件级校验参考（打包后比对）

| 对象 | 数量 / 校验 |
|---|---|
| brand 文件 | 27 个（26 挂载 + 1 个 `.bak-sw` 不参与挂载） |
| sensitive-filter `app/` | 12 个 `.py`，现网与本地仓库 MD5 全一致 |
| 词表 | 现网 `sensitive.csv` MD5 `a4cc3e9c…`，本地种子 `0d5b9edc…`（不同属正常） |
| 编排 | speakr compose 含 2 个 service；whisperx compose 含 1 个 service + 1 个命名卷 |
