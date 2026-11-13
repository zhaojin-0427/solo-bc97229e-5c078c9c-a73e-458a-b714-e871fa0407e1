#!/usr/bin/env bash
# 演示脚本：通过 Web/面板接口走一遍 采集 → 断流续采 → 令牌失效重采 → 撤稿接替。
# 依赖 curl；默认对接 docker-compose 暴露的端口。
set -euo pipefail
WEB="${WEB:-http://localhost:8000}"
A="${A:-http://localhost:8081}"
B="${B:-http://localhost:8082}"

say() { printf "\n\033[1;36m== %s ==\033[0m\n" "$*"; }
wait_while_running() {
  local sid="$1"
  for _ in $(seq 1 60); do
    local st
    st=$(curl -s "$WEB/api/sources" | python3 -c "import sys,json;print(json.load(sys.stdin)[$sid-1]['status'])")
    [ "$st" != "running" ] && { echo "来源 $sid 最终状态: $st"; return 0; }
    sleep 1
  done
  echo "等待超时"; return 1
}

say "1. 并行启动两源全量采集"
curl -s -X POST "$WEB/harvest/start" | python3 -m json.tool
wait_while_running 1; wait_while_running 2
curl -s "$WEB/api/stats" | python3 -m json.tool

say "2. 让来源 A 下一次翻页断流，再启动采集"
curl -s -X POST "$A/admin/stream-break" -o /dev/null
curl -s -X POST "$WEB/harvest/start" -d "source_id=1" -o /dev/null
sleep 3
curl -s "$WEB/api/sources" | python3 -c "import sys,json;s=json.load(sys.stdin)[0];print('故障状态:',s['status'],s['error_code'],'| 已保存令牌:',bool(s['resumption_token']))"

say "3. 续采（沿用同一轮次与令牌）"
curl -s -X POST "$WEB/harvest/start" -d "source_id=1" -o /dev/null
wait_while_running 1

say "4. 让来源 A 令牌过期，观察清空暂存从头重采"
curl -s -X POST "$A/admin/expire-token" -o /dev/null
curl -s -X POST "$WEB/harvest/start" -d "source_id=1" -o /dev/null
wait_while_running 1
curl -s "$WEB/api/sources" | python3 -c "import sys,json;s=json.load(sys.stdin)[0];print('重采次数:',s['restarts'],'| 轮次:',s['round_id'],'| 状态:',s['status'])"

say "5. 撤稿一条双源共有 DOI，观察题名接替"
KEY=$(curl -s "$WEB/api/catalog?limit=200" | python3 -c "
import sys,json
for it in json.load(sys.stdin)['items']:
    if it['source_count']==2: print(it['doi_key']); break")
A_OAI=$(curl -s "$WEB/api/lookup?doi=$KEY" | python3 -c "
import sys,json
for p in json.load(sys.stdin)['provenance']:
    if p['source_code']=='A': print(p['oai_identifier']); break")
echo "目标 DOI: $KEY  撤稿标识: $A_OAI"
curl -s -X POST "$A/admin/delete" --data-urlencode "oai_id=$A_OAI" -o /dev/null
curl -s -X POST "$WEB/harvest/start" -d "source_id=1" -o /dev/null
wait_while_running 1
curl -s "$WEB/api/lookup?doi=$KEY" | python3 -m json.tool

say "完成。浏览器打开 $WEB 查看目录，$A/panel 与 $B/panel 是故障控制台。"
