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
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
