import os

# One deployment serves exactly one event's knowledge pack -- see db.py's docstring.
EVENT_PACK = os.environ.get("EVENT_PACK", "gaya_ji")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

SUPPORTED_LANGUAGES = {
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
    "kn": "Kannada", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi",
    "ml": "Malayalam", "or": "Odia", "as": "Assamese", "en": "English",
}
