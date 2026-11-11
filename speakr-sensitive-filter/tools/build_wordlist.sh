#!/usr/bin/env bash
# 把 wordlists/ 下的分片合并成唯一规范词表；服务侧 app/ 零改动。
# 生成的文件仍由 PUT /wordlist 上传（服务端自带校验 / .bak / mtime 热加载）。
#
# 合并方式：每片去掉表头后汇入，只保留一个总表头。不能 raw 拼接整份文本，
# 因为 app/lexicon.py 的 parse_wordlist 用「整段文本首行」判断 CSV 格式，
# 多余表头会干扰解析（混入 2 列格式更会让全部 CSV 行被丢弃）。
#
# 输出只含统计信息；除 --stdout / --show-diff 显式开启外，绝不打印词条内容。
set -euo pipefail

HEADER='term,lang,level,category,variants,regex,enabled'
MAX_BYTES=200000
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

dir="$ROOT_DIR/wordlists"
output="./sensitive.generated.csv"
drop=""
check=0
report=0
to_stdout=0
show_diff=0

msg() { printf '%s\n' "$*" >&2; }
die() { printf '错误: %s\n' "$*" >&2; exit 1; }

usage() {
  cat >&2 <<'USAGE'
用法: tools/build_wordlist.sh [选项]

把分片合并成唯一规范词表；服务侧 app/ 零改动，生成文件仍由 PUT /wordlist 上传。

分片命名  sensitive-<L1|L2|L3>-<Category>-<lang>.csv
          例 sensitive-L2-finance-id.csv、sensitive-L3-risk-en.csv
每片首行须为 7 列表头（列名不强制，脚本剥掉，输出统一用标准列名）；
文件名只用于发现/分组/硬校验，不覆盖 CSV 列。

选项:
  --dir DIR            分片目录（默认 脚本上级/wordlists）
  --output FILE        输出文件（默认 ./sensitive.generated.csv，默认不写进 wordlists/）
  --drop-category LIST 按 category 整类剔除，逗号分隔（如 profanity,insult）
  --check              只校验并比对 sha256，不写盘；有漂移 exit 1
  --show-diff          --check 有漂移时打印行级 diff（会显示词条，需显式开启）
  --report             额外打印按 category 的条数分布（供确定裁剪清单）
  --stdout             把生成内容写到 stdout（不写文件；统计信息走 stderr）
  -h, --help           显示本帮助
USAGE
}

while (($#)); do
  case "$1" in
    --dir)           dir=${2:?--dir 缺少参数}; shift 2 ;;
    --output)        output=${2:?--output 缺少参数}; shift 2 ;;
    --drop-category) drop=${2:?--drop-category 缺少参数}; shift 2 ;;
    --check)         check=1; shift ;;
    --show-diff)     show_diff=1; shift ;;
    --report)        report=1; shift ;;
    --stdout)        to_stdout=1; shift ;;
    -h|--help)       usage; exit 0 ;;
    *) die "未知参数: $1（用 --help 查看用法）" ;;
  esac
done

(( check == 0 || to_stdout == 0 )) || die "--check 与 --stdout 不能同用"
[[ -d "$dir" ]] || die "分片目录不存在: $dir"

drop_key=""
if [[ -n "$drop" ]]; then
  drop_lc=$(printf '%s' "$drop" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
  drop_key=",$drop_lc,"
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
rows="$tmp/rows"
stats="$tmp/stats"
dups="$tmp/dups"
built="$tmp/built"
: > "$rows"; : > "$stats"; : > "$dups"

# 发现分片：sensitive-*.csv 不会匹配 sensitive.csv / .bak / .tmp / .swp / 隐藏文件
shopt -s nullglob
candidates=( "$dir"/sensitive-*.csv )
shopt -u nullglob
valid=()
for f in "${candidates[@]}"; do
  b=$(basename "$f")
  case "$b" in .*|*.bak|*.tmp|*.swp|*.swo|*.swn|*~) continue ;; esac
  valid+=( "$f" )
