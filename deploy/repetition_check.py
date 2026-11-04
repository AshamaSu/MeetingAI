#!/usr/bin/env python3
"""转写质量重复率检查工具（M1 迭代用）

用法:
  python3 repetition_check.py file1.txt [file2.txt ...]
  python3 repetition_check.py result.json          # whisperx-asr-service 的 JSON 输出
  python3 repetition_check.py *.txt *.json         # 混合对比

判定参考（课堂音频经验值）:
  重复率 < 35%  良好
  35% ~ 50%    可用，建议加 hotwords
  > 50%        红线，换模型（large-v3）或做音频预处理
指标口径: 重复率 = 1 - 去重词数/总词数
"""
import json, re, sys
from collections import Counter

THRESH_GOOD, THRESH_OK = 0.35, 0.50

def load_text(path):
    if path.endswith('.json'):
        d = json.load(open(path, encoding='utf-8'))
        segs = d.get('segments') if isinstance(d, dict) else d
        if isinstance(segs, list):
            lines = []
            for s in segs:
                spk = s.get('speaker', '')
                t = (s.get('text') or '').strip()
                if t:
                    lines.append((f"[{spk}] " if spk else '') + t)
            return "\n".join(lines), len(segs)
        return "", 0
    text = open(path, encoding='utf-8').read()
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return text, len(lines)

def analyze(path):
    text, n_seg = load_text(path)
    clean = re.sub(r'\[SPEAKER_\d+\]|\[\*?[^\]]{1,20}\*\]|\*+\w+\*+', ' ', text)
    words = re.findall(r'\w+', clean.lower())
    total, uniq = len(words), len(set(words))
    dup_rate = 1 - uniq / total if total else 0
    lines = [re.sub(r'^\[SPEAKER_\d+\]:?\s*', '', l).strip()
             for l in text.splitlines() if l.strip()]
    max_run, cur = 1, 1
    for i in range(1, len(lines)):
        cur = cur + 1 if lines[i] == lines[i-1] else 1
        max_run = max(max_run, cur)
    top, cnt = Counter(lines).most_common(1)[0]
    # top duplicate segment (appearing >=2 times)
    dup_seg, dup_cnt = '', 0
    for seg, c in Counter(lines).most_common():
        if c >= 2:
            dup_seg, dup_cnt = seg, c
            break
    flag = '✅ 良好' if dup_rate < THRESH_GOOD else ('⚠️ 可用' if dup_rate <= THRESH_OK else '❌ 红线')
    print(f"--- {path}")
    print(f"    段数={n_seg}  词数={total}  重复率={dup_rate*100:.1f}% {flag}")
    print(f"    最长连续相同段={max_run}  最高频重复段={cnt}次")
    if dup_cnt >= 2:
        print(f"    样例: \"{dup_seg[:60]}...\" ×{dup_cnt}")
    # speaker distribution
    speakers = Counter(re.findall(r'\[SPEAKER_\d+\]', text))
    if speakers:
        dist = ', '.join(f"{k}={v}" for k, v in speakers.most_common())
        warn = '  ⚠️ 单一说话人，分离可能失败' if len(speakers) == 1 and n_seg > 10 else ''
        print(f"    说话人分布: {dist}{warn}")
    return dup_rate

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    rates = [analyze(p) for p in sys.argv[1:]]
    if len(rates) > 1:
        print(f"\n平均重复率: {sum(rates)/len(rates)*100:.1f}%")
