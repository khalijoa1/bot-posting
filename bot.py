*** Begin Patch
*** Update File: bot.py
@@
-from handlers import categories, channels, compose, manage, sources
+from handlers import categories, channels, compose, manage, sources, reposter
@@
     dp.include_router(categories.router)
     dp.include_router(compose.router)
     dp.include_router(manage.router)
     dp.include_router(sources.router)
+    dp.include_router(reposter.router)
*** End Patch
