"""Windows Chrome CDP 页面选购冒烟；仅在服务已就绪后由人工启动。

运行示例（globex Conda 环境含 websocket-client）：
    E:\\anaconda3\\envs\\globex\\python.exe eval/verification/local-startup-20261011-02/browser_e2e.py

脚本只提交一次只读选购提示，不重试；每次在独立目录保留 JSON 和截图。
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import uuid


PROMPT = (
    "预算 300 元以内，找一个寄到中国的轻便背包。"
    "仅检索并展示候选，不创建订单或确认单，也不修改偏好。"
)
DEFAULT_URL = "http://127.0.0.1:5173"
OUTPUT_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
PROFILE_ROOT = OUTPUT_ROOT / ".browser-profiles"


class BrowserCheckError(RuntimeError):
    """页面验收失败；异常信息不包含模型正文、密钥或浏览器请求头。"""


def get_json(url: str, timeout: float = 5) -> dict:
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
        return json.load(response)


def check_health(base_url: str) -> dict:
    try:
        with urlopen(f"{base_url}/health", timeout=8) as response:
            code = response.status
            body = json.load(response)
    except HTTPError as error:
        return {"http_status": error.code, "status": "unavailable"}
    except Exception as error:  # noqa: BLE001
        return {"http_status": None, "status": type(error).__name__}
    return {"http_status": code, "status": body.get("status")}


def chrome_path(override: str | None) -> Path:
    candidates = [
        override,
        str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise BrowserCheckError("chrome_executable_missing")


def remove_profile(profile_dir: Path) -> None:
    """只递归清理由本脚本在 workspace 证据目录下创建的 Chrome profile。"""
    workspace = WORKSPACE_ROOT.resolve(strict=True)
    root = PROFILE_ROOT.resolve(strict=True)
    target = profile_dir.resolve(strict=True)
    if (not root.is_relative_to(workspace) or target.parent != root
            or not target.name.startswith("globex-browser-e2e-")):
        raise BrowserCheckError("profile_cleanup_path_outside_workspace")
    shutil.rmtree(target)


def wait_debug_port(profile: Path, process: subprocess.Popen, timeout: float = 25) -> int:
    marker = profile / "DevToolsActivePort"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise BrowserCheckError("chrome_exited_before_cdp_ready")
        if marker.is_file():
            lines = marker.read_text(encoding="utf-8").splitlines()
            if lines and lines[0].isdigit():
                return int(lines[0])
        time.sleep(0.1)
    raise BrowserCheckError("chrome_cdp_start_timeout")


def wait_page_websocket(port: int, timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pages = get_json(f"http://127.0.0.1:{port}/json/list", timeout=2)
            for page in pages:
                if page.get("type") == "page" and page.get("webSocketDebuggerUrl"):
                    return page["webSocketDebuggerUrl"]
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    raise BrowserCheckError("chrome_page_target_missing")


class CDP:
    def __init__(self, websocket_url: str) -> None:
        try:
            import websocket  # websocket-client；Conda globex 环境已有该依赖。
        except ImportError as error:
            raise BrowserCheckError("websocket_client_missing") from error
        self.socket = websocket.create_connection(websocket_url, timeout=12, origin="http://localhost")
        self.timeout_error = websocket.WebSocketTimeoutException
        self.seq = 0
        self.requests: dict[str, dict] = {}
        self.run_request_ids: list[str] = []
        self.page_errors = 0
        self.console_errors = 0

    def close(self) -> None:
        self.socket.close()

    def _event(self, message: dict) -> None:
        method, params = message.get("method"), message.get("params", {})
        if method == "Network.requestWillBeSent":
            request = params.get("request", {})
            parsed = urlsplit(request.get("url", ""))
            if parsed.path == "/commerce/ag-ui/run" and request.get("method") == "POST":
                request_id = params["requestId"]
                self.run_request_ids.append(request_id)
                self.requests[request_id] = {"path": parsed.path, "method": "POST", "status": None,
                                             "mime_type": None, "data_chunks": 0, "finished": False}
        elif method == "Network.responseReceived":
            entry = self.requests.get(params.get("requestId"))
            if entry is not None:
                response = params.get("response", {})
                entry["status"] = response.get("status")
                entry["mime_type"] = response.get("mimeType")
        elif method == "Network.dataReceived":
            entry = self.requests.get(params.get("requestId"))
            if entry is not None:
                entry["data_chunks"] += 1
        elif method == "Network.loadingFinished":
            entry = self.requests.get(params.get("requestId"))
            if entry is not None:
                entry["finished"] = True
        elif method == "Runtime.exceptionThrown":
            self.page_errors += 1
        elif method == "Log.entryAdded" and params.get("entry", {}).get("level") == "error":
            self.console_errors += 1

    def call(self, method: str, params: dict | None = None, timeout: float = 15) -> dict:
        self.seq += 1
        identifier = self.seq
        self.socket.send(json.dumps({"id": identifier, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.socket.settimeout(max(0.1, deadline - time.monotonic()))
            try:
                message = json.loads(self.socket.recv())
            except self.timeout_error as error:
                raise BrowserCheckError(f"cdp_timeout:{method}") from error
            if "method" in message:
                self._event(message)
                continue
            if message.get("id") == identifier:
                if "error" in message:
                    raise BrowserCheckError(f"cdp_command_failed:{method}")
                return message.get("result", {})
        raise BrowserCheckError(f"cdp_timeout:{method}")

    def evaluate(self, expression: str) -> object:
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True,
                                                 "awaitPromise": True}, timeout=15)
        if result.get("exceptionDetails"):
            raise BrowserCheckError("page_evaluation_failed")
        return result.get("result", {}).get("value")

    def wait(self, expression: str, predicate, timeout: float, label: str, on_step=None) -> object:
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = self.evaluate(expression)
            if on_step is not None:
                on_step(last)
            if predicate(last):
                return last
            time.sleep(0.15)
        raise BrowserCheckError(f"timeout:{label}")

    def click(self, selector: str) -> None:
        # 通过 CDP 鼠标事件点击实际渲染元素，不直接调用 DOM click()。
        target = self.evaluate("""(() => {
          const el = document.querySelector(%s);
          if (!el || el.disabled) return null;
          el.scrollIntoView({block:'center', inline:'center'});
          const r = el.getBoundingClientRect();
          return {x:r.left+r.width/2, y:r.top+r.height/2, width:r.width, height:r.height};
        })()""" % json.dumps(selector))
        if not isinstance(target, dict) or target.get("width", 0) <= 0 or target.get("height", 0) <= 0:
            raise BrowserCheckError(f"click_target_unavailable:{selector}")
        x, y = target["x"], target["y"]
        self.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.call("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                               "button": "left", "clickCount": 1})
        self.call("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                               "button": "left", "clickCount": 1})

    def screenshot(self, path: Path) -> None:
        result = self.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False},
                           timeout=20)
        path.write_bytes(base64.b64decode(result["data"]))


SNAPSHOT = """(() => {
  const events = [...document.querySelectorAll('.diagnostics .event-type')].map(x => x.textContent?.trim());
  const monitor = window.__globexBrowserSmoke || {};
  return {
    inputReady: !!document.querySelector('textarea#query'),
    inputValue: document.querySelector('textarea#query')?.value ?? null,
    newChatEnabled: !!document.querySelector('button.new-chat:not(:disabled)'),
    sendEnabled: !!document.querySelector('button[aria-label="发送选购需求"]:not(:disabled)'),
    running: !!document.querySelector('.progress-line.running'),
    queryCount: document.querySelectorAll('.query-bubble').length,
    answerCount: document.querySelectorAll('.assistant-text').length,
    answerCharacters: [...document.querySelectorAll('.assistant-text')].reduce((n, el) => n + (el.textContent || '').trim().length, 0),
    productCount: document.querySelectorAll('.search-results .product-card').length,
    runFinished: events.includes('RUN_FINISHED'),
    runError: events.includes('RUN_ERROR'),
    eventCount: events.length,
    eventTypes: [...new Set(events)].sort(),
    errorPanel: !!document.querySelector('.error-panel'),
    shoppingForm: !!document.querySelector('.shopping-form'),
    streamTextWhileRunning: monitor.textWhileRunning === true,
    textUpdateCount: monitor.textUpdates || 0,
    dialogLabel: document.querySelector('[role="dialog"]')?.getAttribute('aria-label') || null,
    detailHasPrice: !!document.querySelector('[role="dialog"] .detail-price, [role="dialog"] .price'),
    detailHasSpecs: !!document.querySelector('[role="dialog"] .detail-specs'),
    skuPickerVisible: !!document.querySelector('[role="dialog"] .sku-picker')
  };
})()"""

MONITOR = """(() => {
  const monitor = {textWhileRunning:false, textUpdates:0, lastLength:0};
  window.__globexBrowserSmoke = monitor;
  new MutationObserver(() => {
    const length = [...document.querySelectorAll('.assistant-text')]
      .reduce((n, el) => n + (el.textContent || '').trim().length, 0);
    if (length > 0 && document.querySelector('.progress-line.running'))
      monitor.textWhileRunning = true;
    if (length > 0 && length !== monitor.lastLength) monitor.textUpdates += 1;
    monitor.lastLength = length;
  }).observe(document.getElementById('root'), {subtree:true, childList:true, characterData:true});
  return true;
})()"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="本机前端地址，默认 http://127.0.0.1:5173")
    parser.add_argument("--chrome", help="Chrome.exe 绝对路径；默认搜索 Windows 安装路径")
    parser.add_argument("--output", type=Path, default=OUTPUT_ROOT, help="证据根目录")
    parser.add_argument("--timeout", type=float, default=240, help="单轮完成等待秒数")
    parser.add_argument("--content-timeout", type=float, default=120,
                        help="本轮完成后等待回答和商品卡呈现的秒数")
    args = parser.parse_args()
    if args.url.rstrip("/") != DEFAULT_URL:
        parser.error("本次验收只允许本机 http://127.0.0.1:5173")

    run_dir = args.output / f"browser-e2e-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"status": "failed", "started_at_utc": datetime.now(timezone.utc).isoformat(),
              "frontend_url": DEFAULT_URL, "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
              "prompt_submitted": False, "checks": {}, "screenshots": [], "health": None}
    browser = None
    process = None
    temp = None
    try:
        report["health"] = check_health(DEFAULT_URL)
        if report["health"] != {"http_status": 200, "status": "ok"}:
            raise BrowserCheckError("health_not_ok")

        # Windows 上把独立 profile 建在 workspace 内；退出 Chrome 后再清理。
        PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
        temp = Path(tempfile.mkdtemp(prefix="globex-browser-e2e-", dir=PROFILE_ROOT))
        profile = Path(temp) / "chrome-profile"
        profile.mkdir()
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process = subprocess.Popen([
            str(chrome_path(args.chrome)), "--headless=new", "--disable-gpu", "--no-first-run",
            "--no-default-browser-check", "--disable-extensions", "--remote-allow-origins=*",
            "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
            f"--user-data-dir={profile}", "--window-size=1440,1100", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        port = wait_debug_port(profile, process)
        browser = CDP(wait_page_websocket(port))
        browser.call("Page.enable")
        browser.call("Runtime.enable")
        browser.call("Network.enable")
        browser.call("Log.enable")
        browser.call("Emulation.setDeviceMetricsOverride", {"width": 1440, "height": 1100,
                                                            "deviceScaleFactor": 1, "mobile": False})
        browser.call("Page.navigate", {"url": DEFAULT_URL})
        initial = browser.wait(SNAPSHOT, lambda s: isinstance(s, dict) and s["inputReady"], 30, "frontend_ready")
        if browser.run_request_ids:
            raise BrowserCheckError("unexpected_model_run_before_submission")
        # 独立 profile 首屏可能已经是空会话；此时初始化期间“新的选购”会暂时禁用。
        # 只有加载了已有会话内容时才等待按钮可点并另开会话。
        has_prior_content = any(initial[key] for key in
                                ("queryCount", "answerCount", "productCount", "eventCount", "shoppingForm"))
        if has_prior_content:
            browser.wait(SNAPSHOT, lambda s: isinstance(s, dict) and s["newChatEnabled"],
                         30, "new_chat_available")
            browser.click("button.new-chat")
        report["checks"]["session_opened"] = "new_chat_clicked" if has_prior_content else "empty_landing"
        browser.wait(SNAPSHOT, lambda s: isinstance(s, dict) and s["inputValue"] == ""
                     and not s["running"] and not any(s[key] for key in
                     ("queryCount", "answerCount", "productCount", "eventCount", "shoppingForm")),
                     15, "new_session_ready")
        # 首屏可先显示空会话，后台仍在读取模型/工作区；等 busy 解除再输入。
        browser.wait(SNAPSHOT, lambda s: isinstance(s, dict) and s["newChatEnabled"],
                     60, "workspace_initialization_finished")
        baseline_page_errors = browser.page_errors
        baseline_console_errors = browser.console_errors
        report["checks"]["browser_error_counts_before_submission"] = {
            "page": baseline_page_errors, "console": baseline_console_errors,
        }
        browser.evaluate(MONITOR)
        browser.evaluate("document.querySelector('textarea#query').focus()")
        browser.call("Input.insertText", {"text": PROMPT})
        browser.wait(SNAPSHOT, lambda s: isinstance(s, dict) and s["inputValue"] == PROMPT and s["sendEnabled"]
                     and not any(s[key] for key in
                     ("queryCount", "answerCount", "productCount", "eventCount", "shoppingForm")),
                     45, "prompt_ready")
        if browser.run_request_ids:
            raise BrowserCheckError("unexpected_model_run_before_submission")

        # 唯一一次提交；之后无重试、无订单/确认/收藏动作。
        browser.click('button[aria-label="发送选购需求"]')
        report["prompt_submitted"] = True
        stream_shot_taken = False

        def capture_stream(snapshot: object) -> None:
            nonlocal stream_shot_taken
            if (not stream_shot_taken and isinstance(snapshot, dict)
                    and snapshot["running"] and snapshot["streamTextWhileRunning"]
                    and snapshot["answerCharacters"] > 0):
                browser.screenshot(run_dir / "streaming.png")
                report["screenshots"].append("streaming.png")
                stream_shot_taken = True

        finished = browser.wait(SNAPSHOT, lambda s: isinstance(s, dict) and
                                (s["runFinished"] or s["runError"] or s["errorPanel"]),
                                args.timeout, "run_terminal", on_step=capture_stream)
        report["checks"]["terminal_snapshot"] = {k: v for k, v in finished.items() if k != "inputValue"}
        # RUN_FINISHED 可能先于 React 渲染回答和商品卡；继续观察页面，最多等待 120 秒。
        visible = finished
        content_wait_started = time.monotonic()
        deadline = content_wait_started + args.content_timeout
        while (not (visible["answerCharacters"] > 0 and visible["productCount"] > 0)
               and not (visible["runError"] or visible["errorPanel"])
               and time.monotonic() < deadline):
            time.sleep(0.25)
            visible = browser.evaluate(SNAPSHOT)
            capture_stream(visible)
        report["checks"]["content_wait_seconds"] = round(time.monotonic() - content_wait_started, 2)
        report["checks"]["final_snapshot"] = {k: v for k, v in visible.items() if k != "inputValue"}
        if visible["productCount"] > 0:
            browser.evaluate("document.querySelector('.search-results').scrollIntoView({block:'center'})")
        browser.screenshot(run_dir / "results.png")
        report["screenshots"].append("results.png")
        if visible["runError"] or visible["errorPanel"] or not visible["runFinished"]:
            raise BrowserCheckError("run_did_not_finish_successfully")
        if visible["answerCount"] < 1 or visible["answerCharacters"] < 1:
            raise BrowserCheckError("answer_not_visible")
        if not visible["streamTextWhileRunning"] or visible["textUpdateCount"] < 2:
            raise BrowserCheckError("streamed_answer_not_observed")
        if visible["productCount"] < 1:
            raise BrowserCheckError("product_card_not_visible")

        browser.click(".search-results .product-card .detail-button")
        detail = browser.wait(SNAPSHOT, lambda s: isinstance(s, dict) and
                              s["dialogLabel"] is not None, 10, "product_detail")
        report["checks"]["detail"] = {"dialog_label_is_product_detail": "商品详情" in detail["dialogLabel"],
                                      "price_visible": detail["detailHasPrice"],
                                      "specifications_visible": detail["detailHasSpecs"],
                                      "sku_picker_visible": detail["skuPickerVisible"]}
        browser.screenshot(run_dir / "product-detail.png")
        report["screenshots"].append("product-detail.png")
        if not all(report["checks"]["detail"][key] for key in
                   ("dialog_label_is_product_detail", "price_visible", "specifications_visible")):
            raise BrowserCheckError("product_detail_incomplete")

        report["checks"]["ag_ui_requests"] = [browser.requests[i] for i in browser.run_request_ids]
        if len(browser.run_request_ids) != 1:
            raise BrowserCheckError("ag_ui_post_count_not_one")
        request = browser.requests[browser.run_request_ids[0]]
        if request["status"] != 200 or request["mime_type"] != "text/event-stream":
            raise BrowserCheckError("ag_ui_sse_response_invalid")
        if browser.page_errors > baseline_page_errors:
            raise BrowserCheckError("page_exception_after_submission")
        report["status"] = "passed"
    except Exception as error:  # noqa: BLE001
        report["error"] = {"type": type(error).__name__, "code": str(error)[:200]}
        if browser is not None:
            try:
                browser.screenshot(run_dir / "failure.png")
                report["screenshots"].append("failure.png")
            except Exception:  # noqa: BLE001
                report["failure_screenshot_unavailable"] = True
    finally:
        if browser is not None:
            report["ag_ui_post_count"] = len(browser.run_request_ids)
            report["ag_ui_requests"] = [browser.requests[i] for i in browser.run_request_ids]
            report["page_error_count"] = browser.page_errors
            report["console_error_count"] = browser.console_errors
            try:
                browser.call("Browser.close", timeout=3)
            except Exception:  # noqa: BLE001
                pass
            browser.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if temp is not None:
            try:
                remove_profile(temp)
            except Exception as error:  # noqa: BLE001
                report["status"] = "failed"
                report["profile_cleanup_error"] = type(error).__name__
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "evidence_dir": str(run_dir),
                          "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
