"""
XUI Panel API interactions
"""
import requests
import json
import logging
import time
from datetime import datetime, timedelta
from urllib.parse import quote
import uuid
import config
from config import XUI_URL, XUI_USERNAME, XUI_PASSWORD, INBOUND_ID

logger = logging.getLogger(__name__)
session = requests.Session()
session.trust_env = False
_session_authenticated = False
_last_login_time = 0
_csrf_token = None
# Session timeout in seconds (30 minutes)
SESSION_TIMEOUT = 1800

def login_to_xui(force=False):
    """Login to the XUI panel

    Args:
        force (bool): Force re-login even if session is still valid

    Returns:
        bool: True if login successful, False otherwise
    """
    global _session_authenticated, _last_login_time, _csrf_token

    # Read token at call time so runtime env changes are respected
    token = getattr(config, 'XUI_API_TOKEN', '')
    if token:
        logger.info("XUI_API_TOKEN present — using token auth, skipping cookie login")
        return True

    # If already logged in and session is fresh, don't re-login unless forced
    current_time = time.time()
    if _session_authenticated and (current_time - _last_login_time) < SESSION_TIMEOUT and not force:
        return True

    url = f"{XUI_URL}/login"
    data = {"username": XUI_USERNAME, "password": XUI_PASSWORD}
    try:
        response = session.post(url, json=data, timeout=20)
        if response.ok and response.json().get("success") is True:
            _csrf_token = None
            _session_authenticated = True
            _last_login_time = current_time
            logger.info("Successfully logged in to XUI panel")
            return True
        else:
            _session_authenticated = False
            logger.error(f"Login failed with status code: {response.status_code}")
            return False
    except Exception as e:
        _session_authenticated = False
        logger.error(f"Exception during login: {e}")
        return False

def ensure_authenticated():
    """Ensure the session is authenticated, attempt re-login if needed

    Returns:
        bool: True if authenticated, False otherwise
    """
    # login_to_xui checks both the authentication flag and session age.
    return login_to_xui()

def _api_request(method, path, **kwargs):
    """Call v3 API with Bearer auth or a cookie session and CSRF protection."""
    global _csrf_token
    if not ensure_authenticated():
        raise RuntimeError("Failed to login to XUI panel")
    for attempt in range(2):
        headers = {"Accept": "application/json"}
        token = getattr(config, 'XUI_API_TOKEN', '')
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif method != "get":
            if not _csrf_token:
                csrf = session.get(f"{XUI_URL.rstrip('/')}/csrf-token", timeout=20)
                csrf.raise_for_status()
                result = csrf.json()
                if not result.get("success") or not isinstance(result.get("obj"), str) or not result['obj']:
                    raise RuntimeError("Could not obtain panel CSRF token")
                _csrf_token = result['obj']
            headers["X-CSRF-Token"] = _csrf_token
        response = getattr(session, method)(
            f"{XUI_URL.rstrip('/')}{path}", headers=headers, timeout=20, **kwargs)
        if response.status_code in (401, 403, 404) and attempt == 0 and not XUI_API_TOKEN:
            if login_to_xui(force=True):
                continue
        response.raise_for_status()
        result = response.json()
        if result.get('success') is not True:
            raise RuntimeError(result.get('msg') or "Panel API request failed")
        return result.get('obj')


