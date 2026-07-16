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
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    targets: Mapped[list["PostTarget"]] = relationship(back_populates="channel")
    categories: Mapped[list["Category"]] = relationship(
        secondary=channel_categories, back_populates="channels"
    )


class ContentType(str, enum.Enum):
    TEXT = "text"
    PHOTO = "photo"


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
    status: Mapped[PostStatus] = mapped_column(Enum(PostStatus, name="post_status"), default=PostStatus.DRAFT)
    scheduled_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_delete_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    targets: Mapped[list["PostTarget"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class PostTarget(Base):
    """One row per (post, channel) - carries the per-channel message_id once sent."""

    __tablename__ = "post_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["SourceChannel"] = relationship(back_populates="rules")
    destination: Mapped["Channel"] = relationship()


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
