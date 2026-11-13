"""端到端采集测试：全量采集、并行、任务复用、去重合并、撤稿接替、原子交换。"""
import time

from conftest_helpers import BaseCase
from harvester import repository
from harvester.doi_utils import normalize_doi


class TestFullHarvest(BaseCase):
    def test_full_harvest_and_merge(self):
        self.manager.start(1)
        self.manager.start(2)
        self.wait_status(1, "success")
        self.wait_status(2, "success")

        conn = self.open_conn()
        stats = repository.catalog_stats(conn)
        # A: 30 条（1 初始撤稿，2 条无 DOI）；B: 34 条（1 初始撤稿，3 条无 DOI）
        a = next(s for s in stats["per_source"] if s["code"] == "A")
        b = next(s for s in stats["per_source"] if s["code"] == "B")
        self.assertEqual(a["total"], 30)
        self.assertEqual(a["deleted"], 1)
        self.assertEqual(b["total"], 34)
        self.assertEqual(b["deleted"], 1)
        # 前 12 个带 DOI 的 B 记录与 A 共用 DOI
        self.assertGreaterEqual(stats["shared_doi_count"], 12)
        self.assertEqual(stats["no_doi_count"], 2 + 3)

        # 合并目录：共用 DOI 只出现一次
        rows, total = repository.search_catalog(conn, None, 500, 0)
        keys = [r["doi_key"] for r in rows]
        self.assertEqual(len(keys), len(set(keys)))

        # 优先来源 A 提供题名
        shared = next(r for r in rows if r["source_count"] == 2)
        prov = repository.provenance(conn, shared["doi_key"])
        self.assertEqual([p["code"] for p in prov], ["A", "B"])
        a_title = next(p["title"] for p in prov if p["code"] == "A")
        self.assertEqual(shared["preferred_title"], a_title)
        conn.close()

    def test_parallel_and_reuse(self):
        t0 = time.time()
        first = self.manager.start(1)
        second = self.manager.start(1)  # 同源重复启动
        self.assertIn(first, ("started", "resumed"))
        self.assertEqual(second, "reused")
        # 并行启动另一源
        self.manager.start(2)
        self.wait_status(1, "success")
        self.wait_status(2, "success")
        self.assertLess(time.time() - t0, 25)

        # 只有两个工作线程，且都已结束
        self.assertFalse(self.manager.is_running(1))
        self.assertFalse(self.manager.is_running(2))

    def test_lookup_case_insensitive_with_prefix(self):
        self.manager.start_all()
        self.wait_status(1, "success")
        self.wait_status(2, "success")
        conn = self.open_conn()
        rows, _ = repository.search_catalog(conn, None, 1, 0)
        key = rows[0]["doi_key"]
        # 带前缀 + 大写也能查到
        hit = repository.get_merged_doi(conn, normalize_doi(f"HTTPS://DOI.ORG/{key.upper()}"))
        self.assertIsNotNone(hit)
        self.assertEqual(hit["doi_key"], key)
        conn.close()


class TestDeletionAndSwap(BaseCase):
    def test_deleted_only_removes_own_source(self):
        # 先完整采集两源
        self.manager.start_all()
        self.wait_status(1, "success")
        self.wait_status(2, "success")
        conn = self.open_conn()
        # 找一条两源共有的 DOI
        rows, _ = repository.search_catalog(conn, None, 500, 0)
        shared = next(r for r in rows if r["source_count"] == 2)
        key = shared["doi_key"]
        prov = repository.provenance(conn, key)
        a_oai = next(p["oai_identifier"] for p in prov if p["code"] == "A")
        b_title = next(p["title"] for p in prov if p["code"] == "B")
        conn.close()

        # 在 A 源撤稿，重新采集 A
        port_a = self.httpd_a.server_address[1]
        self._post_admin(port_a, "/admin/delete", {"oai_id": a_oai})
        self.manager.start(1)
        self.wait_status(1, "success")

        conn = self.open_conn()
        # DOI 仍可检索，出处含 A 的撤稿记录，题名改由 B 接替
        merged = repository.get_merged_doi(conn, key)
        self.assertIsNotNone(merged)
        self.assertEqual(merged["preferred_title"], b_title)
        prov2 = repository.provenance(conn, key)
        a_rec = next(p for p in prov2 if p["code"] == "A")
        self.assertEqual(bool(a_rec["is_deleted"]), True)
        b_rec = next(p for p in prov2 if p["code"] == "B")
        self.assertEqual(bool(b_rec["is_deleted"]), False)

        # A 恢复后再采，重新成为优先题名来源
        self._post_admin(port_a, "/admin/restore", {"oai_id": a_oai})
        self.manager.start(1)
        self.wait_status(1, "success")
        prov3 = repository.provenance(conn, key)
        self.assertFalse(any(p["code"] == "A" and p["is_deleted"] for p in prov3))
        merged2 = repository.get_merged_doi(conn, key)
        a_title = next(p["title"] for p in prov3 if p["code"] == "A")
        self.assertEqual(merged2["preferred_title"], a_title)
        conn.close()

    def test_swap_isolation_and_visibility(self):
        # A 先完整成功，B 只采到一半失败，A 目录必须始终完整可查
        self.manager.start(1)
        self.wait_status(1, "success")
        conn = self.open_conn()
        a_count_before = repository.catalog_stats(conn)["per_source"][0]["total"]
        conn.close()

        # 让 B 在第一页后断流
        self.state_b.break_once = True
        self.manager.start(2)
        row = self.wait_status(2, "error")
        self.assertEqual(row["error_code"], "network")

        conn = self.open_conn()
        # 正式目录里没有 B 的半截数据，A 的旧目录原样可查
        b_catalog = conn.execute(
            "SELECT COUNT(*) n FROM catalog_records WHERE source_id=2"
        ).fetchone()["n"]
        self.assertEqual(b_catalog, 0)
        self.assertEqual(
            repository.catalog_stats(conn)["per_source"][0]["total"], a_count_before
        )
        # 暂存与令牌已落盘，可续采
        staging = conn.execute(
            "SELECT COUNT(*) n FROM staging_records WHERE source_id=2"
        ).fetchone()["n"]
        self.assertGreater(staging, 0)
        self.assertIsNotNone(repository.get_state(conn, 2)["resumption_token"])
        conn.close()

        # 续采成功后 B 目录一次性出现
        self.manager.start(2)
        self.wait_status(2, "success")
        conn = self.open_conn()
        self.assertEqual(
            repository.catalog_stats(conn)["per_source"][1]["total"], 34
        )
        # A 的目录在 B 交换后仍然完整（合并保留另一源）
        self.assertEqual(
            repository.catalog_stats(conn)["per_source"][0]["total"], a_count_before
        )
        conn.close()

    @staticmethod
    def _post_admin(port: int, path: str, form: dict) -> None:
        import urllib.request
        import urllib.parse

        data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=data, method="POST"
        )
        urllib.request.urlopen(req, timeout=5).read()
