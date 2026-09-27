import os

# One deployment serves exactly one event's knowledge pack -- see db.py's docstring.
EVENT_PACK = os.environ.get("EVENT_PACK", "gaya_ji")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

# The Android app connects to /ws/ directly (no Origin header, not a browser), and the admin
# panel is same-origin (a browser navigates straight to it) -- so there's no legitimate case for
# a third-party site's JS to call this API cross-origin. Default to just this deployment's own
# domain rather than "*"; override via env if a real cross-origin caller is ever needed.
CORS_ALLOW_ORIGINS = [
    o.strip() for o in os.environ.get("CORS_ALLOW_ORIGINS", "https://vaani.smartsafetytag.com").split(",") if o.strip()
]

SUPPORTED_LANGUAGES = {
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
    "kn": "Kannada", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi",
    "ml": "Malayalam", "or": "Odia", "as": "Assamese", "en": "English",
}
