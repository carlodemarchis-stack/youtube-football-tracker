from __future__ import annotations

import re
from datetime import datetime, timezone


# Stable color per video category — reused across pie charts, bars, tables.
CATEGORY_COLORS: dict[str, str] = {
    "Highlights":           "#EF553B",  # red — flagship
    "Match Recap":          "#FF6692",  # pink-red
    "Full Match":           "#AB63FA",  # purple
    "Full Match (Live)":    "#9467BD",  # deeper purple
    "Live Stream":          "#B15EFF",  # violet
    "Goals & Skills":       "#FFA15A",  # orange
    "Press Conference":     "#00CC96",  # green
    "Interview":            "#19D3F3",  # cyan
    "Training":             "#636EFA",  # blue
    "Matchday":             "#1F77B4",  # steel blue
    "Behind the Scenes":    "#FECB52",  # yellow
    "Documentary & Series": "#E377C2",  # magenta
    "Trailer & Promo":      "#FF97FF",  # pink
    "Transfer & Signings":  "#2CA02C",  # green
    "Academy & Youth":      "#B6E880",  # light green
    "Women's Football":     "#D62728",  # red-crimson
    "Throwback":            "#8C564B",  # brown
    "Merch & Kit":          "#17BECF",  # teal
    "Player Spotlight":     "#BCBD22",  # olive
    "Quiz & Games":         "#FF7F0E",  # dark orange
    "Entertainment":        "#F58518",  # amber
    "Community & CSR":      "#54A24B",  # leaf green
    "Podcast & Talk":       "#72B7B2",  # muted teal
    "Tribute & Farewell":   "#A6A6A6",  # grey
    "Other":                "#7F7F7F",  # dark grey
}


def build_category_pie(values_by_cat: dict[str, float], title: str, value_suffix: str = ""):
    """Standard category pie: drop 'Other' (mention its % in title), drop <1%,
    use CATEGORY_COLORS, donut style, sorted descending.

    Returns a plotly Figure. Caller just does st.plotly_chart(fig, ...).
    """
    import plotly.graph_objects as go
    total = sum(values_by_cat.values()) or 1
    other_pct = (values_by_cat.get("Other", 0) / total * 100)
    named = {k: v for k, v in values_by_cat.items() if k != "Other"}
    named_total = sum(named.values()) or 1
    named = {k: v for k, v in named.items() if v / named_total >= 0.01}
    cats = sorted(named.keys(), key=lambda c: -named[c])
    full_title = f"{title} (Other: {other_pct:.1f}% hidden)" if other_pct > 0 else title
    hover = "%{label}<br>%{value:,}" + (f" {value_suffix}" if value_suffix else "") + " (%{percent})<extra></extra>"
    fig = go.Figure(go.Pie(
        labels=cats,
        values=[named[c] for c in cats],
        marker=dict(colors=[CATEGORY_COLORS.get(c, "#888888") for c in cats]),
        hole=0.45, sort=False,
        textinfo="percent", hovertemplate=hover,
    ))
    fig.update_layout(
        title=full_title, margin=dict(t=40, b=20),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#FAFAFA"), legend=dict(font=dict(size=10)),
    )
    return fig


CHANNEL_PALETTE = [
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
    "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
    "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
    "#8C564B", "#E377C2", "#7F7F7F", "#BCBD22", "#17BECF",
]


def get_channel_colors(channel_names: list[str]) -> dict[str, str]:
    """Return a consistent color map for channel names."""
    return {name: CHANNEL_PALETTE[i % len(CHANNEL_PALETTE)] for i, name in enumerate(sorted(channel_names))}


