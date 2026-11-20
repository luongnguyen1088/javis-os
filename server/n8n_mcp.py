"""Internal MCP tools for the n8n public API and webhook endpoints."""

import json
from urllib.parse import quote

import httpx


def _clip(text, max_chars=8000):
    text = str(text)
    if len(text) <= max_chars:
        return text
    marker = " ... [truncated] ... "
    if max_chars <= len(marker):
        return text[:max_chars]
    remaining = max_chars - len(marker)
    head = int(remaining * 0.6)
    return text[:head] + marker + text[-(remaining - head):]


def _t(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": list(required),
        },
    }


TOOLS = [
    _t("n8n_list_workflows", "List workflows with optional filters.", {
        "active": {"type": "boolean"},
        "tags": {"type": "string"},
        "limit": {"type": "integer"},
    }),
    _t("n8n_get_workflow", "Get workflow details by ID.", {
        "workflow_id": {"type": "string"},
    }, ("workflow_id",)),
    _t("n8n_activate_workflow", "Activate or deactivate a workflow.", {
        "workflow_id": {"type": "string"},
        "active": {"type": "boolean"},
    }, ("workflow_id", "active")),
    _t("n8n_trigger_webhook", "Trigger a workflow via its webhook.", {
        "webhook_url": {"type": "string", "description": "Full URL or endpoint path."},
        "path": {"type": "string", "description": "Webhook node path or /webhook/... endpoint."},
        "method": {"type": "string", "default": "POST"},
        "data": {"type": "object", "description": "JSON body, or query parameters for GET and HEAD."},
    }),
    _t("n8n_list_executions", "List recent executions with optional filters.", {
        "workflow_id": {"type": "string"},
        "status": {"type": "string", "enum": ["success", "error", "waiting"]},
        "limit": {"type": "integer"},
    }),
    _t("n8n_get_execution", "Get execution details by ID.", {
        "execution_id": {"type": "string"},
    }, ("execution_id",)),
]
TOOLS[3]["inputSchema"]["anyOf"] = [{"required": ["webhook_url"]}, {"required": ["path"]}]

_API_ROUTES = {
    "n8n_list_workflows": ("GET", "/workflows"),
    "n8n_get_workflow": ("GET", "/workflows/{workflow_id}"),
    "n8n_activate_workflow": ("POST", "/workflows/{workflow_id}/{action}"),
    "n8n_list_executions": ("GET", "/executions"),
    "n8n_get_execution": ("GET", "/executions/{execution_id}"),
}

_QUERY_FIELDS = {
    "n8n_list_workflows": {"active": "active", "tags": "tags", "limit": "limit"},
    "n8n_list_executions": {"workflow_id": "workflowId", "status": "status", "limit": "limit"},
}


def _result(text, *, error=False):
    result = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return result


def _identifier(args, key):
    value = str(args.get(key) or "").strip()
    if not value or value in {".", ".."}:
        raise ValueError(f"A valid {key} is required.")
    return quote(value, safe="")


def _http_url(value):
    url = httpx.URL(value)
    if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
        raise ValueError("Expected an HTTP(S) URL without embedded credentials.")
    return url


def _webhook_request(base, args):
    endpoint = (args.get("webhook_url") or "").strip()
    if not endpoint:
        path = (args.get("path") or "").strip()
        if not path:
            raise ValueError("webhook_url or path is required.")
        endpoint = path
        if not path.startswith(("/", "webhook/", "webhook-test/")):
            endpoint = "webhook/" + path

    if "://" in endpoint:
        url = _http_url(endpoint)
    else:
        url = _http_url(f"{base}/{endpoint.lstrip('/')}")
    method = (args.get("method") or "POST").strip().upper()
    data = args.get("data")
    if data is not None and not isinstance(data, dict):
        raise ValueError("data must be an object.")
    if method in {"GET", "HEAD"}:
        return method, url.copy_merge_params(data or {}), {}
    return method, url, {"json": data} if data is not None else {}


async def list_tools(spec=None) -> list[dict]:
    return TOOLS


async def call(name: str, arguments: dict, spec: dict) -> dict:
    args = arguments or {}
    secrets = (spec or {}).get("secrets") or {}
    base = str(secrets.get("instance_url") or "").strip().rstrip("/")
    api_key = str(secrets.get("api_key") or "").strip()
    if name not in _API_ROUTES and name != "n8n_trigger_webhook":
        return _result(f"Unknown n8n tool: {_clip(name, 200)}", error=True)
    if not base or not api_key:
        return _result("n8n requires instance_url and api_key.", error=True)
    if "://" not in base:
        base = "https://" + base

    try:
        base_url = _http_url(base)
        if base_url.query or base_url.fragment:
            raise ValueError("instance_url must be a base URL without a query or fragment.")

        if name == "n8n_trigger_webhook":
            # Webhook URLs may point to a different host from the public API.
            method, url, options = _webhook_request(base, args)
        else:
            method, route = _API_ROUTES[name]
            parts = {}
            for key in ("workflow_id", "execution_id"):
                if "{" + key + "}" in route:
                    parts[key] = _identifier(args, key)
            if name == "n8n_activate_workflow":
                active = args.get("active")
                if not isinstance(active, bool):
                    raise ValueError("active must be a boolean.")
                parts["action"] = "activate" if active else "deactivate"
            url = base + "/api/v1" + route.format(**parts)
            options = {
                "headers": {"X-N8N-API-KEY": api_key},
                "params": {
                    target: args[source]
                    for source, target in _QUERY_FIELDS.get(name, {}).items()
                    if args.get(source) is not None
                },
            }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(method, url, **options)
        if not response.is_success:
            detail = _clip(response.text.replace(api_key, "[redacted]"), 300)
            message = f"n8n returned HTTP {response.status_code}"
            return _result(f"{message}: {detail}" if detail else message, error=True)
        try:
            payload = response.json() if response.content else None
        except ValueError:
            payload = response.text
        content = json.dumps(payload, ensure_ascii=False)
        if len(content) > 8000:
            preview = _clip(content)
            # Budget for JSON escaping and the preview metadata.
            while True:
                content = json.dumps({"truncated": True, "preview": preview}, ensure_ascii=False)
                if len(content) <= 8000:
                    break
                preview = _clip(preview, len(preview) // 2)
        return _result(content)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return _result(f"n8n request failed ({type(exc).__name__}).", error=True)
    except (ValueError, TypeError, AttributeError) as exc:
        return _result(f"Invalid n8n request: {_clip(str(exc).replace(api_key, '[redacted]'), 300)}",
                       error=True)
