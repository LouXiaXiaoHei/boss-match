"""Login state detection for BOSS直聘 — supports geek/boss identities."""

import json
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode

from src.core.cdp import CDPSession, create_page_session
from src.core.constants import (
    API_JOB_LIST_PATH,
    LOGIN_PROBE_QUERY,
    LOGIN_PROBE_CITY,
    LOGIN_PROBE_PAGE_SIZE,
    LOGIN_RESTRICTED_CODES,
    LOGIN_RESTRICTED_MESSAGE_KEYWORDS,
    GEEK_CDP_PORT,
    BOSS_CDP_PORT,
)

log = __import__("logging").getLogger(__name__)

_request_counter = 0


def _incr_request():
    global _request_counter
    _request_counter += 1


class LoginProbeStatus(Enum):
    AVAILABLE = "available"
    UNAUTHENTICATED = "unauthenticated"
    RESTRICTED = "restricted"
    EMPTY = "empty"
    RESPONSE_ERROR = "response_error"


@dataclass(frozen=True)
class LoginProbeResult:
    status: LoginProbeStatus
    code: int | None = None
    message: str = ""
    retryable: bool = False


def classify_login_probe_response(data, http_status: int = 200) -> LoginProbeResult:
    """Classify a BOSS search response without collapsing failures to bool."""
    if http_status == 401:
        return LoginProbeResult(LoginProbeStatus.UNAUTHENTICATED, message="HTTP 401")
    if http_status in (403, 429):
        return LoginProbeResult(LoginProbeStatus.RESTRICTED, message=f"HTTP {http_status}")
    if http_status != 200:
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message=f"HTTP {http_status}",
            retryable=http_status == 0 or http_status >= 500,
        )
    if not isinstance(data, dict):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR,
            message="响应不是 JSON 对象",
            retryable=True,
        )

    raw_code = data.get("code")
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None
    message = str(data.get("message") or data.get("msg") or "")

    if code in LOGIN_RESTRICTED_CODES:
        return LoginProbeResult(LoginProbeStatus.RESTRICTED, code=code, message=message)
    if code != 0:
        if any(kw in message for kw in LOGIN_RESTRICTED_MESSAGE_KEYWORDS):
            return LoginProbeResult(LoginProbeStatus.RESTRICTED, code=code, message=message)
        return LoginProbeResult(LoginProbeStatus.RESPONSE_ERROR, code=code, message=message)

    zp_data = data.get("zpData")
    if not isinstance(zp_data, dict):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR, code=code, message="响应缺少 zpData", retryable=True,
        )
    job_list = zp_data.get("jobList")
    if not isinstance(job_list, list):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR, code=code, message="响应缺少 jobList", retryable=True,
        )
    if not job_list:
        return LoginProbeResult(LoginProbeStatus.EMPTY, code=code)
    if any(
        (job.get("salaryDesc") or "").strip()
        for job in job_list
        if isinstance(job, dict)
    ):
        return LoginProbeResult(LoginProbeStatus.AVAILABLE, code=code)
    return LoginProbeResult(LoginProbeStatus.UNAUTHENTICATED, code=code)


def is_logged_in_search_response(data) -> bool:
    result = classify_login_probe_response(data)
    return result.status is LoginProbeStatus.AVAILABLE


def build_login_probe_url(query: str, city_code: str) -> str:
    params = {
        "scene": 1,
        "query": query,
        "city": city_code,
        "page": 1,
        "pageSize": LOGIN_PROBE_PAGE_SIZE,
    }
    return f"{API_JOB_LIST_PATH}?{urlencode(params)}"


