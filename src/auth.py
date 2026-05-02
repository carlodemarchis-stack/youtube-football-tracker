"""Auth: Streamlit native Google OAuth + Supabase email OTP + role-based access.

Roles (hierarchical):
    admin   — everything (user mgmt, refresh, premium pages, all viewer pages)
    premium — premium pages + all viewer pages
    viewer  — login-required pages (default for self-signup)
    (none)  — only Home is visible to unauthenticated visitors

Auth methods:
    1. Google OAuth via `st.login("google")` (Streamlit native)
    2. Email OTP via Supabase — user enters email, gets 6-digit code, pastes it

Both methods write/read the same `user_profiles` table. First login creates a
`viewer` row. Admin can promote to `premium` or `admin` via User Management.
"""
from __future__ import annotations

import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

ADMIN_EMAIL = "carlodemarchis@gmail.com"
ROLE_LEVELS = {"viewer": 0, "premium": 1, "admin": 2}

# Persistent login (browser cookie) — for email OTP users.
# Google users already get a secure session cookie from Streamlit itself.
COOKIE_NAME = "yt_refresh"
COOKIE_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days
# Set secure=True when serving over HTTPS. Detect by env so localhost still works.
_COOKIE_SECURE = os.getenv("APP_HTTPS", "").lower() in ("1", "true", "yes")


def _get_db():
    from src.database import Database
    return Database(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_KEY", ""))


def _cookies():
    """Get (or create) the CookieController instance. Returns None if not installed."""
    try:
        from streamlit_cookies_controller import CookieController
        # Cache in session_state to avoid re-instantiating the component every rerun
        if "_cookie_ctrl" not in st.session_state:
            st.session_state["_cookie_ctrl"] = CookieController()
        return st.session_state["_cookie_ctrl"]
    except Exception:
        return None


def _set_refresh_cookie(refresh_token: str):
    c = _cookies()
    if not c or not refresh_token:
        return
    try:
        c.set(
            COOKIE_NAME, refresh_token,
            max_age=COOKIE_MAX_AGE_SECONDS,
            secure=_COOKIE_SECURE, same_site="lax", path="/",
        )
    except Exception:
        pass


def _clear_refresh_cookie():
    c = _cookies()
    if not c:
        return
    try:
        c.remove(COOKIE_NAME)
    except Exception:
        pass


def _try_restore_from_cookie() -> str | None:
    """If a refresh-token cookie exists, use it to mint a new session.
    Returns the restored email or None."""
    # Prevent hammering refresh_session every rerun if it failed once this session
    if st.session_state.get("_cookie_restore_failed"):
        return None
    c = _cookies()
    if not c:
        return None
    try:
        refresh_token = c.get(COOKIE_NAME)
    except Exception:
        refresh_token = None
    if not refresh_token:
        return None
    try:
        db = _get_db()
        resp = db.client.auth.refresh_session(refresh_token)
        user = getattr(resp, "user", None)
        session = getattr(resp, "session", None)
        if user and getattr(user, "email", None):
            # Rotate cookie with new refresh token
            if session and getattr(session, "refresh_token", None):
                _set_refresh_cookie(session.refresh_token)
            email = user.email.lower().strip()
            st.session_state["sb_email"] = email
            st.session_state["sb_display_name"] = email.split("@")[0]
            return email
    except Exception:
        # Invalid/expired refresh — clear cookie and mark failure for this session
        _clear_refresh_cookie()
        st.session_state["_cookie_restore_failed"] = True
    return None


def _role_level(role: str | None) -> int:
    return ROLE_LEVELS.get((role or "viewer").lower(), 0)


def _load_profile(email: str, display_name: str) -> dict:
    """Fetch or create the user_profiles row, return normalized user dict."""
    email = (email or "").lower().strip()
    db = _get_db()
    profile = db.get_user_profile(email)
    if not profile:
        role = "admin" if email == ADMIN_EMAIL else "viewer"
        profile = db.upsert_user_profile(email, display_name or "", role)
    return {
        "email": email,
        "display_name": display_name or profile.get("display_name", ""),
        "role": profile.get("role", "viewer"),
        "auth_method": "google",  # overwritten by caller if needed
    }


def get_current_user() -> dict | None:
    """Return current user dict if logged in via any method, else None."""
    # Google OAuth (Streamlit native)
    try:
        if st.user.is_logged_in:
            email = (st.user.email or "").lower().strip()
            cached = st.session_state.get("yt_user")
            if cached and cached.get("email") == email:
                return cached
            user = _load_profile(email, st.user.name or "")
            user["auth_method"] = "google"
            st.session_state["yt_user"] = user
            return user
    except Exception:
        pass

    # Email OTP (Supabase) — session stored in st.session_state
    email = st.session_state.get("sb_email")
    if not email:
        # Try to restore from persistent cookie (silent re-login)
        email = _try_restore_from_cookie()
    if email:
        cached = st.session_state.get("yt_user")
        if cached and cached.get("email") == email:
            return cached
        user = _load_profile(email, st.session_state.get("sb_display_name", ""))
        user["auth_method"] = "email"
        st.session_state["yt_user"] = user
        return user

    return None


def is_logged_in() -> bool:
    return get_current_user() is not None


def is_admin() -> bool:
    u = get_current_user()
    return u is not None and _role_level(u.get("role")) >= 2


def is_premium() -> bool:
    u = get_current_user()
    return u is not None and _role_level(u.get("role")) >= 1


def _show_login_ui():
    """Render both login options (Google + email OTP) and stop the page."""
    st.markdown("### Sign in")
    col1, col2 = st.columns([1, 2])
    with col1:
        try:
            if st.button("Sign in with Google", type="primary", use_container_width=True):
                st.login("google")
        except Exception as e:
            st.caption(f"Google login unavailable: {e}")

    with col2:
        with st.expander("Or sign in by email (magic code)", expanded=False):
            _email_otp_flow()
    st.stop()


def _email_otp_flow():
    """Render the two-step email OTP login form."""
    # Step 1: request code
    if not st.session_state.get("sb_otp_sent"):
        email = st.text_input("Email", key="_otp_email_input")
        if st.button("Send code", key="_otp_send_btn"):
            if not email or "@" not in email:
                st.error("Enter a valid email.")
                return
            try:
                db = _get_db()
                db.client.auth.sign_in_with_otp({
                    "email": email.strip().lower(),
                    "options": {"should_create_user": True},
                })
                st.session_state["sb_otp_sent"] = True
                st.session_state["sb_otp_email"] = email.strip().lower()
                st.success(f"Code sent to {email}. Check your inbox.")
                st.rerun()
            except Exception as e:
                st.error(f"Could not send code: {e}")
        return

    # Step 2: verify code
    email = st.session_state.get("sb_otp_email", "")
    st.caption(f"Code sent to **{email}**")
    code = st.text_input("Login code", max_chars=10, key="_otp_code_input",
                         placeholder="Paste the code from the email")
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Verify", key="_otp_verify_btn", type="primary"):
            code_clean = (code or "").strip().replace(" ", "").replace("-", "")
            if len(code_clean) < 6:
                st.error("Enter the code from the email.")
                return
            code = code_clean
            try:
                db = _get_db()
                resp = db.client.auth.verify_otp({
                    "email": email,
                    "token": code.strip(),
                    "type": "email",
                })
                if resp and getattr(resp, "user", None):
                    st.session_state["sb_email"] = email
                    st.session_state["sb_display_name"] = email.split("@")[0]
                    st.session_state.pop("sb_otp_sent", None)
                    st.session_state.pop("sb_otp_email", None)
                    st.session_state.pop("yt_user", None)
                    st.session_state.pop("_cookie_restore_failed", None)
                    # Persist login across browser refreshes / server restarts
                    session = getattr(resp, "session", None)
                    if session and getattr(session, "refresh_token", None):
                        _set_refresh_cookie(session.refresh_token)
                    st.rerun()
                else:
                    st.error("Invalid code.")
            except Exception as e:
                st.error(f"Verification failed: {e}")
    with c2:
        if st.button("Send new code", key="_otp_resend_btn"):
            st.session_state.pop("sb_otp_sent", None)
            st.rerun()


_GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "icloud.com", "me.com", "mac.com",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.it", "ymail.com",
    "protonmail.com", "proton.me", "pm.me", "aol.com", "gmx.com",
    "mail.com", "fastmail.com", "zoho.com", "tutanota.com",
}


