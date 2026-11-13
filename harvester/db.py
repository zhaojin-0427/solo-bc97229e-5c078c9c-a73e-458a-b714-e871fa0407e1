"""SQLite 连接管理与建表。

库内分为两类数据：
- 正式目录 ``catalog_records``：查询入口永远只看到完整轮次交换后的状态；
- 本轮暂存 ``staging_records`` / ``harvest_state``：重启续采、badToken 重采都在这里，
  完整一轮成功后才在单个事务内替换对应来源的正式目录。
"""
from __future__ import annotations

import os
import sqlite3
import threading

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=30000;

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    priority    INTEGER NOT NULL
);

-- 正式目录：每个来源最近一次完整轮次的快照
CREATE TABLE IF NOT EXISTS catalog_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    oai_identifier  TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    doi_raw         TEXT,
    doi_key         TEXT,          -- 归一化后的小写 DOI 合并键
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    harvested_round INTEGER NOT NULL,
    harvested_at    TEXT NOT NULL,
    UNIQUE(source_id, oai_identifier)
);
CREATE INDEX IF NOT EXISTS idx_catalog_doi ON catalog_records(doi_key);
CREATE INDEX IF NOT EXISTS idx_catalog_source ON catalog_records(source_id);

-- 本轮暂存：逐页写入，重启后续用
CREATE TABLE IF NOT EXISTS staging_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER NOT NULL REFERENCES sources(id),
    round_id        INTEGER NOT NULL,
    oai_identifier  TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    doi_raw         TEXT,
    doi_key         TEXT,
    is_deleted      INTEGER NOT NULL DEFAULT 0,
    seq             INTEGER NOT NULL,
    UNIQUE(source_id, round_id, oai_identifier)
);
CREATE INDEX IF NOT EXISTS idx_staging_round ON staging_records(source_id, round_id);

-- 采集状态机（每源一行）
CREATE TABLE IF NOT EXISTS harvest_state (
    source_id       INTEGER PRIMARY KEY REFERENCES sources(id),
    status          TEXT NOT NULL,             -- idle|running|error|success
    round_id        INTEGER NOT NULL DEFAULT 0,
    resumption_token TEXT,                     -- 下一页令牌；NULL 表示首页
    next_seq        INTEGER NOT NULL DEFAULT 0,
    pages_done      INTEGER NOT NULL DEFAULT 0,
    records_fetched INTEGER NOT NULL DEFAULT 0,
    total_expected  INTEGER,
    error_code      TEXT,
    error_message   TEXT,
    restarts        INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT,
    updated_at      TEXT,
    finished_at     TEXT,
    last_success_at TEXT
);

-- 合并目录视图：同一 doi_key 下按来源 priority 取题名，展示全部出处
CREATE VIEW IF NOT EXISTS merged_catalog AS
WITH best AS (
    SELECT doi_key,
           title AS preferred_title,
           ROW_NUMBER() OVER (
               PARTITION BY doi_key
               ORDER BY s.priority ASC, c.source_id ASC
           ) AS rn
    FROM catalog_records c
    JOIN sources s ON s.id = c.source_id
    WHERE doi_key IS NOT NULL AND is_deleted = 0
)
SELECT c.doi_key,
       (SELECT preferred_title FROM best b
         WHERE b.doi_key = c.doi_key AND b.rn = 1) AS preferred_title,
       COUNT(*) AS source_count,
       GROUP_CONCAT(c.source_id) AS source_ids,
       GROUP_CONCAT(s.code) AS source_codes
FROM catalog_records c
JOIN sources s ON s.id = c.source_id
WHERE c.doi_key IS NOT NULL AND c.is_deleted = 0
GROUP BY c.doi_key;
"""

_lock = threading.Lock()


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    # isolation_level=None：关闭 Python 驱动的隐式事务，所有写事务显式
    # BEGIN/COMMIT，避免与「网络等待在事务外」的设计互相干扰。
    conn = sqlite3.connect(
        db_path, timeout=30, check_same_thread=False, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: sqlite3.Connection, sources: list[dict]) -> None:
    """建表并同步来源配置（来源固定为两个，按 id 幂等 upsert）。"""
    with _lock:
        conn.executescript(SCHEMA)
        for src in sources:
            conn.execute(
                """INSERT INTO sources(id, code, name, base_url, priority)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     code=excluded.code, name=excluded.name,
                     base_url=excluded.base_url, priority=excluded.priority""",
                (src["id"], src["code"], src["name"], src["base_url"], src["priority"]),
            )
        # 每个来源确保有一行状态
        for src in sources:
            conn.execute(
                "INSERT OR IGNORE INTO harvest_state(source_id, status, round_id) "
                "VALUES (?, 'idle', 0)",
                (src["id"],),
            )
        conn.commit()
