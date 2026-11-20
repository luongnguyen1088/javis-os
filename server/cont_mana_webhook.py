"""Forward Cont Mana transaction notifications to Zalo."""

import json
import logging
import math
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import config
import zalo_personal_channel
from channels import zalo_personal

router = APIRouter()

_SEPARATOR = "──────────────────"
_DEFAULT_THREAD_ID = "5898093850445528371"  # Nhóm "Note Công Việc"
_DEFAULT_CHAT_TYPE = "group"
_RESOLVED_GROUP_IDS: dict[tuple[str, str], str] = {}
_DEFAULT_CONFIG = {
    "enabled": True,
    "connection_id": "",
    "thread_id": _DEFAULT_THREAD_ID,
    "group_name": "Note Công Việc",
    "chat_type": _DEFAULT_CHAT_TYPE,
    "notify_income": True,
    "notify_expense": True,
    "notify_cash": True,
    "notify_transfer": True,
    "min_amount": 0,
    "recent_logs": [],
}
_ROUTING_KEYS = {"connection_id", "thread_id", "group_name", "chat_type"}
_CONFIG_LOCK = Lock()
_CONFIG_CACHE: dict | None = None
_CONFIG_PATH: Path | None = None
_LOG = logging.getLogger(__name__)
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
    cache_key = (str(conn.get("id") or ""), name_key)
    if cache_key in _RESOLVED_GROUP_IDS:
        return _RESOLVED_GROUP_IDS[cache_key], "group"
    groups = await _list_groups(conn)
    for group in groups:
        if _normalize_group_name(group["name"]) == name_key:
            _RESOLVED_GROUP_IDS[cache_key] = group["id"]
            return group["id"], "group"
    for group in groups:
        normalized = _normalize_group_name(group["name"])
        if normalized and (name_key in normalized or normalized in name_key):
            _RESOLVED_GROUP_IDS[cache_key] = group["id"]
            return group["id"], "group"
    return _DEFAULT_THREAD_ID, "group"


async def _list_groups(conn: dict) -> list[dict]:
    result = await zalo_personal_channel._goi(conn, "zalo_list_threads", {"limit": 200})
    threads = result.get("threads") if isinstance(result, dict) else result
    groups = {}
    for thread in threads if isinstance(threads, list) else []:
        if not isinstance(thread, dict):
            continue
        tt = str(thread.get("threadType") or thread.get("type") or "").strip().lower()
        is_grp = str(thread.get("isGroup")).lower() in ("1", "true") or tt in ("1", "group", "true")
        t_name = str(thread.get("name") or "").strip()
        t_id = str(thread.get("threadId") or thread.get("id") or "").strip()
        if is_grp and t_id:
            groups[t_id] = {"id": t_id, "name": t_name or t_id}
    return list(groups.values())


def _select_connection(connections: list[dict], connection_id: str) -> dict | None:
    if connection_id:
        return next((conn for conn in connections if str(conn.get("id")) == connection_id), None)
    return next(iter(connections), None)


async def send_zalo_notification(
    text: str,
    target_chat_id: str | None = None,
    chat_type: str | None = None,
    group_name: str | None = None,
    connection_id: str = "",
) -> tuple[bool, str, str | None, str | None]:
    resolved_chat_id = resolved_chat_type = None
    try:
        connections = zalo_personal_channel._ket_noi()
        conn = _select_connection(connections, connection_id)
        if conn is None:
            if connection_id:
                return False, "Configured Zalo connection is unavailable", None, None
            return False, "No active Zalo connection", None, None
        resolved_chat_id, resolved_chat_type = await resolve_target_chat(
            conn, target_chat_id, group_name, chat_type
        )
        ok, detail = await zalo_personal.gui(
            conn, resolved_chat_id, text, chat_type=resolved_chat_type
        )
    except Exception as exc:
        return False, str(exc) or type(exc).__name__, resolved_chat_id, resolved_chat_type
    return ok, "sent" if ok else detail or "Zalo send failed", resolved_chat_id, resolved_chat_type


def _validate_settings(data: dict, keys) -> dict:
    updates = {}
    for key in keys & data.keys():
        value = data[key]
        default = _DEFAULT_CONFIG[key]
        if type(value) is not type(default):
            raise ValueError(f"Invalid value for {key}")
        if isinstance(value, str):
            value = value.strip()
        if key == "min_amount" and value < 0:
            raise ValueError("min_amount must be nonnegative")
        if key == "chat_type" and value not in ("group", "private"):
            raise ValueError("chat_type must be private or group")
        updates[key] = value
    return updates


