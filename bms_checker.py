"""
BookMyShow Dolby Cinema Availability Checker with Telegram Notifications.

Monitors a specific venue on BookMyShow for when a movie's bookings open
on a particular screen type (e.g., Dolby Cinema). Sends a Telegram alert
when the show appears.

Uses curl_cffi to bypass Cloudflare protection and extracts showtime data
from the embedded __INITIAL_STATE__ JSON in BookMyShow's cinema pages.
"""

import json
import re
import time
import logging
from datetime import datetime

from curl_cffi import requests as cffi_requests
import requests as plain_requests

import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Telegram ─────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    """Send a message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        resp = plain_requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        log.info("Telegram message sent successfully.")
        return True
    except Exception as e:
        log.error("Failed to send Telegram message: %s", e)
        return False


# ── BookMyShow scraper ───────────────────────────────────────────

def create_session() -> cffi_requests.Session:
    """Create a curl_cffi session that impersonates Chrome."""
    return cffi_requests.Session(impersonate="chrome")


def extract_state(html: str) -> dict | None:
    """Extract window.__INITIAL_STATE__ JSON from BMS page HTML."""
    for marker in ["window.__INITIAL_STATE__=", "window.__INITIAL_STATE__ = "]:
        idx = html.find(marker)
        if idx != -1:
            json_start = idx + len(marker)
            end_idx = html.find("</script>", json_start)
            raw = html[json_start:end_idx].rstrip().rstrip(";")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                log.warning("Failed to parse __INITIAL_STATE__: %s", e)
    return None


def get_showtimes_for_date(session: cffi_requests.Session, venue_code: str,
                           date_code: str) -> dict | None:
    """
    Fetch the BMS cinema page for a specific date and extract the
    showtime data from __INITIAL_STATE__.
    """
    region_slug = config.REGION_NAME.lower()
    venue_slug = config.VENUE_SLUG
    url = (
        f"https://in.bookmyshow.com/cinemas/{region_slug}/"
        f"{venue_slug}/buytickets/{venue_code}/{date_code}"
    )
    try:
        resp = session.get(url, timeout=25)
        if resp.status_code != 200:
            log.warning("Got status %d for %s", resp.status_code, url)
            return None

        state = extract_state(resp.text)
        if not state:
            log.warning("Could not extract __INITIAL_STATE__ from %s", url)
            return None

        return state
    except Exception as e:
        log.error("Failed to fetch %s: %s", url, e)
        return None


def get_venue_url_slug(state: dict) -> str:
    """Try to extract the actual venue URL slug from the state data."""
    seo = state.get("seo", {}).get("queries", {})
    for key in seo:
        if "/buytickets/" in key:
            # key looks like /cinemas/hyderabad/allu-cinemas-kokapet/buytickets/ALUC/20260318
            parts = key.split("/")
            # Find the part before "buytickets"
            for i, p in enumerate(parts):
                if p == "buytickets" and i >= 2:
                    return parts[i - 1]
    return ""


def extract_show_dates(state: dict) -> list[dict]:
    """Extract list of available show dates from state."""
    return state.get("venueShowtimesNew", {}).get("showDates", [])


def extract_events(state: dict, venue_code: str, date_code: str) -> list[dict]:
    """Extract event/showtime data from the state."""
    st_api = state.get("venueShowtimesFunctionalApi", {}).get("queries", {})
    key = f"getShowtimesByVenue-{venue_code}-{date_code}"
    if key in st_api:
        data = st_api[key].get("data", {})
        sd = data.get("showDetailsTransformed", {})
        return sd.get("Event", [])
    return []


def find_matching_shows(events: list[dict]) -> list[dict]:
    """
    Search events for shows matching our criteria:
    - Movie matches EVENT_CODE or MOVIE_NAME
    - Screen matches SCREEN_FILTER (e.g., "DOLBY CINEMA")

    Returns list of matching show dicts with details.
    """
    matches = []
    event_code = config.EVENT_CODE.upper() if config.EVENT_CODE else ""
    movie_name = config.MOVIE_NAME.lower() if config.MOVIE_NAME else ""
    screen_filter = config.SCREEN_FILTER.lower() if config.SCREEN_FILTER else ""

    for event in events:
        event_title = event.get("EventTitle", "")
        for child in event.get("ChildEvents", []):
            child_code = child.get("EventCode", "").upper()
            child_name = child.get("EventName", "")

            # Check movie match
            movie_match = False
            if event_code and child_code == event_code:
                movie_match = True
            elif movie_name and movie_name in child_name.lower():
                movie_match = True
            elif movie_name and movie_name in event_title.lower():
                movie_match = True

            if not movie_match:
                continue

            for show in child.get("ShowTimes", []):
                attributes = show.get("Attributes", "").lower()
                screen_name = show.get("ScreenName", "")

                # Check screen filter
                if screen_filter and screen_filter not in attributes:
                    continue

                matches.append({
                    "movie": event_title,
                    "child_name": child_name,
                    "dimension": child.get("EventDimension", ""),
                    "language": child.get("EventLanguage", ""),
                    "session_id": show.get("SessionId", ""),
                    "show_time": show.get("ShowTime", ""),
                    "screen_name": screen_name,
                    "attributes": show.get("Attributes", ""),
                    "date_code": show.get("ShowDateCode", ""),
                    "avail_status": show.get("AvailStatus", ""),
                    "min_price": show.get("MinPrice", ""),
                    "max_price": show.get("MaxPrice", ""),
                    "categories": show.get("Categories", []),
                })

    return matches


# ── Core check logic ─────────────────────────────────────────────

def check_availability() -> list[dict]:
    """
    Main check: fetch the venue page, look for matching shows.
    If TARGET_DATE is set, only check that date.
    Otherwise, check all available dates.
    """
    session = create_session()
    all_matches = []

    # First, fetch the base page to get available dates
    today = datetime.now().strftime("%Y%m%d")
    base_date = config.TARGET_DATE if config.TARGET_DATE else today

    state = get_showtimes_for_date(session, config.VENUE_CODE, base_date)
    if not state:
        log.warning("Could not fetch base page.")
        return []

    if config.TARGET_DATE:
        # Only check the specific target date
        dates_to_check = [config.TARGET_DATE]
    else:
        # Check all available dates
        show_dates = extract_show_dates(state)
        dates_to_check = [d["DateCode"] for d in show_dates]
        log.info("Available dates: %s", ", ".join(dates_to_check))

    for date_code in dates_to_check:
        if date_code == base_date:
            # Already have this page's data
            date_state = state
        else:
            date_state = get_showtimes_for_date(session, config.VENUE_CODE, date_code)
            time.sleep(1)  # Be respectful

        if not date_state:
            continue

        events = extract_events(date_state, config.VENUE_CODE, date_code)
        matches = find_matching_shows(events)

        for m in matches:
            m["date_code"] = date_code
            all_matches.append(m)

    return all_matches


# ── Notification formatting ──────────────────────────────────────

def format_notification(matches: list[dict]) -> str:
    """Build a Telegram message from matching shows."""
    lines = [
        f"🎬 <b>BookMyShow Alert!</b>",
        f"🏟 <b>{config.VENUE_NAME}</b>",
        "",
    ]

    # Group by date
    by_date = {}
    for m in matches:
        dc = m["date_code"]
        by_date.setdefault(dc, []).append(m)

    for date_code in sorted(by_date):
        # Format date nicely
        try:
            dt = datetime.strptime(date_code, "%Y%m%d")
            date_str = dt.strftime("%A, %d %B %Y")
        except ValueError:
            date_str = date_code

        lines.append(f"📅 <b>{date_str}</b>")
        for m in by_date[date_code]:
            avail = "✅ Available" if m["avail_status"] == "1" else "🟡 Listed"
            lines.append(
                f"  🎥 {m['movie']} ({m['language']} {m['dimension']})"
            )
            lines.append(
                f"  🕐 {m['show_time']} | {m['screen_name']} | {m['attributes']}"
            )
            lines.append(f"  💰 ₹{m['min_price']} - ₹{m['max_price']} | {avail}")

            # Show category details
            for cat in m.get("categories", []):
                cat_avail = "✅" if cat.get("AvailStatus") == "1" else "❌ Sold"
                lines.append(
                    f"     {cat.get('PriceDesc', '?')}: ₹{cat.get('CurPrice', '?')} {cat_avail}"
                )
            lines.append("")

    booking_url = (
        f"https://in.bookmyshow.com/cinemas/{config.REGION_NAME.lower()}/"
        f"{config.VENUE_SLUG}/buytickets/{config.VENUE_CODE}"
    )
    lines.append(f"🔗 <a href=\"{booking_url}\">Book Now on BookMyShow</a>")
    return "\n".join(lines)


# ── Main loop ────────────────────────────────────────────────────

def validate_config():
    """Check that required config values are filled in."""
    errors = []
    if not config.TELEGRAM_BOT_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is empty")
    if not config.TELEGRAM_CHAT_ID:
        errors.append("TELEGRAM_CHAT_ID is empty")
    if not config.VENUE_CODE:
        errors.append("VENUE_CODE is empty")
    if not config.REGION_NAME:
        errors.append("REGION_NAME is empty")
    if not config.EVENT_CODE and not config.MOVIE_NAME:
        errors.append("Either EVENT_CODE or MOVIE_NAME must be set")
    if errors:
        log.error("Configuration errors:\n  - %s", "\n  - ".join(errors))
        raise SystemExit(1)


def main():
    validate_config()

    log.info("=" * 55)
    log.info("BookMyShow Checker Started")
    log.info("  Venue:  %s (%s)", config.VENUE_NAME, config.VENUE_CODE)
    log.info("  Movie:  %s (code: %s)", config.MOVIE_NAME, config.EVENT_CODE)
    log.info("  Screen: %s", config.SCREEN_FILTER or "(any)")
    log.info("  Date:   %s", config.TARGET_DATE or "(all available)")
    log.info("  Check interval: %ds", config.CHECK_INTERVAL)
    log.info("=" * 55)

    # Track what we've already notified about (by session_id+date)
    notified_shows: set[str] = set()

    stop_hour, stop_minute = 20, 30  # Stop at 8:30 PM IST

    while True:
        now = datetime.now()
        if now.hour > stop_hour or (now.hour == stop_hour and now.minute >= stop_minute):
            log.info("Reached %02d:%02d. Stopping for today.", stop_hour, stop_minute)
            send_telegram("⏹ BookMyShow checker stopped for today (8:30 PM). No new Dolby shows found.")
            break

        log.info("Checking BookMyShow...")
        try:
            matches = check_availability()

            # Filter out already-notified shows
            new_matches = []
            for m in matches:
                key = f"{m['date_code']}-{m['session_id']}"
                if key not in notified_shows:
                    new_matches.append(m)

            if new_matches:
                msg = format_notification(new_matches)
                log.info("NEW SHOWS FOUND! Sending notification...")
                print(msg)
                success = send_telegram(msg)
                if success:
                    for m in new_matches:
                        notified_shows.add(f"{m['date_code']}-{m['session_id']}")
                    log.info("Notified about %d show(s). Tracking %d total.",
                             len(new_matches), len(notified_shows))
                else:
                    log.warning("Telegram send failed, will retry next cycle.")
            elif matches:
                log.info("Shows exist but already notified. Watching for new ones.")
            else:
                log.info("No matching shows found yet. Checking again in %ds.",
                         config.CHECK_INTERVAL)

        except Exception as e:
            log.error("Error during check: %s", e, exc_info=True)

        time.sleep(config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
