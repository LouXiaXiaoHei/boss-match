"""Job list and detail scraping — supports geek/boss identity parameter."""

import hashlib
import json
import random
import time
import logging
import threading
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from src.core.cdp import CDPSession, create_page_session
from src.core.city import resolve_city
from src.core.detail import (
    extract_detail_fields,
    DetailExtractionError,
    DetailLoginRequiredError,
    resolve_boss_active_status,
)
from src.core.js_templates import FETCH_API_JS_TEMPLATE, EXTRACT_DETAIL_JS
from src.core.constants import (
    API_JOB_LIST_PATH,
    SCALE_MAP,
    STAGE_MAP,
    SALARY_MAP,
    EXPERIENCE_MAP,
    DEGREE_MAP,
    INDUSTRY_MAP,
    MAX_API_REQUESTS,
    GEEK_CDP_PORT,
    BOSS_CDP_PORT,
)

log = logging.getLogger(__name__)

_request_counter = 0


def _incr_request():
    global _request_counter
    _request_counter += 1
    if _request_counter > MAX_API_REQUESTS:
        raise RuntimeError(f"已达到单次最大请求数 {MAX_API_REQUESTS}")


def build_search_url(keyword: str, city_code: str, page: int, filters: dict) -> str:
    params = {"query": keyword, "city": city_code, "page": page}
    for key, code in filters.items():
        if code:
            params[key] = code
    return f"https://www.zhipin.com/web/geek/job?{urlencode(params)}"


def parse_api_jobs_eval_value(value) -> list[dict]:
    if not value:
        return []
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    jobs = []
    for item in parsed:
        if not isinstance(item, dict) or item.get("error"):
            continue
        if item.get("title") or item.get("job_link"):
            jobs.append(item)
    return jobs


def build_detail_url(job: dict) -> str:
    link = job.get("job_link", "")
    if not link:
        return ""
    parsed = urlparse(link)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    existing_keys = {key for key, _ in params}
    for query_key, job_key in (("lid", "lid"), ("securityId", "security_id")):
        value = job.get(job_key) or job.get(query_key) or ""
        if value and query_key not in existing_keys:
            params.append((query_key, value))
            existing_keys.add(query_key)
    return urlunparse(parsed._replace(query=urlencode(params)))


def build_detail_record(job: dict, extracted: dict) -> dict:
    link = job.get("job_link", "")
    boss_active_status = resolve_boss_active_status(
        list_status=job.get("boss_active_status", ""),
        detail_status=extracted.get("boss_active_status", ""),
    )
    return {
        "job_id": job.get("job_id", ""),
        "title": job.get("title", ""),
        "company": job.get("boss_name", ""),
        "salary": job.get("salary", ""),
        "salary_source": job.get("salary_source", ""),
        "location": job.get("location", ""),
        "boss_active_status": boss_active_status,
        "tags_list": job.get("tags", ""),
        "job_link": link,
        "link": link,
        "skill_tags": extracted.get("tags", []),
        "jd": extracted.get("jd", ""),
    }


def _human_scroll(cdp: CDPSession, sid: str) -> None:
    total_scrolls = random.randint(3, 6)
    for _ in range(total_scrolls):
        if random.random() < 0.15:
            delta = -random.randint(50, 150)
        else:
            delta = random.randint(150, 500)
        cdp.eval_js(f"window.scrollBy(0,{delta})", sid)
        if random.random() < 0.3:
            time.sleep(random.uniform(2.0, 4.0))
        else:
            time.sleep(random.uniform(0.5, 1.5))


def _human_mouse_jitter(cdp: CDPSession, sid: str) -> None:
    if random.random() < 0.4:
        x = random.randint(100, 800)
        y = random.randint(100, 600)
        cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, sid)