def _status(data):
    total = int(data.get('total', data.get('totalGB', 0)))
    used = int(data.get('up', 0)) + int(data.get('down', 0))
    expiry = int(data.get('expiryTime', 0))
    remaining = max(0, expiry / 1000 - time.time())
    days, hours = int(remaining // 86400), int(remaining % 86400 // 3600)
    display = f"{days} روز و {hours} ساعت" if days else f"{hours} ساعت"
    return {
        'email': data.get('email'), 'total_gb': total / 1024 ** 3,
        'remaining_gb': round(max(0, total - used) / 1024 ** 3, 2),
        'remaining_days': days, 'remaining_hours': hours,
        'remaining_time_display': display if expiry > 0 else "نامحدود",
        'expiry_time_ms': expiry,
        'expiry_date': datetime.fromtimestamp(expiry / 1000).strftime('%Y-%m-%d') if expiry > 0 else "نامحدود",
        'is_active': data.get('enable', False), 'subId': data.get('subId'),
    }


def get_client_status(email):
    try:
        data = _api_request('get', f"/panel/api/clients/traffic/{quote(email, safe='')}")
        return _status({**data, 'email': email}) if data else None
    except Exception as exc:
        logger.error("Error getting client status: %s", exc)
        return None


def _expiry_time_ms(duration):
    if isinstance(duration, timedelta):
        return int((datetime.now() + duration).timestamp() * 1000)
    return int(duration)


def create_client(email, total_gb, expiry_time_ms=None, limit_ip=0):
    """Create a v3 client; total_gb is bytes (zero means unlimited)."""
    try:
        client_id = str(uuid.uuid4())
        payload = {
            "client": {
                "id": client_id, "email": email, "flow": "",
                "limitIp": int(limit_ip), "totalGB": int(total_gb),
                "expiryTime": _expiry_time_ms(expiry_time_ms if expiry_time_ms is not None else timedelta(days=31)),
                "enable": True, "tgId": 0, "subId": uuid.uuid4().hex[:16], "reset": 0,
            },
            "inboundIds": [INBOUND_ID],
        }
        _api_request('post', '/panel/api/clients/add', json=payload)
        return client_id, None
    except Exception as exc:
        logger.error("Error creating client: %s", exc)
        return None, str(exc)


def extend_client(email, client_id, additional_gb, new_expiry_time_ms=None, limit_ip=None, unlimited=False):
    """Renew by email, preserving credentials and fields required by v3 replacement updates."""
    try:
        details = _api_request('get', f"/panel/api/clients/get/{quote(email, safe='')}")
        client = dict(details['client'])
        if client.get('uuid') != client_id:
            raise ValueError("Client UUID does not match the requested service")
        client['id'] = client.pop('uuid')
        # Read records use a numeric id and serialized tunnel addresses; writes use the protocol UUID.
        for key in ('createdAt', 'updatedAt'):
            if key in client:
                client[{'createdAt': 'created_at', 'updatedAt': 'updated_at'}[key]] = client.pop(key)
        if isinstance(client.get('allowedIPs'), str):
            client['allowedIPs'] = [ip.strip() for ip in client['allowedIPs'].split(',') if ip.strip()]
        if isinstance(client.get('reverse'), str):
            client['reverse'] = json.loads(client['reverse']) if client['reverse'] else None
        if details.get('tunnelAllowedIPs'):
            client['allowedIPsByInbound'] = details['tunnelAllowedIPs']
        current_total = int(client.get('totalGB', 0))
        if current_total == 0 and additional_gb > 0 and not unlimited:
            raise ValueError("نمیتوان به پلن نامحدود حجم اضافه کرد")
        client['totalGB'] = 0 if unlimited else current_total + int(additional_gb * 1024 ** 3)
        if limit_ip is not None:
            client['limitIp'] = int(limit_ip)
        duration = new_expiry_time_ms if new_expiry_time_ms is not None else timedelta(days=31)
        if isinstance(duration, timedelta):
            client['expiryTime'] = max(int(client.get('expiryTime', 0)), int(time.time() * 1000)) + int(duration.total_seconds() * 1000)
        else:
            client['expiryTime'] = _expiry_time_ms(duration)
        client['enable'] = True
        _api_request('post', f"/panel/api/clients/update/{quote(email, safe='')}", json=client)
        return True, None
    except Exception as exc:
        logger.error("Error extending client: %s", exc)
        return False, str(exc)


def get_all_clients():
    try:
        records = _api_request('get', '/panel/api/clients/list')
        clients = []
        for record in records:
            if INBOUND_ID not in record.get('inboundIds', []):
                continue
            client = dict(record)
            client['id'] = record.get('uuid')
            client['inboundId'] = INBOUND_ID
            client.update(_status({**record, **(record.get('traffic') or {})}))
            clients.append(client)
        return clients
    except Exception as exc:
        logger.error("Error getting clients: %s", exc)
        return None


def delete_client(client_id):
    """Resolve the existing UUID-based app interface to the v3 email endpoint."""
    try:
        clients = get_all_clients()
        if clients is None:
            raise RuntimeError("Could not load clients")
        matches = [client for client in clients if client['id'] == client_id]
        if len(matches) != 1:
            raise ValueError("Client UUID was not found or is ambiguous")
        email = matches[0]['email']
        _api_request('post', f"/panel/api/clients/del/{quote(email, safe='')}")
        return True, None
    except Exception as exc:
        logger.error("Error deleting client: %s", exc)
        return False, str(exc)
