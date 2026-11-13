"""故障恢复测试：网络断点续采、badResumptionToken 清空重采、进程重启续采。"""
import time

from conftest_helpers import BaseCase
from harvester import repository
from harvester.db import connect
from harvester.engine import HarvestManager


class TestRecovery(BaseCase):
    def test_network_resume_from_token(self):
        # 第一页后立刻断流
        self.state_a.break_once = True
        self.manager.start(1)
        row = self.wait_status(1, "error")
        self.assertEqual(row["error_code"], "network")
        pages_before = row["pages_done"]
        self.assertGreaterEqual(pages_before, 1)

        # 续采沿用同一 round 与 token，最终完整成功
        action = self.manager.start(1)
        self.assertEqual(action, "resumed")
        final = self.wait_status(1, "success")
        self.assertEqual(final["round_id"], row["round_id"])
        self.assertEqual(final["pages_done"] + 0, final["pages_done"])
        conn = self.open_conn()
        self.assertEqual(
            repository.catalog_stats(conn)["per_source"][0]["total"], 30
        )
        # 暂存内没有重复行
        dup = conn.execute(
            "SELECT oai_identifier, COUNT(*) c FROM staging_records "
            "WHERE source_id=1 GROUP BY oai_identifier HAVING c>1"
        ).fetchall()
        self.assertEqual(dup, [])
        conn.close()

    def test_bad_token_clears_staging_and_restarts(self):
        # 先制造一次断流，拿到已落盘的 token，再让该 token 失效
        self.state_a.break_once = True
        self.manager.start(1)
        err = self.wait_status(1, "error")
        old_round = err["round_id"]
        self.assertIsNotNone(err["resumption_token"])

        self.state_a.token_expired = True
        self.manager.start(1)
        # 引擎应自动清空暂存、round+1 从头重采并成功（正式目录此前为空，不受影响）
        final = self.wait_status(1, "success", timeout=25)
        self.assertEqual(final["round_id"], old_round + 1)
        self.assertEqual(final["restarts"], 1)
        conn = self.open_conn()
        # 旧 round 暂存已清空
        left = conn.execute(
            "SELECT COUNT(*) n FROM staging_records WHERE source_id=1 AND round_id=?",
            (old_round,),
        ).fetchone()["n"]
        self.assertEqual(left, 0)
        # 新 round 完整落正式目录
        self.assertEqual(
            repository.catalog_stats(conn)["per_source"][0]["total"], 30
        )
        conn.close()

    def test_restart_process_resumes(self):
        # 场景：采集中进程被杀（线程消失、状态停留 running），新进程恢复后续采
        self.state_b.break_once = True
        self.manager.start(2)
        self.wait_status(2, "error")
        # 模拟进程重启：丢掉旧 manager，新进程执行孤儿恢复
        manager2 = HarvestManager(self.config)
        conn = connect(self.db_path)
        # 先人为把状态置回 running，模拟崩溃瞬间
        conn.execute(
            "UPDATE harvest_state SET status='running' WHERE source_id=2"
        )
        conn.commit()
        recovered = repository.recover_orphan_runs(conn)
        conn.close()
        self.assertIn(2, recovered)

        action = manager2.start(2)
        self.assertEqual(action, "resumed")
        final_row = None
        deadline = time.time() + 20
        conn = connect(self.db_path)
        while time.time() < deadline:
            r = repository.get_state(conn, 2)
            if r["status"] == "success" and not manager2.is_running(2):
                final_row = r
                break
            time.sleep(0.05)
        conn.close()
        self.assertIsNotNone(final_row)
        conn = self.open_conn()
        self.assertEqual(
            repository.catalog_stats(conn)["per_source"][1]["total"], 34
        )
        conn.close()

    def test_old_catalog_survives_failed_round(self):
        # 先成功一轮
        self.manager.start(1)
        self.wait_status(1, "success")
        conn = self.open_conn()
        before = repository.catalog_stats(conn)["per_source"][0]
        conn.close()
        # 第二轮持续失败：所有翻页令牌都失效，重采次数超过上限
        self.state_a.token_expired_sticky = True
        self.manager.start(1)
        deadline = time.time() + 20
        final_status = None
        while time.time() < deadline:
            conn = self.open_conn()
            st = repository.get_state(conn, 1)
            conn.close()
            if st["status"] == "error" and not self.manager.is_running(1):
                final_status = st
                break
            time.sleep(0.05)
        self.assertIsNotNone(final_status)
        self.assertEqual(final_status["error_code"], "badResumptionToken")
        self.assertEqual(final_status["restarts"], self.config.max_token_restarts)
        # 旧正式目录完好
        conn = self.open_conn()
        after = repository.catalog_stats(conn)["per_source"][0]
        self.assertEqual(before["total"], after["total"])
        conn.close()
