# speakr-sensitive-filter

speakr 敏感词提示 — Part B 完整版（Webhook 秒级触发 + 词表/LLM 双层检测 + 标签/备注/总结回写 + 气泡高亮）。独立于 Part A（speakr-sensitive-lite，保持不动）；验证后由本服务接管触发层。

设计文档：仓库外 docs/敏感词处理方案.md（Part B / B7 / B8）。

## 架构

- POST /hooks/speakr — 接收 speakr Webhook（HMAC V2 验签 + Delivery-Id 幂等 + 立即 202）
- 后台 worker — 拉转写 → 词表粗筛 →（可选）LLM 精判 → SQLite 落库 → 回写标签/备注 → 总结追加清单
- GET /hits?recording_id=N — 命中查询（widget 数据源）
- GET /widget.js — 气泡高亮脚本（语句级竖条+角标，关键词级 mark 下划线）
- POST /scan/{rid} — 手动重扫单条录音
- GET /alerts — 告警看板（Basic Auth，可选）

## 本地开发

```bash
cp .env.example .env         # 填入 SPEAKR_TOKEN / WEBHOOK_SECRET
pip install -r requirements.txt -r requirements-dev.txt
pytest                       # 测试
uvicorn app.main:app --port 8898
```

## 部署（B2，需重启 speakr 一次）

1. 上传本工程到服务器 /data/speakr/sensitive-filter/，填写 .env
2. compose 追加 sensitive-filter 服务（见 docker-compose.snippet.yml）
3. speakr .env 追加 WEBHOOK_INTRANET_HOST_ALLOWLIST=^sensitive-filter$
4. 新建 Token（read,write,webhooks）→ POST /api/v1/webhooks：
   {"name":"sensitive-filter","url":"http://sensitive-filter:8898/hooks/speakr",
    "events":["recording.transcription.completed","recording.summary.completed"],
    "allow_http":true,"enabled":true}
   secret 只在创建响应返回一次 → 写入本服务 .env
5. POST /api/v1/webhooks/{id}/test 验签；重转写一条真实录音做端到端验证
6. widget 注入：见 deploy/loading_overlay.snippet.html（并入已挂载的 loading_overlay.html）

## 词表

wordlists/sensitive.csv（volume 挂载，按 mtime 热加载）：
列 term,lang,level,category,variants,regex,enabled；兼容 lite words.txt 两列格式。
印尼语变体手写进 variants（空格分隔）；LLM 兜底词形/委婉/错字。

id 脏话词表来自 [drizki/indonesian-badwords](https://github.com/drizki/indonesian-badwords)（MIT，commit `cd5ea59`）：只取 reviewed-core、非地区语词形，按词义聚合；severity 3-4 → L2，severity 2 → L3，L1 不用于脏话。

en/zh 脏话词表来自 [LDNOOBW](https://github.com/LDNOOBW/List-of-Dirty-Naughty-Obscene-and-Otherwise-Bad-Words)（CC BY 4.0，master）：全量导入后剔除归一化空值、纯数字/符号、单字符及非中文短条目（修改说明见 CSV 注释），等级统一 L3、category=profanity。

词表热加载，但历史录音需 `POST /rescan-all` 才会用新词重算。

## 词表与重扫操作示例

```bash
# 下载当前词表(查看全部关键词与各级启用数)
curl -s http://127.0.0.1:8898/wordlist | python3 -m json.tool

# 更新词表:下载 → 编辑(加一行 赌博,zh,L1,gambling,,,1)→ 上传整份(PUT 是全文替换,不是增量)
curl -s http://127.0.0.1:8898/wordlist | python3 -c "import json,sys; print(json.load(sys.stdin)['content'])" > words.txt
echo '赌博,zh,L1,gambling,,,1' >> words.txt
curl -s -X PUT http://127.0.0.1:8898/wordlist -H "Content-Type: text/plain" --data-binary @words.txt

# 重扫:单条(词表调整后验证)与全量(词表大改后重跑历史)
curl -s -X POST http://127.0.0.1:8898/scan/13
curl -s -X POST http://127.0.0.1:8898/rescan-all
```

远程调用:先开 SSH 隧道 `ssh -N -L 8898:127.0.0.1:8898 <host>` 再访问 http://127.0.0.1:8898;PUT 是整份替换,上传后新转写即时生效,历史会议需 rescan。

## 可靠性

- 验签：V2（t+HMAC，±300s 窗口，支持 secret 轮换宽限）→ V1 回退
- 幂等：delivery_id 主键，speakr 重试投递不重复处理
- 重试：时序未就绪 30s 固定重试；其他错误指数退避（30s→600s 封顶），超限标 dead
- 降级：LLM 失败/非法 JSON/置信度<0.6 → 保留纯词表命中，检测不中断
- 自愈：总结重新生成自动重追加清单；零命中自动删除残留块；重扫替换旧命中

## 多语言

- 提示文案（备注块/总结清单）语言 = owner 的 `users/me.preferences.ui_language`（缓存 300s）
- 支持 zh/en/id/de/es/fr/pt/ru；ui_language 未覆盖时回落 `NOTIFY_LANG`（默认 zh）
- 标签 SW-L1/L2/L3 语言中立；机器可读数据块与语言无关，widget 高亮不受影响
- 词表远程管理：GET/PUT /wordlist（校验+原子替换+.bak）、POST /rescan-all 全量重扫