def fmt_date(raw: str | None) -> str:
    """Format an ISO timestamp as a human-friendly relative string.

    Examples: '2 hours ago', 'yesterday', '3 days ago', 'Apr 12'.
    Falls back to the raw string if parsing fails.
    """
    if not raw:
        return "Never"
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = now - dt
        secs = diff.total_seconds()
        if secs < 0:
            return "just now"
        if secs < 60:
            return "just now"
        mins = int(secs // 60)
        if mins < 60:
            return f"{mins}m ago"
        hrs = int(secs // 3600)
        if hrs < 24:
            return f"{hrs}h ago"
        days = int(secs // 86400)
        if days == 1:
            return "yesterday"
        if days < 7:
            return f"{days}d ago"
        if days < 30:
            weeks = days // 7
            return f"{weeks}w ago"
        # Older than a month — show date
        return dt.strftime("%b %d")
    except Exception:
        # Can't parse — return truncated raw
        return str(raw)[:16]


def fmt_num(n: int | float) -> str:
    """Format numbers: 1.2B, 78.4M, 14.5K, or 999."""
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)

import pandas as pd


def yt_popup_js() -> str:
    """Return a <script> block that intercepts YouTube video links/window.open
    calls inside a components.html iframe and sends a postMessage to the
    parent page to open the video in an overlay player.

    Inject this into every components.html block that contains video links.
    The overlay itself is created by yt_overlay_html() in app.py.
    """
    return """<script>
(function(){
  var _origOpen = window.open;
  window.open = function(url, target, features) {
    if (url && url.indexOf('youtube.com/watch') !== -1) {
      try {
        var id = new URL(url).searchParams.get('v');
        if (id) { window.parent.postMessage({type:'ytplay',id:id},'*'); return; }
      } catch(e) {}
    }
    if (url && url.indexOf('youtube.com/embed') !== -1) {
      try {
        var parts = url.split('/embed/');
        var id2 = parts[1] ? parts[1].split('?')[0] : null;
        if (id2) { window.parent.postMessage({type:'ytplay',id:id2},'*'); return; }
      } catch(e) {}
    }
    return _origOpen.call(window, url, target, features);
  };
  document.addEventListener('click', function(e) {
    var a = e.target.closest('a[href*="youtube.com/watch"]');
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    try {
      var id = new URL(a.href).searchParams.get('v');
      if (id) window.parent.postMessage({type:'ytplay',id:id},'*');
    } catch(e2) {}
  }, true);
})();
</script>"""


def yt_overlay_html() -> str:
    """Return a components.html snippet that creates a YouTube overlay player
    in the parent Streamlit page. Call once from app.py."""
    return """
<script>
(function(){
  var p = window.parent.document;
  if (p.getElementById('yt-overlay')) return;
  var ov = p.createElement('div');
  ov.id = 'yt-overlay';
  ov.style.cssText = 'display:none;position:fixed;top:0;left:0;width:100vw;height:100vh;'+
    'background:rgba(0,0,0,0.88);z-index:100000;justify-content:center;align-items:center;cursor:pointer;';
  ov.innerHTML = '<div style="position:relative;width:80vw;max-width:960px;aspect-ratio:16/9;cursor:default">' +
    '<iframe id="yt-player" style="width:100%;height:100%;border:none;border-radius:8px" ' +
    'allowfullscreen allow="autoplay; encrypted-media"></iframe>' +
    '<button id="yt-close" style="position:absolute;top:-40px;right:0;background:none;border:none;' +
    'color:#fff;font-size:30px;cursor:pointer;padding:4px 10px">✕</button></div>';
  p.body.appendChild(ov);
  function closeOverlay() {
    ov.style.display = 'none';
    p.getElementById('yt-player').src = '';
  }
  ov.addEventListener('click', function(e) { if (e.target === ov) closeOverlay(); });
  p.getElementById('yt-close').addEventListener('click', closeOverlay);
  p.addEventListener('keydown', function(e) { if (e.key === 'Escape') closeOverlay(); });
  window.parent.addEventListener('message', function(e) {
    if (e.data && e.data.type === 'ytplay' && e.data.id) {
      ov.style.display = 'flex';
      p.getElementById('yt-player').src = 'https://www.youtube.com/embed/' + e.data.id + '?autoplay=1';
    }
  });
  // Intercept YouTube links in the parent page (st.markdown content)
  p.addEventListener('click', function(e) {
    var a = e.target.closest('a[href*="youtube.com/watch"]');
    if (!a) return;
    e.preventDefault(); e.stopPropagation();
    try {
      var id = new URL(a.href).searchParams.get('v');
      if (id) {
        ov.style.display = 'flex';
        p.getElementById('yt-player').src = 'https://www.youtube.com/embed/' + id + '?autoplay=1';
      }
    } catch(ex) {}
  }, true);
})();
</script>"""


# ── Theme Detection ───────────────────────────────────────────
# Multi-language keyword patterns. Layered classifier uses title,
# duration_seconds, and format. Rules checked in priority order; first
# match wins. Patterns are pre-compiled for speed.

_THEME_RULES: list[tuple[str, "re.Pattern"]] = [
    # Highlights (all common languages) — must check BEFORE Full Match so
    # "Juventus vs Inter | Highlights" classifies correctly even if duration
    # is long (some clubs upload 15-min cuts).
    ("Highlights", re.compile(
        r"highlight|sintesi|resumen|zusammenfassung|r[ée]sum[ée]|"
        r"hoogtepunten|melhores momentos|\bgoles del partido\b|"
        r"tous les buts|top arr[êe]ts|top saves|\btop\s?10\s?(goals|buts|gols)"
    )),
    # Press conference — coach / player pre/post-match press, "PK", quotes
    ("Press Conference", re.compile(
        r"press\s?conference|press\s?conf|presser|conferenza\s?stampa|"
        r"rueda de prensa|pressekonferenz|conf[ée]rence de presse|coletiva|"
        r"roda de premsa|\bpk\b\s*(mit|con|with|post|pre|nach|vor)|post\s?partit|"
        r"confer[eê]ncia|la conf[ée]rence de|post\s?gara|"
        r"le parole di|las palabras de|las declaraciones de|"
        r"\ben la previa\b|previa del\b|avant[- ]match|"
        r"\bstimmen\b.*\||\bpk\s+(nach|vor)\b"
    )),
    # Interview & post-match reactions
    ("Interview", re.compile(
        r"interview|intervista|entrevista|entretien|interviu|"
        r"\bparla\b|\bhabla\b|r[ée]actions?\b|post[- ]?match|post[- ]?game|"
        r"post[- ]?partido|declaraciones|dichiarazioni|"
        r"\bstimmen\s+(nach|vor|zum|aus|zu)|zona\s?mixta|zone\s?mixte|"
        r"flash\s?interview|les\s?[ée]motions|mixed\s?zone|"
        r"post\s?partita|nach dem spiel|apr[eè]s le match"
    )),
    # Podcast & Talk — long-format conversational shows
    ("Podcast & Talk", re.compile(
        r"\bpodcast\b|fpl\s?podcast|radio\s?tv\s?serie\s?a|storie di serie a con|"
        r"\bcharlamos\b|l[‘’]int[ée]grale|talk\s?show|\btertulia\b|"
        r"\bsmall talk\b|\buncut\b"
    )),
    # Tribute & Farewell — retirement, memorials, legacy goodbyes
    ("Tribute & Farewell", re.compile(
        r"merci\s+\w+\s*[!?¡]|\babschieds(spiel|rede|party)\b|hommage|"
        r"\bfarewell\b|\badi[oó]s\b|\bdespedida\b|\baddio\b|in memory|in memoriam|"
        r"\btribute\b|\btributo\b|\blegenda\b|legacy of|\bsuperga\b|"
        r"retirement|retires?\b|ritira|se retira|\bwe salute\b"
    )),
    # Celebration / Trophy — parade, title won, champion
    ("Celebration & Trophy", re.compile(
        r"\bparade\b|\btrophy\b|\btrofeo\b|\btrophée\b|\bpokal\b|"
        r"\bchampion(s|ne)?\b|\bcampe[oó]n(es)?\b|\bcampione\b|\bmeister\b|"
        r"\btitle\b|\btitolo\b|\bt[ií]tulo\b|\bscudetto\b|"
        r"\bcelebrat|festeggia|celebraci[oó]n|festejos|party\s+time|"
        r"\bcoupe\b|\bcopa\b|\bcoppa\b|\bcup final\b|trophy lift|"
        r"we are the champion|\bsupercup\b|\bsupercopa\b|\bsupercoppa\b"
    )),
    # Training
    ("Training", re.compile(
        r"\btraining\b|allenamento|entrenamiento|entra[îi]nement|treino|"
        r"\bprep\b|warm.?up|pre\s?season|pretemporada|pr[ée]saison|"
        r"trainingslager|\bpremi[eè]re s[ée]ance\b|primera sesi[oó]n"
    )),
    # Transfer / Welcome / Signing (extended)
    ("Transfer & Signings", re.compile(
        r"\bwelcome\b|signing|ufficiale|oficial|offiziell|officiel|"
        r"\bmercato\b|\bfichaje\b|transfer|transfer[êe]ncia|unveil|"
        r"presentazione|presentaci[oó]n|\bis here\b|ya es\b|vuelve a\b|"
        r"de retour|bienvenu|bienvenido|benvenuto|willkommen|"
        r"extends until|\brenew(al|s|ed)?\b|\brinnova\b|\brenueva\b|"
        r"prolong(e|ation|aci[oó]n|amento)|prolunga|"
        r"promesse\s+[\w\s]+\s?20\d{2}|\bhas signed\b|\bsigned\b|"
        r"pr[ée]sent[ée]\s+[àa]\s+la presse|\bday\s?1\b|nouvel\s+(attaquant|d[ée]fenseur|gardien|milieu)"
    )),
    # Women’s football (flag before Academy since both can appear)
    ("Women’s Football", re.compile(
        r"\bwomen\b|femminil|femenin|femenil|\bfrauen\b|f[ée]minin"
    )),
    # Academy / youth
    ("Academy & Youth", re.compile(
        r"\bacademy\b|primavera|\bu\s?1[5-9]\b|\bu\s?2[0-3]\b|"
        r"\bcantera\b|jugend|jeunes|giovanili|juvenil|youth"
    )),
    # Matchday prep / preview / journey
    ("Matchday", re.compile(
        r"matchday|gameday|giornata|d[ií]a de partido|spieltag|jour de match|"
        r"pre\s?match|pre.?game|\blineup\b|starting\s?xi|convocat|"
        r"teamnews|team\s?news|arrivo allo stadio|llegada al estadio|"
        r"\barrive[sd]?\s+(for|at)\b|\bh[- ]?\d+\s?avant\b|\bambiance\b|"
        r"\bderby\b|\bderbi\b|\bklassiker\b|\bel\s?cl[áa]sico\b|"
        r"ankunft|arrivée|llegada|journ[ée]e\b.*ligue|jornada\b.*laliga|"
        r"\bfull.?time\b|scenes at\b|in the building"
    )),
    # Behind the scenes / inside / locker room / "no comment" / access
    ("Behind the Scenes", re.compile(
        r"behind the scene|dietro le quinte|detr[aá]s de|hinter den kulissen|"
        r"coulisses|\bvlog\b|\binside\b|backstage|tunnel cam|\bbts\b|"
        r"no comment|vestiaire|dressing.?room|locker.?room|spogliatoio|"
        r"travel\s?asmr|zimmerduell|kabinen[- ]?(ansprache|insights|talk)|"
        r"\binside training\b|inside the club|"
        r"\bful\s?access\b|\baccess all\b|\bref\s?cam\b|\bpov\b|"
        r"\btunnel\b|travel\s?log|\bday in\b.*with|a day in\b|"
        r"dans les souvenirs|im herzen von"
    )),
    # Documentary / series (episodic content) — before Trailer so ‘Ep2’ sticks
    ("Documentary & Series", re.compile(
        r"\bdoku\b|documentary|documental|documentario|\blong.?format\b|"
        r"\b[ée]pisode\s?\d|\bepisode\s?\d|\bep\.?\s?\d|\bsaison\s?\d|"
        r"\bseason\s?\d|\bseries\b|\bserie\s?\d|\bcap[ií]tulo\s?\d|"
        r"all about\s|\bthe howl\b|made in paris|the film|il film|"
        r"la renaissance|miracle men|l[‘’]aventure"
    )),
    # Trailer / promo
    ("Trailer & Promo", re.compile(
        r"\btrailer\b|\bpromo\b|\bteaser\b|\bpreview\b|anteprima|avance|vorschau"
    )),
    # Merch & Kit
    ("Merch & Kit", re.compile(
        r"\bkit\b|\bshirt\b|\bjersey\b|\bmaillot\b|\bcamiseta\b|\btrikot\b|"
        r"\bmerch\b|\bcollection\b|\bcollezione\b|\bvintage\b|\bhome kit\b|"
        r"\baway kit\b|\bthird kit\b|nuev[ao] camiseta|new kit|\bmaglia\b"
    )),
    # Throwback / retro / birthday
    ("Throwback", re.compile(
        r"\b#?tb\b|throwback|flashback|\bprime\s\w+|top\s?1?\d+\s+(goal|buts|gols|skill|save)|"
        r"\bremember\b|classic\b|storico|histórico|hist[óo]rica|"
        r"\bvintage goals?\b|legendary|\blegendario\b|"
        r"\banniversaire\b|\banniversary\b|\bgeburtstag\b|\bcumplea[ñn]os\b|"
        r"\d+\s?years ago|\bin memory\b|\bon this day\b|\botd\b|"
        r"joyeux anniversaire|feliz cumple|happy birthday|buon compleanno"
    )),
    # Community / CSR / foundation
    ("Community & CSR", re.compile(
        r"\bcommunity\b|fondazione|fundaci[óo]n|fundazioa|fondation|stiftung|"
        r"foundation|\bcharity\b|\bcsr\b|visite?\s+(à|au|de|en|del|della|dello)|"
        r"hospital|b[ée]n[ée]vol|voluntari|awareness|donation|"
        r"\blap of appreciation\b|d[ií]a internacional|\bsamaritans\b|"
        r"together against|\bmovember\b|world cup of kindness|"
        r"h[ée]roes an[oó]nimos"
    )),
    # Player Spotlight / Player Cam / portraits
    ("Player Spotlight", re.compile(
        r"in focus|player cam|player of the (week|month|year)|\bpotm\b|"
        r"spotlight|focus on\b|riflettori|profilo|\bprofile\b|close.?up|"
        r"every angle|\bposter\b|\bday with\b|une journ[ée]e avec|un d[ií]a con|"
        r"\bportrait\b|\bporträt\b|\bretrato\b|j[- ]?\d+\s+avec\b"
    )),
    # Quiz / games / FIFA gaming
    ("Quiz & Games", re.compile(
        r"\bquiz+\b|\bquizz?\b|¿qui[eé]n|tu pr[ée]f[èe]res|ti preferisci|"
        r"\bchallenge\b|\bdefi\b|\bsfida\b|who knows|connais.?tu|"
        r"guess the|adivina|indovina|"
        r"fifa\s?\d*\s?ratings|fifa\s?\d*\s?prediction|uno\s?showdown|"
        r"petit\s?bac|\ba[- ]?to[- ]?z\b|a[- ]z\s+(of|player)|"
        r"build your perfect|rate the|who[‘’]s your pick|\btier list\b|"
        r"\bfc\s?\d{2}\s?toty\b|\bwho[‘’]s better\b|\bendevina\b|"
        r"goal recreation|\bvs\b.*scoring|doha quest"
    )),
    # Entertainment / comedy / pop-culture / fun social
    ("Entertainment", re.compile(
        r"half.?time show|bad bunny|squid game|\bfilter\b|\bprank\b|"
        r"comedia|comedy|\bblooper\b|tu ris tu perds|funny moments|"
        r"mr beast|concert|\baged like\b|\bfunny\b|valentín|valentine|"
        r"\bchristmas\b|\bnatale\b|\bnavidad\b|\bno[ëe]l\b|\bweihnacht\b|"
        r"feliz año|happy new year|bonne ann[ée]e|buon anno"
    )),
    # Goal compilations — "every goal", "all goals", "best goals of the month"
    ("Goal Compilation", re.compile(
        r"\bevery\s+(goal|treffer|tor|but|gol)\b|"
        r"\ball\s+(goal|treffer|tor|but|gol)s\b|"
        r"\balle\s+(tore|treffer)\b|"
        r"\btous\s+les\s+(buts|gols)\b|"
        r"\btodos\s+los\s+goles\b|"
        r"\blos\s+mejores\s+goles\b|"
        r"\bbest\s+of\s+(the\s+)?(month|year|season|week)\b|"
        r"\btore?\s+des\s+monats\b|"
        r"\bgoals?\s+of\s+the\s+(month|year|season|week)\b|"
        r"\b(premier league|laliga|serie a|ligue 1|bundesliga)\s+goals\b|"
        r"\bgol(s|es)?\s+del?\s+(mes|temporada|a[ñn]o)\b|"
        r"1\s?hour of.*\b(goal|save)\b|"
        r"\bbiggest.*moments\b|\bevery\s+\w+\s+goal\b"
    )),
    # Goals & skills (viral short plays)
    ("Goals & Skills", re.compile(
        r"\bbest goal|\btop goal|\bskill\b|dribbl|nutmeg|\btrick\b|\bassist\b|"
        r"\bsave\b|\bparata\b|compilation|\bfree.?kick\b|\bpunizione\b|"
        r"golazo|gola[çc]o|\bstunner\b|\bscreamer\b|\bbanger\b|"
        r"wonder.?goal|long.?range|\bvolley\b|bicycle kick|chilena|"
        r"\bstrike\b|\bgoalazo\b|poetry in motion|\bclass[ie]c goals?\b|"
        r"\brocket\b|\bthunderbolt\b|\bheader(s|ed)?\b"
    )),
    # Match Recap (narrative, usually has team names + score + outcome verb).
    # Runs late so Highlights / Full Match grab theirs first.
    ("Match Recap", re.compile(
        r"\b\d\s?[-–]\s?\d\b|\bvittoria\b|\bvictoria\b|\bvictoire\b|"
        r"\bsconfitta\b|\bd[ée]faite\b|\bdefeat\b|\bderrota\b|"
        r"\brimonta\b|\bcomeback\b|\bremontada\b|\bremonta\b|"
        r"\bpareggio\b|\bempate\b|\bdraw\b|\b[ée]crase\b|\bdouche\b|"
        r"\baccroche\b|accrochent|\bbat(s|tu|tent)\b|\bb[ae]ts\b|"
        r"\bwin\b|\bwins\b|\bloss\b|\bhome win\b|\baway win\b|"
        r"\bs[‘’]impose\b|\bfait tomber\b"
    )),
]

# Match-pattern: "Team A vs Team B" / "Team A - Team B" / "Team A v Team B"
_MATCH_PATTERN = re.compile(r"\bv(s\.?)?\b|\s[-–—]\s|\bvs\b")


def detect_theme(
    title: str,
    duration_seconds: int | None = None,
    format_: str | None = None,
) -> str:
    """Classify a video into a theme. Uses title + duration + format signals.

    Priority:
      1. Highlights keyword → Highlights
      2. Keyword-based rules in priority order
      3. Duration/format fallbacks:
         - live ≥ 60 min → Full Match (Live)
         - live < 60 min → Live Stream
         - VOD ≥ 80 min + match-pattern in title → Full Match
         - everything else → Other
    """
    t = (title or "").lower()

    # 1. Keyword rules in order
    for theme, rx in _THEME_RULES:
        if rx.search(t):
            return theme

    # 2. Duration / format fallbacks
    dur = duration_seconds or 0
    fmt = (format_ or "").lower()
    if fmt == "live":
        if dur >= 3600:
            return "Full Match (Live)"
        return "Live Stream"
    if dur >= 4800 and _MATCH_PATTERN.search(t):
        return "Full Match"

    return "Other"


def classify_videos(videos: list[dict]) -> list[dict]:
    for v in videos:
        v["category"] = detect_theme(
            v.get("title", ""),
            v.get("duration_seconds"),
            v.get("format"),
        )
    return videos


# ── Stats Computation ─────────────────────────────────────────

def compute_tier_stats(df: pd.DataFrame, current_year: int | None = None) -> dict:
    if current_year is None:
        current_year = datetime.now(timezone.utc).year

    if df.empty:
        return {"top_10": {}, "top_50": {}, "top_100": {}, "current_year": {}}

    df = df.copy()
    if "published_at" in df.columns:
        df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
        now = pd.Timestamp.now(tz="UTC")
        df["age_days"] = (now - df["published_at"]).dt.days

    def tier_summary(subset: pd.DataFrame) -> dict:
        result = {
            "count": len(subset),
            "avg_views": int(subset["view_count"].mean()) if len(subset) > 0 else 0,
            "avg_likes": int(subset["like_count"].mean()) if len(subset) > 0 else 0,
            "avg_comments": int(subset["comment_count"].mean()) if len(subset) > 0 else 0,
        }
        if "age_days" in subset.columns and len(subset) > 0:
            result["avg_age_days"] = int(subset["age_days"].mean())
            result["avg_age_years"] = round(result["avg_age_days"] / 365.25, 1)
        return result

    stats = {
        "top_10": tier_summary(df.head(10)),
        "top_50": tier_summary(df.head(50)),
        "top_100": tier_summary(df.head(100)),
    }

    if "published_at" in df.columns:
        current_year_videos = df[df["published_at"].dt.year == current_year]
        current_year_in_top100 = df.head(100)
        current_year_in_top100 = current_year_in_top100[
            current_year_in_top100["published_at"].dt.year == current_year
        ]
        stats["current_year"] = {
            "total_videos_this_year": len(current_year_videos),
            "in_top_100": len(current_year_in_top100),
            "positions": current_year_in_top100.index.tolist() if len(current_year_in_top100) > 0 else [],
            "avg_views": int(current_year_videos["view_count"].mean()) if len(current_year_videos) > 0 else 0,
        }

    return stats


def compute_theme_distribution(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "category" not in df.columns:
        return pd.DataFrame(columns=["category", "count", "pct"])
    dist = df["category"].value_counts().reset_index()
    dist.columns = ["category", "count"]
    dist["pct"] = (dist["count"] / dist["count"].sum() * 100).round(1)
    return dist


def compute_channel_comparison(channels: list[dict]) -> pd.DataFrame:
    if not channels:
        return pd.DataFrame()
    df = pd.DataFrame(channels)
    if "video_count" in df.columns and "total_views" in df.columns:
        df["avg_views_per_video"] = (df["total_views"] / df["video_count"].replace(0, 1)).astype(int)
    return df.sort_values("total_views", ascending=False)
