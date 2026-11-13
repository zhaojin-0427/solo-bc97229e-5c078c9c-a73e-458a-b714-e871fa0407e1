"""Flask 应用工厂：采集控制台、进度查看、DOI 目录查询。"""
from __future__ import annotations

import math

from flask import (
    Flask,
    abort,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from . import repository
from .config import load_config
from .db import connect, init_db
from .doi_utils import normalize_doi
from .engine import HarvestManager

PAGE_SIZE = 25


def create_app() -> Flask:
    config = load_config()
    app = Flask(__name__)
    app.config["HARVESTER_CONFIG"] = config
    app.config["HARVESTER_DB_PATH"] = config.db_path

    with connect(config.db_path) as conn:
        init_db(conn, config.sources)
        repository.recover_orphan_runs(conn)

    manager = HarvestManager(config)
    app.extensions["harvest_manager"] = manager

    @app.teardown_appcontext
    def _close_db(exc):  # noqa: ANN001
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def db():
        if "db" not in g:
            g.db = connect(app.config["HARVESTER_DB_PATH"])
        return g.db

    # ---------------- 页面 ----------------

    @app.get("/")
    def dashboard():
        conn = db()
        states = repository.all_states(conn)
        stats = repository.catalog_stats(conn)
        running = {
            s["source_id"]: manager.is_running(s["source_id"]) for s in states
        }
        return render_template(
            "dashboard.html",
            states=states,
            stats=stats,
            running=running,
            panel_links=config.mock_panel_links,
        )

    @app.get("/catalog")
    def catalog():
        conn = db()
        q = request.args.get("q", "").strip()
        page = max(1, request.args.get("page", default=1, type=int))
        rows, total = repository.search_catalog(
            conn, q or None, PAGE_SIZE, (page - 1) * PAGE_SIZE
        )
        pages = max(1, math.ceil(total / PAGE_SIZE))
        return render_template(
            "catalog.html", rows=rows, total=total, q=q, page=page, pages=pages
        )

    @app.get("/doi/<path:doi_key>")
    def doi_detail(doi_key: str):
        conn = db()
        key = normalize_doi(doi_key)
        if not key:
            abort(404)
        merged = repository.get_merged_doi(conn, key)
        prov = repository.provenance(conn, key)
        if not prov:
            abort(404)
        return render_template(
            "doi_detail.html", doi_key=key, merged=merged, provenance=prov
        )

    @app.get("/no-doi")
    def no_doi():
        conn = db()
        rows = repository.list_without_doi(conn)
        return render_template("no_doi.html", rows=rows)

    # ---------------- 操作 ----------------

    @app.post("/harvest/start")
    def harvest_start():
        source_id = request.form.get("source_id", type=int)
        if source_id is None:
            result = manager.start_all()
            return jsonify(
                {"status": "ok", "actions": {str(k): v for k, v in result.items()}}
            )
        if not any(s["id"] == source_id for s in config.sources):
            abort(404)
        action = manager.start(source_id)
        if request.accept_mimetypes.accept_json and not request.form.get("html"):
            return jsonify({"status": "ok", "action": action, "source_id": source_id})
        return redirect(url_for("dashboard"))

    # ---------------- JSON API ----------------

    @app.get("/api/sources")
    def api_sources():
        conn = db()
        states = repository.all_states(conn)
        running = {
            s["source_id"]: manager.is_running(s["source_id"]) for s in states
        }
        return jsonify(
            [
                {
                    "source_id": s["source_id"],
                    "code": s["code"],
                    "name": s["name"],
                    "base_url": s["base_url"],
                    "priority": s["priority"],
                    "status": s["status"],
                    "round_id": s["round_id"],
                    "resumption_token": s["resumption_token"],
                    "next_seq": s["next_seq"],
                    "pages_done": s["pages_done"],
                    "records_fetched": s["records_fetched"],
                    "total_expected": s["total_expected"],
                    "error_code": s["error_code"],
                    "error_message": s["error_message"],
                    "restarts": s["restarts"],
                    "started_at": s["started_at"],
                    "updated_at": s["updated_at"],
                    "finished_at": s["finished_at"],
                    "last_success_at": s["last_success_at"],
                    "is_running_thread": running[s["source_id"]],
                }
                for s in states
            ]
        )

    @app.get("/api/stats")
    def api_stats():
        return jsonify(repository.catalog_stats(db()))

    @app.get("/api/lookup")
    def api_lookup():
        raw = request.args.get("doi", "").strip()
        key = normalize_doi(raw)
        if not key:
            return jsonify({"error": "缺少或无法识别的 DOI 参数"}), 400
        conn = db()
        merged = repository.get_merged_doi(conn, key)
        if not merged:
            return jsonify({"doi_key": key, "found": False}), 404
        prov = repository.provenance(conn, key)
        return jsonify(
            {
                "doi_key": key,
                "found": True,
                "preferred_title": merged["preferred_title"],
                "source_count": merged["source_count"],
                "provenance": [
                    {
                        "source_code": p["code"],
                        "source_name": p["name"],
                        "priority": p["priority"],
                        "oai_identifier": p["oai_identifier"],
                        "title": p["title"],
                        "doi_raw": p["doi_raw"],
                        "is_deleted": bool(p["is_deleted"]),
                        "harvested_round": p["harvested_round"],
                        "harvested_at": p["harvested_at"],
                    }
                    for p in prov
                ],
            }
        )

    @app.get("/api/catalog")
    def api_catalog():
        conn = db()
        q = request.args.get("q", "").strip() or None
        limit = min(200, request.args.get("limit", default=PAGE_SIZE, type=int))
        offset = max(0, request.args.get("offset", default=0, type=int))
        rows, total = repository.search_catalog(conn, q, limit, offset)
        return jsonify(
            {
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [dict(r) for r in rows],
            }
        )

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    return app
