"""Analytics dashboard (Telegram Mini App + browser) for the posting bot.

Serves a mobile-friendly page at "/" and a JSON feed at "/api/data" from the same
database the bot uses. Protected by the ?key= token (DASHBOARD_TOKEN).

Dimensions: member growth per channel and combined (daily net change, % growth, 30-day
projection, best/worst days), channel share and leaderboard, posting volume over time,
when you post (weekday x hour heatmap), content-type mix, post status, auto-delete /
repeat / scheduled usage, how posting relates to growth (posting vs quiet days and the
average growth in the 6 hours after a post, by hour of day), audience from join requests,
file sharing, forwarding rules, moderation and half-time replies.
All times are East Africa Time (UTC+3).
"""
import logging
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

from aiohttp import web
from sqlalchemy import func, select, text

from config import get_settings
from db import session
from models import (BroadcastUser, Category, Channel, ChannelStatSnapshot, ModeratedGroup, Post, PostTarget,
                    RecurringMessage, RepostRule, SourceChannel)

logger = logging.getLogger(__name__)
EAT = timedelta(hours=3)


def _naive(dt):
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _authorized(request: web.Request) -> bool:
    token = get_settings().dashboard_token
    return not token or request.query.get("key") == token


def _val(x):
    return getattr(x, "value", None) or str(x)


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
        base = series[0][1]
    return latest - base


def _eat_day(t):
    return (t + EAT).date()


