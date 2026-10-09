"""Backend engine API adapter. Endpoint/envelope contract is a team proposal."""

import json
from datetime import timezone, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from engine.delivery import new_job
from engine.gateway import Eligibility
from engine.mail_assembly import email_address


class GatewayUnavailable(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GatewayUnavailable("BACKEND_REDIRECT_BLOCKED")


def validate_snapshot(data, kind):
    if not isinstance(data, dict):
        raise ValueError("SNAPSHOT_INVALID")
    for field in ("subscription_id", "user_id", "recipient_email"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ValueError("SNAPSHOT_IDENTITY_INVALID")
    email_address(data["recipient_email"])
    if kind in {"daily_briefing", "subscription_preview"}:
        if data.get("status") != "active":
            raise ValueError("SNAPSHOT_NOT_ACTIVE")
        categories, keywords = data.get("categories"), data.get("keywords")
        allowed = {"economy", "it_science", "politics", "society", "world", "culture"}
        if not isinstance(categories, list) or not categories or any(x not in allowed for x in categories):
            raise ValueError("SNAPSHOT_CATEGORIES_INVALID")
        if (not isinstance(keywords, list) or len(keywords) > 5
                or any(not isinstance(x, str) or x != x.strip() or not 1 <= len(x) <= 20 for x in keywords)):
            raise ValueError("SNAPSHOT_KEYWORDS_INVALID")
    elif data.get("status") != "expired":
        raise ValueError("SNAPSHOT_NOT_EXPIRED")
    new_job(data, kind)
    return data


class HttpEngineGateway:
    def __init__(self, base_url, token, *, timeout=20, opener=None):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"})
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(c.isspace() for c in base_url)):
            raise ValueError("ENGINE_API_URL_INVALID")
        if not token or any(c.isspace() for c in token):
            raise ValueError("ENGINE_API_TOKEN_REQUIRED")
        self.base_url, self._token, self.timeout = base_url.rstrip("/"), token, timeout
        self.opener = opener or build_opener(NoRedirect())

    def request(self, path, *, query=None, body=None, idempotency_key=None):
        url = self.base_url + path + ("?" + urlencode(query) if query else "")
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        try:
            with self.opener.open(Request(url, data=payload, headers=headers), timeout=self.timeout) as response:
                if response.status != 200:
                    raise GatewayUnavailable("BACKEND_STATUS_INVALID")
                raw = response.read(2000001)
                if len(raw) > 2000000:
                    raise GatewayUnavailable("BACKEND_RESPONSE_TOO_LARGE")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except (HTTPError, URLError, OSError, ValueError):
            raise GatewayUnavailable("BACKEND_REQUEST_FAILED") from None

    def list_due_subscriptions(self, now, *, subscription_id=None):
        query = {"now": now.isoformat()}
        if subscription_id:
            query["subscription_id"] = subscription_id
        rows = self.request("/subscriptions/due", query=query)["subscriptions"]
        return self.validate_list(rows, "daily_briefing")

    def list_expired_subscriptions(self, now):
        rows = self.request("/subscriptions/expired", query={"now": now.isoformat()})["subscriptions"]
        return self.validate_list(rows, "subscription_end")

    def list_preview_subscriptions(self, now, *, subscription_id=None):
        query = {"now": now.isoformat()}
        if subscription_id:
            query["subscription_id"] = subscription_id
        rows = self.request("/subscriptions/previews", query=query)["subscriptions"]
        snapshots = self.validate_list(rows, "subscription_preview")
        if subscription_id and any(row["subscription_id"] != subscription_id for row in snapshots):
            raise GatewayUnavailable("BACKEND_PREVIEW_IDENTITY_MISMATCH")
        return snapshots

    def check_preview_eligibility(self, subscription_id, now):
        result = self.request("/subscriptions/" + quote(subscription_id, safe="") + "/eligibility",
                              query={"now": now.isoformat(), "preview": "true"})
        return Eligibility(result["eligible"], result["reason"])

    @staticmethod
    def validate_list(rows, kind):
        if not isinstance(rows, list) or len(rows) > 1000:
            raise GatewayUnavailable("BACKEND_LIST_INVALID")
        return [validate_snapshot(row, kind) for row in rows]

    def get_subscription_snapshot(self, subscription_id, scheduled_date_kst):
        result = self.request("/subscriptions/" + quote(subscription_id, safe="") + "/snapshot",
                              query={"scheduled_date_kst": scheduled_date_kst.isoformat()})
        snapshot = validate_snapshot(result["subscription"], "daily_briefing")
        if snapshot["subscription_id"] != subscription_id or snapshot["scheduled_date_kst"] != scheduled_date_kst.isoformat():
            raise GatewayUnavailable("BACKEND_SNAPSHOT_MISMATCH")
        return snapshot

    def check_delivery_eligibility(self, subscription_id, now):
        result = self.request("/subscriptions/" + quote(subscription_id, safe="") + "/eligibility",
                              query={"now": now.isoformat()})
        return Eligibility(result["eligible"], result["reason"])

    def issue_feedback_token(self, job_id, *, environment="production"):
        if environment not in {"production", "test"}:
            raise ValueError("DELIVERY_ENVIRONMENT_INVALID")
        body = {"job_id": job_id}
        if environment != "production":
            body["environment"] = environment
        result = self.request("/feedback-tokens", body=body, idempotency_key=job_id)
        token = result.get("token")
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise GatewayUnavailable("BACKEND_FEEDBACK_TOKEN_INVALID")
        return token

    def privacy_cleanup(self, now):
        """Backend owns deletion policy; a missing endpoint is an explicit failure."""
        result = self.request("/privacy-cleanup", body={"now": now.isoformat()},
                              idempotency_key="privacy-" + now.astimezone(timezone(timedelta(hours=9))).date().isoformat())
        if result.get("status") != "completed":
            raise GatewayUnavailable("BACKEND_PRIVACY_CLEANUP_INCOMPLETE")
        return result
