import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import cont_mana_webhook


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cont_mana_webhook.config, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(cont_mana_webhook.router)
    return TestClient(app)


@pytest.fixture
def zalo_connection(monkeypatch):
    conn = {"label": "test"}
    monkeypatch.setattr(cont_mana_webhook, "_RESOLVED_GROUP_IDS", {})
    monkeypatch.delenv("CONT_MANA_ZALO_THREAD_ID", raising=False)
    monkeypatch.delenv("CONT_MANA_ZALO_GROUP_NAME", raising=False)
    monkeypatch.delenv("CONT_MANA_ZALO_CHAT_TYPE", raising=False)
    monkeypatch.setattr(cont_mana_webhook.zalo_personal_channel, "_ket_noi", lambda: [conn])
    return conn


@pytest.mark.parametrize("thread_name", ["Note Công Việc", "NOTE  CONG VIEC", "Team - Note Công Việc"])
def test_send_to_matching_group(monkeypatch, zalo_connection, thread_name):
    list_threads = AsyncMock(return_value={"threads": [
        {"threadId": "dm-id", "name": "Note Công Việc", "threadType": "dm"},
        {"threadId": "group-id", "name": thread_name, "threadType": "group"},
    ]})
    monkeypatch.setattr(cont_mana_webhook.zalo_personal_channel, "_goi", list_threads)
    send = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(cont_mana_webhook.zalo_personal, "gui", send)

    assert asyncio.run(cont_mana_webhook.resolve_target_chat(
        zalo_connection, None, "Note Công Việc", None
    )) == ("group-id", "group")
    assert asyncio.run(cont_mana_webhook.send_zalo_notification("transaction")) == (
        True, "sent", "group-id", "group"
    )
    list_threads.assert_awaited_once_with(zalo_connection, "zalo_list_threads", {"limit": 200})
    send.assert_awaited_once_with(zalo_connection, "group-id", "transaction", chat_type="group")


def test_resolve_target_chat_falls_back_to_private(monkeypatch, zalo_connection):
    list_threads = AsyncMock(return_value=[
        {"id": "other-id", "name": "Other Group", "type": "group"},
        {"id": "dm-id", "name": "Note Công Việc", "type": "dm"},
    ])
    monkeypatch.setattr(cont_mana_webhook.zalo_personal_channel, "_goi", list_threads)

    assert asyncio.run(cont_mana_webhook.resolve_target_chat(
        zalo_connection, None, None, "group"
    )) == (cont_mana_webhook._DEFAULT_THREAD_ID, "group")
    assert cont_mana_webhook._RESOLVED_GROUP_IDS == {}


def test_resolve_target_chat_honors_configuration(monkeypatch, zalo_connection):
    monkeypatch.setenv("CONT_MANA_ZALO_GROUP_NAME", "Configured Group")
    list_threads = AsyncMock(return_value=[
        {"id": "configured-id", "name": "Configured Group", "type": "group"},
        {"id": "payload-id", "name": "Payload Group", "type": "group"},
    ])
    monkeypatch.setattr(cont_mana_webhook.zalo_personal_channel, "_goi", list_threads)
    resolve = cont_mana_webhook.resolve_target_chat

    assert asyncio.run(resolve(zalo_connection, None, None, None)) == ("configured-id", "group")
    assert asyncio.run(resolve(zalo_connection, None, "Payload Group", None)) == ("payload-id", "group")
    monkeypatch.setenv("CONT_MANA_ZALO_THREAD_ID", "env-id")
    monkeypatch.setenv("CONT_MANA_ZALO_CHAT_TYPE", "group")
    assert asyncio.run(resolve(zalo_connection, None, "Payload Group", None)) == ("env-id", "group")
    assert asyncio.run(resolve(zalo_connection, "explicit-id", None, "private")) == ("explicit-id", "private")
    assert list_threads.await_count == 2


