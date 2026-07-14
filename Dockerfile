*** Begin Patch
*** Update File: Dockerfile
@@
-COPY bot.py .
-
-# Railway uses a web process to keep the container alive; we keep polling in foreground
-CMD ["python", "bot.py"]
+COPY bot.py .
+COPY migrations migrations
+COPY scripts scripts
+RUN chmod +x scripts/run.sh
+
+# Railway uses a web process to keep the container alive; run migrations then bot
+CMD ["./scripts/run.sh"]
*** End Patch