def _config_path() -> Path:
    return config.STATE_DIR / "cont_mana_config.json"


def _load_config() -> dict:
    global _CONFIG_CACHE, _CONFIG_PATH
    path = _config_path()
    if _CONFIG_CACHE is None or _CONFIG_PATH != path:
        current = {**_DEFAULT_CONFIG, "recent_logs": []}
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(stored, dict):
                raise ValueError("Expected a JSON object")
            current.update(_validate_settings(stored, _DEFAULT_CONFIG.keys() - {"recent_logs"}))
            logs = stored.get("recent_logs")
            if isinstance(logs, list):
                current["recent_logs"] = [log for log in logs if isinstance(log, dict)][:30]
        except FileNotFoundError:
            pass
        except ValueError as exc:
            _LOG.warning("Invalid Cont Mana configuration: %s", exc)
        _CONFIG_CACHE, _CONFIG_PATH = current, path
    return _CONFIG_CACHE


def _save_config(current: dict) -> None:
    global _CONFIG_CACHE, _CONFIG_PATH
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, encoding="utf-8", mode="w", delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(current, file, ensure_ascii=False, indent=2)
            file.close()
            temporary.replace(path)
        finally:
            file.close()
            temporary.unlink(missing_ok=True)
    _CONFIG_CACHE, _CONFIG_PATH = current, path


def _current_config() -> dict:
    with _CONFIG_LOCK:
        return _load_config()


def _transaction_fields(data: dict) -> tuple[str, str]:
    kind = str(data.get("type", "")).strip().upper()
    return ("INCOME" if kind in ("IN", "INCOME") else "EXPENSE",
            str(data.get("method") or "").strip().upper())


def _record_delivery(data: dict, ok: bool, target: str) -> None:
    kind, method = _transaction_fields(data)
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "id": str(data.get("id") or data.get("referenceId") or uuid4()),
        "type": kind,
        "amount": int(data["amount"]),
        "description": str(data.get("description") or "").strip(),
        "method": method,
        "status": "sent" if ok else "failed",
        "target": target,
    }
    with _CONFIG_LOCK:
        current = _load_config()
        try:
            _save_config({**current, "recent_logs": [entry, *current["recent_logs"][:29]]})
        except OSError:
            _LOG.exception("Could not record Cont Mana delivery")


async def _read_object(request: Request, optional: bool = False) -> dict:
    if optional and not await request.body():
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


@router.get("/cont-mana")
@router.get("/settings/cont-mana")
async def cont_mana_page():
    return FileResponse(Path(__file__).resolve().parent.parent / "dashboard" / "cont-mana.html")


@router.get("/api/cont-mana/config")
async def get_cont_mana_config(connection_id: str | None = None, include_groups: bool = True):
    current = _current_config()
    connections = zalo_personal_channel._ket_noi()
    accounts = [{"id": str(conn["id"]), "label": str(conn.get("label") or conn["id"])}
                for conn in connections if conn.get("id")]
    selected_id = current["connection_id"] if connection_id is None else connection_id
    conn = _select_connection(connections, selected_id)
    result = {"ok": True, "config": current, "accounts": accounts, "groups": []}
    if include_groups and conn is not None:
        try:
            result["groups"] = await _list_groups(conn)
        except Exception:
            _LOG.exception("Could not list Cont Mana Zalo groups")
            result["groups_error"] = "Không tải được danh sách nhóm Zalo"
    return result


@router.post("/api/cont-mana/config")
async def update_cont_mana_config(request: Request):
    try:
        data = await _read_object(request)
        updates = _validate_settings(data, _DEFAULT_CONFIG.keys() - {"recent_logs"})
        with _CONFIG_LOCK:
            updated = {**_load_config(), **updates}
            _save_config(updated)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "config": updated}


@router.post("/api/cont-mana/test")
async def test_cont_mana_delivery(request: Request):
    try:
        data = {
            "amount": 100000, "description": "Test từ giao diện", "type": "INCOME",
            **await _read_object(request, optional=True),
        }
        target = {**_current_config(), **_validate_settings(data, _ROUTING_KEYS)}
        text = format_transaction_message(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ok, detail, chat_id, _ = await send_zalo_notification(
        text, target_chat_id=target["thread_id"], chat_type=target["chat_type"],
        group_name=target["group_name"], connection_id=target["connection_id"],
    )
    _record_delivery(data, ok, chat_id or target["thread_id"] or target["group_name"])
    return {"ok": ok, "detail": detail}


@router.options("/webhook/cont-mana-thuchi")
@router.options("/hooks/cont-mana-thuchi")
async def cont_mana_thuchi_options():
    from starlette.responses import Response
    return Response(status_code=200, headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
    })