done
if ((${#valid[@]} == 0)); then
  die "未发现任何分片（$dir/sensitive-*.csv）；拒绝生成空词表"
fi
mapfile -t shards < <(printf '%s\n' "${valid[@]}" | LC_ALL=C sort)

msg "分片目录: $dir"
msg "发现分片: ${#shards[@]} 个"

for f in "${shards[@]}"; do
  b=$(basename "$f")
  if [[ ! "$b" =~ ^sensitive-(L[123])-([A-Za-z][A-Za-z0-9_-]*)-([a-z]{2}(-[A-Za-z]{2})?)\.csv$ ]]; then
    die "分片文件名不合规（应为 sensitive-<L1|L2|L3>-<Category>-<lang>.csv）: $b"
  fi
  f_level=${BASH_REMATCH[1]}
  f_cat=${BASH_REMATCH[2]}
  f_lang=${BASH_REMATCH[3]}
  f_cat_lc=${f_cat,,}

  # 表头必须存在且为 7 列；列名不强制（脚本会剥掉表头，输出统一用标准列名）。
  # 若首行第 3 列是合法 level，说明没有表头 → 报错，避免静默吞掉首行数据。
  hdr_nf=$(awk -F',' 'NR==1{ sub(/\r$/, ""); print NF; exit }' "$f")
  hdr_lvl=$(awk -F',' 'NR==1{ gsub(/^[[:space:]]+|[[:space:]]+$/, "", $3); print $3; exit }' "$f")
  [[ "$hdr_nf" == 7 ]] || die "分片首行表头列数应为 7（实际 $hdr_nf）: $b"
  [[ ! "$hdr_lvl" =~ ^(L1|L2|L3)$ ]] || die "分片缺少表头（首行是数据行）: $b"

  # 第 8 列是来源文件名（仅用于重复检测定位，写出前裁掉）
  if ! awk -F',' \
      -v lvl="$f_level" -v lng="$f_lang" -v cat="$f_cat_lc" -v fn="$b" \
      -v dropkey="$drop_key" -v out="$rows" -v stat="$stats" '
    BEGIN { bad=0; n=0; kept=0; dropped=0; catwarn=0 }
    {
      if (NR == 1) next
      sub(/\r$/, "")
      if ($0 ~ /^[[:space:]]*$/) next
      if ($0 ~ /^[[:space:]]*#/) next
      # 模板文件可能在注释后又写一行表头；level 列值为 "level" 的只能是表头，跳过
      if (tolower($3) == "level") next
      n++
      if (NF != 7) {
        printf("  %s:第%d行 列数=%d（应为7；字段内不能含逗号）\n", fn, NR, NF) > "/dev/stderr"
        bad=1; next
      }
      for (i=1; i<=7; i++) gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i)
      term=$1; lang=$2; level=$3; category=$4; variants=$5; regex=$6; enabled=$7
      if (term == "") {
        printf("  %s:第%d行 term 为空\n", fn, NR) > "/dev/stderr"; bad=1; next
      }
      if (level !~ /^(L1|L2|L3)$/ || level != lvl) {
        printf("  %s:第%d行 level 非法或与文件名不符（文件名声明 %s）\n", fn, NR, lvl) > "/dev/stderr"
        bad=1; next
      }
      if (lang != lng) {
        printf("  %s:第%d行 lang 与文件名不符（文件名声明 %s）\n", fn, NR, lng) > "/dev/stderr"
        bad=1; next
      }
      if (category == "") {
        printf("  %s:第%d行 category 为空\n", fn, NR) > "/dev/stderr"; bad=1; next
      }
      category=tolower(category)
      if (category != cat && !catwarn) {
        printf("  %s: category 与文件名不一致（文件名=%s），仅提示不报错\n", fn, cat) > "/dev/stderr"
        catwarn=1
      }
      e=tolower(enabled)
      if (e == "0" || e == "false" || e == "no") en="0"
      else if (e == "1" || e == "true" || e == "yes") en="1"
      else {
        printf("  %s:第%d行 enabled 非法（应为 0/1）\n", fn, NR) > "/dev/stderr"
        bad=1; next
      }
      gsub(/[[:space:]]+/, " ", variants)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", variants)
      if (dropkey != "" && index(dropkey, "," category ",") > 0) { dropped++; next }
      printf "%s,%s,%s,%s,%s,%s,%s,%s\n", term, lang, level, category, variants, regex, en, fn >> out
      kept++
    }
    END {
      if (n == 0) {
        printf("  %s: 无数据行（仅表头/注释），已跳过\n", fn) > "/dev/stderr"
      }
      printf("%d\t%d\t%d\n", n, kept, dropped) >> stat
      exit bad
    }
  ' "$f"; then
    die "分片校验失败: $b"
  fi
done

# 跨分片重复 term（键 term,lang）→ 硬失败；只报数量与来源文件名，不报词条
awk -F',' '{ print tolower($1) "," $2 }' "$rows" | LC_ALL=C sort | uniq -d > "$dups"
if [[ -s "$dups" ]]; then
  n=$(wc -l < "$dups")
  srcs=$(awk -F',' 'NR==FNR { d[$0]=1; next } d[tolower($1) "," $2] { print $8 }' \
         "$dups" "$rows" | LC_ALL=C sort -u | tr '\n' ' ')
  die "检测到 $n 个跨分片重复 term 键(term,lang)；涉及分片: ${srcs:-未知}。请把重复项合并到同一分片后重试"
fi

total=$(wc -l < "$rows")
(( total > 0 )) || die "合并后 0 条有效词（分片为空或全被 --drop-category 过滤）；拒绝生成空文件覆盖"

{
  printf '%s\n' "$HEADER"
  LC_ALL=C sort -t, -k3,3 -k2,2 -k4,4 -k1,1 "$rows" | cut -d, -f1-7
} > "$built"

bytes=$(wc -c < "$built")
(( bytes <= MAX_BYTES )) || die "合并结果 ${bytes} 字节 > ${MAX_BYTES}（PUT 会返回 413；分片不增加总容量）"

by_level=$(awk -F',' 'NR>1 { c[$3]++ } END { printf "L1=%d L2=%d L3=%d", c["L1"], c["L2"], c["L3"] }' "$built")
dropped=$(awk -F'\t' '{ s+=$3 } END { print s+0 }' "$stats")
sha=$(sha256sum "$built" | awk '{print $1}')

msg "---- 合并统计 ----"
msg "词条总数: $total"
msg "按级别:   $by_level"
if ((report)); then
  msg "按类别:"
  awk -F',' 'NR>1 { c[$4]++ } END { for (k in c) printf("  %-24s %d\n", k, c[k]) }' "$built" \
    | LC_ALL=C sort >&2
fi
msg "已过滤:   $dropped 条${drop:+（--drop-category: $drop）}"
msg "字节:     $bytes / $MAX_BYTES"
msg "sha256:   $sha"

if ((check)); then
  if [[ -f "$output" ]]; then
    old=$(sha256sum "$output" | awk '{print $1}')
    if [[ "$old" == "$sha" ]]; then
      msg "CHECK: 无变化（与 $output 的 sha256 一致）"
      exit 0
    fi
    msg "CHECK: 有漂移（与 $output 的 sha256 不同）"
    if ((show_diff)); then
      diff -u --label "$output" --label generated "$output" "$built" >&2 || true
    fi
    exit 1
  fi
  msg "CHECK: 校验通过；参考文件不存在（$output），本次为首次生成"
  exit 0
fi

if ((to_stdout)); then
  cat "$built"
  exit 0
fi

mkdir -p "$(dirname "$output")"
tmp_out="${output}.tmp"
cp "$built" "$tmp_out"
if [[ -e "$output" ]]; then
  mv -f "$output" "${output}.bak"
fi
mv -f "$tmp_out" "$output"
msg "已写出: $output"
