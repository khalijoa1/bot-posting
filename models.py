@@
 class RepostRule(Base):
@@
     id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
     source_channel_id: Mapped[int] = mapped_column(ForeignKey("source_channels.id", ondelete="CASCADE"))
     destination_channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
+    # Optional caption template applied after the message is copied. Use placeholders like
+    # {original_text}, {source_title}, {source_username}, {post_url}
+    caption_template: Mapped[str | None] = mapped_column(String(2048), nullable=True)
+    # Optional JSON string describing replacements. Format example:
+    # {"default": {"foo": "bar"}, "-1001234567890": {"Hello": "Hi"}}
+    replacements_json: Mapped[str | None] = mapped_column(String, nullable=True)
+    # Optional per-rule auto-delete (seconds). If set, created Post.delete_at will be now()+seconds
+    auto_delete_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
@@
     source: Mapped["SourceChannel"] = relationship(back_populates="rules")
     destination: Mapped["Channel"] = relationship()