def scrape_list(keyword: str, city_input: str, max_pages: int, filters: dict,
                identity: str = "geek",
                progress_callback=None,
                cancel_event: threading.Event | None = None) -> dict:
    """Scrape job list for the given identity.

    Returns {ok, data: {keyword, city, total, jobs}} or {ok: False, error: ...}.

    progress_callback: called after each page with (page, max_pages, jobs_found, phase="list", new_jobs)
    cancel_event: if set, scraping stops after the current page
    """
    global _request_counter
    _request_counter = 0

    cdp_port = GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT
    city_name, city_code = resolve_city(city_input)
    all_jobs = []
    seen = set()

    try:
        cdp = CDPSession(cdp_port)
    except Exception as e:
        return {"ok": False, "error": f"CDP 连接失败: {e}"}

    tid = None
    try:
        tid, sid = create_page_session(cdp)

        for pg in range(1, max_pages + 1):
            if cancel_event and cancel_event.is_set():
                log.info("抓取被取消")
                break

            log.info(f"[{pg}/{max_pages} 页, {len(all_jobs)} 条已抓]")
            _incr_request()

            if pg == 1:
                url = build_search_url(keyword, city_code, pg, filters)
                cdp.send("Page.navigate", {"url": url}, sid)
                time.sleep(random.uniform(6, 10))
                _human_scroll(cdp, sid)
                _human_mouse_jitter(cdp, sid)

            api_params = {
                "scene": "1",
                "query": keyword,
                "city": city_code,
                "page": pg,
                "pageSize": 30,
            }
            for k, v in filters.items():
                if v:
                    api_params[k] = v
            api_url = f"{API_JOB_LIST_PATH}?{urlencode(api_params)}"
            api_js = FETCH_API_JS_TEMPLATE.replace("__API_URL__", api_url)
            val = cdp.eval_js(api_js, sid)

            jobs = parse_api_jobs_eval_value(val)
            if not jobs:
                log.warning("API 未返回职位数据")
                continue

            new = 0
            page_new_jobs = []
            for j in jobs:
                key = j.get("job_link") or j["title"]
                j["job_id"] = hashlib.md5(key.encode()).hexdigest()[:16]
                if key in seen:
                    continue
                seen.add(key)
                all_jobs.append(j)
                page_new_jobs.append(j)
                new += 1

            log.info(f"本页 {len(jobs)} 条, 新增 {new}, 累计 {len(all_jobs)}")

            if progress_callback:
                try:
                    progress_callback(pg, max_pages, len(all_jobs), "list", page_new_jobs)
                except Exception:
                    pass

            if pg < max_pages:
                time.sleep(random.uniform(12, 22))

    except KeyboardInterrupt:
        log.info("用户中断抓取")
    except RuntimeError as e:
        log.warning(str(e))
    finally:
        if tid and cdp:
            try:
                cdp.send("Target.closeTarget", {"targetId": tid})
            except Exception:
                pass
        try:
            cdp.close()
        except Exception:
            pass

    return {
        "ok": True,
        "data": {
            "keyword": keyword,
            "city": city_name,
            "total": len(all_jobs),
            "jobs": all_jobs,
        },
    }


def scrape_details(jobs: list[dict], identity: str = "geek",
                   max_details: int | None = None,
                   progress_callback=None,
                   cancel_event: threading.Event | None = None) -> dict:
    """Scrape job details for the given list of jobs.

    Returns {ok, data: {details}} or {ok: False, error: ...}.

    progress_callback: called after each detail with (index, total, phase="details", detail_dict)
    cancel_event: if set, scraping stops after the current detail
    """
    if max_details:
        jobs = jobs[:max_details]

    cdp_port = GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT
    results = []
    seen_links = set()

    for idx, job in enumerate(jobs):
        if cancel_event and cancel_event.is_set():
            log.info("详情抓取被取消")
            break

        link = job.get("job_link", "")
        title = job.get("title", "")
        company = job.get("boss_name", "")
        if not link:
            continue
        if link in seen_links:
            continue
        seen_links.add(link)

        log.info(f"[{idx+1}/{len(jobs)}] {company} - {title}")
        _incr_request()

        try:
            ws = CDPSession(cdp_port)
        except Exception as e:
            log.warning(f"CDP 连接失败: {e}")
            continue

        tid = None
        try:
            tid, sid = create_page_session(ws)
            detail_url = build_detail_url(job)
            ws.send("Page.navigate", {"url": detail_url}, sid)
            time.sleep(random.uniform(5, 10))

            scroll_count = random.randint(3, 7)
            for _ in range(scroll_count):
                if random.random() < 0.12:
                    delta = -random.randint(80, 200)
                else:
                    delta = random.randint(200, 600)
                ws.eval_js(f"window.scrollBy(0,{delta})", sid)
                if random.random() < 0.35:
                    time.sleep(random.uniform(2.0, 5.0))
                else:
                    time.sleep(random.uniform(0.8, 1.8))

            if random.random() < 0.5:
                ws.send("Input.dispatchMouseEvent", {
                    "type": "mouseMoved",
                    "x": random.randint(200, 800),
                    "y": random.randint(200, 600),
                }, sid)
                time.sleep(random.uniform(0.5, 1.5))

            val = ws.eval_js(EXTRACT_DETAIL_JS, sid)
            try:
                d = json.loads(val) if isinstance(val, str) else {"jd": "", "tags": []}
            except (json.JSONDecodeError, ValueError, TypeError):
                d = {"jd": "", "tags": []}

            try:
                fields = extract_detail_fields(d)
                d["jd"] = fields["jd"]
                d["boss_active_status"] = resolve_boss_active_status(
                    list_status=job.get("boss_active_status", ""),
                    detail_status=fields["boss_active_status"],
                )
            except DetailLoginRequiredError:
                log.error("BOSS 登录态过期，停止抓取详情")
                break
            except DetailExtractionError as exc:
                log.warning(f"跳过无效详情页: {exc}")
                continue

            detail = build_detail_record(job, d)
            results.append(detail)

            if progress_callback:
                try:
                    progress_callback(idx + 1, len(jobs), 0, "details", detail)
                except Exception:
                    pass

        except Exception as e:
            log.warning(f"详情抓取异常: {e}")
        finally:
            if tid:
                try:
                    ws.send("Target.closeTarget", {"targetId": tid})
                except Exception:
                    pass
            try:
                ws.close()
            except Exception:
                pass

        time.sleep(random.uniform(10, 25))

    return {"ok": True, "data": {"details": results}}
