"""Brevo (v3) client — powers the admin "Email Users" broadcast page.

Reads BREVO_API_KEY from the host env (Railway), same as every other API
key — the secret is never handled in code or the UI. Optional defaults:
BREVO_SENDER_EMAIL, BREVO_SENDER_NAME.

Flow the page uses: sync app users into a Brevo contact list, then create
+ send an email campaign to that list (with a self-test first). Brevo
handles unsubscribe links and deliverability.
"""
from __future__ import annotations

import os

import requests

_BASE = "https://api.brevo.com/v3"
_OK = (200, 201, 202, 204)


def _key() -> str:
    return (os.environ.get("BREVO_API_KEY") or "").strip()


def enabled() -> bool:
    return bool(_key())


def _headers() -> dict:
    return {"api-key": _key(), "accept": "application/json",
            "content-type": "application/json"}


def default_sender() -> tuple[str, str]:
    """(name, email) defaults from env; email blank until configured."""
    return (os.environ.get("BREVO_SENDER_NAME") or "YouTube Football Tracker",
            (os.environ.get("BREVO_SENDER_EMAIL") or "").strip())


def get_lists() -> list[dict]:
    """All Brevo contact lists: [{id, name, totalSubscribers}]. Raises on
    auth/HTTP error so the page can surface a clear message."""
    r = requests.get(f"{_BASE}/contacts/lists?limit=50&offset=0",
                     headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json().get("lists", [])


def sync_contacts(users: list[dict], list_id: int) -> dict:
    """Upsert each user (email + name) into the given list. Idempotent
    (updateEnabled). Returns {ok, failed, errors[]}."""
    ok = failed = 0
    errs: list[str] = []
    for u in users:
        email = (u.get("email") or "").strip()
        if not email:
            continue
        first = (u.get("first_name")
                 or (u.get("display_name") or "").split(" ")[0] or "")
        body = {
            "email": email,
            "attributes": {"FIRSTNAME": first,
                           "LASTNAME": u.get("last_name") or ""},
            "listIds": [int(list_id)],
            "updateEnabled": True,
        }
        try:
            resp = requests.post(f"{_BASE}/contacts", headers=_headers(),
                                 json=body, timeout=20)
            if resp.status_code in _OK:
                ok += 1
            else:
                failed += 1
                if len(errs) < 3:
                    errs.append(f"{email}: {resp.status_code} {resp.text[:120]}")
        except Exception as e:
            failed += 1
            if len(errs) < 3:
                errs.append(f"{email}: {e}")
    return {"ok": ok, "failed": failed, "errors": errs}


def create_campaign(name: str, subject: str, sender_name: str,
                    sender_email: str, html: str, list_id: int) -> dict:
    """Create a draft email campaign targeting one list. Returns
    {ok, id} or {ok:False, error}."""
    body = {
        "name": name,
        "subject": subject,
        "sender": {"name": sender_name, "email": sender_email},
        "htmlContent": html,
        "recipients": {"listIds": [int(list_id)]},
    }
    r = requests.post(f"{_BASE}/emailCampaigns", headers=_headers(),
                      json=body, timeout=30)
    if r.status_code not in _OK:
        return {"ok": False, "error": f"{r.status_code} {r.text[:300]}"}
    return {"ok": True, "id": r.json().get("id")}


def send_test(campaign_id: int, emails: list[str]) -> dict:
    r = requests.post(f"{_BASE}/emailCampaigns/{campaign_id}/sendTest",
                      headers=_headers(), json={"emailTo": emails}, timeout=30)
    if r.status_code not in _OK:
        return {"ok": False, "error": f"{r.status_code} {r.text[:300]}"}
    return {"ok": True}


def send_now(campaign_id: int) -> dict:
    r = requests.post(f"{_BASE}/emailCampaigns/{campaign_id}/sendNow",
                      headers=_headers(), timeout=30)
    if r.status_code not in _OK:
        return {"ok": False, "error": f"{r.status_code} {r.text[:300]}"}
    return {"ok": True}