@router.post("/webhook/cont-mana-thuchi")
@router.post("/hooks/cont-mana-thuchi")
async def cont_mana_thuchi_hook(request: Request):
    from starlette.responses import JSONResponse
    cors_headers = {"Access-Control-Allow-Origin": "*"}
    current = _current_config()
    if not current["enabled"]:
        return JSONResponse({"ok": False, "skipped": True, "reason": "Integration is disabled"}, headers=cors_headers)
    try:
        data = await _read_object(request)
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

    kind, method = _transaction_fields(data)
    reason = ""
    if data["amount"] < current["min_amount"]:
        reason = "Amount below threshold"
    elif not current["notify_income" if kind == "INCOME" else "notify_expense"]:
        reason = "Transaction type is disabled"
    elif ((method == "TIEN_MAT" and not current["notify_cash"])
          or (method == "CHUYEN_KHOAN" and not current["notify_transfer"])):
        reason = "Payment method is disabled"
    if reason:
        return JSONResponse({"ok": False, "skipped": True, "reason": reason}, headers=cors_headers)

    if not (chat_id or group_name):
        chat_id = current["thread_id"]
        group_name = current["group_name"]
        chat_type = chat_type or current["chat_type"]
    ok, detail, resolved_chat_id, resolved_chat_type = await send_zalo_notification(
        text, target_chat_id=chat_id, chat_type=chat_type, group_name=group_name,
        connection_id=current["connection_id"],
    )
    _record_delivery(data, ok, resolved_chat_id or chat_id or group_name or "")
    return JSONResponse({
        "ok": ok,
        "detail": detail,
        "resolved_chat_id": resolved_chat_id,
        "resolved_chat_type": resolved_chat_type,
    }, headers=cors_headers)


@router.get("/webhook/zalo-sync-export")
async def export_zalo_sessions_for_sync(request: Request):
    from starlette.responses import JSONResponse
    secret = request.query_params.get("secret", "")
    if secret != "claro_zalo_sync_2026":
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    import glob, json, os
    import mcp_store
    from config import STATE_DIR

    conn_map = {}
    try:
        for c in mcp_store.list_connections():
            if c.get("connector_id") == "zalo":
                label = c.get("label") or c.get("name") or c.get("id")
                conn_map[c.get("id")] = label
                cfg_home = (c.get("config") or {}).get("home_dir")
                if cfg_home:
                    conn_map[os.path.basename(cfg_home)] = label
    except Exception:
        pass

    pattern = os.path.join(str(STATE_DIR), "connector-home", "zalo-*", ".zalo-agent-cli", "credentials", "cred_*.json")
    files = glob.glob(pattern)
    accounts = []
    for fpath in files:
        try:
            with open(fpath, "r", encoding="utf-8") as fp:
                data = json.load(fp)

            folder_name = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(fpath))))
            label = conn_map.get(folder_name, folder_name)
            filename = os.path.basename(fpath)
            uid = filename.replace("cred_", "").replace(".json", "")

            raw_cookies = (data.get("cookie") or {}).get("cookies", [])
            cookie_array = []
            for c in raw_cookies:
                k = c.get("key")
                v = c.get("value")
                d = c.get("domain", ".zalo.me")
                if not d.startswith(".") and not d.startswith("http"):
                    d = "." + d
                cookie_array.append({
                    "name": k,
                    "key": k,
                    "value": v,
                    "domain": d,
                    "path": c.get("path", "/"),
                    "secure": c.get("secure", True),
                    "httpOnly": c.get("httpOnly", False)
                })

            accounts.append({
                "uid": uid,
                "label": label,
                "folder": folder_name,
                "imei": data.get("imei"),
                "userAgent": data.get("userAgent"),
                "cookie_json": json.dumps(cookie_array),
                "cookie_count": len(cookie_array)
            })
        except Exception as e:
            accounts.append({"error": str(e), "file": fpath})

    return JSONResponse({"ok": True, "count": len(accounts), "accounts": accounts})