@pytest.mark.parametrize("transaction_type", ["INCOME", "IN"])
def test_format_income(transaction_type):
    data = {
        "type": transaction_type,
        "amount": 1500000,
        "method": "TIEN_MAT",
        "categoryName": "Cược vỏ container",
        "walletName": "Quỹ Tiền Mặt VP",
        "description": "Thu cọc vỏ cont MSKU1234567",
        "referenceId": "PT-20261120-001",
        "creatorName": "Luong Nguyen",
        "date": "2026-11-20T11:30:00",
    }
    assert cont_mana_webhook.format_transaction_message(data).splitlines() == [
        "🔔 BIẾN ĐỘNG THU CHI - CONT MANA",
        "──────────────────",
        "Loại: 🟢 THU TIỀN (+1,500,000 đ)",
        "Phương thức: 💵 Tiền mặt",
        "Danh mục: Cược vỏ container",
        "Quỹ/TK: Quỹ Tiền Mặt VP",
        "Nội dung: Thu cọc vỏ cont MSKU1234567",
        "Mã tham chiếu: PT-20261120-001",
        "Người tạo: Luong Nguyen",
        "Thời gian: 2026-11-20T11:30:00",
        "──────────────────",
    ]


@pytest.mark.parametrize("transaction_type", ["EXPENSE", "OUT"])
def test_format_expense(transaction_type):
    data = {
        "type": transaction_type,
        "amount": 1500000,
        "method": "CHUYEN_KHOAN",
        "description": " ",
    }
    assert cont_mana_webhook.format_transaction_message(data).splitlines() == [
        "🔔 BIẾN ĐỘNG THU CHI - CONT MANA",
        "──────────────────",
        "Loại: 🔴 CHI TIỀN (-1,500,000 đ)",
        "Phương thức: 💳 Chuyển khoản",
        "──────────────────",
    ]


@pytest.mark.parametrize("ok, detail", [(True, "sent"), (False, "Zalo unavailable")])
def test_cont_mana_hook(monkeypatch, ok, detail):
    from main import app

    send = AsyncMock(return_value=(ok, detail, "123456789", "group"))
    monkeypatch.setattr(cont_mana_webhook, "send_zalo_notification", send)
    data = {
        "id": "test-transaction",
        "type": "INCOME",
        "amount": 1500000,
        "chatId": "123456789",
        "chatType": "group",
        "groupName": "Custom Group",
    }
    response = TestClient(app, base_url="http://localhost").post(
        "/hooks/cont-mana-thuchi", json=data
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": ok,
        "detail": detail,
        "resolved_chat_id": "123456789",
        "resolved_chat_type": "group",
    }
    send.assert_awaited_once_with(
        cont_mana_webhook.format_transaction_message(data),
        target_chat_id="123456789",
        chat_type="group",
        group_name="Custom Group",
        connection_id="",
    )
    entry = cont_mana_webhook._current_config()["recent_logs"][0]
    assert entry["status"] == ("sent" if ok else "failed")
    assert entry["target"] == "123456789"


def test_get_config_lists_groups_for_selected_account(client, monkeypatch):
    accounts = [{"id": "first", "label": "Personal"}, {"id": "second", "label": "Office", "env": {}}]
    monkeypatch.setattr(cont_mana_webhook.zalo_personal_channel, "_ket_noi", lambda: accounts)
    list_threads = AsyncMock(return_value={"threads": [
        {"threadId": "note", "name": "Note Công Việc", "threadType": "group"},
        {"threadId": "dm", "name": "Private", "isGroup": "false"},
    ]})
    monkeypatch.setattr(cont_mana_webhook.zalo_personal_channel, "_goi", list_threads)
    response = client.get("/api/cont-mana/config")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True, "config": cont_mana_webhook._DEFAULT_CONFIG,
        "accounts": [{"id": "first", "label": "Personal"}, {"id": "second", "label": "Office"}],
        "groups": [{"id": "note", "name": "Note Công Việc"}],
    }
    list_threads.assert_awaited_once_with(accounts[0], "zalo_list_threads", {"limit": 200})
    client.post("/api/cont-mana/config", json={"connection_id": "second"})
    client.get("/api/cont-mana/config")
    list_threads.assert_awaited_with(accounts[1], "zalo_list_threads", {"limit": 200})
    assert client.get("/api/cont-mana/config?include_groups=false").json()["groups"] == []
    assert list_threads.await_count == 2


def test_post_config_persists_partial_updates(client, isolated_state, monkeypatch):
    updates = {"connection_id": " office ", "min_amount": 5000, "notify_expense": False}
    response = client.post("/api/cont-mana/config", json={**updates, "recent_logs": [{}]})
    assert response.status_code == 200
    current = {**cont_mana_webhook._DEFAULT_CONFIG, **updates, "connection_id": "office"}
    assert response.json() == {"ok": True, "config": current}
    path = isolated_state / "cont_mana_config.json"
    assert json.loads(path.read_text(encoding="utf-8")) == current
    monkeypatch.setattr(cont_mana_webhook, "_CONFIG_CACHE", None)
    assert client.get("/api/cont-mana/config?include_groups=false").json()["config"] == current


