"""Admin — Email Users (Brevo broadcast).

Sync app users into a Brevo list, then compose + send a campaign to that
list. Always send a self-test first; the real blast is behind an explicit
type-to-confirm gate. Admin-only.
"""
import os
import datetime as _dt

import streamlit as st
from dotenv import load_dotenv

from src.database import Database
from src.auth import require_admin, get_current_user
from src import brevo

load_dotenv()
require_admin()

st.title("✉️ Email Users")
st.caption("Broadcast to your users via Brevo. Sync the list, compose, "
           "send a test to yourself, then send to everyone.")

# ── Setup guard ───────────────────────────────────────────────────────
if not brevo.enabled():
    st.warning("Brevo isn't configured yet.")
    st.markdown(
        "**To enable, set these on Railway (env vars) and redeploy:**\n"
        "- `BREVO_API_KEY` — create one in Brevo → *SMTP & API → API Keys*.\n"
        "- `BREVO_SENDER_EMAIL` — a **verified** sender in Brevo "
        "(*Senders, Domains & Dedicated IPs*). Required or sends are rejected.\n"
        "- `BREVO_SENDER_NAME` *(optional)* — display name, e.g. "
        "\"YouTube Football Tracker\".\n\n"
        "Then create (or reuse) a contact list in Brevo — this page lets you "
        "pick it and sync users into it."
    )
    st.stop()

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY", "")
db = Database(SUPABASE_URL, SUPABASE_KEY)
_all_with_email = [u for u in (db.get_all_users() or [])
                   if (u.get("email") or "").strip()]
_me = (get_current_user() or {}).get("email") or ""

# Audience filter — sync only the chosen subset into the Brevo list.
_n_consent = sum(1 for u in _all_with_email if bool(u.get("email_consent")))
_n_onb = sum(1 for u in _all_with_email if bool(u.get("onboarded")))
_audience = st.radio(
    "Audience to sync",
    [f"Consented only ({_n_consent})",
     f"Onboarded only ({_n_onb})",
     f"All users with email ({len(_all_with_email)})"],
    horizontal=True,
)
if _audience.startswith("Consented"):
    _users = [u for u in _all_with_email if bool(u.get("email_consent"))]
elif _audience.startswith("Onboarded"):
    _users = [u for u in _all_with_email if bool(u.get("onboarded"))]
else:
    _users = _all_with_email
_n_users = len(_users)

st.markdown(f"**{_n_users}** users selected to sync.")
st.caption("“Consented only” = users who opted in to emails at onboarding "
           "— the compliant default for product/marketing sends.")

# ── 1. Target list + sync ─────────────────────────────────────────────
st.subheader("1 · Audience list")
try:
    _lists = brevo.get_lists()
except Exception as e:
    st.error(f"Couldn't reach Brevo (check BREVO_API_KEY): {e}")
    st.stop()

if not _lists:
    st.info("No contact lists in Brevo yet — create one in Brevo "
            "(*Contacts → Lists*), then refresh this page.")
    st.stop()

_opts = {f"{l['name']}  (#{l['id']} · {l.get('totalSubscribers', 0)} subs)": l['id']
         for l in _lists}
_pick = st.selectbox("Send to which Brevo list?", list(_opts.keys()))
_list_id = _opts[_pick]

if st.button(f"🔄 Sync {_n_users} app users → this list"):
    with st.spinner("Syncing contacts to Brevo…"):
        res = brevo.sync_contacts(_users, _list_id)
    st.success(f"Synced: {res['ok']} ok, {res['failed']} failed.")
    if res["errors"]:
        st.caption("First errors: " + " | ".join(res["errors"]))

# ── 2. Compose ────────────────────────────────────────────────────────
st.subheader("2 · Compose")
_def_name, _def_email = brevo.default_sender()
_c1, _c2 = st.columns(2)
with _c1:
    _sender_name = st.text_input("Sender name", value=_def_name)
with _c2:
    _sender_email = st.text_input("Sender email (must be verified in Brevo)",
                                  value=_def_email)
_subject = st.text_input("Subject")
_body = st.text_area("Message (plain text — blank lines separate paragraphs)",
                     height=240)


def _to_html(text: str) -> str:
    paras = "".join(f"<p>{ln.strip()}</p>" for ln in text.split("\n") if ln.strip())
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;'
        'color:#222;line-height:1.6;max-width:600px;margin:0 auto">'
        f'{paras}'
        '<hr style="border:none;border-top:1px solid #ddd;margin:24px 0 12px">'
        '<p style="font-size:12px;color:#888">You\'re receiving this because '
        'you signed up to YouTube Football Tracker '
        '(ytft.aguywithascarf.com). '
        '<a href="{{ unsubscribe }}">Unsubscribe</a>.</p></div>'
    )


_ready = bool(_subject.strip() and _body.strip() and _sender_email.strip())
if _subject.strip() and _body.strip():
    with st.expander("👁️ Preview"):
        st.markdown(_to_html(_body), unsafe_allow_html=True)

# ── 3. Test send ──────────────────────────────────────────────────────
st.subheader("3 · Test")
st.caption(f"Sends a test of this exact email to **{_me or '(your account email)'}** only.")
if st.button("📨 Send test to me", disabled=not (_ready and _me)):
    camp = brevo.create_campaign(
        name=f"[YTFT test] {_subject[:60]} {_dt.datetime.now():%H:%M}",
        subject=_subject, sender_name=_sender_name, sender_email=_sender_email,
        html=_to_html(_body), list_id=_list_id)
    if not camp["ok"]:
        st.error(f"Couldn't create campaign: {camp['error']}")
    else:
        res = brevo.send_test(camp["id"], [_me])
        st.success(f"Test sent to {_me}. ✓") if res["ok"] \
            else st.error(f"Test failed: {res['error']}")

# ── 4. Send to everyone ───────────────────────────────────────────────
st.subheader("4 · Send to all")
st.markdown(
    f"<div style='background:#3a1212;border-left:3px solid #e5484d;"
    f"border-radius:4px;padding:10px 14px;color:#FAFAFA;font-size:14px'>"
    f"⚠️ This sends the email to <b>every contact in '{_pick}'</b> "
    f"immediately. There's no undo. Send a test first.</div>",
    unsafe_allow_html=True,
)
_confirm = st.text_input("Type SEND to confirm")
if st.button("🚀 Send to all now",
             type="primary", disabled=not (_ready and _confirm.strip() == "SEND")):
    camp = brevo.create_campaign(
        name=f"YTFT {_dt.date.today().isoformat()} — {_subject[:60]}",
        subject=_subject, sender_name=_sender_name, sender_email=_sender_email,
        html=_to_html(_body), list_id=_list_id)
    if not camp["ok"]:
        st.error(f"Couldn't create campaign: {camp['error']}")
    else:
        res = brevo.send_now(camp["id"])
        if res["ok"]:
            st.success(f"🚀 Campaign sent to list '{_pick}'.")
            st.balloons()
        else:
            st.error(f"Send failed: {res['error']}")