def _show_onboarding_card(user: dict):
    """One-time form: first name, last name, company, optional LinkedIn URL.
    Pre-fills from Google OAuth (given_name / family_name / email) and the
    email-domain heuristic for Company when possible."""
    email = user.get("email", "") or ""
    display = (user.get("display_name") or "").strip()

    # ── Pre-fill: First name / Last name / Email from Google ──
    default_first, default_last, default_email = "", "", email
    try:
        if st.user.is_logged_in:
            default_first = (getattr(st.user, "given_name", "") or "").strip()
            default_last = (getattr(st.user, "family_name", "") or "").strip()
            default_email = ((getattr(st.user, "email", "") or "").strip()
                             or default_email)
    except Exception:
        pass
    # Fallback: split display_name when given/family aren't available
    # (e.g. Supabase magic-link auth, or Google account with no first/last).
    if not default_first and display:
        parts = display.split(None, 1)
        default_first = parts[0]
        if len(parts) > 1 and not default_last:
            default_last = parts[1]
    # Last-resort fallback: derive from the email's local part.
    if not default_first and default_email and "@" in default_email:
        local = default_email.split("@", 1)[0]
        # 'jane.doe' → 'Jane Doe'; 'jane' → 'Jane'
        bits = [p for p in local.replace("_", ".").replace("-", ".").split(".")
                if p]
        if bits:
            default_first = bits[0].capitalize()
            if len(bits) > 1 and not default_last:
                default_last = " ".join(b.capitalize() for b in bits[1:])

    # Pre-fill Company from email domain if not a generic provider
    domain = default_email.split("@", 1)[1].lower() if "@" in default_email else ""
    default_company = ("" if domain in _GENERIC_EMAIL_DOMAINS or not domain
                       else domain.split(".")[0].title())

    st.markdown("## 👋 Welcome")
    st.caption(
        "Quick one-time intro so I know who's using this. "
        "Takes 10 seconds, we'll never ask again."
    )
    with st.form("_onboarding_form", clear_on_submit=False):
        col1, col2 = st.columns(2)
        first_name = col1.text_input("First name *", value=default_first)
        last_name = col2.text_input("Last name *", value=default_last)
        # Email shown read-only — useful confirmation, can't be edited
        # (user is already authenticated under it).
        st.text_input("Email *", value=default_email, disabled=True,
                      help="Linked to your sign-in account.")
        company = st.text_input(
            "Company *", value=default_company,
            placeholder="e.g. DAZN, AS Roma, freelance, student",
        )
        linkedin_url = st.text_input(
            "LinkedIn URL (optional)",
            placeholder="https://linkedin.com/in/your-handle",
        )
        submitted = st.form_submit_button("Save and continue", type="primary")

    if submitted:
        if not first_name.strip() or not last_name.strip() or not company.strip():
            st.error("First name, last name and company are required.")
            return
        try:
            db = _get_db()
            db.update_user_onboarding(
                email,
                first_name=first_name, last_name=last_name,
                company=company, linkedin_url=linkedin_url,
            )
            # Invalidate cache so next run loads the updated profile
            st.session_state.pop("yt_user", None)
            st.rerun()
        except Exception as e:
            st.error(f"Could not save: {e}")


