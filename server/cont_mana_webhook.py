"""Forward Cont Mana transaction notifications to Zalo."""

import math
import os
import unicodedata

from fastapi import APIRouter, HTTPException, Request

import zalo_personal_channel
from channels import zalo_personal

router = APIRouter()

_SEPARATOR = "──────────────────"
_DEFAULT_THREAD_ID = "1575127037194205234"
_RESOLVED_GROUP_IDS: dict[str, str] = {}
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


def _normalize_group_name(name: str) -> str:
    decomposed = unicodedata.normalize("NFD", name.casefold()).replace("đ", "d")
    return "".join(
        char for char in decomposed if not char.isspace() and not unicodedata.combining(char)
    )


async def resolve_target_chat(
    conn: dict, target_chat_id: str | None, group_name: str | None, chat_type: str | None
) -> tuple[str, str]:
    target_chat_id = target_chat_id or os.environ.get("CONT_MANA_ZALO_THREAD_ID")
    if target_chat_id:
        default_type = "group" if os.environ.get("CONT_MANA_ZALO_CHAT_TYPE") == "group" else "private"
        return target_chat_id, chat_type or default_type

    name = (group_name or "").strip() or os.environ.get("CONT_MANA_ZALO_GROUP_NAME", "").strip()
    name_key = _normalize_group_name(name or "Note Công Việc")
    if name_key in _RESOLVED_GROUP_IDS:
        return _RESOLVED_GROUP_IDS[name_key], "group"

    result = await zalo_personal_channel._goi(conn, "zalo_list_threads", {"limit": 200})
    threads = result.get("threads") if isinstance(result, dict) else (result if isinstance(result, list) else [])
    found_tid = None
    all_groups_debug = []
    for thread in threads or []:
        if not isinstance(thread, dict):
            continue
        tt = str(thread.get("threadType") or thread.get("type") or "").strip().lower()
        is_grp = bool(thread.get("isGroup")) or tt in ("1", "group", "true")
        t_name = str(thread.get("name") or "").strip()
        t_id = str(thread.get("threadId") or thread.get("id") or "").strip()
        if is_grp and t_id:
            all_groups_debug.append({"id": t_id, "name": t_name})
            norm_name = _normalize_group_name(t_name)
            if norm_name == name_key or name_key in norm_name or norm_name in name_key:
                found_tid = t_id
                break
    if found_tid is not None:
        _RESOLVED_GROUP_IDS[name_key] = found_tid
        return found_tid, "group"
    return _DEFAULT_THREAD_ID, f"not_found (available: {all_groups_debug[:15]})"


async def send_zalo_notification(
    text: str,
    target_chat_id: str | None = None,
    chat_type: str | None = None,
    group_name: str | None = None,
) -> tuple[bool, str, str | None, str | None]:
    resolved_chat_id = resolved_chat_type = None
    try:
        connections = zalo_personal_channel._ket_noi()
        if not connections:
            return False, "No active Zalo connection", None, None
        conn = connections[0]
        resolved_chat_id, resolved_chat_type = await resolve_target_chat(
            conn, target_chat_id, group_name, chat_type
        )
        ok, detail = await zalo_personal.gui(
            conn, resolved_chat_id, text, chat_type=resolved_chat_type
        )
    except Exception as exc:
        return False, str(exc) or type(exc).__name__, resolved_chat_id, resolved_chat_type
    return ok, "sent" if ok else detail or "Zalo send failed", resolved_chat_id, resolved_chat_type


@router.post("/webhook/cont-mana-thuchi")
@router.post("/hooks/cont-mana-thuchi")
async def cont_mana_thuchi_hook(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        text = format_transaction_message(data)
        chat_id = data.get("chatId")
        chat_type = data.get("chatType")
        group_name = data.get("groupName")
        if chat_id is not None and not isinstance(chat_id, str):
            raise ValueError("chatId must be a string")
        if chat_type not in (None, "", "private", "group"):
            raise ValueError("chatType must be private or group")
        if group_name is not None and not isinstance(group_name, str):
            raise ValueError("groupName must be a string")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ok, detail, resolved_chat_id, resolved_chat_type = await send_zalo_notification(
        text, target_chat_id=chat_id, chat_type=chat_type, group_name=group_name
    )
    return {
        "ok": ok,
        "detail": detail,
        "resolved_chat_id": resolved_chat_id,
        "resolved_chat_type": resolved_chat_type,
    }