def probe_login_state(cdp: CDPSession, sid: str,
                      query: str = LOGIN_PROBE_QUERY,
                      city_code: str = LOGIN_PROBE_CITY) -> LoginProbeResult:
    """Run exactly one budgeted search probe and return its structured state."""
    probe_url = build_login_probe_url(query, city_code)
    js = f"""
    (function(){{
        var xhr = new XMLHttpRequest();
        xhr.open('GET', '{probe_url}', false);
        xhr.send();
        return JSON.stringify({{
            httpStatus: xhr.status,
            body: xhr.responseText
        }});
    }})()
    """
    _incr_request()
    val = cdp.eval_js(js, sid)
    if not val:
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR, message="探测响应为空", retryable=True,
        )
    try:
        envelope = json.loads(val) if isinstance(val, str) else val
    except (json.JSONDecodeError, ValueError):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR, message="探测响应不是有效 JSON", retryable=True,
        )
    if not isinstance(envelope, dict):
        return LoginProbeResult(
            LoginProbeStatus.RESPONSE_ERROR, message="探测响应格式异常", retryable=True,
        )

    raw_http_status = envelope.get("httpStatus", 200)
    try:
        http_status = int(raw_http_status)
    except (TypeError, ValueError):
        http_status = 0
    body = envelope.get("body", envelope)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return LoginProbeResult(
                LoginProbeStatus.RESPONSE_ERROR, message="搜索接口响应不是有效 JSON", retryable=True,
            )
    return classify_login_probe_response(body, http_status=http_status)


def describe_login_probe_result(result: LoginProbeResult) -> str:
    """Return a concise user-facing explanation for a non-available state."""
    context = []
    if result.code is not None:
        context.append(f"code: {result.code}")
    if result.message:
        context.append(result.message)
    suffix = f"（{'; '.join(context)}）" if context else ""

    if result.status is LoginProbeStatus.UNAUTHENTICATED:
        return f"未检测到可用登录态{suffix}"
    if result.status is LoginProbeStatus.RESTRICTED:
        return f"BOSS 接口返回限制状态{suffix}"
    if result.status is LoginProbeStatus.EMPTY:
        return "探测样本没有职位，暂时无法确认登录态"
    return f"登录探测响应异常{suffix}"


def check_login_state(identity: str = "geek") -> dict:
    """Check BOSS直聘 login state for the given identity.

    Returns {ok, data: {status, message}} or {ok: False, error: ...}.
    """
    cdp_port = GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT
    cdp = None
    tid = None
    try:
        cdp = CDPSession(cdp_port)
        tid, sid = create_page_session(cdp)

        cdp.send("Page.navigate", {"url": "https://www.zhipin.com/"}, sid)
        time.sleep(4)

        result = probe_login_state(cdp, sid)
        return {
            "ok": True,
            "data": {
                "status": result.status.value,
                "code": result.code,
                "message": result.message or describe_login_probe_result(result),
                "logged_in": result.status is LoginProbeStatus.AVAILABLE,
            },
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if tid and cdp:
            try:
                cdp.send("Target.closeTarget", {"targetId": tid})
            except Exception:
                pass
        if cdp:
            try:
                cdp.close()
            except Exception:
                pass


def wait_for_login(identity: str = "geek", timeout: int = 300, interval: int = 3) -> dict:
    """Poll login state until logged in or timeout.

    Returns {ok, data: {logged_in}} or {ok: False, error: ...}.
    """
    cdp_port = GEEK_CDP_PORT if identity == "geek" else BOSS_CDP_PORT
    elapsed = 0
    while elapsed < timeout:
        try:
            cdp = CDPSession(cdp_port)
            tid, sid = create_page_session(cdp)
            try:
                cdp.send("Page.navigate", {"url": "https://www.zhipin.com/"}, sid)
                time.sleep(3)
                result = probe_login_state(cdp, sid)
                if result.status is LoginProbeStatus.AVAILABLE:
                    return {"ok": True, "data": {"logged_in": True}}
            finally:
                if tid:
                    try:
                        cdp.send("Target.closeTarget", {"targetId": tid})
                    except Exception:
                        pass
                cdp.close()
        except Exception:
            pass
        time.sleep(interval)
        elapsed += interval
    return {"ok": False, "error": f"登录等待超时 ({timeout}s)"}
