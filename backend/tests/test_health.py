"""健康检查：三依赖聚合、降级返回 503、两条路径同一实现。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.v1.health as health_module

_OK = {
    "db": {"ok": True, "detail": "ok"},
    "redis": {"ok": True, "detail": "ok"},
    "minio": {"ok": True, "detail": "ok"},
}


def _fake_checks(result: dict):
    def _run(settings, checkers=None):  # noqa: ANN001
        return result

    return _run


def test_health_all_healthy_returns_200(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(health_module, "run_checks", _fake_checks(_OK))
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "ok"
    assert set(body["data"]["checks"]) == {"db", "redis", "minio"}
    assert body["data"]["version"] == "0.1.0"
    assert body["data"]["ts"].endswith("+00:00")
    assert body["trace_id"] == response.headers["X-Trace-Id"]


def test_health_degraded_returns_503(client: TestClient, monkeypatch) -> None:
    degraded = {
        "db": {"ok": True, "detail": "ok"},
        "redis": {"ok": True, "detail": "ok"},
        "minio": {"ok": False, "detail": "bucket 不存在：study-ocr-test"},
    }
    monkeypatch.setattr(health_module, "run_checks", _fake_checks(degraded))
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    body = response.json()
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"]["minio"]["ok"] is False


@pytest.mark.parametrize("path", ["/health", "/api/v1/health"])
def test_health_available_on_both_paths(client: TestClient, monkeypatch, path: str) -> None:
    """/health 给容器探针、/api/v1/health 给前端；两者同一实现、同一结构。"""
    monkeypatch.setattr(health_module, "run_checks", _fake_checks(_OK))
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"


def test_health_checker_crash_does_not_break_endpoint(client: TestClient, monkeypatch) -> None:
    """单个检查器自己抛异常时，接口仍要返回结构化降级，而不是 500 崩掉。"""

    def _boom(settings, checkers=None):  # noqa: ANN001
        raise RuntimeError("checker 崩了")

    monkeypatch.setattr(health_module, "run_checks", _boom)
    response = client.get("/api/v1/health")
    # 探针自身故障也要给结构化降级（503），而不是 500 —— 容器编排只认状态码
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["status"] == "degraded"
    assert body["data"]["checks"]["internal"]["ok"] is False
    assert "RuntimeError" not in response.text