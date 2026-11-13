"""运行配置。所有配置均可通过环境变量覆盖，默认值面向 docker-compose 双源。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


DEFAULT_SOURCES = [
    {
        "id": 1,
        "code": "A",
        "name": "华东理工大学机构库（优先来源）",
        "base_url": "http://mock-a:8081/oai",
        "priority": 1,
    },
    {
        "id": 2,
        "code": "B",
        "name": "西部师范大学知识库",
        "base_url": "http://mock-b:8082/oai",
        "priority": 2,
    },
]


@dataclass(frozen=True)
class Config:
    db_path: str
    sources: list[dict]
    oai_connect_timeout: float
    oai_read_timeout: float
    max_token_restarts: int
    mock_panel_links: list[dict]


def _load_sources() -> list[dict]:
    raw = os.environ.get("HARVESTER_SOURCES")
    if not raw:
        return DEFAULT_SOURCES
    data = json.loads(raw)
    required = {"id", "code", "name", "base_url", "priority"}
    for item in data:
        missing = required - item.keys()
        if missing:
            raise ValueError(f"来源配置缺少字段: {sorted(missing)}")
    return data


def _load_panel_links() -> list[dict]:
    raw = os.environ.get("MOCK_PANEL_LINKS")
    if not raw:
        return [
            {"code": "A", "url": "http://localhost:8081/panel"},
            {"code": "B", "url": "http://localhost:8082/panel"},
        ]
    return json.loads(raw)


def load_config() -> Config:
    return Config(
        db_path=os.environ.get("HARVESTER_DB", "/data/harvester.db"),
        sources=_load_sources(),
        oai_connect_timeout=float(os.environ.get("OAI_CONNECT_TIMEOUT", "10")),
        oai_read_timeout=float(os.environ.get("OAI_READ_TIMEOUT", "45")),
        max_token_restarts=int(os.environ.get("MAX_TOKEN_RESTARTS", "2")),
        mock_panel_links=_load_panel_links(),
    )
