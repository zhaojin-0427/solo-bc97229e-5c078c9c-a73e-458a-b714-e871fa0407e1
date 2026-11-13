#!/usr/bin/env bash
# 不使用 Docker 时的本地一键启动（需要 Python 3.11，依赖位于 .pydeps）。
# 用法：./run_local.sh [mock-a|mock-b|harvester]
set -euo pipefail
cd "$(dirname "$0")"

export PYTHONPATH="$PWD/.pydeps:$PWD${PYTHONPATH:+:$PYTHONPATH}"
export HARVESTER_DB="${HARVESTER_DB:-$PWD/data/harvester.db}"
mkdir -p data
ROLE="${1:-all}"

start() {
  if [ "$ROLE" = "all" ] || [ "$ROLE" = "$1" ]; then
    shift
    "$@" &
    echo "started pid $!"
  fi
}

if [ "$ROLE" = "all" ]; then
  python3 -m mock_oai.server --code A --port 8081 \
    --seed 101 --count 40 --registrant 1001 --page-size 10 &
  python3 -m mock_oai.server --code B --port 8082 \
    --seed 202 --count 45 --registrant 2002 --page-size 12 \
    --overlap 15 --overlap-registrant 1001 &
  export HARVESTER_SOURCES='[
    {"id":1,"code":"A","name":"华东理工大学机构库（优先来源）","base_url":"http://127.0.0.1:8081/oai","priority":1},
    {"id":2,"code":"B","name":"西部师范大学知识库","base_url":"http://127.0.0.1:8082/oai","priority":2}]'
  export MOCK_PANEL_LINKS='[{"code":"A","url":"http://localhost:8081/panel"},{"code":"B","url":"http://localhost:8082/panel"}]'
  echo "Mock A: http://localhost:8081/panel"
  echo "Mock B: http://localhost:8082/panel"
  echo "Web:    http://localhost:8000 （Ctrl+C 停止全部）"
  exec python3 -m flask --app harvester.app:create_app run --host 0.0.0.0 --port 8000
fi

case "$ROLE" in
  mock-a) exec python3 -m mock_oai.server --code A --port 8081 \
    --seed 101 --count 40 --registrant 1001 --page-size 10 ;;
  mock-b) exec python3 -m mock_oai.server --code B --port 8082 \
    --seed 202 --count 45 --registrant 2002 --page-size 12 \
    --overlap 15 --overlap-registrant 1001 ;;
  harvester)
    export HARVESTER_SOURCES="${HARVESTER_SOURCES:-[
      {"id":1,"code":"A","name":"来源A","base_url":"http://127.0.0.1:8081/oai","priority":1},
      {"id":2,"code":"B","name":"来源B","base_url":"http://127.0.0.1:8082/oai","priority":2}]}"
    exec python3 -m flask --app harvester.app:create_app run --host 0.0.0.0 --port 8000 ;;
  *) echo "未知角色: $ROLE（可选 all|mock-a|mock-b|harvester）"; exit 1 ;;
esac
