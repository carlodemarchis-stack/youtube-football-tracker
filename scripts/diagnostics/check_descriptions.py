import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()
from src.database import Database
db = Database(os.environ['SUPABASE_URL'],
              os.environ.get('SUPABASE_SERVICE_KEY') or os.environ['SUPABASE_KEY'])

chans = db.client.table('channels').select('id,entity_type').execute().data or []
top5 = {c['id'] for c in chans if c.get('entity_type') in ('Club', 'League')}
nontop5 = [c['id'] for c in chans if c['id'] not in top5]

since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

def fill(ids, label):
    total = nn = 0
    sample = []
    for i in range(0, len(ids), 50):
        rows = (db.client.table('videos')
                .select('youtube_video_id,description,last_updated,published_at')
                .in_('channel_id', ids[i:i+50])
                .gte('last_updated', since)
                .limit(1000).execute().data or [])
        for r in rows:
            total += 1
            d = r.get('description')
            if d is not None and d != '':
                nn += 1
                if len(sample) < 3:
                    sample.append((r['youtube_video_id'], len(d), (d[:60].replace(chr(10),' '))))
    print(f"{label}: {nn}/{total} have non-empty description (last_updated >= {since[:10]})")
    for s in sample:
        print(f"    {s[0]}  len={s[1]}  {s[2]}")

fill(nontop5, "NON-top5 cohorts (never backfilled)")
fill(list(top5), "top-5")
