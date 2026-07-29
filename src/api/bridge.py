"""PyWebView JS API bridge - main entry point for frontend calls."""

import json
import logging
import threading
from datetime import datetime

from src.db.database import Database
from src.db.repository import Repository
from src.core.chrome import setup_chrome, stop_chrome, is_cdp_ready
from src.core.login import check_login_state, wait_for_login
from src.core.constants import GEEK_CDP_PORT, BOSS_CDP_PORT
from src.ai.embedder import Embedder
from src.api.geek_api import GeekAPI

log = logging.getLogger(__name__)

LOGIN_CACHE_TTL_MINUTES = 30


MATCH_EVENT_TYPES = {
    "phase_start", "phase_progress", "phase_done",
    "model_download_progress",
    "job_scored", "job_failed",
    "summary_chunk", "summary_done",
    "match_completed", "error", "cancelled",
    "match",  # backward compat
    "embedder_init",  # standalone embedder init events
}


class AppAPI:
    def __init__(self):
        self.db = Database()
        self.repo = Repository(self.db)
        self._identity = "geek"
        self._window = None
        self._geek_api = None
        self._embedder = Embedder()
        self._embedder_status = "idle"  # idle, downloading, ready, failed
        self._embedder_error = ""

    # ---- Identity ----

    def get_identity(self):
        return self._identity

    def switch_identity(self, mode):
        if mode not in ("geek", "boss"):
            return {"ok": False, "error": f"Invalid mode: {mode}"}
        self._identity = mode
        return {"ok": True, "data": {"identity": mode}}

    def get_app_state(self):
        return {
            "identity": self._identity,
            "version": "0.1.0",
            "db_path": self.db.db_path,
        }

    # ---- Embedder ----

    def init_embedder(self):
        """Start embedding model download in background. Returns immediately."""
        log.info(f"init_embedder called, current status={self._embedder_status}")
        if self._embedder_status in ("downloading", "ready"):
            return {"ok": True, "data": {"status": self._embedder_status}}

        self._embedder_status = "downloading"
        self._embedder_error = ""

        def _download():
            try:
                def progress_callback(progress, status, speed=0.0):
                    self._notify_frontend(json.dumps({
                        "type": "embedder_init",
                        "status": "downloading",
                        "progress": progress,
                        "speed": speed,
                    }))

                self._embedder.ensure_model(progress_callback)
                self._embedder_status = "ready"
                log.info("Embedder model ready")
                self._notify_frontend(json.dumps({
                    "type": "embedder_init",
                    "status": "ready",
                    "progress": 1.0,
                }))
            except Exception as e:
                self._embedder_status = "failed"
                self._embedder_error = str(e)
                log.exception("Embedder init failed")
                self._notify_frontend(json.dumps({
                    "type": "embedder_init",
                    "status": "failed",
                    "error": str(e),
                }))

        threading.Thread(target=_download, daemon=True).start()
        return {"ok": True, "data": {"status": "downloading"}}

    def get_embedder_status(self):
        """Return current embedder download status."""
        return {"ok": True, "data": {"status": self._embedder_status, "error": self._embedder_error}}

    # ---- Chrome ----

    def setup_chrome(self, identity=None):
        identity = identity or self._identity
        if identity not in ("geek", "boss"):
            return {"ok": False, "error": f"Invalid identity: {identity}"}
        result = setup_chrome(identity)
        if result.get("ok"):
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 0}
            )
        return result

    def stop_chrome(self, identity=None):
        identity = identity or self._identity
        if identity not in ("geek", "boss"):
            return {"ok": False, "error": f"Invalid identity: {identity}"}
        result = stop_chrome(identity)
        if result.get("ok"):
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 0,
                   f"{identity}_login_checked_at": ""}
            )
        return result

    def check_login(self, identity=None):
        identity = identity or self._identity
        if identity not in ("geek", "boss"):
            return {"ok": False, "error": f"Invalid identity: {identity}"}
        cdp_port = GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT
        if not is_cdp_ready(cdp_port):
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 0,
                   f"{identity}_login_checked_at": ""}
            )
            return {"ok": True, "data": {"logged_in": False, "status": "chrome_not_running"}}
        result = check_login_state(identity)
        if result.get("ok") and result.get("data", {}).get("logged_in"):
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 1,
                   f"{identity}_login_checked_at": datetime.utcnow().isoformat()}
            )
        else:
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 0,
                   f"{identity}_login_checked_at": ""}
            )
        return result

    def get_chrome_status(self, identity=None):
        """Return Chrome running + login status for the given identity.

        Uses cached login state if within TTL and Chrome is still running.
        """
        identity = identity or self._identity
        cdp_port = GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT
        running = is_cdp_ready(cdp_port)

        if not running:
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 0,
                   f"{identity}_login_checked_at": ""}
            )
            return {
                "ok": True,
                "data": {
                    "identity": identity,
                    "running": False,
                    "logged_in": False,
                    "cdp_port": cdp_port,
                },
            }

        # Chrome is running — check cache first
        cached = self.repo.get_cached_login_state(identity, LOGIN_CACHE_TTL_MINUTES)
        if cached:
            return {
                "ok": True,
                "data": {
                    "identity": identity,
                    "running": True,
                    "logged_in": True,
                    "cdp_port": cdp_port,
                    "cached": True,
                    "checked_at": cached["checked_at"],
                },
            }

        # No valid cache — read DB state (may need re-probe)
        state = self.repo.get_chrome_state()
        logged_in = False
        if state:
            logged_in = bool(state.get(f"{identity}_logged_in", 0))
        return {
            "ok": True,
            "data": {
                "identity": identity,
                "running": True,
                "logged_in": logged_in,
                "cdp_port": cdp_port,
            },
        }

    def auto_detect_login(self, identity=None):
        """Auto-detect Chrome status and login state on app start.

        If Chrome is running but login state is unknown (no cache), probe it.
        Returns the same format as get_chrome_status.
        """
        identity = identity or self._identity
        cdp_port = GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT
        running = is_cdp_ready(cdp_port)

        if not running:
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 0,
                   f"{identity}_login_checked_at": ""}
            )
            return {
                "ok": True,
                "data": {
                    "identity": identity,
                    "running": False,
                    "logged_in": False,
                    "cdp_port": cdp_port,
                },
            }

        # Chrome is running — check cache
        cached = self.repo.get_cached_login_state(identity, LOGIN_CACHE_TTL_MINUTES)
        if cached:
            return {
                "ok": True,
                "data": {
                    "identity": identity,
                    "running": True,
                    "logged_in": True,
                    "cdp_port": cdp_port,
                    "cached": True,
                    "checked_at": cached["checked_at"],
                },
            }

        # Chrome running but no cache — auto probe login
        log.info(f"Chrome 运行中但无登录缓存，自动探测 {identity} 登录态")
        result = check_login_state(identity)
        if result.get("ok") and result.get("data", {}).get("logged_in"):
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 1,
                   f"{identity}_login_checked_at": datetime.utcnow().isoformat()}
            )
            return {
                "ok": True,
                "data": {
                    "identity": identity,
                    "running": True,
                    "logged_in": True,
                    "cdp_port": cdp_port,
                },
            }
        else:
            self.repo.update_chrome_state(
                **{f"{identity}_logged_in": 0,
                   f"{identity}_login_checked_at": ""}
            )
            return {
                "ok": True,
                "data": {
                    "identity": identity,
                    "running": True,
                    "logged_in": False,
                    "cdp_port": cdp_port,
                },
            }

    # ---- Settings ----

    def get_settings(self):
        row = self.db.get_identity()
        return {
            "ok": True,
            "data": {
                "identity": row.get("mode", "geek") if row else "geek",
                "api_base_url": row.get("api_base_url", "https://api.openai.com/v1") if row else "https://api.openai.com/v1",
                "api_key": row.get("api_key", "") if row else "",
                "api_model": row.get("api_model", "gpt-4o") if row else "gpt-4o",
            },
        }

    def save_settings(self, settings_json):
        try:
            s = json.loads(settings_json) if isinstance(settings_json, str) else settings_json
            self.db.upsert_identity(
                mode=s.get("identity", "geek"),
                api_base_url=s.get("api_base_url", ""),
                api_key=s.get("api_key", ""),
                api_model=s.get("api_model", "gpt-4o"),
            )
            self._identity = s.get("identity", "geek")
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ---- Geek Search ----

    def _get_geek_api(self):
        if self._geek_api is None:
            self._geek_api = GeekAPI(self.repo, notify_callback=self._notify_frontend,
                                     embedder=self._embedder)
        return self._geek_api

    def _notify_frontend(self, payload_json: str):
        try:
            if self._window:
                payload = json.loads(payload_json)
                ptype = payload.get("type", "scrape")
                if ptype in MATCH_EVENT_TYPES:
                    js_call = f'window.__onMatchProgress && window.__onMatchProgress({payload_json})'
                else:
                    js_call = f'window.__onScrapeProgress && window.__onScrapeProgress({payload_json})'
                self._window.evaluate_js(js_call)
        except Exception as e:
            log.debug(f"Frontend notification failed: {e}")

    def search_jobs(self, keyword, city="", max_pages="3", filters_json="{}"):
        try:
            max_pages = int(max_pages)
        except (TypeError, ValueError):
            max_pages = 3
        return self._get_geek_api().search_jobs(keyword, city, max_pages, filters_json)

    def get_scrape_progress(self):
        return self._get_geek_api().get_scrape_progress()

    def get_scraped_jobs(self, keyword="", city="", limit="50", offset="0"):
        try:
            limit = int(limit)
            offset = int(offset)
        except (TypeError, ValueError):
            limit, offset = 50, 0
        return self._get_geek_api().get_scraped_jobs(keyword, city, limit, offset)

    def get_scraped_details(self, job_ids_json="[]"):
        return self._get_geek_api().get_scraped_details(job_ids_json)

    def list_cities(self, keyword=""):
        return self._get_geek_api().list_cities(keyword)

    def get_filter_options(self):
        return self._get_geek_api().get_filter_options()

    def cancel_scrape(self):
        return self._get_geek_api().cancel_scrape()

    # ---- Resume ----

    def save_resume(self, content):
        try:
            self.repo.save_resume(content)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_resume(self):
        content = self.repo.get_resume()
        return {"ok": True, "data": {"content": content}}

    def upload_resume(self, filename, base64_content):
        """接收上传的简历文件，解析提取文本。"""
        import base64
        from src.core.resume_parser import parse_resume_file

        try:
            file_bytes = base64.b64decode(base64_content)
            content = parse_resume_file(filename, file_bytes)
            return {"ok": True, "data": {"content": content}}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.error(f"简历文件解析失败: {e}")
            return {"ok": False, "error": f"文件解析失败: {str(e)}"}

    # ---- Match ----

    def start_match(self, job_ids_json, supplements_json="[]"):
        try:
            job_ids = json.loads(job_ids_json) if isinstance(job_ids_json, str) else job_ids_json
            supplements = json.loads(supplements_json) if isinstance(supplements_json, str) else supplements_json
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "Invalid JSON parameters"}
        resume = self.repo.get_resume()
        return self._get_geek_api().start_match(resume, job_ids, supplements)

    def get_match_progress(self):
        return self._get_geek_api().get_match_progress()

    def cancel_match(self):
        return self._get_geek_api().cancel_match()

    def upload_supplement(self, filename, base64_content):
        """接收上传的补充材料文件，解析提取文本。"""
        import base64
        from src.core.resume_parser import parse_resume_file

        try:
            file_bytes = base64.b64decode(base64_content)
            content = parse_resume_file(filename, file_bytes)
            return {"ok": True, "data": {"content": content}}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.error(f"补充材料文件解析失败: {e}")
            return {"ok": False, "error": f"文件解析失败: {str(e)}"}

    def get_match_summary(self):
        return self._get_geek_api().get_match_summary()

    def get_match_results(self, source_id="1", limit="50", offset="0"):
        try:
            source_id = int(source_id)
            limit = int(limit)
            offset = int(offset)
        except (TypeError, ValueError):
            source_id, limit, offset = 1, 50, 0
        return self._get_geek_api().get_match_results(source_id, limit, offset)
