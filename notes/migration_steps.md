# Railway migration — exact steps

Companion to `notes/custom_domain_migration.md`. Run sequentially.
**[you]** = Carlo only. **[me]** = can be done via Railway CLI / code.

**Target domain (decided May 2026):** `ytft.aguywithascarf.com`
(subdomain of an existing domain — DNS managed wherever
`aguywithascarf.com` lives).

---

## Phase 0 — Prep (5 min)

**[you]** Decide custom domain (`app.factory63.com` / `tracker.…` / etc.).

**[you]** Make sure you have:
- DNS access (Cloudflare / Namecheap / wherever).
- Google Cloud Console access for the YTFT OAuth client.
- Streamlit Cloud secrets — `[auth]` block visible (we'll port).

---

## Phase 1 — Create the Railway web service (15 min)

### 1.1 — [you] Create the service in the dashboard

Railway → YTFT project → **+ New** → **GitHub Repo** →
`carlodemarchis-stack/youtube-football-tracker` → branch `main`.

Name the service **"Web"**.

Don't worry about settings yet — Railway will fail its first build
attempt because no start command is configured. That's fine; I fix it
in 1.2.

### 1.2 — [me] Configure the service via CLI

After 1.1, ping me. I'll set:

- **Start command**: `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true`
- **Healthcheck path**: `/_stcore/health` (Streamlit's built-in liveness endpoint)
- **Restart policy**: on failure, max 5 retries

### 1.3 — [me] Set environment variables on Web

I'll copy from one of the existing cron services and override where
needed. End state on Web:

| Var | Value | Source |
|---|---|---|
| `SUPABASE_URL` | (same) | Copy from cron |
| `SUPABASE_KEY` | **anon** | Read from local `.env` (`SUPABASE_KEY` post‑Phase‑3 split is anon) |
| `SUPABASE_SERVICE_KEY` | service_role | Copy from cron |
| `YOUTUBE_API_KEY` | (same) | Copy from cron |
| `ANTHROPIC_API_KEY` | (same) | Copy from cron |
| `NTFY_TOPIC` | `ytft` | Copy from cron (optional) |

**Cron services stay unchanged** — they keep `SUPABASE_KEY=service_role`
because they need to write. Only the Web service uses anon for
public‑facing reads under RLS.

### 1.4 — [me] Trigger first deploy

After 1.2 + 1.3 land. Build + boot logs in Railway dashboard. If the
container starts and `/_stcore/health` returns 200, Phase 1 is done.

If anything errors, I read logs, fix, redeploy.

---

## Phase 2 — Port the `[auth]` block (30 min)

Streamlit reads `.streamlit/secrets.toml` at startup. On Railway: extend
`app.py`'s existing env→secrets bootstrap to also build the `[auth]`
section from individual env vars.

**[me]** Code change in `app.py`:
- Read `STREAMLIT_AUTH_*` env vars.
- If `.streamlit/secrets.toml` doesn't already have `[auth]`, write a
  composed file at startup.

**[you]** Set these 5 env vars on the Web service in Railway dashboard
(paste values from Streamlit Cloud's Secrets UI — **NEVER paste in chat**):
- `STREAMLIT_AUTH_REDIRECT_URI` — placeholder for now: `https://<railway-app>.up.railway.app/oauth2callback`
- `STREAMLIT_AUTH_COOKIE_SECRET`
- `STREAMLIT_AUTH_CLIENT_ID`
- `STREAMLIT_AUTH_CLIENT_SECRET`
- `STREAMLIT_AUTH_SERVER_METADATA_URL` — typically `https://accounts.google.com/.well-known/openid-configuration`

---

## Phase 3 — First deploy & smoke test on the Railway URL (15 min)

**[me]** Trigger deploy via CLI; tail logs for issues.

**[you]** Get the Railway-issued URL (e.g. `web-production-xyz.up.railway.app`)
from the dashboard.

**[you]** Add the Railway URL to Google OAuth **temporarily** so the
test login works:
- Google Cloud Console → APIs & Services → Credentials → OAuth 2.0
  Client → Authorized redirect URIs → add
  `https://<railway-url>.up.railway.app/oauth2callback`.

**[you]** Smoke-test on `https://<railway-url>.up.railway.app`:
- [ ] Public Home loads
- [ ] Sign in with Google → lands on Daily Recap
- [ ] All Channels / Latest Videos / Season Top render
- [ ] Sign out → unlogged Home

If anything breaks → ping me, I read Railway logs, fix, redeploy.

---

## Phase 4 — Add the custom domain (15 min)

**[you]** Railway → Web service → **Settings** → **Domains** →
**+ Custom Domain** → enter `ytft.aguywithascarf.com`.

Railway shows a CNAME target like `xyz.up.railway.app`.

**[you]** At registrar / DNS:
- Add `CNAME` record: `app` → `xyz.up.railway.app`
- For an apex (`<yourdomain>.com`), use `ALIAS` / `ANAME` or proxy
  through Cloudflare.

**[you]** Wait 1–10 min for DNS propagation + automatic SSL provisioning.
Railway UI shows ✅ when ready.

---

## Phase 5 — Repoint OAuth + secrets (10 min)

**[you]** Google Cloud Console → Credentials → OAuth client →
Authorized redirect URIs:
- Add `https://ytft.aguywithascarf.com/oauth2callback`
- Keep the Railway URL one as fallback for now.

**[me]** Update `STREAMLIT_AUTH_REDIRECT_URI` on Railway →
`https://ytft.aguywithascarf.com/oauth2callback`.

**[me]** Trigger redeploy so the new value is picked up.

---

## Phase 6 — Final smoke test on the real domain (10 min)

**[you]** From a fresh browser tab — `https://ytft.aguywithascarf.com`:
- [ ] Public Home loads (vibe note + leaderboards visible)
- [ ] Sign in with Google → onboarding → admin sees admin pages
- [ ] Latest video popup opens (yt_popup_js works through the proxy)
- [ ] Refresh Data admin page writes to Supabase successfully
- [ ] ntfy alerts arrive after next cron tick (cron env unchanged)

**[you]** Incognito:
- [ ] All pages load
- [ ] Sign-in completes
- [ ] Cookie persists across reloads

---

## Phase 7 — Cleanup (anytime after 24h clean run)

**[you]** Streamlit Cloud → Manage app → "Delete app" (or pause).

**[you]** Google OAuth → remove the Streamlit Cloud redirect URI
(`*.streamlit.app/oauth2callback`).

**[me]** grep for hardcoded URL references and update:
```bash
git grep -i "streamlit.app\|youtube-serie-a-tracker\.streamlit"
```

**Optional**: leave Streamlit Cloud running as a cold backup; update its
`redirect_uri` to point to the new domain too.

---

## Rollback (if needed)

- **DNS** → switch CNAME back; Streamlit Cloud `*.streamlit.app` is
  still up since we don't delete until Phase 7.
- **Railway service** → pause/delete; cron services unaffected.
- **Total rollback time**: ~5 min.

---

## Estimated total time

- **Active work**: ~1.5 hours (most is waiting for DNS / SSL / deploys).
- **Calendar time**: do on a weekday morning with ~3 hours free.

---

## To start (when ready)

1. Domain choice → tell me.
2. Confirmation you've done Phase 0.
3. **You create the Railway service** (Phase 1 step 1).
4. **You paste the 5 `STREAMLIT_AUTH_*` values** into Railway's env-var
   UI (don't paste in chat).

Then ping me "ready" — I take over from Phase 1 step 2.
