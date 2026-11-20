import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import cont_mana_webhook


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
    )
