"""统一错误结构与 trace_id 贯穿。

这是 S0-5 最重要的两个不变量：
1. 任何错误（业务异常 / 校验失败 / 路由不存在 / 未捕获崩溃）都是同一个壳，且带 trace_id；
2. 未捕获崩溃**不泄露堆栈**，用户凭 trace_id 找开发定位。
"""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, get_trace_id, reset_trace_id, set_trace_id


def _assert_envelope(body: dict, expected_code: int) -> None:
    assert set(body) == {"code", "message", "data", "trace_id"}
    assert body["code"] == expected_code
    assert body["data"] is None
    assert body["trace_id"] and body["trace_id"] != "-"


def test_unknown_route_returns_404_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/definitely-not-exists")
    assert response.status_code == 404
    body = response.json()
    _assert_envelope(body, 40400)
    assert body["message"] == "资源不存在"


def test_app_error_is_mapped_to_its_status_and_code(client: TestClient) -> None:
    response = client.get("/api/v1/_test/boom-app-error")
    assert response.status_code == 404
    _assert_envelope(response.json(), 40400)
    assert response.json()["message"] == "测试用的业务异常"


def test_validation_error_returns_422_and_does_not_echo_payload(client: TestClient) -> None:
    response = client.get("/api/v1/_test/validation/not-an-int")
    assert response.status_code == 422
    body = response.json()
    _assert_envelope(body, 42200)
    assert "item_id" in body["message"]


def test_unhandled_exception_hides_traceback(client: TestClient) -> None:
    response = client.get("/api/v1/_test/boom-crash")
    assert response.status_code == 500
    body = response.json()
    _assert_envelope(body, 50000)
    assert body["message"] == "服务器内部错误"
    for leaked in ("Traceback", "RuntimeError", "boom-crash", "File \""):
        assert leaked not in response.text


def test_trace_id_is_echoed_when_provided(client: TestClient) -> None:
    given = "abc123def456"
    response = client.get("/api/v1/health", headers={"X-Trace-Id": given})
    assert response.headers["X-Trace-Id"] == given
    assert response.json()["trace_id"] == given


def test_invalid_trace_id_is_replaced(client: TestClient) -> None:
    """过长或含非法字符的 trace_id 一律换新，避免日志注入与超长 header。"""
    for bad in ("x" * 65, "has space", "inject\nnewline", "<script>"):
        response = client.get("/api/v1/health", headers={"X-Trace-Id": bad})
        trace_id = response.headers["X-Trace-Id"]
        assert trace_id != bad
        assert len(trace_id) == 32  # uuid4().hex
        assert trace_id.isalnum()


def test_context_var_is_reset_after_request(client: TestClient) -> None:
    """请求结束后 trace_id 必须复位，否则会串到下一个请求（尤其线程池复用场景）。"""
    assert get_trace_id() == "-"
    client.get("/api/v1/health", headers={"X-Trace-Id": "first-request"})
    assert get_trace_id() == "-"


def test_json_formatter_includes_trace_id() -> None:
    token = set_trace_id("trace-in-log")
    try:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="hello",
            args=(),
            exc_info=None,
        )
        record.event = "unit_test"
        record.duration_ms = 1.5
        payload = JsonFormatter().format(record)
    finally:
        reset_trace_id(token)
    assert '"trace_id": "trace-in-log"' in payload
    assert '"event": "unit_test"' in payload
    assert '"duration_ms": 1.5' in payload