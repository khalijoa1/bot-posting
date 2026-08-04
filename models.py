import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Table, UniqueConstraint, func, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

channel_categories = Table(
    "channel_categories",
    Base.metadata,
    Column("channel_id", ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    channels: Mapped[list["Channel"]] = relationship(
        secondary=channel_categories, back_populates="categories"
    )


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True)
    title: Mapped[str] = mapped_column(String(255))
    auto_approve_members: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Message sent immediately when someone submits a join request to this
    # channel, BEFORE their request is approved (or before a force-join
    # gate even gets checked) - e.g. "Thanks for requesting to join!" or a
    # captcha/rules notice. Telegram allows a bot to DM a user the instant
    # their join request arrives, even if they've never started a chat
    # with the bot before - a chat_join_request itself counts as the
    # qualifying interaction (Bot API 5.5+), for up to 24h from the
    # request or until it's resolved by any admin. See
    # handlers/join_requests.py:handle_join_request. None means no
    # pre-approval message is sent.
    pre_approval_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Force-subscribe: JSON list of {"identifier": "@channel_or_-100id",
    # "title": str|None, "link": "https://t.me/..."} that a user must already
    # belong to before their join request to THIS channel gets auto-approved.
    # None/empty means no gating - approval works exactly as before.
    required_join_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    targets: Mapped[list["PostTarget"]] = relationship(back_populates="channel")
    categories: Mapped[list["Category"]] = relationship(
        secondary=channel_categories, back_populates="channels"
    )
    stat_snapshots: Mapped[list["ChannelStatSnapshot"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class ContentType(str, enum.Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    ALBUM = "album"


class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENT = "sent"
    CANCELED = "canceled"
    DELETED = "deleted"


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, index=True)
    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType, name="content_type"))
    text: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    video_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[PostStatus] = mapped_column(Enum(PostStatus, name="post_status"), default=PostStatus.DRAFT)
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_delete_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # If set, this post repeats: once it is sent and (if auto-delete is set)
    # deleted, the scheduler recycles the SAME Post row back to SCHEDULED
    # with a fresh scheduled_time this many seconds in the future, instead
    # of leaving it DELETED for good. None means "post once".
    repeat_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Optional single inline button shown under this post when sent - same
    # idea as RepostRule.inline_button_text/inline_button_url above,
    # collected as an optional step in handlers/compose.py's compose flow.
    # Both must be set for the button to show. Never applied to ALBUM
    # posts: Telegram's send_media_group has no reply_markup parameter at
    # all (a hard platform limitation, already documented in
    # services/reposter.py for reposts) - the compose flow's button step
    # is skipped entirely for albums for this reason.
    button_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    button_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    targets: Mapped[list["PostTarget"]] = relationship(back_populates="post", cascade="all, delete-orphan")
    media_items: Mapped[list["PostMediaItem"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="PostMediaItem.position"
    )


class PostMediaItem(Base):
    """One photo/video belonging to an ALBUM post (Telegram media group).

    A single Post row still represents "one thing the user composed" - for
    an album that's a caption plus N media items, each recorded here in
    order so the whole set can be resent (e.g. for a scheduled post) or
    reconstructed later.
    """

    __tablename__ = "post_media_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0)
    media_type: Mapped[str] = mapped_column(String(16))  # "photo" | "video"
    file_id: Mapped[str] = mapped_column(String(255))

    post: Mapped["Post"] = relationship(back_populates="media_items")


class PostTarget(Base):
    """One row per (post, channel) - carries the per-channel message_id once sent."""

    __tablename__ = "post_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # For ALBUM posts, Telegram's send_media_group returns one Message per
    # item; message_id above holds the first one and the rest (as a JSON
    # list of ints) are kept here so auto-delete/edit can find every message
    # that belongs to the album instead of leaving stragglers behind.
    extra_message_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    post: Mapped["Post"] = relationship(back_populates="targets")
    channel: Mapped["Channel"] = relationship(back_populates="targets")


class SourceChannel(Base):
    """A public channel that the userbot watches for new posts."""

    __tablename__ = "source_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, index=True)
    identifier: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    rules: Mapped[list["RepostRule"]] = relationship(back_populates="source", cascade="all, delete-orphan")


class RepostRule(Base):
    """Maps a watched source channel to a destination channel the bot posts copies into."""

    __tablename__ = "repost_rules"
    __table_args__ = (UniqueConstraint("source_channel_id", "destination_channel_id", name="uq_repost_rule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_channel_id: Mapped[int] = mapped_column(ForeignKey("source_channels.id", ondelete="CASCADE"))
    destination_channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    caption_template: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    replacements_json: Mapped[str | None] = mapped_column(String, nullable=True)
    auto_delete_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Optional single inline button appended to every post this rule
    # forwards. Both must be set for the button to show - see
    # services/reposter.py:_build_button. Telegram buttons only support
    # plain text (no custom emoji rendering, no background styling), so
    # inline_button_text is exactly what shows on the button face.
    inline_button_text: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inline_button_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Text prepended (with a blank line after it) to every message this rule
    # forwards, e.g. a channel header/tagline. None means no prefix.
    prefix_text: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # How Telegram should render the link preview on forwarded TEXT posts
    # (media posts never get a link preview - that's a Telegram limitation,
    # not this app's). One of: None/"default" (Telegram's normal behavior),
    # "disabled", "small", "large", "above" (preview shown above the text
    # instead of below). See services/reposter.py:_build_link_preview.
    link_preview_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["SourceChannel"] = relationship(back_populates="rules")
    destination: Mapped["Channel"] = relationship()


class ChannelStatSnapshot(Base):
    """A point-in-time member-count reading for a registered channel, taken
    periodically by services/stats.py so /analytics can show growth over
    time instead of just a single current number."""

    __tablename__ = "channel_stat_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    member_count: Mapped[int] = mapped_column(Integer)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    channel: Mapped["Channel"] = relationship(back_populates="stat_snapshots")


class LinkPolicy(str, enum.Enum):
    DELETE_ALL = "delete_all"
    DELETE_INVITES_ADS = "delete_invites_ads"
    ADMINS_ONLY = "admins_only"


class SpamAction(str, enum.Enum):
    DELETE_ONLY = "delete_only"
    WARN_MUTE = "warn_mute"
    DELETE_KICK = "delete_kick"


class ModeratedGroup(Base):
    """A group/supergroup the operator has opted into automatic moderation for."""

    __tablename__ = "moderated_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_id: Mapped[int] = mapped_column(Integer, unique=True)
    title: Mapped[str] = mapped_column(String(255))
    moderation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    link_policy: Mapped[LinkPolicy] = mapped_column(
        Enum(LinkPolicy, name="link_policy"), default=LinkPolicy.DELETE_INVITES_ADS
    )
    spam_action: Mapped[SpamAction] = mapped_column(
        Enum(SpamAction, name="spam_action"), default=SpamAction.WARN_MUTE
    )
    # --- Welcome message, sent to the group when someone new joins -------
    # Same text/media/buttons shape as a recurring message (see
    # RecurringMessage below) but stored inline here since a group has at
    # most one welcome message, whereas it can have many recurring ones.
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    welcome_media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "photo" | "video" | None
    welcome_media_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON list of {"text": str, "url": str} - rendered as one inline
    # button per row, in order. None/empty means no buttons.
    welcome_buttons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    recurring_messages: Mapped[list["RecurringMessage"]] = relationship(
        back_populates="group", cascade="all, delete-orphan"
    )


class RecurringMessage(Base):
    """A message a moderated group's operator wants posted to that group on
    a repeating interval (announcements, rules reminders, promos, etc.).
    Same text/media/buttons shape as the group's welcome message; sent by
    services/recurring.py rather than on-join. A group can have several of
    these (e.g. a rules reminder every 12h and a promo every 24h)."""

    __tablename__ = "recurring_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("moderated_groups.id", ondelete="CASCADE"), index=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # "photo" | "video" | None
    media_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    buttons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    group: Mapped["ModeratedGroup"] = relationship(back_populates="recurring_messages")


class BroadcastUser(Base):
    """One row per unique person who has ever submitted a join request to
    any of the operator's channels - this is the audience /broadcast
    (handlers/broadcast.py) sends to.

    Telegram only lets a bot message someone who has interacted with it in
    a qualifying way; a chat_join_request IS that qualifying interaction
    (the same Bot API 5.5+ exception handlers/join_requests.py's
    pre-approval/welcome messages rely on). Recording happens the instant
    a join request arrives, regardless of whether that particular channel
    currently has auto-approve turned on - so this list only ever contains
    people who have actually reached out to one of the operator's channels
    through this bot, never a scraped member list. Rows are removed when a
    broadcast send comes back "forbidden" (blocked the bot / deactivated
    account), so the audience self-cleans over time instead of
    re-attempting dead ends on every future broadcast.
    """

    __tablename__ = "broadcast_users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
