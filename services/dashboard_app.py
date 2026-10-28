"""Analytics dashboard (Telegram Mini App + browser) for the posting bot.

Serves a mobile-friendly page at "/" and a JSON feed at "/api/data" from the same
database the bot uses: channel member growth (from the periodic snapshots taken by
services/stats.py), post activity and per-channel breakdowns. Protected by the same
?key= token as the old dashboard (DASHBOARD_TOKEN).
"""
import logging
from datetime import datetime, timedelta, timezone

from aiohttp import web
from sqlalchemy import func, select

from config import get_settings
from db import session
from models import Category, Channel, ChannelStatSnapshot, ModeratedGroup, Post, PostStatus, PostTarget

logger = logging.getLogger(__name__)


def _naive(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _authorized(request: web.Request) -> bool:
    token = get_settings().dashboard_token
    return not token or request.query.get("key") == token


def _delta(series, now, days):
    """Growth over the last N days from a time-sorted [(datetime, count)] series."""
    if not series:
        return None
    latest = series[-1][1]
    cutoff = now - timedelta(days=days)
    base = None
    for t, c in series:
        if t <= cutoff:
            base = c
        else:
            break
    if base is None:
        base = series[0][1]  # channel is younger than the window: growth since first reading
    return latest - base


async def _handle_api(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    now = datetime.utcnow()
    async with session() as s:
        channels = (await s.execute(select(Channel).order_by(Channel.title))).scalars().all()
        snaps = (await s.execute(
            select(ChannelStatSnapshot.channel_id, ChannelStatSnapshot.member_count, ChannelStatSnapshot.taken_at)
            .order_by(ChannelStatSnapshot.taken_at))).all()
        sent_rows = (await s.execute(
            select(PostTarget.channel_id, func.count()).where(PostTarget.message_id.is_not(None))
            .group_by(PostTarget.channel_id))).all()
        status_rows = (await s.execute(select(Post.status, func.count()).group_by(Post.status))).all()
        created = (await s.execute(select(Post.created_at).where(Post.owner_user_id != 0))).scalars().all()
        created_sys = (await s.execute(select(Post.created_at).where(Post.owner_user_id == 0))).scalars().all()
        category_count = (await s.execute(select(func.count()).select_from(Category))).scalar_one()
        group_count = (await s.execute(select(func.count()).select_from(ModeratedGroup))).scalar_one()

    by_channel = {}
    for cid, count, taken in snaps:
        by_channel.setdefault(cid, []).append((_naive(taken), count))
    posts_by_channel = {cid: n for cid, n in sent_rows}

    rows = []
    total_series = {}
    for ch in channels:
        series = by_channel.get(ch.id, [])
        for t, c in series:
            key = t.replace(minute=0, second=0, microsecond=0, hour=(t.hour // 6) * 6)
            total_series.setdefault(key, {})[ch.id] = c
        rows.append({
            "id": ch.id,
            "title": ch.title,
            "members": series[-1][1] if series else None,
            "d1": _delta(series, now, 1),
            "d7": _delta(series, now, 7),
            "d30": _delta(series, now, 30),
            "posts": posts_by_channel.get(ch.id, 0),
            "auto_approve": bool(ch.auto_approve_members),
            "series": [[t.isoformat(), c] for t, c in series[-180:]],
        })

    # Combined timeline: carry each channel's last known value forward bucket by bucket.
    combined = []
    last = {}
    for key in sorted(total_series):
        last.update(total_series[key])
        combined.append([key.isoformat(), sum(last.values())])
    totals = [(datetime.fromisoformat(t), c) for t, c in combined]

    day_counts = {}
    for dt in created:
        d = _naive(dt)
        if d and d >= now - timedelta(days=14):
            day_counts.setdefault(d.date().isoformat(), [0, 0])[0] += 1
    for dt in created_sys:
        d = _naive(dt)
        if d and d >= now - timedelta(days=14):
            day_counts.setdefault(d.date().isoformat(), [0, 0])[1] += 1
    per_day = []
    for i in range(13, -1, -1):
        day = (now - timedelta(days=i)).date().isoformat()
        c = day_counts.get(day, [0, 0])
        per_day.append({"day": day, "composed": c[0], "reposted": c[1]})

    data = {
        "generated": now.isoformat() + "Z",
        "summary": {
            "channels": len(channels),
            "members": sum(r["members"] or 0 for r in rows),
            "d1": _delta(totals, now, 1),
            "d7": _delta(totals, now, 7),
            "d30": _delta(totals, now, 30),
            "categories": category_count,
            "groups": group_count,
            "posts_composed": len(created),
            "posts_reposted": len(created_sys),
        },
        "status": {getattr(st, "value", str(st)): n for st, n in status_rows},
        "total_series": combined[-240:],
        "per_day": per_day,
        "channels": rows,
    }
    return web.json_response(data)


async def _handle_page(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.Response(status=401, text="Missing or wrong ?key=")
    return web.Response(text=PAGE, content_type="text/html")


def register(app: web.Application) -> None:
    app.router.add_get("/", _handle_page)
    app.router.add_get("/api/data", _handle_api)


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Channel Analytics</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:#0e1621;--card:#17212b;--line:#243447;--txt:#e8eef5;--mut:#8b9bb0;--up:#35c47c;--dn:#ef5b5b;--acc:#f6b90f;--blue:#4aa3ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.4 -apple-system,Segoe UI,Roboto,sans-serif;padding:14px 14px 40px}
h1{font-size:18px;margin:2px 0 2px}.sub{color:var(--mut);font-size:12px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.04em}.v{font-size:22px;font-weight:700;margin-top:2px}
.up{color:var(--up)}.dn{color:var(--dn)}.mut{color:var(--mut)}
.tabs{display:flex;gap:6px;margin:6px 0 10px;flex-wrap:wrap}.tab{background:var(--card);border:1px solid var(--line);color:var(--mut);padding:6px 12px;border-radius:20px;font-size:12px;cursor:pointer}
.tab.on{background:var(--acc);color:#111;border-color:var(--acc);font-weight:600}
h2{font-size:14px;margin:16px 0 8px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.ch{margin-bottom:10px}.top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}.name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pills{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}.pill{font-size:11px;padding:2px 8px;border-radius:10px;background:#1f2e3e}
svg{width:100%;display:block}.bars{display:flex;align-items:flex-end;gap:4px;height:90px}.bar{flex:1;display:flex;flex-direction:column;justify-content:flex-end;min-width:0}
.bar i{display:block;border-radius:3px 3px 0 0}.lab{display:flex;gap:4px;margin-top:4px}.lab span{flex:1;text-align:center;font-size:9px;color:var(--mut)}
.err{color:var(--dn);padding:20px;text-align:center}
</style></head><body>
<h1>Channel Analytics</h1><div class="sub" id="gen">Loading...</div>
<div id="root"></div>
<script>
var tg=window.Telegram&&window.Telegram.WebApp; if(tg){tg.ready();tg.expand();}
var range=30, sortBy='members', DATA=null;
function fmt(n){return n==null?'-':Number(n).toLocaleString();}
function dl(n){if(n==null)return '<span class="mut">-</span>';var c=n>0?'up':(n<0?'dn':'mut');return '<span class="'+c+'">'+(n>0?'+':'')+fmt(n)+'</span>';}
function cut(series){var d=Date.now()-range*864e5;if(range===0)return series;return series.filter(function(p){return new Date(p[0]+(p[0].indexOf('Z')<0?'Z':'')).getTime()>=d;});}
function line(series,h,color){
  if(!series||series.length<2)return '<div class="mut" style="padding:14px 0;font-size:12px">Not enough data yet (a reading is saved every 6 hours).</div>';
  var w=320,pad=4,vs=series.map(function(p){return p[1];}),mn=Math.min.apply(null,vs),mx=Math.max.apply(null,vs);if(mx===mn){mx=mn+1;}
  var pts=series.map(function(p,i){var x=pad+(w-2*pad)*i/(series.length-1),y=h-pad-(h-2*pad)*(p[1]-mn)/(mx-mn);return x.toFixed(1)+','+y.toFixed(1);});
  var area='M'+pts[0]+' L'+pts.join(' L')+' L'+(w-pad)+','+(h-pad)+' L'+pad+','+(h-pad)+' Z';
  return '<svg viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" style="height:'+h+'px"><path d="'+area+'" fill="'+color+'" opacity=".15"/><polyline points="'+pts.join(' ')+'" fill="none" stroke="'+color+'" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>'+
  '<div class="top mut" style="font-size:11px"><span>'+fmt(vs[0])+'</span><span>min '+fmt(mn)+' / max '+fmt(mx)+'</span><span>'+fmt(vs[vs.length-1])+'</span></div>';
}
function render(){
  var d=DATA,s=d.summary,h='';
  h+='<div class="grid">'+
   '<div class="card"><div class="k">Total members</div><div class="v">'+fmt(s.members)+'</div></div>'+
   '<div class="card"><div class="k">Channels</div><div class="v">'+s.channels+'</div></div>'+
   '<div class="card"><div class="k">Growth 24h</div><div class="v">'+dl(s.d1)+'</div></div>'+
   '<div class="card"><div class="k">Growth 7d</div><div class="v">'+dl(s.d7)+'</div></div>'+
   '<div class="card"><div class="k">Growth 30d</div><div class="v">'+dl(s.d30)+'</div></div>'+
   '<div class="card"><div class="k">Posts (you / reposts)</div><div class="v">'+fmt(s.posts_composed)+' <span class="mut" style="font-size:14px">/ '+fmt(s.posts_reposted)+'</span></div></div></div>';
  h+='<div class="tabs">'+[[7,'7d'],[30,'30d'],[90,'90d'],[0,'All']].map(function(r){return '<div class="tab '+(range===r[0]?'on':'')+'" onclick="setRange('+r[0]+')">'+r[1]+'</div>';}).join('')+'</div>';
  h+='<div class="card"><div class="k">All channels combined</div>'+line(cut(d.total_series),110,'#f6b90f')+'</div>';
  var st=d.status,keys=Object.keys(st);
  h+='<h2>Posts</h2><div class="card"><div class="pills" style="margin:0 0 10px">'+keys.map(function(k){return '<span class="pill">'+k+': <b>'+st[k]+'</b></span>';}).join('')+'</div>';
  var mx=Math.max.apply(null,d.per_day.map(function(x){return x.composed+x.reposted;}).concat([1]));
  h+='<div class="k" style="margin-bottom:6px">Created per day (last 14 days) - gold: composed, blue: reposted</div><div class="bars">'+d.per_day.map(function(x){var a=90*x.composed/mx,b=90*x.reposted/mx;return '<div class="bar" title="'+x.day+'"><i style="height:'+b+'px;background:#4aa3ff"></i><i style="height:'+a+'px;background:#f6b90f"></i></div>';}).join('')+'</div>'+
    '<div class="lab">'+d.per_day.map(function(x){return '<span>'+x.day.slice(8)+'</span>';}).join('')+'</div></div>';
  h+='<h2>Channels</h2><div class="tabs">'+[['members','Members'],['d7','7d growth'],['d30','30d growth'],['posts','Posts']].map(function(r){return '<div class="tab '+(sortBy===r[0]?'on':'')+'" onclick="setSort(\''+r[0]+'\')">'+r[1]+'</div>';}).join('')+'</div>';
  var rows=d.channels.slice().sort(function(a,b){return (b[sortBy]==null?-1e9:b[sortBy])-(a[sortBy]==null?-1e9:a[sortBy]);});
  rows.forEach(function(c){
    h+='<div class="card ch"><div class="top"><div class="name">'+esc(c.title)+'</div><div style="font-weight:700">'+fmt(c.members)+'</div></div>'+
      '<div class="pills"><span class="pill">24h '+dl(c.d1)+'</span><span class="pill">7d '+dl(c.d7)+'</span><span class="pill">30d '+dl(c.d30)+'</span><span class="pill">posts '+fmt(c.posts)+'</span>'+(c.auto_approve?'<span class="pill">auto-approve</span>':'')+'</div>'+
      '<div style="margin-top:8px">'+line(cut(c.series),60,'#35c47c')+'</div></div>';
  });
  document.getElementById('root').innerHTML=h;
  document.getElementById('gen').textContent='Updated '+new Date(d.generated).toLocaleString();
}
function esc(t){var e=document.createElement('div');e.textContent=t==null?'':t;return e.innerHTML;}
function setRange(r){range=r;render();} function setSort(k){sortBy=k;render();}
function load(){
  fetch('/api/data'+location.search).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})
   .then(function(j){DATA=j;render();})
   .catch(function(e){document.getElementById('root').innerHTML='<div class="err">Could not load data ('+e.message+'). Check the link includes ?key=...</div>';});
}
load(); setInterval(load,120000);
</script></body></html>"""
