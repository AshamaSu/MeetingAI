#!/usr/bin/env bash
# meetily-alternative 部署兼容性自检脚本
# 用法:
#   speakr 服务器上:  bash env_check.sh server [ASR地址，可选，如 http://192.168.1.50:9000]
#   GPU 算力机上:     bash env_check.sh gpu
# 只做只读检查，不修改任何系统配置。

set -u
ROLE="${1:-}"
EXTRA="${2:-}"
OK="✅"; WARN="⚠️"; FAIL="❌"
issues=0

section() { echo; echo "======== $1 ========"; }
check()  { # check <级别> <说明>
  echo "$1 $2"
  [ "$1" = "$FAIL" ] && issues=$((issues+1))
}

if [ "$ROLE" != "server" ] && [ "$ROLE" != "gpu" ]; then
  echo "用法: bash $0 server|gpu [ASR_BASE_URL]"; exit 1
fi

echo "角色: $ROLE   检查时间: $(date '+%F %T')"

section "1. 系统基础"
OS=$(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d'"' -f2 || echo "未知")
echo "   系统: $OS"
echo "   内核: $(uname -r)"
echo "   CPU 核数: $(nproc 2>/dev/null || echo '?')"
MEM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}')
echo "   内存: ${MEM_GB:-?} GB"
if [ -n "${MEM_GB:-}" ]; then
  if [ "$MEM_GB" -ge 16 ]; then check "$OK" "内存 >= 16GB（官方推荐）"
  elif [ "$MEM_GB" -ge 8 ]; then check "$WARN" "内存 8-15GB，达最低线但偏紧"
  else check "$FAIL" "内存 < 8GB，不满足官方最低要求"; fi
fi
DISK_AVAIL=$(df -BG / 2>/dev/null | awk 'NR==2{gsub("G","",$4);print $4}')
echo "   根分区可用: ${DISK_AVAIL:-?} GB"
if [ -n "${DISK_AVAIL:-}" ]; then
  if [ "$DISK_AVAIL" -ge 50 ]; then check "$OK"  "磁盘 >= 50GB（模型+数据充足）"
  elif [ "$DISK_AVAIL" -ge 20 ]; then check "$WARN" "磁盘 20-49GB，可跑但模型缓存要留意"
  else check "$FAIL" "磁盘 < 20GB，模型下载都放不下"; fi
fi

section "2. Docker 环境"
if command -v docker >/dev/null 2>&1; then
  check "$OK" "Docker: $(docker --version 2>/dev/null | head -1)"
else
  check "$FAIL" "未安装 Docker —— 两台机器都必须安装"
fi
if docker compose version >/dev/null 2>&1; then
  check "$OK" "Docker Compose v2: $(docker compose version 2>/dev/null | head -1)"
elif command -v docker-compose >/dev/null 2>&1; then
  check "$WARN" "检测到旧版 docker-compose（v1），建议升级到 Compose v2"
else
  check "$FAIL" "未安装 Docker Compose"
fi

if [ "$ROLE" = "gpu" ]; then
  section "3. GPU 与 NVIDIA 环境（仅算力机）"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | while read -r line; do
      echo "   GPU: $line"
    done
    VRAM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | sort -nr | head -1)
    if [ -n "${VRAM_MB:-}" ]; then
      if [ "$VRAM_MB" -ge 15000 ]; then check "$OK" "显存 >= 15GB，可跑 large-v3 + 分离（推荐档）"
      elif [ "$VRAM_MB" -ge 8000 ];  then check "$OK" "显存 8-15GB，达最低要求；large-v3 需注意 BATCH_SIZE 降到 8/int8"
      else check "$FAIL" "显存 < 8GB，不满足 WhisperX + pyannote 最低要求"; fi
    fi
  else
    check "$FAIL" "未检测到 nvidia-smi —— 需先装 NVIDIA 驱动"
  fi
  if command -v nvidia-ctk >/dev/null 2>&1 || docker info 2>/dev/null | grep -qi nvidia; then
    check "$OK" "NVIDIA Container Toolkit 已就绪"
  else
    check "$FAIL" "未检测到 NVIDIA Container Toolkit —— GPU 容器无法调用显卡"
  fi
  if command -v nvidia-smi >/dev/null 2>&1; then
    CUDA_VER=$(nvidia-smi 2>/dev/null | grep -o 'CUDA Version: [0-9.]*' | head -1 | awk '{print $3}')
    echo "   驱动支持的 CUDA: ${CUDA_VER:-?}（容器自带 CUDA 运行时，此值只需 >= 12.0 即可）"
  fi
  section "4. 端口监听（算力机）"
  if command -v ss >/dev/null 2>&1 && ss -tln 2>/dev/null | grep -q ':9000 '; then
    check "$WARN" "9000 端口已被占用 —— 部署前确认是否冲突"
  else
    check "$OK" "9000 端口空闲"
  fi
fi

section "5. 出网连通性"
for url in https://hf-mirror.com https://huggingface.co; do
  if curl -s -m 6 -o /dev/null -w '%{http_code}' "$url" 2>/dev/null | grep -qE '^[23]'; then
    check "$OK" "$url 可达"
  else
    check "$WARN" "$url 不可达（影响：模型下载；可用 hf-mirror.com 替代，设 HF_ENDPOINT）"
  fi
done
if [ "$ROLE" = "server" ]; then
  LLM_URL="${LLM_URL:-https://api.deepseek.com}"
  CODE=$(curl -s -m 6 -o /dev/null -w '%{http_code}' "$LLM_URL" 2>/dev/null || echo "000")
  if [ "$CODE" != "000" ]; then check "$OK" "LLM 端点 $LLM_URL 可达（HTTP $CODE）"
  else check "$FAIL" "LLM 端点 $LLM_URL 不可达 —— 检查出网防火墙/代理"; fi
fi

if [ "$ROLE" = "server" ]; then
  section "6. 与算力机连通（speakr 服务器）"
  if [ -n "$EXTRA" ]; then
    CODE=$(curl -s -m 6 "$EXTRA/health" 2>/dev/null || echo "")
    if echo "$CODE" | grep -qi healthy; then
      check "$OK" "ASR 服务 $EXTRA/health 响应正常: $CODE"
    else
      check "$FAIL" "ASR 服务 $EXTRA/health 无响应 —— 检查 GPU 机防火墙(9000/tcp)与服务是否启动"
    fi
  else
    check "$WARN" "未提供 ASR 地址，跳过（部署 GPU 机后重跑: bash $0 server http://<GPU机IP>:9000）"
  fi
fi

echo
if [ "$issues" -eq 0 ]; then
  echo "总结: 无阻断项 ✅（⚠️ 为提示项，不阻断部署）"
else
  echo "总结: 发现 $issues 个阻断项 ❌，需先解决"
fi
echo ">>> 请把本脚本完整输出贴回对话，2B 帮你判读。"