def compute(now, channels, snaps, posts, targets, audience, counts, extras):
    """Pure function: turns raw rows into the JSON the page draws (easy to test)."""
    today = _eat_day(now)
    days90 = [(today - timedelta(days=i)) for i in range(89, -1, -1)]
    day_idx = {d: i for i, d in enumerate(days90)}

    # ---- member series per channel -------------------------------------------------
    by_channel = {}
    for cid, count, taken in snaps:
        t = _naive(taken)
        if t is not None:
            by_channel.setdefault(cid, []).append((t, count))
    for v in by_channel.values():
        v.sort(key=lambda p: p[0])
    times = {cid: [p[0] for p in v] for cid, v in by_channel.items()}

    def value_at(cid, t):
        ts = times.get(cid)
        if not ts:
            return None
        i = bisect_right(ts, t)
        return by_channel[cid][i - 1][1] if i else None

    # last reading of every EAT day, forward filled (None until the first reading)
    daily = {}
    for ch in channels:
        series = by_channel.get(ch["id"], [])
        last_of_day = {}
        for t, c in series:
            last_of_day[_eat_day(t)] = c
        before = [c for t, c in series if _eat_day(t) < days90[0]]
        cur = before[-1] if before else None
        out = []
        for d in days90:
            if d in last_of_day:
                cur = last_of_day[d]
            out.append(cur)
        daily[ch["id"]] = out

    # posts sent per channel per EAT day
    sent_days = {}
    for cid, sent in targets:
        sent = _naive(sent)
        if sent is not None:
            sent_days.setdefault(cid, set()).add(_eat_day(sent))

    net_total = [0] * 90
    net_known = [False] * 90
    members_total = [0] * 90
    post_net, quiet_net = [], []
    rows = []
    for ch in channels:
        cid = ch["id"]
        series = by_channel.get(cid, [])
        d = daily[cid]
        net = [None] * 90
        for i in range(1, 90):
            if d[i] is not None and d[i - 1] is not None:
                net[i] = d[i] - d[i - 1]
                net_total[i] += net[i]
                net_known[i] = True
                (post_net if days90[i] in sent_days.get(cid, ()) else quiet_net).append(net[i])
        for i in range(90):
            members_total[i] += d[i] or 0
        members = series[-1][1] if series else None
        d7 = _delta(series, now, 7)
        rows.append({
            "id": cid, "title": ch["title"], "members": members,
            "h1": _delta(series, now, 1 / 24), "d1": _delta(series, now, 1), "d7": d7, "d30": _delta(series, now, 30),
            "g7": round(100.0 * d7 / (members - d7), 2) if (members and d7 is not None and members - d7 > 0) else None,
            "posts": 0, "posts30": 0, "auto_approve": bool(ch["auto_approve"]),
            "daily": d, "net14": [v for v in net[-14:]],
        })
    by_id = {r["id"]: r for r in rows}
    d30_cut = now - timedelta(days=30)
    for cid, sent in targets:
        r = by_id.get(cid)
        if r is not None:
            r["posts"] += 1
            s = _naive(sent)
            if s is not None and s >= d30_cut:
                r["posts30"] += 1

    total_series = [(datetime.combine(d, datetime.min.time()), members_total[i]) for i, d in enumerate(days90) if members_total[i]]
    members_now = sum(r["members"] or 0 for r in rows)
    all_series = []
    for cid, v in by_channel.items():
        all_series.extend(v)
    # combined series for deltas: sum of per-channel deltas (avoids jumps when a channel is added)
    h1 = sum(r["h1"] or 0 for r in rows) if any(r["h1"] is not None for r in rows) else None
    d1 = sum(r["d1"] or 0 for r in rows) if any(r["d1"] is not None for r in rows) else None
    d7 = sum(r["d7"] or 0 for r in rows) if any(r["d7"] is not None for r in rows) else None
    d30 = sum(r["d30"] or 0 for r in rows) if any(r["d30"] is not None for r in rows) else None
    last14 = [net_total[i] for i in range(76, 90) if net_known[i]]
    avg_daily = round(sum(last14) / len(last14), 1) if last14 else None

    known_days = [(net_total[i], str(days90[i])) for i in range(90) if net_known[i]]
    best = sorted(known_days, reverse=True)[:3]
    worst = sorted(known_days)[:3]

    # ---- posts -----------------------------------------------------------------------
    per_day = {d: [0, 0] for d in days90[-30:]}
    types, status, auto_n, repeat_n, sched_n, composed = {}, {}, 0, 0, 0, 0
    for p in posts:
        created = _naive(p["created_at"])
        mine = p["owner"] != 0
        status[_val(p["status"])] = status.get(_val(p["status"]), 0) + 1
        if created is not None:
            d = _eat_day(created)
            if d in per_day:
                per_day[d][0 if mine else 1] += 1
        if mine:
            composed += 1
            types[_val(p["type"])] = types.get(_val(p["type"]), 0) + 1
            if p["auto_delete"]:
                auto_n += 1
            if p["repeat"]:
                repeat_n += 1
        if _val(p["status"]) == "scheduled":
            sched_n += 1
    reposted = len(posts) - composed

    heat = {}
    hour_growth = {}
    for cid, sent in targets:
        s = _naive(sent)
        if s is None:
            continue
        e = s + EAT
        key = ((e.weekday() + 1) % 7, e.hour)  # Sunday = 0
        heat[key] = heat.get(key, 0) + 1
        end = s + timedelta(hours=6)
        if end <= now and cid in by_channel:
            a, b = value_at(cid, s), value_at(cid, end)
            if a is not None and b is not None and by_channel[cid][0][0] <= s:
                g = hour_growth.setdefault(e.hour, [0, 0])
                g[0] += b - a
                g[1] += 1
    heat_rows = [{"wd": k[0], "h": k[1], "n": n} for k, n in heat.items()]
    hours = [{"h": h, "avg": round(v[0] / v[1], 2), "n": v[1]} for h, v in sorted(hour_growth.items())]

    # ---- audience (join requests) --------------------------------------------------
    first_days = {}
    new7 = active7 = 0
    for first, last in audience:
        f, l = _naive(first), _naive(last)
        if f is not None:
            d = _eat_day(f)
            first_days[d] = first_days.get(d, 0) + 1
            if f >= now - timedelta(days=7):
                new7 += 1
        if l is not None and l >= now - timedelta(days=7):
            active7 += 1

    sent_30 = sum(r["posts30"] for r in rows)
    return {
        "generated": now.isoformat() + "Z",
        "summary": {
            "channels": len(channels), "members": members_now, "h1": h1, "d1": d1, "d7": d7, "d30": d30,
            "g7": round(100.0 * d7 / (members_now - d7), 2) if (d7 is not None and members_now - d7 > 0) else None,
            "avg_daily": avg_daily,
            "projection30": round(avg_daily * 30) if avg_daily is not None else None,
            "categories": counts.get("categories", 0), "groups": counts.get("groups", 0),
            "posts_composed": composed, "posts_reposted": reposted,
            "posts_today": per_day.get(today, [0, 0])[0] + per_day.get(today, [0, 0])[1],
            "sent_30": sent_30,
            "avg_posts_per_day_30": round(sent_30 / 30.0, 1),
        },
        "status": status, "types": types,
        "usage": {"auto_delete": auto_n, "repeat": repeat_n, "scheduled": sched_n, "composed": composed},
        "total_series": [[t.isoformat(), c] for t, c in total_series],
        "days": [str(d) for d in days90],
        "net": [net_total[i] if net_known[i] else None for i in range(90)],
        "per_day": [{"day": str(d), "composed": v[0], "reposted": v[1]} for d, v in per_day.items()],
        "records": {"best": [{"d": d, "v": v} for v, d in best], "worst": [{"d": d, "v": v} for v, d in worst]},
        "effect": {
            "post_days": len(post_net), "quiet_days": len(quiet_net),
            "post_avg": round(sum(post_net) / len(post_net), 2) if post_net else None,
            "quiet_avg": round(sum(quiet_net) / len(quiet_net), 2) if quiet_net else None,
        },
        "hours": hours, "heat": heat_rows,
        "audience": {
            "total": len(audience), "new7": new7, "active7": active7,
            "daily": [{"d": str(d), "n": first_days.get(d, 0)} for d in days90[-30:]],
        },
        "automation": {
            "sources": counts.get("sources", 0), "rules": counts.get("rules", 0), "rules_on": counts.get("rules_on", 0),
            "groups": counts.get("groups", 0), "groups_welcome": counts.get("groups_welcome", 0),
            "recurring": counts.get("recurring", 0), "recurring_on": counts.get("recurring_on", 0),
            "half_replies": extras.get("half_replies", 0),
        },
        "shares": extras.get("shares", {}),
        "channels": rows,
    }


