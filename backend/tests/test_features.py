"""New feature contracts. Uses an isolated DB and a mock AI transport only."""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("SECRET_KEY", "test-feature-secret-32-characters-not-production")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db, User, UserStatus, Server, Account, ServerTag, ServerInterface, Switch
from app.api import ai, summary, server
from app.api.auth import create_access_token
from app.errors import install_error_handlers


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        admin = User(username="featureadmin", realname="Admin", account_name="featureadmin", mail="a@example.com", password="do-not-send-password", public_key="do-not-send-public-key", is_admin=True, status=UserStatus.ACTIVE)
        member = User(username="featureuser", realname="Member", account_name="featureuser", mail="u@example.com", password="secret", public_key="secret", status=UserStatus.ACTIVE)
        srv = Server(host="gpu-lab", port=22, ipmi="do-not-send-ipmi")
        db.add_all([admin, member, srv])
        db.flush()
        db.add_all([Account(user=admin, server=srv, last_login_date=datetime.now()-timedelta(days=31)), ServerTag(server=srv, tag="research"), ServerInterface(server=srv, interface="eth0", manufacturer="Mellanox", pci_address="0000:01:00.0"), Switch(name="lab-switch", num_row=2, num_col=8)])
        db.commit()
        app = FastAPI()
        install_error_handlers(app)
        for name, module in [("ai", ai), ("summary", summary), ("server", server)]:
            app.include_router(module.router, prefix="/"+name)
        def override_db():
            yield db
        app.dependency_overrides[get_db] = override_db
        @app.get("/crash")
        def crash():
            raise RuntimeError("do-not-show-internal-secret")
        with TestClient(app, raise_server_exceptions=False) as client:
            def login(user=member):
                client.cookies.clear()
                client.cookies.set("access_token", create_access_token({"sub":user.username}, timedelta(minutes=5)))
            yield db, client, admin, member, srv, login
    engine.dispose()


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "https://ai.invalid/v1/")
    monkeypatch.setenv("AI_MODEL", "mock-model")
    monkeypatch.setenv("AI_API_KEY", "do-not-show-api-key")
    monkeypatch.setenv("AI_TIMEOUT_SECONDS", "45")


