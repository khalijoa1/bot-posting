import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Table, UniqueConstraint, func
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
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_delete_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    targets: Mapped[list["PostTarget"]] = relationship(back_populates="post", cascade="all, delete-orphan")


class PostTarget(Base):
    """One row per (post, channel) - carries the per-channel message_id once sent,
    which is what makes editing a multi-channel post possible afterward."""

    __tablename__ = "post_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    post: Mapped["Post"] = relationship(back_populates="targets")
    channel: Mapped["Channel"] = relationship(back_populates="targets")


class SourceChannel(Base):
    """A public channel (not owned by the operator) that the userbot watches for new posts."""

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
    # Optional caption template applied after the message is copied. Use placeholders like
    # {original_text}, {source_title}, {source_username}, {post_url}
    caption_template: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # Optional JSON string describing replacements. Format example:
    # {"default": {"foo": "bar"}, "-1001234567890": {"Hello": "Hi"}}
    replacements_json: Mapped[str | None] = mapped_column(String, nullable=True)
    # Optional per-rule auto-delete (seconds). If set, created Post.delete_at will be now()+seconds
    auto_delete_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["SourceChannel"] = relationship(back_populates="rules")
    destination: Mapped["Channel"] = relationship()

