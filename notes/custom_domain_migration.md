# Custom domain migration — plan

Decision (May 2026): **migrate from Streamlit Community Cloud to Railway**
when ready. Path B from the options below. Not now — revisit after launch
stabilises.

## Why Railway (Option B)

- Already running 7 cron services on Railway → single host for app + jobs.
- Railway has first-class custom-domain support (CNAME + auto SSL).
- Better visibility on traffic / logs than Streamlit Cloud.
- Avoids reverse-proxy quirks (WebSocket forwarding, cookie domains).
- ~$10/mo extra for the web service vs. free Streamlit Cloud — trivial.

## Other paths considered (and shelved)

### Option A — Cloudflare Worker reverse proxy
- DNS: `app.yourdomain.com` → CNAME → `<app>.streamlit.app`.
- Cloudflare Worker proxies HTTP + WebSocket, rewrites `Host:` header.
- Free; ~30 min setup.
- Trade-offs: WebSocket forwarding is fiddly (`Upgrade`/`Connection`
  headers), and one extra hop in the request path.
- Use as a *temporary* bridge if Railway migration can't happen pre-launch.

### Option C — Plain 302 redirect
- DNS-level redirect to the `.streamlit.app` URL.
- Cheapest, but the address bar reveals the streamlit.app host after the
  redirect — not a real custom-domain experience.

## Migration checklist (when ready)

1. **Add a Streamlit web service on Railway.**
   - New service in the YTFT project, repo same as crons (main branch).
   - Start command: `streamlit run app.py --server.port=$PORT --server.address=0.0.0.0`
   - Inherit env vars from cron services (or set via dashboard):
     - `SUPABASE_URL`, `SUPABASE_KEY` (anon — same as Streamlit Cloud now)
     - `SUPABASE_SERVICE_KEY`
     - `YOUTUBE_API_KEY`
     - `ANTHROPIC_API_KEY`
     - Streamlit secrets equivalent: `[auth]` block — needs porting
       from `secrets.toml` (Streamlit Cloud's web UI) to env vars or a
       file. Streamlit reads `secrets.toml` from `.streamlit/` —
       Railway can mount via build step or env-var-to-file shim.
   - Resource: start with 0.5 vCPU / 512MB, scale if needed.

2. **Configure custom domain on Railway.**
   - Service → Settings → Domains → Add custom domain.
   - Railway gives a CNAME target (`<service>.up.railway.app`).
   - Add CNAME at the registrar: `app.yourdomain.com` → that target.
   - SSL provisioning is automatic (Let's Encrypt) once DNS resolves.

3. **Update Google OAuth.**
   - Google Cloud Console → OAuth 2.0 Client ID → Authorized redirect URIs:
     - Add `https://app.yourdomain.com/oauth2callback`
     - Keep the streamlit.app URL during cutover, remove after switch.
   - Update `secrets.toml`-equivalent on Railway:
     - `[auth] redirect_uri = "https://app.yourdomain.com/oauth2callback"`

4. **Smoke-test on the Railway URL** (the `*.up.railway.app` one) before
   pointing DNS:
   - Anonymous user → Home loads.
   - Google sign-in works → onboarding form → admin sees admin pages.
   - Latest videos popup opens correctly (yt_popup_js iframe message).
   - Verify ntfy alerts still arrive after the next cron tick (Railway
     env vars carry over, no change needed).

5. **Cut over DNS** to the Railway target.
   - Watch the Streamlit Cloud app for a day in case anyone has it
     bookmarked — keep it alive, optionally redirect.

6. **Decommission Streamlit Community Cloud** after a week of clean
   Railway operation. (Or keep it as cold backup — costs nothing.)

## Things to grep before migrating

```bash
git grep -i "streamlit.app\|youtube-serie-a-tracker"
```
Anything hardcoding the old URL needs an update (mailto links, OAuth
redirect, README, etc.).

## Streamlit Cloud quirks Railway won't have

- Cold starts: Streamlit Cloud spins idle apps down (~10 min). Railway
  keeps them warm — better for "shared a link, click in 30 min" UX.
- Resource limits: Streamlit Cloud free tier caps memory; Railway is
  pay-as-you-use.
- Logs: Railway logs are searchable in their dashboard + CLI; Streamlit
  Cloud's "Manage app" log view is more limited.

## Streamlit-on-Railway gotchas to remember

- **secrets.toml** — Streamlit reads `.streamlit/secrets.toml`. On
  Railway, easiest pattern: write the file at container startup from an
  env var. Or set every secret as a separate env var and have `app.py`
  bootstrap copy them in (it already does this for SUPABASE_URL,
  SUPABASE_KEY, YOUTUBE_API_KEY at the top of the file).
- **WebSockets**: Railway's reverse proxy handles them natively, no
  extra config needed.
- **Memory**: Streamlit holds session state per user. Public traffic +
  Streamlit's memory model = grow gradually. Watch the first day.
