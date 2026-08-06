"""Lightweight read-only stats dashboard, served over HTTP alongside the bot.

Exposes a single page showing channel/post/group counts pulled straight
from the same database the bot uses - no traffic-growth logic, no
separate app, just a fast way to check on things without opening Telegram.

Protected by a shared-secret token in the query string: set DASHBOARD_TOKEN
(see config.py) and the page only renders for requests with a matching
?key=... . Leave DASHBOARD_TOKEN unset and the page is open to anyone with
the URL - not recommended once a public domain is attached to this service.
"""
import logging
import os

from aiohttp import web
from sqlalchemy import func, select

from config import get_settings
from db import session
from models import Category, Channel, ChannelStatSnapshot, ModeratedGroup, Post, PostStatus

logger = logging.getLogger(__name__)


async def _handle_dashboard(request: web.Request) -> web.Response:
    token = get_settings().dashboard_token
    if token and request.query.get("key") != token:
        return web.Response(status=401, text="Missing or wrong ?key=")

    async with session() as s:
        channel_count = (await s.execute(select(func.count()).select_from(Channel))).scalar_one()
        auto_approve_count = (await s.execute(
            select(func.count()).select_from(Channel).where(Channel.auto_approve_members.is_(True))
        )).scalar_one()
        category_count = (await s.execute(select(func.count()).select_from(Category))).scalar_one()
        group_count = (await s.execute(select(func.count()).select_from(ModeratedGroup))).scalar_one()

        post_counts: dict[str, int] = {}
        for status in PostStatus:
            post_counts[status.value] = (await s.execute(
                select(func.count()).select_from(Post).where(Post.status == status)
            )).scalar_one()

        channels = (await s.execute(
            select(Channel).order_by(Channel.added_at.desc())
        )).scalars().all()

        channel_rows = []
        for ch in channels:
            latest_snap = (await s.execute(
                select(ChannelStatSnapshot)
                .where(ChannelStatSnapshot.channel_id == ch.id)
                .order_by(ChannelStatSnapshot.taken_at.desc())
                .limit(1)
            )).scalars().first()
            channel_rows.append({
                "title": ch.title,
                "chat_id": ch.chat_id,
                "auto_approve": ch.auto_approve_members,
                "members": latest_snap.member_count if latest_snap else None,
            })

    html = _render(channel_count, auto_approve_count, category_count, group_count, post_counts, channel_rows)
    return web.Response(text=html, content_type="text/html")


def _esc(value: object) -> str:
    """Minimal HTML-escaping for values pulled from the DB (channel titles
    etc. can contain anything an operator typed) before they go into the
    page - avoids reflected HTML/script injection via a channel title."""
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render(channel_count, auto_approve_count, category_count, group_count, post_counts, channel_rows) -> str:
    rows_html = "".join(
        f"<tr><td>{_esc(r['title'])}</td><td>{r['chat_id']}</td>"
        f"<td>{'\u2705' if r['auto_approve'] else '\u2014'}</td>"
        f"<td>{r['members'] if r['members'] is not None else '\u2014'}</td></tr>"
        for r in channel_rows
    )
    if not rows_html:
        rows_html = '<tr><td colspan="4" style="color:#999">No channels registered yet</td></tr>'

    post_stats_html = "".join(
        f"<li><b>{v}</b> {_esc(k)}</li>" for k, v in post_counts.items()
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>bot-posting dashboard</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 960px; margin: 40px auto; padding: 0 16px; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  .subtitle {{ color: #777; font-size: 13px; margin-bottom: 24px; }}
  .stats {{ display: flex; gap: 16px; margin: 24px 0; flex-wrap: wrap; }}
  .stat {{ background: #fff; border: 1px solid #eee; border-radius: 10px; padding: 14px 22px; min-width: 120px; }}
  .stat b {{ display: block; font-size: 26px; line-height: 1.3; }}
  .stat span {{ font-size: 13px; color: #666; }}
  h2 {{ font-size: 16px; margin-top: 32px; }}
  ul.postlist {{ list-style: none; padding: 0; display: flex; gap: 20px; flex-wrap: wrap; }}
  ul.postlist li {{ background: #fff; border: 1px solid #eee; border-radius: 8px; padding: 8px 14px; font-size: 14px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; background: #fff; border-radius: 8px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid #f0f0f0; font-size: 14px; }}
  th {{ color: #666; font-weight: 600; font-size: 12px; text-transform: uppercase; }}
  footer {{ color: #aaa; font-size: 12px; margin-top: 40px; }}
</style></head>
<body>
  <h1>\U0001f4ca bot-posting dashboard</h1>
  <div class="subtitle">Read-only snapshot. Reload the page for current data.</div>

  <div class="stats">
    <div class="stat"><b>{channel_count}</b><span>Channels</span></div>
    <div class="stat"><b>{auto_approve_count}</b><span>Auto-approve ON</span></div>
    <div class="stat"><b>{category_count}</b><span>Categories</span></div>
    <div class="stat"><b>{group_count}</b><span>Moderated groups</span></div>
  </div>

  <h2>Posts</h2>
  <ul class="postlist">{post_stats_html}</ul>

  <h2>Channels</h2>
  <table>
    <tr><th>Title</th><th>Chat ID</th><th>Auto-approve</th><th>Members (latest snapshot)</th></tr>
    {rows_html}
  </table>

  <footer>bot-posting internal dashboard</footer>
</body></html>"""


async def run_dashboard() -> None:
    """Starts the aiohttp dashboard server on $PORT. Railway sets PORT
    automatically for any service it detects listening on one; if it's
    not set (e.g. running locally), defaults to 8080. Runs as a
    background asyncio task alongside the bot's polling loop - see
    bot.py.
    """
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", _handle_dashboard)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    try:
        await site.start()
    except Exception:
        logger.exception("Dashboard failed to start on port %s", port)
        return

    logger.info("Dashboard listening on port %s", port)
