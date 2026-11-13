"""共享测试夹具：临时库 + 真实 mock OAI 服务器。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harvester.config import Config  # noqa: E402
from harvester.db import connect, init_db  # noqa: E402
from harvester import repository  # noqa: E402
from harvester.engine import HarvestManager  # noqa: E402
from mock_oai.server import MockState, make_handler  # noqa: E402


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_mock(code: str, port: int = 0, **kwargs) -> tuple[ThreadingHTTPServer, MockState]:
    state = MockState(
        code,
        kwargs.pop("seed", 1),
        kwargs.pop("count", 30),
        kwargs.pop("registrant", "1001"),
        kwargs.pop("page_size", 8),
        kwargs.pop("overlap", 0),
        kwargs.pop("overlap_registrant", None),
    )
    httpd = _ReusableServer(("127.0.0.1", port), make_handler(state))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, state


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        self.httpd_a, self.state_a = start_mock(
            "A", seed=11, count=30, registrant="1001", page_size=8
        )
        self.httpd_b, self.state_b = start_mock(
            "B", seed=22, count=34, registrant="2002", page_size=9,
            overlap=12, overlap_registrant="1001",
        )
        port_a = self.httpd_a.server_address[1]
        port_b = self.httpd_b.server_address[1]
        self.sources = [
            {"id": 1, "code": "A", "name": "来源A",
             "base_url": f"http://127.0.0.1:{port_a}/oai", "priority": 1},
            {"id": 2, "code": "B", "name": "来源B",
             "base_url": f"http://127.0.0.1:{port_b}/oai", "priority": 2},
        ]
        self.config = Config(
            db_path=self.db_path, sources=self.sources,
            oai_connect_timeout=5, oai_read_timeout=15,
            max_token_restarts=2, mock_panel_links=[],
        )
        conn = connect(self.db_path)
        init_db(conn, self.sources)
        conn.close()
        self.manager = HarvestManager(self.config)

    def tearDown(self):
        self.httpd_a.shutdown()
        self.httpd_b.shutdown()
        self.tmp.cleanup()

    def wait_status(self, source_id: int, expected: str, timeout: float = 20) -> dict:
        deadline = time.time() + timeout
        conn = connect(self.db_path)
        try:
            while time.time() < deadline:
                row = dict(repository.get_state(conn, source_id))
                if row["status"] == expected and not self.manager.is_running(source_id):
                    return row
                if row["status"] in ("error", "success") and not self.manager.is_running(source_id):
                    if row["status"] == expected:
                        return row
                time.sleep(0.05)
            raise AssertionError(
                f"来源 {source_id} 未在 {timeout}s 内到达 {expected}，"
                f"当前 {dict(repository.get_state(conn, source_id))}"
            )
        finally:
            conn.close()

    def open_conn(self):
        return connect(self.db_path)
