"""采集引擎：每源一个后台线程，同源重复启动复用同一任务。

关键约束：
- 网络请求（等待 OAI 响应）在数据库事务之外进行，不持有任何写事务；
- 每一页拿到结果后开一个短事务落盘记录 + resumptionToken，重启即可续采；
- badResumptionToken 时清空本轮暂存、round 加一从首页重采，正式目录完全不动；
- 仅完整一轮（最后一页无 token）才调用 complete_round 原子替换本来源目录；
- 两源线程独立运行，可并行；同一来源并发启动只复用，不新建。
"""
from __future__ import annotations

import logging
import threading

from . import repository
from .config import Config
from .db import connect
from .oai import OaiClient, OaiError, OaiNetworkError

log = logging.getLogger("harvester.engine")


class HarvestManager:
    def __init__(self, config: Config):
        self.config = config
        # 控制连接只承担启动/续采的短事务；工作线程各自持有独立连接
        self._control_conn = connect(config.db_path)
        self._threads: dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def is_running(self, source_id: int) -> bool:
        with self._lock:
            t = self._threads.get(source_id)
            return bool(t and t.is_alive())

    def start(self, source_id: int) -> str:
        """启动/续采某来源。返回 'started' / 'resumed' / 'reused'。"""
        with self._lock:
            existing = self._threads.get(source_id)
            if existing and existing.is_alive():
                return "reused"
            action, round_id = repository.start_or_resume(
                self._control_conn, source_id
            )
            t = threading.Thread(
                target=self._run_safely,
                args=(source_id, round_id),
                name=f"harvest-src{source_id}",
                daemon=True,
            )
            self._threads[source_id] = t
            t.start()
            return action

    def start_all(self) -> dict[int, str]:
        return {src["id"]: self.start(src["id"]) for src in self.config.sources}

    def _run_safely(self, source_id: int, round_id: int) -> None:
        try:
            self._run(source_id, round_id)
        except Exception:  # 线程内兜底，绝不让异常静默杀死线程
            log.exception("来源 %s 采集线程异常退出", source_id)
            try:
                conn = connect(self.config.db_path)
                repository.mark_failed(conn, source_id, "internal", "采集线程内部错误")
                conn.close()
            except Exception:
                log.exception("标记内部失败也失败了")

    def _run(self, source_id: int, round_id: int) -> None:
        src = next(s for s in self.config.sources if s["id"] == source_id)
        conn = connect(self.config.db_path)
        client = OaiClient(
            src["base_url"],
            self.config.oai_connect_timeout,
            self.config.oai_read_timeout,
        )
        log.info("来源 %s 采集开始（round=%s）", src["code"], round_id)
        try:
            while True:
                state = repository.get_state(conn, source_id)
                token = state["resumption_token"]
                # —— 网络等待在事务之外，不占数据库写事务 ——
                try:
                    page = client.fetch_page(token)
                except OaiNetworkError as exc:
                    # 网络失败：进度与令牌都已落盘，保留 error 状态等待续采
                    repository.mark_failed(
                        conn, source_id, "network", str(exc)
                    )
                    log.warning("来源 %s 网络失败，可续采: %s", src["code"], exc)
                    return
                except OaiError as exc:
                    if exc.code == "badResumptionToken":
                        if state["restarts"] >= self.config.max_token_restarts:
                            repository.mark_failed(
                                conn, source_id, "badResumptionToken",
                                f"重采次数超过上限 {self.config.max_token_restarts}",
                            )
                            return
                        round_id = repository.restart_from_beginning(
                            conn, source_id, str(exc)
                        )
                        log.warning(
                            "来源 %s 令牌失效，清空暂存从头重采（round=%s）",
                            src["code"], round_id,
                        )
                        continue
                    repository.mark_failed(conn, source_id, exc.code, exc.message)
                    return

                # —— 短事务：逐页持久化记录与下一页令牌（网络等待已在事务外完成）——
                next_seq = repository.persist_page(
                    conn, source_id, round_id, state["next_seq"], page.records
                )
                # advance_page 内部 BEGIN IMMEDIATE，把上面的暂存写入一并原子提交
                repository.advance_page(
                    conn, source_id, round_id, next_seq,
                    len(page.records), page.resumption_token,
                    page.complete_list_size,
                )

                if page.resumption_token is None:
                    count = repository.complete_round(conn, source_id, round_id)
                    log.info(
                        "来源 %s 完整一轮结束，正式目录已替换（%s 条）",
                        src["code"], count,
                    )
                    return
        finally:
            conn.close()
