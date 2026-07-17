"""Manage rules that repost messages from a watched source channel into a
destination channel the bot posts into. Requires a source added via
/add_source (see handlers/sources.py) and the Telethon userbot configured
(see services/telethon_client.py) to actually detect new source posts.
"""
import json

from aiogram import Router, types
from aiogram.filters import Command, CommandObject
from sqlalchemy import select

from db import session
from models import Channel, RepostRule, SourceChannel

router = Router()


@router.message(Command("add_rule"))
async def add_rule(message: types.Message, command: CommandObject):
    """Usage: /add_rule <source_identifier_or_id> <destination_chat_id_or_channel_id> [auto_delete_seconds] [caption_template]
Example: /add_rule @source -1001234567890 3600 From {source_title}: {original_text}"""
    args = command.args
    if not args:
        await message.reply(
            "Usage: /add_rule SOURCE_ID_OR_IDENTIFIER DEST_CHAT_ID_OR_CHANNEL_ID "
            "[auto_delete_seconds] [caption_template]"
        )
        return
    parts = args.split(None, 3)
    if len(parts) < 2:
        await message.reply("Need at least source and destination")
        return
    source_key = parts[0]
    dest_key = parts[1]
    auto_seconds = None
    caption_template = None
    if len(parts) >= 3:
        try:
            auto_seconds = int(parts[2])
        except ValueError:
            auto_seconds = None
    if len(parts) == 4:
        caption_template = parts[3]

    async with session() as s:
        try:
            sid = int(source_key)
            q = select(SourceChannel).where(SourceChannel.id == sid)
        except ValueError:
            q = select(SourceChannel).where(SourceChannel.identifier == source_key)
        res = await s.execute(q)
        source = res.scalars().first()
        if not source:
            await message.reply("Source not found; add it with /add_source first")
            return

        try:
            dval = int(dest_key)
            q2 = select(Channel).where((Channel.chat_id == dval) | (Channel.id == dval))
        except ValueError:
            await message.reply("destination must be numeric chat_id or channel id")
            return
        res2 = await s.execute(q2)
        dest = res2.scalars().first()
        if not dest:
            await message.reply("Destination channel not found; add it with /add_channel first")
            return

        rr = RepostRule(
            source_channel_id=source.id,
            destination_channel_id=dest.id,
            caption_template=caption_template,
            auto_delete_seconds=auto_seconds,
            replacements_json=json.dumps({}),
        )
        s.add(rr)
        await s.commit()
        await message.reply(f"Added repost rule id={rr.id} source={source.identifier} -> dest={dest.chat_id}")


@router.message(Command("list_rules"))
async def list_rules(message: types.Message):
    async with session() as s:
        q = select(RepostRule)
        res = await s.execute(q)
        rows = res.scalars().all()
        if not rows:
            await message.reply("No repost rules")
            return
        lines = [
            f"ID={r.id} source_id={r.source_channel_id} dest_id={r.destination_channel_id} "
            f"auto_delete={r.auto_delete_seconds} template={r.caption_template}"
            for r in rows
        ]
        await message.reply("\n".join(lines))


@router.message(Command("remove_rule"))
async def remove_rule(message: types.Message, command: CommandObject):
    args = command.args
    if not args:
        await message.reply("Usage: /remove_rule RULE_ID")
        return
    try:
        rid = int(args.strip())
    except ValueError:
        await message.reply("rule_id must be numeric")
        return
    async with session() as s:
        q = select(RepostRule).where(RepostRule.id == rid)
        res = await s.execute(q)
        row = res.scalars().first()
        if not row:
            await message.reply("Rule not found")
            return
        await s.delete(row)
        await s.commit()
        await message.reply("Removed rule")
