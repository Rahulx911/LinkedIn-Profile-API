import os

# app.config.Settings requires LI_AT_COOKIE; tests never make real LinkedIn
# calls, but the module-level `get_settings()` call in app.main still needs
# something to load. Set a dummy value before any app module is imported.
os.environ.setdefault("LI_AT_COOKIE", "test-dummy-cookie")
