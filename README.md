# speakr-sensitive-lite

speakr 敏感词提示外挂 — Demo 极简版(Part A)。单文件 Python 脚本 + 文本词表,轮询 speakr API 检测敏感词,命中后自动挂标签、写备注、记录审计日志。不依赖 Webhook、不改 compose、不重启 speakr。

## 文件

- `lite.py` — 检测主脚本(轮询 → 拉 transcript → 词表匹配 → 回写标签/备注)
- `words.txt` — 词表(每行一条,`L1,词` 格式,支持 CSV 双列序,`#` 注释;L1 违禁 L2 警示 L3 关注)
- `widget.user.js` — Tampermonkey 油猴脚本,浏览器端高亮备注中的命中句
- `.env.example` — 环境变量模板

## 快速开始

```bash
cp .env.example .env   # 填入 SPEAKR_URL 和 TOKEN
pip install requests   # 如尚未安装
python3 lite.py            # 常驻轮询(默认 60s)
python3 lite.py --backfill # 存量全量重扫
python3 lite.py --id <recording_id>  # 只扫指定录音
```

词表按 mtime 热加载,改完即生效。运行时状态与审计日志写在 `data/`(不入库)。

## 设计文档

见仓库外 `docs/敏感词处理方案.md`(Part A 已交付,Part B 完整版为后续演进)。
