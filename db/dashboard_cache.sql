-- Persisted pre-computed dashboard data so the Streamlit pages can read a
-- single row instead of paginating ~5K-row queries on each first-of-the-hour
-- visitor. Refreshed by the daily refresh + hourly_rss crons whenever
-- underlying data changes.
--
-- name      — what was computed (e.g. 'format_trend')
-- scope_key — the filter scope ('all' | 'league:<name>' | 'club:<uuid>')
-- payload   — the JSON-serializable result
create table if not exists public.dashboard_cache (
    name        text not null,
    scope_key   text not null,
    payload     jsonb not null,
    computed_at timestamptz not null default now(),
    primary key (name, scope_key)
);

create index if not exists dashboard_cache_computed_at_idx
    on public.dashboard_cache (computed_at desc);