def _needs_onboarding(user: dict) -> bool:
    """Check the user_profiles row for the `onboarded` flag."""
    email = user.get("email")
    if not email:
        return False
    try:
        db = _get_db()
        profile = db.get_user_profile(email)
        if not profile:
            return True
        return not bool(profile.get("onboarded"))
    except Exception:
        # If the column doesn't exist yet, don't block the user
        return False


def require_login():
    """Block page if user is not signed in at all (any role)."""
    user = get_current_user()
    if user is None:
        st.warning("Please sign in to access this page.")
        _show_login_ui()
    if _needs_onboarding(user):
        _show_onboarding_card(user)
        st.stop()
    return user


def gate_onboarding():
    """Global gate: if a user is logged in but hasn't completed onboarding,
    render the onboarding form full-screen and stop the script.

    Safe to call on every page — including public ones — because it's a
    no-op when the user isn't logged in. Intended to run from app.py so
    it covers Home (which has no require_login()) and any future page
    that forgets to gate itself."""
    user = get_current_user()
    if user is None:
        return
    if _needs_onboarding(user):
        _show_onboarding_card(user)
        st.stop()


def require_premium():
    """Block page if user is not premium or admin. Shows an upgrade CTA for viewers."""
    user = require_login()
    if _role_level(user.get("role")) < 1:
        _show_upgrade_cta(user)
        st.stop()
    return user


def _show_upgrade_cta(user: dict):
    """Friendly upgrade page shown to signed-in viewers when they hit a premium page."""
    st.markdown(
        """
        <div style="text-align:center; padding:48px 24px;">
          <div style="font-size:64px; margin-bottom:16px;">🔒</div>
          <h2 style="margin:0 0 12px 0; color:#FAFAFA;">Premium feature</h2>
          <p style="color:#AAA; font-size:16px; max-width:500px; margin:0 auto 24px;">
            This page is available to <b>Premium</b> members only. Premium unlocks
            AI-powered analysis, free-form data questions, and side-by-side club comparisons.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.info(
            f"Signed in as **{user.get('email', '—')}** ({user.get('role', 'viewer')}). "
            f"Contact the admin ({ADMIN_EMAIL}) to request an upgrade."
        )


def require_admin():
    """Block page if user is not an admin."""
    user = require_login()
    if _role_level(user.get("role")) < 2:
        st.error("Admin access required.")
        st.stop()
    return user


def show_auth_sidebar():
    """Render login/logout + user info in the sidebar."""
    user = get_current_user()
    if user:
        st.sidebar.markdown(f"**{user['display_name'] or user['email']}**")
        role = user.get("role", "viewer")
        badge = {"admin": "🛡️ Admin", "premium": "⭐ Premium", "viewer": "👤 Viewer"}.get(role, role.title())
        st.sidebar.caption(badge)
        if st.sidebar.button("Sign out"):
            method = user.get("auth_method", "google")
            st.session_state.pop("yt_user", None)
            st.session_state.pop("sb_email", None)
            st.session_state.pop("sb_display_name", None)
            st.session_state.pop("sb_otp_sent", None)
            st.session_state.pop("sb_otp_email", None)
            _clear_refresh_cookie()
            try:
                if method == "google":
                    st.logout()
            except Exception:
                pass
            st.rerun()
    else:
        with st.sidebar:
            st.markdown("---")
            st.markdown("**Sign in**")
            try:
                if st.button("Continue with Google", use_container_width=True):
                    st.login("google")
            except Exception as e:
                st.caption(f"Google unavailable: {e}")

            with st.expander("Or use email", expanded=False):
                _email_otp_flow()