@pytest.mark.parametrize("settings", [
    [], {"enabled": "false"}, {"min_amount": -1}, {"min_amount": True},
    {"min_amount": 0.5}, {"thread_id": 123}, {"chat_type": "invalid"},
])
def test_post_config_rejects_invalid_settings(client, settings, isolated_state):
    response = client.post("/api/cont-mana/config", json=settings)
    assert response.status_code == 400
    assert not (isolated_state / "cont_mana_config.json").exists()


@pytest.mark.parametrize("settings, transaction, reason", [
    ({"enabled": False}, {}, "Integration is disabled"),
    ({"min_amount": 100001}, {}, "Amount below threshold"),
    ({"notify_income": False}, {"type": " in "}, "Transaction type is disabled"),
    ({"notify_expense": False}, {"type": "out"}, "Transaction type is disabled"),
    ({"notify_cash": False}, {"method": " tien_mat "}, "Payment method is disabled"),
    ({"notify_transfer": False}, {"method": "CHUYEN_KHOAN"}, "Payment method is disabled"),
])
def test_webhook_filters_transactions(client, monkeypatch, settings, transaction, reason):
    send = AsyncMock()
    monkeypatch.setattr(cont_mana_webhook, "send_zalo_notification", send)
    client.post("/api/cont-mana/config", json=settings)
    response = client.post("/hooks/cont-mana-thuchi", json={
        "type": "INCOME", "amount": 100000, **transaction,
    })
    assert response.json() == {"ok": False, "skipped": True, "reason": reason}
    send.assert_not_awaited()
    assert client.get("/api/cont-mana/config?include_groups=false").json()["config"]["recent_logs"] == []


def test_configured_delivery_and_test_message_keep_latest_logs(client, monkeypatch):
    accounts = [{"id": "first"}, {"id": "selected"}]
    monkeypatch.setattr(cont_mana_webhook.zalo_personal_channel, "_ket_noi", lambda: accounts)
    send = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(cont_mana_webhook.zalo_personal, "gui", send)
    client.post("/api/cont-mana/config", json={
        "connection_id": "selected", "thread_id": "note", "min_amount": 100000,
    })
    for index in range(30):
        response = client.post("/webhook/cont-mana-thuchi", json={
            "id": str(index), "type": "OUT", "amount": 100000,
            "method": "TIEN_MAT", "description": "Delivery",
        })
        assert response.json()["ok"]
    assert send.await_args.args[:2] == (accounts[1], "note")

    client.post("/api/cont-mana/config", json={"enabled": False})
    assert client.post("/api/cont-mana/test").json() == {"ok": True, "detail": "sent"}
    assert send.await_args.args[:2] == (accounts[1], "note")
    response = client.post("/api/cont-mana/test", json={"connection_id": "first", "thread_id": "draft"})
    assert response.json() == {"ok": True, "detail": "sent"}
    assert send.await_args.args[:2] == (accounts[0], "draft")
    current = client.get("/api/cont-mana/config?include_groups=false").json()["config"]
    assert current["connection_id"] == "selected"
    logs = current["recent_logs"]
    assert len(logs) == 30
    assert logs[0]["description"] == "Test từ giao diện"
    assert logs[0]["target"] == "draft"
    assert logs[2] == {
        "time": logs[2]["time"], "id": "29", "type": "EXPENSE", "amount": 100000,
        "description": "Delivery", "method": "TIEN_MAT", "status": "sent", "target": "note",
    }
    assert logs[-1]["id"] == "2"


def test_public_pages_and_protected_apis(monkeypatch):
    from main import app

    monkeypatch.setattr(cont_mana_webhook.config, "gate_active", lambda: True)
    monkeypatch.setattr(cont_mana_webhook.config, "valid_session", lambda session: session == "valid")
    client = TestClient(app, base_url="http://localhost")
    for path in ("/cont-mana", "/settings/cont-mana"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Cont Mana Integration" in response.text
    assert client.get("/api/cont-mana/config").status_code == 401
    assert client.post("/api/cont-mana/config", json={}).status_code == 401
    assert client.post("/api/cont-mana/test").status_code == 401
    client.cookies.set("javis_session", "valid")
    assert client.get("/api/cont-mana/config?include_groups=false").status_code == 200
