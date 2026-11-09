#!/usr/bin/env bash
# 把单体词表按 (level, category, lang) 拆成分片，可选并入额外词条；按 (term,lang) 去重。
# 只输出统计，不打印词条内容。
set -euo pipefail

HEADER='term,lang,level,category,variants,regex,enabled'
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

input="$ROOT_DIR/wordlists/sensitive.csv"
add=""
dir="$ROOT_DIR/wordlists"
force=0
dry=0

msg() { printf '%s\n' "$*" >&2; }
die() { printf '错误: %s\n' "$*" >&2; exit 1; }

usage() {
  cat >&2 <<'U'
用法: tools/split_wordlist.sh [选项]
  把单体词表按 (level, category, lang) 拆成 sensitive-<LVL>-<category>-<lang>.csv。
  可选 --add 并入额外词条；按 (term,lang) 去重，先出现者优先（--input 先于 --add）。

  --input FILE   输入词表（默认 wordlists/sensitive.csv）
  --add FILE     额外并入的词条（同一 7 列格式）
  --dir DIR      输出分片目录（默认 wordlists/）
  --force        目标分片非空时允许覆盖
  --dry-run      只打印计划，不写盘
U
}

while (($#)); do
  case "$1" in
    --input) input="$2"; shift 2 ;;
    --add) add="$2"; shift 2 ;;
    --dir) dir="$2"; shift 2 ;;
    --force) force=1; shift ;;
    --dry-run) dry=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数: $1" ;;
  esac
done

[ -f "$input" ] || die "输入不存在: $input"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
all="$tmp/all"
: > "$all"

ingest() {
  file="$1"
  [ -f "$file" ] || die "文件不存在: $file"
  if ! awk -F',' -v fn="$file" -v out="$all" '
    {
      sub(/\r$/, "")
      if (NR == 1 && tolower($3) == "level") next
      if ($0 ~ /^[[:space:]]*$/) next
      if ($0 ~ /^[[:space:]]*#/) next
      if (tolower($3) == "level") next
      if (NF != 7) { printf("  %s:第%d行 列数=%d（应为7）\n", fn, NR, NF) > "/dev/stderr"; bad=1; next }
      for (i=1;i<=7;i++) gsub(/^[[:space:]]+|[[:space:]]+$/, "", $i)
      term=$1; lang=tolower($2); level=$3; category=$4; variants=$5; regex=$6; enabled=$7
      if (term == "") { printf("  %s:第%d行 term 为空\n", fn, NR) > "/dev/stderr"; bad=1; next }
      if (level !~ /^(L1|L2|L3)$/) { printf("  %s:第%d行 level 非法\n", fn, NR) > "/dev/stderr"; bad=1; next }
      if (lang !~ /^[a-z][a-z](-[A-Za-z][A-Za-z])?$/) { printf("  %s:第%d行 lang 非法\n", fn, NR) > "/dev/stderr"; bad=1; next }
      e=tolower(enabled)
      if (e == "0" || e == "false" || e == "no") en="0"; else en="1"
      gsub(/[[:space:]]+/, " ", variants)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", variants)
      ctg=tolower(category)
      gsub(/[^a-z0-9_-]+/, "-", ctg)
      gsub(/^-+/, "", ctg)
      gsub(/-+$/, "", ctg)
      if (ctg == "") ctg="misc"
      printf "%s,%s,%s,%s,%s,%s,%s\n", term, lang, level, ctg, variants, regex, en >> out
    }
    END { exit (bad ? 1 : 0) }
  ' "$file"; then
    die "解析失败: $file"
  fi
}

ingest "$input"
if [ -n "$add" ]; then ingest "$add"; fi

awk -F',' 'BEGIN{OFS=","} { k=tolower($1) "," $2; if (!(k in seen)) { seen[k]=1; print } }' "$all" > "$all.dedup"
before=$(wc -l < "$all")
after=$(wc -l < "$all.dedup")
dups=$((before - after))

awk -F',' '{ print $3 "|" $4 "|" $2 }' "$all.dedup" | LC_ALL=C sort | uniq -c > "$tmp/plan"

msg "输入词条: $before 条；去重后: $after 条（重复 $dups 条，先出现者保留）"

if [ "$dry" = "1" ]; then
  msg "---- 计划写入 ----"
  while read -r cnt key; do
    lvl=$(printf '%s' "$key" | cut -d'|' -f1)
    ctg=$(printf '%s' "$key" | cut -d'|' -f2)
    lng=$(printf '%s' "$key" | cut -d'|' -f3)
    msg "  $cnt  sensitive-$lvl-$ctg-$lng.csv"
  done < "$tmp/plan"
  exit 0
fi

files=0
while read -r cnt key; do
  lvl=$(printf '%s' "$key" | cut -d'|' -f1)
  ctg=$(printf '%s' "$key" | cut -d'|' -f2)
  lng=$(printf '%s' "$key" | cut -d'|' -f3)
  out="$dir/sensitive-$lvl-$ctg-$lng.csv"
  if [ -e "$out" ] && [ -s "$out" ] && [ "$force" != "1" ]; then
    die "目标已存在且非空: $out（加 --force 覆盖）"
  fi
  mkdir -p "$dir"
  {
    printf '%s\n' "$HEADER"
    awk -F',' -v l="$lvl" -v c="$ctg" -v g="$lng" '$3==l && $4==c && $2==g' "$all.dedup" \
      | LC_ALL=C sort -t, -k3,3 -k2,2 -k4,4 -k1,1
  } > "$out"
  files=$((files + 1))
  msg "  写入 $out（$cnt 条）"
done < "$tmp/plan"
msg "完成: $files 个分片，$after 条词。"
