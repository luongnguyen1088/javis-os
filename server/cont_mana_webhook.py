"""Forward Cont Mana transaction notifications to Zalo."""

import math
import os

from fastapi import APIRouter, HTTPException, Request

import zalo_personal_channel
from channels import zalo_personal

router = APIRouter()

_SEPARATOR = "──────────────────"
_DEFAULT_THREAD_ID = "1575127037194205234"
_METHODS = {"TIEN_MAT": "💵 Tiền mặt", "CHUYEN_KHOAN": "💳 Chuyển khoản"}
_FIELDS = (
    ("categoryName", "Danh mục"),
    ("walletName", "Quỹ/TK"),
    ("description", "Nội dung"),
    ("referenceId", "Mã tham chiếu"),
    ("creatorName", "Người tạo"),
    ("date", "Thời gian"),
)


def format_transaction_message(data: dict) -> str:
    transaction_type = str(data.get("type", "")).strip().upper()
    if transaction_type not in {"INCOME", "IN", "EXPENSE", "OUT"}:
        raise ValueError("Invalid transaction type")
    amount = data.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        raise ValueError("Amount must be a number")
    if isinstance(amount, float) and not math.isfinite(amount):
        raise ValueError("Amount must be finite")

    income = transaction_type in {"INCOME", "IN"}
    label = "🟢 THU TIỀN" if income else "🔴 CHI TIỀN"
    sign = "+" if income else "-"
    lines = [
        "🔔 BIẾN ĐỘNG THU CHI - CONT MANA",
        _SEPARATOR,
        f"Loại: {label} ({sign}{abs(amount):,} đ)",
    ]
    method = str(data.get("method") or "").strip()
    if method:
        lines.append(f"Phương thức: {_METHODS.get(method.upper(), method)}")
    for key, field_label in _FIELDS:
        value = str(data.get(key) or "").strip()
        if value:
            lines.append(f"{field_label}: {value}")
    lines.append(_SEPARATOR)
    return "\n".join(lines)


async def send_zalo_notification(
    text: str, target_chat_id: str | None = None, chat_type: str | None = None
) -> tuple[bool, str]:
    target_chat_id = target_chat_id or os.environ.get("CONT_MANA_ZALO_THREAD_ID", _DEFAULT_THREAD_ID)
    chat_type = chat_type or ("group" if len(target_chat_id) > 18 else "private")
    try:
        connections = zalo_personal_channel._ket_noi()
        if not connections:
            return False, "No active Zalo connection"
        ok, detail = await zalo_personal.gui(
            connections[0], target_chat_id, text, chat_type=chat_type
        )
    except Exception as exc:
        return False, str(exc) or type(exc).__name__
    return (True, "sent") if ok else (False, detail or "Zalo send failed")


@router.post("/hooks/cont-mana-thuchi")
async def cont_mana_thuchi_hook(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        text = format_transaction_message(data)
        chat_id = data.get("chatId")
        chat_type = data.get("chatType")
        if chat_id is not None and not isinstance(chat_id, str):
            raise ValueError("chatId must be a string")
        if chat_type not in (None, "", "private", "group"):
            raise ValueError("chatType must be private or group")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ok, detail = await send_zalo_notification(text, target_chat_id=chat_id, chat_type=chat_type)
    return {"ok": ok, "detail": detail}
