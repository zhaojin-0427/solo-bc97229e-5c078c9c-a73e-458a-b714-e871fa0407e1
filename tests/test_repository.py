"""仓储层事务与状态机的窄测试（不依赖网络）。"""
import os
import tempfile
import unittest

from conftest_helpers import BaseCase
from harvester import repository
from harvester.oai import OaiRecord


class TestStateMachine(BaseCase):
    def test_new_round_clears_old_staging(self):
        conn = self.open_conn()
        # 手动塞入一轮暂存并模拟成功
        action, round_id = repository.start_or_resume(conn, 1)
        self.assertEqual(action, "started")
        repository.persist_page(
            conn, 1, round_id, 0,
            [OaiRecord("oai:a:p1", "题名", "https://doi.org/10.1/X", False)],
        )
        repository.advance_page(conn, 1, round_id, 1, 1, None, 1)
        repository.complete_round(conn, 1, round_id)
        self.assertEqual(repository.get_state(conn, 1)["status"], "success")

        # 再启动应开新一轮并清空旧暂存，正式目录保留
        action2, round2 = repository.start_or_resume(conn, 1)
        self.assertEqual(action2, "started")
        self.assertEqual(round2, round_id + 1)
        left = conn.execute(
            "SELECT COUNT(*) n FROM staging_records WHERE source_id=1"
        ).fetchone()["n"]
        self.assertEqual(left, 0)
        catalog = conn.execute(
            "SELECT COUNT(*) n FROM catalog_records WHERE source_id=1"
        ).fetchone()["n"]
        self.assertEqual(catalog, 1)
        conn.close()

    def test_running_state_rejects_second_start(self):
        conn = self.open_conn()
        repository.start_or_resume(conn, 1)
        with self.assertRaises(repository.AlreadyRunning):
            repository.start_or_resume(conn, 1)
        conn.close()

    def test_bad_token_restart_wipes_round_staging(self):
        conn = self.open_conn()
        _, round_id = repository.start_or_resume(conn, 1)
        repository.persist_page(
            conn, 1, round_id, 0,
            [OaiRecord("oai:a:p1", "t", "doi:10.1/Y", False),
             OaiRecord("oai:a:p2", "t2", None, True)],
        )
        repository.advance_page(conn, 1, round_id, 2, 2, "tok-x", 2)
        new_round = repository.restart_from_beginning(conn, 1, "bad token")
        self.assertEqual(new_round, round_id + 1)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) n FROM staging_records WHERE source_id=1"
            ).fetchone()["n"],
            0,
        )
        st = repository.get_state(conn, 1)
        self.assertEqual(st["status"], "running")
        self.assertIsNone(st["resumption_token"])
        self.assertEqual(st["restarts"], 1)
        # 正式目录从未被触碰
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) n FROM catalog_records WHERE source_id=1"
            ).fetchone()["n"],
            0,
        )
        conn.close()

    def test_deleted_backfill_keeps_doi_on_swap(self):
        conn = self.open_conn()
        # 第一轮：正常在架
        _, r1 = repository.start_or_resume(conn, 1)
        repository.persist_page(
            conn, 1, r1, 0,
            [OaiRecord("oai:a:p1", "原题名", "10.1/Z", False)],
        )
        repository.advance_page(conn, 1, r1, 1, 1, None, 1)
        repository.complete_round(conn, 1, r1)

        # 第二轮：同标识变成 deleted（OAI 撤稿 header 无 metadata，DOI 为空）
        _, r2 = repository.start_or_resume(conn, 1)
        repository.persist_page(
            conn, 1, r2, 0, [OaiRecord("oai:a:p1", "", None, True)]
        )
        repository.advance_page(conn, 1, r2, 1, 1, None, 1)
        repository.complete_round(conn, 1, r2)

        row = conn.execute(
            "SELECT * FROM catalog_records WHERE source_id=1 AND oai_identifier='oai:a:p1'"
        ).fetchone()
        self.assertEqual(row["is_deleted"], 1)
        self.assertEqual(row["doi_key"], "10.1/z")  # 回填成功，出处不丢
        self.assertEqual(row["title"], "原题名")
        conn.close()


class TestMockTokenStability(BaseCase):
    def test_deletion_does_not_invalidate_issued_token(self):
        # 取首页令牌
        import urllib.request

        port = self.httpd_a.server_address[1]
        xml = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/oai?verb=ListRecords&metadataPrefix=oai_dc",
            timeout=5,
        ).read().decode()
        import re

        token = re.search(
            r"<resumptionToken[^>]*>([^<]+)</resumptionToken>", xml
        ).group(1)
        target = self.state_a.records[0]["oai_id"]
        import urllib.parse

        data = urllib.parse.urlencode({"oai_id": target}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{port}/admin/delete", data=data, method="POST"
            ),
            timeout=5,
        ).read()
        # 撤稿后旧令牌仍可翻页（epoch 不变）
        page = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/oai?verb=ListRecords&resumptionToken={token}",
            timeout=5,
        ).read().decode()
        self.assertNotIn("badResumptionToken", page)
        self.assertIn("<record>", page)


if __name__ == "__main__":
    unittest.main()
