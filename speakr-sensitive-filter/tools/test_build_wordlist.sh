#!/usr/bin/env bash
# build_wordlist.sh 自检：全部用 mktemp -d + 合成假词，不依赖/不读取真实词表。
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BUILD="$SCRIPT_DIR/build_wordlist.sh"
HEADER='term,lang,level,category,variants,regex,enabled'

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
pass=0; fail=0

ok()  { printf '  PASS  %s\n' "$1"; pass=$((pass+1)); }
bad() { printf '  FAIL  %s\n' "$1" >&2; fail=$((fail+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

reset() {
  rm -rf "$tmp/shards" "$tmp/out.csv" "$tmp/stdout" "$tmp/stderr"
  mkdir -p "$tmp/shards"
}
mk() { # mk <filename> <数据行...>
  local name=$1; shift
  { printf '%s\n' "$HEADER"; printf '%s\n' "$@"; } > "$tmp/shards/$name"
}
run() { "$BUILD" "$@" >"$tmp/stdout" 2>"$tmp/stderr"; }
expect_ok()   { local d=$1; shift; if run "$@"; then ok "$d"; else bad "$d"; sed -n '1,6p' "$tmp/stderr" >&2; fi; }
expect_fail() { local d=$1; shift; if run "$@"; then bad "$d（本应失败却成功）"; else ok "$d"; fi; }

printf '== build_wordlist.sh 自检 ==\n'

# --- 1. 合法合并：表头剥离 / 排序 / 确定性 / .bak 原子替换 ---
reset
mk sensitive-L2-finance-id.csv 'betaa,id,L2,finance,,,1' 'alphaa,id,L2,finance,,,1'
mk sensitive-L1-violence-zh.csv 'gammaa,zh,L1,violence,,,1'
expect_ok '合法合并成功' --dir "$tmp/shards" --output "$tmp/out.csv"
check '只保留一个总表头'   "[[ \$(grep -c '^term,lang,level,category,variants,regex,enabled\$' \"$tmp/out.csv\") == 1 ]]"
check '输出 1 表头 + 3 词条' "[[ \$(wc -l < \"$tmp/out.csv\") == 4 ]]"
check '按 level 排序（L1 在前）' "[[ \$(sed -n '2p' \"$tmp/out.csv\" | cut -d, -f3) == L1 ]]"
sha1=$(sha256sum "$tmp/out.csv" | awk '{print $1}')
expect_ok '二次生成成功' --dir "$tmp/shards" --output "$tmp/out.csv"
sha2=$(sha256sum "$tmp/out.csv" | awk '{print $1}')
check '两次输出字节一致（确定性）' "[[ '$sha1' == '$sha2' ]]"
check '生成 .bak（原子替换）' "[[ -f \"$tmp/out.csv.bak\" ]]"

# --- 2. category 大小写归一 / enabled 归一 / variants 归一 ---
reset
mk sensitive-L3-RISK-en.csv 'wordx,en,L3,RISK,  a   b  ,,yes'
expect_ok '大小写 mixed 分片通过' --dir "$tmp/shards" --output "$tmp/out.csv"
check 'category 归一为小写' "grep -q '^wordx,en,L3,risk,' \"$tmp/out.csv\""
check 'enabled 归一为 1'    "grep -q '^wordx,en,L3,risk,a b,,1\$' \"$tmp/out.csv\""

# --- 3. 文件名硬校验 ---
reset
mk sensitive-L4-finance-id.csv 'wordx,id,L4,finance,,,1'
expect_fail '非法 level 段（L4）被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
mk sensitive-l2-finance-id.csv 'wordx,id,L2,finance,,,1'
expect_fail '小写 l2 被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
mk sensitive-L2-finance.csv 'wordx,id,L2,finance,,,1'
expect_fail '非法文件名格式（缺 lang）被拒' --dir "$tmp/shards" --output "$tmp/out.csv"

# --- 4. 列硬校验（堵住 parse_wordlist 的静默丢行） ---
reset
mk sensitive-L2-finance-id.csv 'wordx,id,L3,finance,,,1'
expect_fail 'level 与文件名不符被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
mk sensitive-L2-finance-id.csv 'wordx,en,L2,finance,,,1'
expect_fail 'lang 与文件名不符被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
mk sensitive-L2-finance-id.csv 'wordx,id,L2,finance,a,b,c,1'
expect_fail '字段含逗号（8 列）被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
mk sensitive-L2-finance-id.csv 'wordx,id,L2,finance,,,maybe'
expect_fail 'enabled 非法被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
printf 'foo,bar\n' > "$tmp/shards/sensitive-L2-finance-id.csv"
expect_fail '表头不合规被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
mk sensitive-L2-finance-id.csv
expect_fail '只有表头（0 条）被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
{ printf 'word,lang,tier,group,forms,pattern,on\n'; printf 'w1,id,L2,finance,,,1\n'; } \
  > "$tmp/shards/sensitive-L2-finance-id.csv"
expect_ok '不同列名的 7 列表头被接受' --dir "$tmp/shards" --output "$tmp/out.csv"
check '输出仍统一用标准表头' "[[ \$(sed -n '1p' \"$tmp/out.csv\") == '$HEADER' ]]"
reset
printf 'w1,id,L2,finance,,,1\n' > "$tmp/shards/sensitive-L2-finance-id.csv"
expect_fail '无表头（首行即数据行）被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
{ printf 'word,lang,level,category,variants,regex,enabled\n'; printf '# 说明注释\n';
  printf 'term,lang,level,category,variants,regex,enabled\n'; printf 'w1,id,L2,finance,,,1\n'; } \
  > "$tmp/shards/sensitive-L2-finance-id.csv"
expect_ok '模板内重复表头/注释行被跳过' --dir "$tmp/shards" --output "$tmp/out.csv"
check '模板仍正确保留 1 条数据' "[[ \$(wc -l < \"$tmp/out.csv\") == 2 ]]"

reset
mk sensitive-L2-finance-id.csv
mk sensitive-L2-risk-id.csv 'w1,id,L2,risk,,,1'
expect_ok '空模板与其他分片共存时跳过空片' --dir "$tmp/shards" --output "$tmp/out.csv"
n=$(wc -l < "$tmp/out.csv")
check '空片被跳过且仍 1 条数据' "[[ $n == 2 ]]"

# --- 5. 跨分片重复 term ---
reset
mk sensitive-L2-finance-id.csv 'dupword,id,L2,finance,,,1'
mk sensitive-L2-risk-id.csv    'dupword,id,L2,risk,,,1'
expect_fail '跨分片重复 term 被拒' --dir "$tmp/shards" --output "$tmp/out.csv"
check '重复报错不泄露词条' "! grep -q 'dupword' \"$tmp/stderr\""

# --- 6. --drop-category 按类过滤 ---
reset
mk sensitive-L3-profanity-en.csv 'w1,en,L3,profanity,,,1' 'w2,en,L3,profanity,,,1'
mk sensitive-L2-finance-id.csv  'w3,id,L2,finance,,,1'
expect_ok '带 --drop-category 生成成功' --dir "$tmp/shards" --output "$tmp/out.csv" \
  --drop-category 'Profanity, insult'
check '过滤后仅剩 1 条' "[[ \$(wc -l < \"$tmp/out.csv\") == 2 ]]"
check '统计里报已过滤 2 条' "grep -q '已过滤:   2 条' \"$tmp/stderr\""
check '输出不含被过滤类别' "! grep -q 'profanity' \"$tmp/out.csv\""
expect_fail '全部类别被过滤时拒绝生成空文件' --dir "$tmp/shards" --output "$tmp/out.csv" \
  --drop-category 'profanity,finance'

# --- 7. --report 只打印分类统计 ---
reset
mk sensitive-L2-finance-id.csv 'w1,id,L2,finance,,,1'
expect_ok '--report 生成成功' --dir "$tmp/shards" --output "$tmp/out.csv" --report
check 'report 含按类别行' "grep -q '按类别' \"$tmp/stderr\""

# --- 8. 200000 字节上限 ---
reset
long=$(printf '%*s' 210000 '' | tr ' ' 'x')
mk sensitive-L2-finance-id.csv "$long,en,L2,finance,,,1"
expect_fail '超 200000 字节被拒' --dir "$tmp/shards" --output "$tmp/out.csv"

# --- 9. 0 分片 / 杂项文件排除 ---
reset
expect_fail '0 分片被拒（不生成空文件）' --dir "$tmp/shards" --output "$tmp/out.csv"
reset
mk sensitive-L2-finance-id.csv 'w1,id,L2,finance,,,1'
cp "$tmp/shards/sensitive-L2-finance-id.csv" "$tmp/shards/sensitive-L2-finance-id.csv.bak"
printf 'junk\n' > "$tmp/shards/.sensitive-L2-hidden-id.csv"
printf 'junk\n' > "$tmp/shards/sensitive-L2-junk-id.csv.swp"
printf 'junk\n' > "$tmp/shards/sensitive.csv"
expect_ok '杂项文件存在时正常合并' --dir "$tmp/shards" --output "$tmp/out.csv"
check '杂项文件未被读入（仍 1 条）' "[[ \$(wc -l < \"$tmp/out.csv\") == 2 ]]"

# --- 10. --check 漂移检测（只比 sha256，不写盘） ---
reset
mk sensitive-L2-finance-id.csv 'w1,id,L2,finance,,,1'
expect_ok '首次 --check（无参考文件）通过' --dir "$tmp/shards" --output "$tmp/out.csv" --check
check '--check 未写盘' "[[ ! -f \"$tmp/out.csv\" ]]"
expect_ok '生成基准文件' --dir "$tmp/shards" --output "$tmp/out.csv"
expect_ok '内容未变时 --check 返回 0' --dir "$tmp/shards" --output "$tmp/out.csv" --check
mk sensitive-L2-finance-id.csv 'w2,id,L2,finance,,,1'
expect_fail '内容变化后 --check 返回 1' --dir "$tmp/shards" --output "$tmp/out.csv" --check
check '--check 有漂移时仍未写盘' "grep -q '^w1,id,L2,finance,,,1\$' \"$tmp/out.csv\""

# --- 11. --stdout / 参数互斥 ---
reset
mk sensitive-L2-finance-id.csv 'w1,id,L2,finance,,,1'
expect_ok '--stdout 输出干净（统计走 stderr）' --dir "$tmp/shards" --stdout
check 'stdout 恰为表头+1 行' "[[ \$(wc -l < \"$tmp/stdout\") == 2 ]]"
expect_fail '--check 与 --stdout 互斥' --dir "$tmp/shards" --check --stdout

printf '\n== 结果: %d passed, %d failed ==\n' "$pass" "$fail"
(( fail == 0 ))
