# ─── Configuration ───────────────────────────────────────────────
# Fill in these values before running the bot.

# Telegram Bot token (get from @BotFather on Telegram)
TELEGRAM_BOT_TOKEN = "8656002412:AAGM8swbPOTybvNrwRRRu9l_G9bOnAOxFJA"

# Your Telegram chat ID (get by messaging @userinfobot on Telegram)
TELEGRAM_CHAT_ID = "955419217"

# ─── BookMyShow Settings ────────────────────────────────────────

# Venue code (from the URL: /buytickets/ALUC/ → "ALUC")
VENUE_CODE = "ALUC"
VENUE_NAME = "ALLU Cinemas: Kokapet"

# Venue URL slug (from the URL path: /cinemas/hyderabad/allu-cinemas-kokapet/...)
VENUE_SLUG = "allu-cinemas-kokapet"

# Region
REGION_CODE = "HYD"
REGION_NAME = "Hyderabad"

# Movie event code (from the URL: /ET00478890/ → "ET00478890")
# Leave empty to match by MOVIE_NAME instead
EVENT_CODE = "ET00478890"

# Movie name (substring match, used if EVENT_CODE is empty)
MOVIE_NAME = "Dhurandhar"

# Screen filter — only notify for shows matching this attribute
# e.g. "DOLBY CINEMA", "IMAX", "4DX", "BARCO LASER 4K ATMOS"
# Leave empty to match any screen
SCREEN_FILTER = "DOLBY CINEMA"

# Date to track (YYYYMMDD). If this date isn't listed yet, the bot
# will keep checking until it appears with your movie on the Dolby screen.
# Leave empty to get notified for ANY new date that appears.
TARGET_DATE = "20260318"

# How often to check (in seconds). Default: 300 (5 minutes)
CHECK_INTERVAL = 30