async def _handle_api(request: web.Request) -> web.Response:
    if not _authorized(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    now = datetime.utcnow()
    since = now - timedelta(days=125)
    async with session() as s:
        ch_rows = (await s.execute(select(Channel).order_by(Channel.title))).scalars().all()
        channels = [{"id": c.id, "title": c.title, "auto_approve": c.auto_approve_members} for c in ch_rows]
        snaps = (await s.execute(
            select(ChannelStatSnapshot.channel_id, ChannelStatSnapshot.member_count, ChannelStatSnapshot.taken_at)
            .where(ChannelStatSnapshot.taken_at >= since).order_by(ChannelStatSnapshot.taken_at))).all()
        # one baseline reading from before the window so deltas have a starting point
        base = (await s.execute(
            select(ChannelStatSnapshot.channel_id, func.max(ChannelStatSnapshot.taken_at))
            .where(ChannelStatSnapshot.taken_at < since).group_by(ChannelStatSnapshot.channel_id))).all()
        for cid, taken in base:
            cnt = (await s.execute(select(ChannelStatSnapshot.member_count).where(
                ChannelStatSnapshot.channel_id == cid, ChannelStatSnapshot.taken_at == taken))).scalar()
            if cnt is not None:
                snaps.insert(0, (cid, cnt, taken))
        post_rows = (await s.execute(
            select(Post.owner_user_id, Post.content_type, Post.status, Post.created_at, Post.auto_delete_seconds,
                   Post.repeat_interval_seconds))).all()
        posts = [{"owner": r[0], "type": r[1], "status": r[2], "created_at": r[3], "auto_delete": r[4], "repeat": r[5]}
                 for r in post_rows]
        t_rows = (await s.execute(
            select(PostTarget.channel_id, PostTarget.sent_at, Post.created_at)
            .join(Post, Post.id == PostTarget.post_id).where(PostTarget.message_id.is_not(None)))).all()
        targets = [(cid, sent or created) for cid, sent, created in t_rows]
        audience = (await s.execute(select(BroadcastUser.first_seen, BroadcastUser.last_seen))).all()

        async def count(stmt):
            try:
                return (await s.execute(stmt)).scalar_one() or 0
            except Exception:
                logger.exception("dashboard count failed")
                return 0

        counts = {
            "categories": await count(select(func.count()).select_from(Category)),
            "groups": await count(select(func.count()).select_from(ModeratedGroup)),
            "groups_welcome": await count(select(func.count()).select_from(ModeratedGroup).where(ModeratedGroup.welcome_enabled.is_(True))),
            "sources": await count(select(func.count()).select_from(SourceChannel)),
            "rules": await count(select(func.count()).select_from(RepostRule)),
            "rules_on": await count(select(func.count()).select_from(RepostRule).where(RepostRule.enabled.is_(True))),
            "recurring": await count(select(func.count()).select_from(RecurringMessage)),
            "recurring_on": await count(select(func.count()).select_from(RecurringMessage).where(RecurringMessage.enabled.is_(True))),
        }

        extras = {}
        try:
            extras["half_replies"] = (await s.execute(text("SELECT COUNT(*) FROM half_reply"))).scalar() or 0
        except Exception:
            extras["half_replies"] = 0
        try:
            tot = (await s.execute(text(
                "SELECT COUNT(*), COALESCE(SUM(downloads),0), COALESCE(SUM(CASE WHEN active=1 THEN 1 ELSE 0 END),0) "
                "FROM shared_files"))).first()
            users = (await s.execute(text("SELECT COUNT(DISTINCT user_id) FROM shared_file_log"))).scalar() or 0
            top = (await s.execute(text(
                "SELECT code, downloads, (SELECT COUNT(*) FROM shared_file_items i WHERE i.code = f.code) "
                "FROM shared_files f ORDER BY downloads DESC LIMIT 5"))).all()
            extras["shares"] = {"total": tot[0], "downloads": tot[1], "active": tot[2], "users": users,
                                "top": [{"code": r[0], "downloads": r[1] or 0, "files": r[2]} for r in top]}
        except Exception:
            extras["shares"] = {}

    try:
        data = compute(now, channels, snaps, posts, targets, audience, counts, extras)
    except Exception as exc:
        logger.exception("dashboard compute failed")
        return web.json_response({"error": str(exc)}, status=500)
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
:root{--bg:#070b14;--card:rgba(255,255,255,.045);--bd:rgba(255,255,255,.09);--txt:#eef2fb;--mut:#8a97b2;--g:#2ee59d;--r:#ff6b7a;--a:#ffc23d;--b:#5b9dff;--p:#a77bff;--acc:linear-gradient(135deg,#ffc23d,#ff8a3d)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:radial-gradient(1100px 600px at 85% -10%,#1b2a52 0,transparent 60%),radial-gradient(900px 500px at -10% 8%,#2a1c4d 0,transparent 55%),var(--bg);color:var(--txt);font:14px/1.45 -apple-system,"Segoe UI",Roboto,Inter,sans-serif;padding:16px 14px 60px;min-height:100vh}
.wrap{max-width:1000px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
h1{font-size:20px;margin:0;font-weight:800;letter-spacing:-.02em}.sub{color:var(--mut);font-size:12px}
.live{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:var(--g);background:rgba(46,229,157,.1);border:1px solid rgba(46,229,157,.25);padding:4px 10px;border-radius:20px}
.live i{width:6px;height:6px;border-radius:50%;background:var(--g);animation:pulse 1.8s infinite}
@keyframes pulse{70%{box-shadow:0 0 0 8px rgba(46,229,157,0)}0%{box-shadow:0 0 0 0 rgba(46,229,157,.7)}}
.hero{border-radius:20px;padding:20px;background:linear-gradient(135deg,rgba(91,157,255,.18),rgba(167,123,255,.14));border:1px solid var(--bd);backdrop-filter:blur(14px);margin-bottom:12px}
.hero .big{font-size:42px;font-weight:800;letter-spacing:-.03em;margin:2px 0}
.chips{display:flex;gap:8px;flex-wrap:wrap}.chip{background:rgba(255,255,255,.06);border:1px solid var(--bd);border-radius:12px;padding:8px 12px;font-size:12px}.chip b{display:block;font-size:15px;margin-top:1px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:12px}
@media(min-width:720px){.grid{grid-template-columns:repeat(4,1fr)}.two{display:grid;grid-template-columns:1.4fr 1fr;gap:12px}.even{display:grid;grid-template-columns:1fr 1fr;gap:12px}.hero .big{font-size:52px}}
.card{background:var(--card);border:1px solid var(--bd);border-radius:16px;padding:14px;backdrop-filter:blur(10px);margin-bottom:12px}
.grid .card{margin:0}
.k{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.07em}.v{font-size:23px;font-weight:800;margin-top:4px;letter-spacing:-.02em}
.d{font-size:12px;margin-top:2px}.up{color:var(--g)}.dn{color:var(--r)}.mut{color:var(--mut)}
h2{font-size:12px;margin:20px 2px 8px;color:var(--mut);font-weight:700;text-transform:uppercase;letter-spacing:.09em}
.tabs{display:flex;gap:6px;flex-wrap:wrap}.tab{padding:5px 12px;border-radius:20px;font-size:12px;color:var(--mut);border:1px solid var(--bd);cursor:pointer;user-select:none}
.tab.on{background:var(--acc);color:#1a1200;border-color:transparent;font-weight:700}
.row{display:flex;justify-content:space-between;align-items:center;gap:8px}
svg{width:100%;display:block;overflow:visible}
.tip{position:absolute;pointer-events:none;background:#0b1020;border:1px solid var(--bd);border-radius:10px;padding:6px 10px;font-size:12px;opacity:0;transition:opacity .12s;white-space:nowrap;z-index:5}
.chart{position:relative}
.heat{display:grid;grid-template-columns:28px repeat(24,1fr);gap:2px;font-size:9px;color:var(--mut)}.heat div{aspect-ratio:1;border-radius:3px}.heat .l{aspect-ratio:auto;display:flex;align-items:center}
.ch .name{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill{display:inline-block;font-size:11px;padding:2px 9px;border-radius:10px;background:rgba(255,255,255,.07);margin:6px 6px 0 0}
.legend{display:flex;flex-direction:column;gap:6px;font-size:12px}.legend div{display:flex;justify-content:space-between;gap:10px}.dot{width:9px;height:9px;border-radius:3px;display:inline-block;margin-right:6px}
.sk{height:90px;border-radius:12px;background:linear-gradient(90deg,rgba(255,255,255,.04),rgba(255,255,255,.1),rgba(255,255,255,.04));background-size:200% 100%;animation:sh 1.2s infinite}
@keyframes sh{to{background-position:-200% 0}}
.err{color:var(--r);text-align:center;padding:40px 10px}
table{width:100%;border-collapse:collapse;font-size:12px}th{text-align:left;color:var(--mut);font-weight:600;padding:5px 6px;border-bottom:1px solid var(--bd)}td{padding:6px;border-bottom:1px solid rgba(255,255,255,.05)}
</style></head><body><div class="wrap">
<header><div><h1>Channel Analytics</h1><div class="sub" id="gen">Loading...</div></div><div class="live"><i></i>LIVE &middot; EAT</div></header>
<div id="root"><div class="sk"></div></div>
</div><div class="tip" id="tip"></div>
<script>
var tg=window.Telegram&&window.Telegram.WebApp; if(tg){tg.ready();tg.expand();try{tg.setHeaderColor('#070b14');tg.setBackgroundColor('#070b14');}catch(e){}}
var DATA=null,range=30,metric='members',sortBy='members';
var tip=document.getElementById('tip');
function fm(n){return n==null?'-':Number(n).toLocaleString();}
function sg(n){if(n==null)return '<span class="mut">-</span>';var c=n>0?'up':(n<0?'dn':'mut');return '<span class="'+c+'">'+(n>0?'+':'')+fm(n)+'</span>';}
function esc(t){var e=document.createElement('div');e.textContent=t==null?'':t;return e.innerHTML;}
function pc(a,b){return b?(100*a/b).toFixed(1)+'%':'-';}
function showTip(html,x,y){tip.innerHTML=html;tip.style.opacity=1;var w=tip.offsetWidth;tip.style.left=Math.min(Math.max(x-w/2,8),window.innerWidth-w-8)+window.scrollX+'px';tip.style.top=(y-48+window.scrollY)+'px';}
function hideTip(){tip.style.opacity=0;}
function area(id,pts,color,fv){
  var w=640,h=190,pl=6,pr=6,pt=12,pb=22,vs=pts.map(function(p){return p.v;}),mx=Math.max.apply(null,vs.concat([1])),mn=Math.min.apply(null,vs.concat([mx]));if(mx===mn){mn=Math.max(0,mn-1);}
  var xs=function(i){return pl+(w-pl-pr)*(pts.length<2?0.5:i/(pts.length-1));},ys=function(v){return pt+(h-pt-pb)*(1-(v-mn)/(mx-mn));};
  var d='M'+pts.map(function(p,i){return xs(i).toFixed(1)+','+ys(p.v).toFixed(1);}).join(' L');
  var fill=d+' L'+xs(pts.length-1)+','+(h-pb)+' L'+xs(0)+','+(h-pb)+' Z';
  var grid='';for(var g=0;g<4;g++){var gy=pt+(h-pt-pb)*g/3;grid+='<line x1="0" x2="'+w+'" y1="'+gy+'" y2="'+gy+'" stroke="rgba(255,255,255,.06)"/><text x="2" y="'+(gy-3)+'" fill="#8a97b2" font-size="9">'+fv(mn+(mx-mn)*(1-g/3))+'</text>';}
  var step=Math.ceil(pts.length/6),lab='';pts.forEach(function(p,i){if(i%step===0)lab+='<text x="'+xs(i)+'" y="'+(h-6)+'" fill="#8a97b2" font-size="9" text-anchor="middle">'+p.d.slice(5)+'</text>';});
  return '<div class="chart" id="'+id+'"><svg viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" style="height:'+h+'px"><defs><linearGradient id="g'+id+'" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="'+color+'" stop-opacity=".45"/><stop offset="1" stop-color="'+color+'" stop-opacity="0"/></linearGradient></defs>'+grid+
   '<path d="'+fill+'" fill="url(#g'+id+')"/><path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="2.4" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'+lab+
   '<circle id="cd'+id+'" r="4.5" fill="'+color+'" stroke="#070b14" stroke-width="2" style="display:none"/></svg></div>';
}
function bindArea(id,pts,fv){
  var host=document.getElementById(id);if(!host)return;var svg=host.querySelector('svg'),w=640,h=190,pl=6,pr=6,pt=12,pb=22;
  var vs=pts.map(function(p){return p.v;}),mx=Math.max.apply(null,vs.concat([1])),mn=Math.min.apply(null,vs.concat([mx]));if(mx===mn){mn=Math.max(0,mn-1);}
  function move(e){var r=svg.getBoundingClientRect(),cx=(e.touches?e.touches[0].clientX:e.clientX),i=Math.round((cx-r.left)/r.width*(pts.length-1));i=Math.max(0,Math.min(pts.length-1,i));var p=pts[i];
    var x=pl+(w-pl-pr)*(pts.length<2?0.5:i/(pts.length-1)),y=pt+(h-pt-pb)*(1-(p.v-mn)/(mx-mn)),cd=document.getElementById('cd'+id);cd.setAttribute('cx',x);cd.setAttribute('cy',y);cd.style.display='';
    showTip('<b>'+fv(p.v)+'</b><br><span class="mut">'+p.d+'</span>',r.left+r.width*(x/w),r.top+r.height*(y/h));}
  host.addEventListener('mousemove',move);host.addEventListener('touchmove',move,{passive:true});host.addEventListener('touchstart',move,{passive:true});
  host.addEventListener('mouseleave',hideTip);host.addEventListener('touchend',function(){setTimeout(hideTip,900);});
}
function spark(vals,color,h){var pts=vals.map(function(v,i){return {v:v,i:i};}).filter(function(p){return p.v!=null;});
  if(pts.length<2)return '<div class="mut" style="font-size:11px;padding:10px 0">Collecting data - a reading is saved every hour.</div>';
  var w=320,pad=3,vs=pts.map(function(p){return p.v;}),mn=Math.min.apply(null,vs),mx=Math.max.apply(null,vs);if(mx===mn)mx=mn+1;var n=vals.length-1;
  var xy=pts.map(function(p){return (pad+(w-2*pad)*p.i/n).toFixed(1)+','+(h-pad-(h-2*pad)*(p.v-mn)/(mx-mn)).toFixed(1);});
  return '<svg viewBox="0 0 '+w+' '+h+'" preserveAspectRatio="none" style="height:'+h+'px"><path d="M'+xy[0]+' L'+xy.join(' L')+' L'+xy[xy.length-1].split(',')[0]+','+(h-pad)+' L'+xy[0].split(',')[0]+','+(h-pad)+' Z" fill="'+color+'" opacity=".15"/><polyline points="'+xy.join(' ')+'" fill="none" stroke="'+color+'" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>';}
function bars(vals,labels,color,h,signed){var mx=Math.max.apply(null,vals.map(function(v){return Math.abs(v||0);}).concat([1]));
  return '<div style="display:flex;align-items:'+(signed?'center':'flex-end')+';gap:2px;height:'+h+'px">'+vals.map(function(v,i){v=v||0;var bh=Math.max(v?2:1,(signed?(h/2-2):(h-4))*Math.abs(v)/mx);var c=signed?(v>=0?'#2ee59d':'#ff6b7a'):color;
    var st=signed?'height:'+bh+'px;'+(v>=0?'align-self:flex-end;margin-bottom:'+(h/2)+'px;':'align-self:flex-start;margin-top:'+(h/2)+'px;'):'height:'+bh+'px;';
    return '<div title="'+labels[i]+': '+fm(v)+'" style="flex:1;'+st+'border-radius:3px;background:'+c+';opacity:'+(v?1:.2)+'"></div>';}).join('')+'</div>'+
   '<div style="display:flex;gap:2px;margin-top:3px">'+labels.map(function(l,i){return '<span style="flex:1;text-align:center;font-size:8px;color:#8a97b2">'+(i%Math.ceil(labels.length/8)===0?l.slice(5)||l:'')+'</span>';}).join('')+'</div>';}
function hbar(rows,color){var mx=Math.max.apply(null,rows.map(function(r){return Math.abs(r.v);}).concat([1]));
  return rows.map(function(r){return '<div style="margin:8px 0"><div class="row" style="font-size:12px"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%">'+esc(r.l)+'</span><b>'+r.s+'</b></div><div style="height:8px;border-radius:6px;background:rgba(255,255,255,.07);margin-top:3px"><div style="height:8px;border-radius:6px;width:'+Math.max(2,100*Math.abs(r.v)/mx).toFixed(1)+'%;background:'+(r.c||color)+'"></div></div></div>';}).join('');}
function donut(rows,label){var cols=['#ffc23d','#5b9dff','#2ee59d','#a77bff','#ff6b7a','#ff8a3d','#4dd0e1','#c0ca33'],tot=rows.reduce(function(a,r){return a+r.v;},0)||1,off=0,R=52,C=2*Math.PI*R,arcs='';
  rows.forEach(function(r,i){var len=C*r.v/tot;arcs+='<circle r="'+R+'" cx="70" cy="70" fill="none" stroke="'+cols[i%8]+'" stroke-width="18" stroke-dasharray="'+len+' '+(C-len)+'" stroke-dashoffset="'+(-off)+'" transform="rotate(-90 70 70)"/>';off+=len;});
  return '<div class="row" style="align-items:center"><svg viewBox="0 0 140 140" style="width:130px;flex:none">'+arcs+'<text x="70" y="66" text-anchor="middle" fill="#8a97b2" font-size="9">'+label+'</text><text x="70" y="83" text-anchor="middle" fill="#eef2fb" font-size="14" font-weight="800">'+fm(tot)+'</text></svg><div class="legend" style="flex:1">'+
   rows.map(function(r,i){return '<div><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><span class="dot" style="background:'+cols[i%8]+'"></span>'+esc(r.l)+'</span><b>'+Math.round(100*r.v/tot)+'%</b></div>';}).join('')+'</div></div>';}
function heat(rows){var m={},mx=1;rows.forEach(function(r){m[r.wd+'_'+r.h]=r.n;mx=Math.max(mx,r.n);});var days=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'],h='<div class="heat"><div class="l"></div>';
  for(var x=0;x<24;x++)h+='<div class="l" style="justify-content:center;aspect-ratio:auto">'+(x%6===0?x:'')+'</div>';
  for(var d=0;d<7;d++){h+='<div class="l">'+days[d]+'</div>';for(var x2=0;x2<24;x2++){var n=m[d+'_'+x2]||0,a=n?(0.18+0.82*n/mx):0.05;h+='<div title="'+days[d]+' '+x2+':00 - '+n+' posts" style="background:rgba(91,157,255,'+a.toFixed(2)+')"></div>';}}return h+'</div>';}
function tbl(head,rows){return '<div style="overflow-x:auto"><table><tr>'+head.map(function(x){return '<th>'+x+'</th>';}).join('')+'</tr>'+rows.map(function(r){return '<tr>'+r.map(function(c){return '<td>'+c+'</td>';}).join('')+'</tr>';}).join('')+'</table></div>';}
function countUp(el,to){var t0=performance.now();function f(t){var p=Math.min(1,(t-t0)/900),e=1-Math.pow(1-p,3);el.textContent=fm(Math.round(to*e));if(p<1)requestAnimationFrame(f);}requestAnimationFrame(f);}
function cut(arr){return range===0?arr:arr.slice(-range);}
function render(){
  var d=DATA,s=d.summary,h='',ef=d.effect,au=d.audience,a=d.automation,sh=d.shares||{};
  h+='<div class="hero"><div class="k">Total members across '+s.channels+' channels</div><div class="big" id="big">0</div><div class="chips" style="margin-top:8px">'+
   '<div class="chip">1 hour<b>'+sg(s.h1)+'</b></div><div class="chip">24 hours<b>'+sg(s.d1)+'</b></div><div class="chip">7 days<b>'+sg(s.d7)+'</b></div><div class="chip">30 days<b>'+sg(s.d30)+'</b></div>'+
   '<div class="chip">7-day growth<b>'+(s.g7==null?'-':sg(s.g7)+'%')+'</b></div><div class="chip">Avg / day<b>'+(s.avg_daily==null?'-':sg(s.avg_daily))+'</b></div><div class="chip">30-day projection<b>'+(s.projection30==null?'-':sg(s.projection30))+'</b></div></div></div>';
  h+='<div class="grid">'+
   '<div class="card"><div class="k">Posts today</div><div class="v">'+fm(s.posts_today)+'</div><div class="d mut">'+s.avg_posts_per_day_30+' / day (30d)</div></div>'+
   '<div class="card"><div class="k">Posts you composed</div><div class="v">'+fm(s.posts_composed)+'</div><div class="d mut">'+fm(s.posts_reposted)+' reposted</div></div>'+
   '<div class="card"><div class="k">Join-request audience</div><div class="v">'+fm(au.total)+'</div><div class="d up">+'+fm(au.new7)+' this week</div></div>'+
   '<div class="card"><div class="k">Categories / groups</div><div class="v">'+fm(s.categories)+' / '+fm(s.groups)+'</div></div></div>';
  var days=d.days;
  h+='<div class="card"><div class="row"><div class="tabs">'+[['members','Members'],['net','Daily growth'],['posts','Posts']].map(function(r){return '<div class="tab '+(metric===r[0]?'on':'')+'" onclick="setMetric(\''+r[0]+'\')">'+r[1]+'</div>';}).join('')+'</div>'+
   '<div class="tabs">'+[[7,'7d'],[30,'30d'],[90,'90d']].map(function(r){return '<div class="tab '+(range===r[0]?'on':'')+'" onclick="setRange('+r[0]+')">'+r[1]+'</div>';}).join('')+'</div></div><div id="main" style="margin-top:10px"></div></div>';
  var rows=d.channels.slice();
  var lead=rows.filter(function(c){return c.d7!=null;}).sort(function(x,y){return y.d7-x.d7;}).slice(0,8);
  h+='<div class="two"><div class="card"><div class="k" style="margin-bottom:6px">Growth leaders (7 days)</div>'+(lead.length?hbar(lead.map(function(c){return {l:c.title,v:c.d7,s:sg(c.d7)+(c.g7!=null?' <span class="mut">('+c.g7+'%)</span>':''),c:c.d7>=0?'#2ee59d':'#ff6b7a'};}),'#2ee59d'):'<div class="mut">Not enough readings yet.</div>')+'</div>'+
   '<div class="card"><div class="k" style="margin-bottom:6px">Member share</div>'+donut(rows.filter(function(c){return c.members;}).sort(function(x,y){return y.members-x.members;}).slice(0,8).map(function(c){return {l:c.title,v:c.members};}),'MEMBERS')+'</div></div>';
  h+='<h2>Records</h2><div class="even"><div class="card"><div class="k">Best growth days</div>'+(d.records.best.length?d.records.best.map(function(x){return '<div class="row" style="margin-top:6px"><span class="mut">'+x.d+'</span><b>'+sg(x.v)+'</b></div>';}).join(''):'<div class="mut">-</div>')+'</div>'+
   '<div class="card"><div class="k">Weakest days</div>'+(d.records.worst.length?d.records.worst.map(function(x){return '<div class="row" style="margin-top:6px"><span class="mut">'+x.d+'</span><b>'+sg(x.v)+'</b></div>';}).join(''):'<div class="mut">-</div>')+'</div></div>';
  h+='<h2>Posting vs growth</h2><div class="even"><div class="card"><div class="k" style="margin-bottom:8px">Average daily growth per channel</div>'+
   (ef.post_avg==null||ef.quiet_avg==null?'<div class="mut">Needs a few days of readings with and without posts.</div>':hbar([{l:'On days you posted ('+fm(ef.post_days)+' days)',v:ef.post_avg,s:sg(ef.post_avg),c:'#ffc23d'},{l:'On quiet days ('+fm(ef.quiet_days)+' days)',v:ef.quiet_avg,s:sg(ef.quiet_avg),c:'#5b9dff'}],'#ffc23d'))+'</div>'+
   '<div class="card"><div class="k" style="margin-bottom:8px">Members gained in the 6 hours after a post, by hour posted (EAT)</div>'+
   (d.hours.length?hbar(d.hours.slice().sort(function(x,y){return y.avg-x.avg;}).slice(0,6).map(function(x){return {l:(x.h<10?'0':'')+x.h+':00 ('+x.n+' posts)',v:x.avg,s:sg(x.avg)+' avg',c:x.avg>=0?'#2ee59d':'#ff6b7a'};}),'#2ee59d'):'<div class="mut">Fills in as posts get a full 6 hours of readings.</div>')+'</div></div>';
  var pd=d.per_day;
  h+='<h2>Posting activity</h2><div class="card"><div class="k" style="margin-bottom:6px">Posts created per day (30 days) &middot; composed + reposted</div>'+bars(pd.map(function(x){return x.composed+x.reposted;}),pd.map(function(x){return x.day;}),'linear-gradient(180deg,#ffc23d,#ff8a3d)',90,false)+'</div>';
  var ty=Object.keys(d.types).map(function(k){return {l:k,v:d.types[k]};}),st=Object.keys(d.status).map(function(k){return {l:k,v:d.status[k]};}),u=d.usage;
  h+='<div class="even"><div class="card"><div class="k" style="margin-bottom:6px">Content mix (composed)</div>'+(ty.length?donut(ty,'POSTS'):'<div class="mut">No posts yet</div>')+'</div>'+
   '<div class="card"><div class="k" style="margin-bottom:6px">Status and options</div>'+st.map(function(x){return '<span class="pill">'+esc(x.l)+': <b>'+fm(x.v)+'</b></span>';}).join('')+
   '<div style="margin-top:10px">'+hbar([{l:'Auto-delete used',v:u.auto_delete,s:fm(u.auto_delete)+' &middot; '+pc(u.auto_delete,u.composed),c:'#a77bff'},{l:'Repeating posts',v:u.repeat,s:fm(u.repeat)+' &middot; '+pc(u.repeat,u.composed),c:'#5b9dff'},{l:'Scheduled now',v:u.scheduled,s:fm(u.scheduled),c:'#ffc23d'}],'#a77bff')+'</div></div></div>';
  h+='<div class="card"><div class="k" style="margin-bottom:8px">When you post (EAT, all channels)</div>'+(d.heat.length?heat(d.heat):'<div class="mut">No sent posts yet</div>')+'</div>';
  h+='<h2>Audience from join requests</h2><div class="card"><div class="chips" style="margin-bottom:10px"><div class="chip">Total<b>'+fm(au.total)+'</b></div><div class="chip">New (7d)<b>'+fm(au.new7)+'</b></div><div class="chip">Active (7d)<b>'+fm(au.active7)+'</b></div></div>'+
   bars(au.daily.map(function(x){return x.n;}),au.daily.map(function(x){return x.d;}),'linear-gradient(180deg,#5b9dff,#a77bff)',70,false)+'</div>';
  if(sh.total!=null){h+='<h2>File sharing</h2><div class="card"><div class="chips"><div class="chip">Shares<b>'+fm(sh.total)+'</b></div><div class="chip">Active<b>'+fm(sh.active)+'</b></div><div class="chip">Downloads<b>'+fm(sh.downloads)+'</b></div><div class="chip">People reached<b>'+fm(sh.users)+'</b></div></div>'+
   (sh.top&&sh.top.length?'<div style="margin-top:10px">'+tbl(['Share','Files','Downloads'],sh.top.map(function(x){return [esc(x.code),fm(x.files),fm(x.downloads)];}))+'</div>':'')+'</div>';}
  h+='<h2>Automation</h2><div class="card"><div class="chips"><div class="chip">Watched sources<b>'+fm(a.sources)+'</b></div><div class="chip">Forward rules<b>'+fm(a.rules_on)+' / '+fm(a.rules)+' on</b></div><div class="chip">Moderated groups<b>'+fm(a.groups)+'</b></div><div class="chip">Welcome messages<b>'+fm(a.groups_welcome)+'</b></div><div class="chip">Recurring messages<b>'+fm(a.recurring_on)+' / '+fm(a.recurring)+' on</b></div><div class="chip">"You were mentioned" replies<b>'+fm(a.half_replies)+'</b></div></div></div>';
  h+='<h2>Channels</h2><div class="tabs" style="margin-bottom:10px">'+[['members','Members'],['d7','7d growth'],['g7','7d %'],['d30','30d growth'],['posts30','Posts 30d']].map(function(r){return '<div class="tab '+(sortBy===r[0]?'on':'')+'" onclick="setSort(\''+r[0]+'\')">'+r[1]+'</div>';}).join('')+'</div>';
  rows.sort(function(x,y){return (y[sortBy]==null?-1e9:y[sortBy])-(x[sortBy]==null?-1e9:x[sortBy]);});
  var tot=s.members||1;
  rows.forEach(function(c){
    h+='<div class="card ch"><div class="row"><div class="name">'+esc(c.title)+'</div><div style="font-weight:800;font-size:16px">'+fm(c.members)+'</div></div>'+
     '<div><span class="pill">1h '+sg(c.h1)+'</span><span class="pill">24h '+sg(c.d1)+'</span><span class="pill">7d '+sg(c.d7)+(c.g7!=null?' ('+c.g7+'%)':'')+'</span><span class="pill">30d '+sg(c.d30)+'</span><span class="pill">posts '+fm(c.posts)+' &middot; '+fm(c.posts30)+' in 30d</span><span class="pill">'+pc(c.members||0,tot)+' of members</span>'+(c.auto_approve?'<span class="pill">auto-approve</span>':'')+'</div>'+
     '<div style="margin-top:8px">'+spark(cut(c.daily),'#2ee59d',58)+'</div></div>';
  });
  document.getElementById('root').innerHTML=h;
  document.getElementById('gen').textContent='Updated '+new Date(d.generated).toLocaleString();
  countUp(document.getElementById('big'),s.members||0);drawMain();
}
function drawMain(){
  var d=DATA,el=document.getElementById('main');if(!el)return;var days=cut(d.days),pts,fv=fm,col;
  if(metric==='members'){var all=d.total_series.map(function(p){return {d:p[0].slice(0,10),v:p[1]};});pts=cut(all);col='#5b9dff';
    if(pts.length<2){el.innerHTML='<div class="mut" style="padding:20px 0">Not enough readings yet - the bot now saves one every hour.</div>';return;}
    el.innerHTML=area('ar',pts,col,fm);bindArea('ar',pts,fm);return;}
  if(metric==='net'){var net=cut(d.net);el.innerHTML='<div class="mut" style="font-size:12px;margin-bottom:4px">Net members gained per day, all channels (green up, red down)</div>'+bars(net,days,'',110,true);return;}
  var pd=cut(d.days).map(function(day){return 0;});var map={};d.per_day.forEach(function(x){map[x.day]=x.composed+x.reposted;});var ds=d.per_day.map(function(x){return x.day;});
  el.innerHTML='<div class="mut" style="font-size:12px;margin-bottom:4px">Posts created per day</div>'+bars(d.per_day.map(function(x){return x.composed+x.reposted;}).slice(-Math.min(range,30)),ds.slice(-Math.min(range,30)),'linear-gradient(180deg,#ffc23d,#ff8a3d)',110,false);
}
function setRange(r){range=r;render();} function setMetric(m){metric=m;render();} function setSort(k){sortBy=k;render();}
function load(){fetch('/api/data'+location.search).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j;});})
 .then(function(j){DATA=j;render();}).catch(function(e){if(!DATA)document.getElementById('root').innerHTML='<div class="err">Could not load data ('+esc(e.message)+'). Open the dashboard from the bot menu.</div>';});}
load();setInterval(load,120000);
</script></body></html>"""