def mock_ai(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def test_ai_auth_and_unconfigured(setup, monkeypatch):
    _, client, _, _, _, login = setup
    assert client.get("/ai/status").status_code == 401
    assert client.post("/ai/chat", json={"messages":[{"role":"user","content":"设备？"}]}).status_code == 401
    login()
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    response = client.get("/ai/status")
    assert response.json()["enabled"] is False
    response = client.post("/ai/chat", json={"messages":[{"role":"user","content":"设备？"}]})
    assert response.status_code == 503
    assert "AI_BASE_URL" in response.json()["detail"]


def test_chat_uses_real_allowlisted_inventory(setup, configured, monkeypatch):
    _, client, _, _, _, login = setup
    login()
    import json
    def handler(request):
        assert str(request.url) == "https://ai.invalid/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer do-not-show-api-key"
        payload = json.loads(request.content)
        prompt = payload["messages"][0]["content"]
        assert "gpu-lab" in prompt and "Mellanox" in prompt and "lab-switch" in prompt
        assert "do-not-send" not in prompt and "featureadmin" not in prompt
        assert payload["model"] == "mock-model"
        assert payload["messages"][-1]["role"] == "user"
        return httpx.Response(200, json={"choices":[{"message":{"content":"登记设备：gpu-lab (ID 1)。"}}]})
    mock_ai(monkeypatch, handler)
    response = client.post("/ai/chat", json={"messages":[{"role":"user","content":"我们组有哪些设备？"}]})
    assert response.status_code == 200
    assert "gpu-lab" in response.json()["answer"]
    assert "api-key" not in response.text


@pytest.mark.parametrize("status,reason", [(401,"AI_API_KEY"),(404,"接口地址"),(429,"限流"),(500,"暂时异常")])
def test_upstream_errors_are_clear_and_safe(setup, configured, monkeypatch, status, reason):
    _, client, _, _, _, login = setup
    login()
    mock_ai(monkeypatch, lambda request: httpx.Response(status, text="do-not-show-upstream-secret"))
    response = client.post("/ai/chat", json={"messages":[{"role":"user","content":"hi"}]})
    assert response.status_code == 502
    assert reason in response.json()["detail"]
    assert "do-not-show" not in response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_ai_timeout_invalid_response_and_recovery(setup, configured, monkeypatch):
    _, client, _, member, _, login = setup
    login()
    def timeout(request):
        raise httpx.ReadTimeout("secret", request=request)
    mock_ai(monkeypatch, timeout)
    data = {"messages":[{"role":"user","content":"hi"}]}
    assert client.post("/ai/chat", json=data).status_code == 504
    assert member.id not in ai._active_users


def test_invalid_ai_response(setup, configured, monkeypatch):
    _, client, _, _, _, login = setup
    login()
    mock_ai(monkeypatch, lambda request: httpx.Response(200, json={"choices":[]}))
    response = client.post("/ai/chat", json={"messages":[{"role":"user","content":"hi"}]})
    assert response.status_code == 502 and "无效" in response.json()["detail"]


@pytest.mark.parametrize("messages", [[], [{"role":"system","content":"bad"}], [{"role":"user","content":"  "}], [{"role":"assistant","content":"hi"}], [{"role":"user","content":"a"*4001}]])
def test_chat_validation(setup, messages):
    _, client, _, _, _, login = setup
    login()
    response = client.post("/ai/chat", json={"messages":messages})
    assert response.status_code == 422
    assert all("input" not in error for error in response.json()["detail"])


def test_inventory_limit_and_busy(setup, configured, monkeypatch):
    _, client, _, member, _, login = setup
    login()
    data = {"messages":[{"role":"user","content":"hi"}]}
    ai._active_users.add(member.id)
    try:
        assert client.post("/ai/chat", json=data).status_code == 429
    finally:
        ai._active_users.discard(member.id)
    monkeypatch.setattr(ai, "inventory_snapshot", lambda db: {"oversize":"a"*60001})
    assert client.post("/ai/chat", json=data).status_code == 413


def test_summary_admin_flag_and_server_retention(setup):
    _, client, admin, _, _, login = setup
    login()
    response = client.get("/summary/get")
    assert response.status_code == 200
    account = response.json()[0]["users"][0]
    assert account["isAdmin"] is True
    assert "T" in account["lastLogin"]
    assert client.get("/summary/sync-errors").status_code == 403
    login(admin)
    assert client.get("/summary/sync-errors").status_code == 200


def test_server_validation_and_disabled_refresh(setup, monkeypatch):
    _, client, admin, _, srv, login = setup
    login(admin)
    assert client.post("/server/add", json={"host":"host","port":0}).status_code == 422
    assert client.post("/server/add", json={"host":"host\nProxyCommand bad","port":22}).status_code == 422
    assert client.post("/server/add", json={"host":"host","port":22,"proxyServerId":999}).status_code == 404
    assert client.post("/server/add", json={"host":srv.host,"port":srv.port}).status_code == 409
    monkeypatch.setenv("SYNC_ENABLED", "false")
    response = client.post("/server/refresh", json={"server_id":srv.id})
    assert response.status_code == 503 and "SYNC_ENABLED" in response.json()["detail"]


def test_unexpected_error_has_safe_reason_and_id(setup):
    _, client, *_ = setup
    response = client.get("/crash")
    assert response.status_code == 500
    assert "do-not-show" not in response.text
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_long_assistant_response_can_be_sent_as_history(setup, configured, monkeypatch):
    _, client, _, _, _, login = setup
    login()
    mock_ai(monkeypatch, lambda request: httpx.Response(200, json={"choices":[{"message":{"content":"ok"}}]}))
    response = client.post("/ai/chat", json={"messages":[
        {"role":"user","content":"list"}, {"role":"assistant","content":"a"*5000}, {"role":"user","content":"more"}
    ]})
    assert response.status_code == 200


def test_sync_diagnostics_are_actionable_without_echoing_stderr():
    from sync_errors import command_failure
    from types import SimpleNamespace
    import account_sync
    error = command_failure("sudo tee /private/path", SimpleNamespace(exit_status=1, stderr="sudo: a password is required do-not-expose-secret"))
    assert "sudo" in error.safe_message and "密码" in error.safe_message
    assert "do-not-expose-secret" not in error.safe_message
    account_sync._record_error("account", 998, "Account synchronization failed", error)
    try:
        messages = [item["message"] for item in account_sync.get_sync_errors() if item["id"] == 998]
        assert any("密码" in message for message in messages)
        assert not any("do-not-expose" in message for message in messages)
    finally:
        account_sync._clear_error("account", 998)
