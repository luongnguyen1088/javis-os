from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import cont_mana_webhook


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

    send = AsyncMock(return_value=(ok, detail))
    monkeypatch.setattr(cont_mana_webhook, "send_zalo_notification", send)
    data = {
        "id": "test-transaction",
        "type": "INCOME",
        "amount": 1500000,
        "chatId": "123456789",
        "chatType": "group",
    }
    response = TestClient(app, base_url="http://localhost").post(
        "/hooks/cont-mana-thuchi", json=data
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": ok, "detail": detail}
    send.assert_awaited_once_with(
        cont_mana_webhook.format_transaction_message(data),
        target_chat_id="123456789",
        chat_type="group",
    )
