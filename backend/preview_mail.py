"""구독 저장 후 해당 구독의 미리보기 Actions만 요청한다. 실패해도 구독은 유지한다."""

from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4
from urllib.request import HTTPRedirectHandler, Request, build_opener

from google.cloud import firestore


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("GITHUB_REDIRECT_BLOCKED")


def dispatch_preview(db, subscription_ref, subscription_id, *, opener=None,
                     clock=lambda: datetime.now(timezone.utc)):
    token = os.environ.get("GITHUB_PREVIEW_DISPATCH_TOKEN", "")
    if not token:
        return "not_configured"
    claim = uuid4().hex
    now = clock()
    @firestore.transactional
    def reserve(tx):
        doc = subscription_ref.get(transaction=tx)
        data = doc.to_dict() if doc.exists else {}
        if data.get("subscription_id") != subscription_id or data.get("status") != "active":
            return "cancelled"
        if data.get("preview_dispatch_status") == "accepted":
            return "accepted"
        if data.get("preview_dispatch_until", now) > now:
            return "dispatching"
        data.update(preview_dispatch_status="dispatching", preview_dispatch_claim=claim,
                    preview_dispatch_until=now + timedelta(seconds=60))
        tx.set(subscription_ref, data)
        return "reserved"
    state = reserve(db.transaction())
    if state != "reserved":
        return state
    # Repository and workflow are server configuration, never browser-controlled URLs.
    request = Request(
        "https://api.github.com/repos/contest-p/ai-news-card/actions/workflows/engine-mail.yml/dispatches",
        data=json.dumps({"ref": "main", "inputs": {"mode": "run", "delivery_at": "",
                                                   "preview_subscription_id": subscription_id}}).encode(),
        headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "ai-news-card",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with (opener or build_opener(NoRedirect())).open(request, timeout=8) as response:
            state = "accepted" if response.status in {200, 204} else "failed"
    except Exception:
        # A lost HTTP response may have been accepted; fixed preview job IDs prevent duplicate mail.
        state = "failed"
    @firestore.transactional
    def finish(tx):
        doc = subscription_ref.get(transaction=tx)
        data = doc.to_dict() if doc.exists else {}
        if data.get("subscription_id") == subscription_id and data.get("preview_dispatch_claim") == claim:
            data.update(preview_dispatch_status=state, preview_dispatch_claim=None,
                        preview_dispatch_until=clock(), preview_dispatch_at=clock())
            tx.set(subscription_ref, data)
    finish(db.transaction())
    return state
