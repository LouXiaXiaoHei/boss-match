"""CDP (Chrome DevTools Protocol) session management."""

import json
import time
import logging

log = logging.getLogger(__name__)

websocket = None


def ensure_websocket():
    global websocket
    if websocket is None:
        import websocket as _ws
        websocket = _ws
    return websocket


class CDPSession:
    def __init__(self, cdp_port=9222):
        ws_mod = ensure_websocket()
        import requests as req

        self.cdp_port = cdp_port
        resp = req.get(f"http://127.0.0.1:{cdp_port}/json/version", timeout=10)
        ws_url = resp.json()["webSocketDebuggerUrl"]
        self.ws = ws_mod.create_connection(ws_url, timeout=60)
        self.mid = 0

    def send(self, method, params=None, sid=None, timeout=30):
        """Send a CDP command and wait for the matching response."""
        self.mid += 1
        msg = {"id": self.mid, "method": method, "params": params or {}}
        if sid:
            msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))

        start_time = time.time()
        max_retries = 1000

        for attempt in range(max_retries):
            elapsed = time.time() - start_time
            if elapsed > timeout:
                raise TimeoutError(
                    f"CDP send({method}) timeout ({timeout}s), "
                    f"skipped {attempt} unmatched messages"
                )

            try:
                raw = self.ws.recv()
            except Exception:
                raise TimeoutError(f"CDP WebSocket recv timeout, method={method}")

            try:
                r = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                log.debug(f"Skip non-JSON message: {raw[:100]}")
                continue

            if r.get("id") == self.mid:
                return r

            event_name = r.get("method", "unknown")
            log.debug(f"Skip unmatched message (id={r.get('id')}, event={event_name})")

        raise TimeoutError(
            f"CDP send({method}) no matching response in {max_retries} messages"
        )

    def eval_js(self, js, sid):
        r = self.send("Runtime.evaluate", {"expression": js, "returnByValue": True}, sid)
        return r.get("result", {}).get("result", {}).get("value", None)

    def close(self):
        self.ws.close()


BACKGROUND_VISIBILITY_SCRIPT = (
    "Object.defineProperty(document, 'hidden', {get: () => false});"
    "Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});"
    "Object.defineProperty(document, 'webkitHidden', {get: () => false});"
    "Object.defineProperty(document, 'webkitVisibilityState', {get: () => 'visible'});"
)


def create_page_session(cdp, background=True):
    """Create and attach an about:blank target.

    Background pages report themselves as hidden, which prevents BOSS detail
    pages from rendering reliably. Register the visibility override before
    callers navigate. Interactive callers such as the login flow must opt into
    a foreground target explicitly.
    """
    target = cdp.send(
        "Target.createTarget",
        {"url": "about:blank", "background": background},
    )
    target_id = target["result"]["targetId"]
    attached = cdp.send(
        "Target.attachToTarget",
        {"targetId": target_id, "flatten": True},
    )
    session_id = attached["result"]["sessionId"]
    if background:
        cdp.send(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": BACKGROUND_VISIBILITY_SCRIPT},
            session_id,
        )
    return target_id, session_id
