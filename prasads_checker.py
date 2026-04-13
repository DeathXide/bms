"""
Prasads PCX Screen Checker — rows H-N only.

Monitors the PCX SCREEN show at Prasads Multiplex (PRHN) for
Dhurandhar The Revenge on today's date. Uses:
  1. curl_cffi to check if the show is available (AvailStatus)
  2. Playwright + Konva.js extraction to check rows H-N seat availability

Sends Telegram alert if any seat in rows H-N becomes available.
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

# ── Constants ────────────────────────────────────────────────────

VENUE_CODE = "PRHN"
VENUE_NAME = "Prasads Multiplex: Hyderabad"
VENUE_SLUG = "prasads-multiplex-hyderabad"
EVENT_CODE = "ET00478890"
MOVIE_NAME = "Dhurandhar"
SCREEN_FILTER = "PCX"
TARGET_ROWS = ["H", "I", "J", "K", "L", "M", "N"]
TODAY = datetime.now().strftime("%Y%m%d")
STOP_HOUR, STOP_MINUTE = 20, 30  # 8:30 PM


# ── Telegram ─────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = plain_requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
        }, timeout=15)
        resp.raise_for_status()
        log.info("Telegram message sent.")
        return True
    except Exception as e:
        log.error("Telegram failed: %s", e)
        return False


# ── BMS page scraper ─────────────────────────────────────────────

def extract_state(html: str) -> dict | None:
    for marker in ["window.__INITIAL_STATE__=", "window.__INITIAL_STATE__ = "]:
        idx = html.find(marker)
        if idx == -1:
            continue
        json_start = idx + len(marker)
        depth = 0
        i = json_start
        while i < len(html):
            c = html[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        try:
            return json.loads(html[json_start : i + 1])
        except json.JSONDecodeError:
            pass
    return None


def check_show_exists() -> dict | None:
    """
    Check if the PCX show exists today and return its info.
    Returns dict with session_id, avail_status, show_time, etc. or None.
    """
    session = cffi_requests.Session(impersonate="chrome")
    url = (
        f"https://in.bookmyshow.com/cinemas/hyderabad/"
        f"{VENUE_SLUG}/buytickets/{VENUE_CODE}/{TODAY}"
    )
    try:
        resp = session.get(url, timeout=25)
        if resp.status_code != 200:
            log.warning("Cinema page returned %d", resp.status_code)
            return None

        state = extract_state(resp.text)
        if not state:
            return None

        st_api = state.get("venueShowtimesFunctionalApi", {}).get("queries", {})
        key = f"getShowtimesByVenue-{VENUE_CODE}-{TODAY}"
        if key not in st_api:
            return None

        data = st_api[key]["data"]
        sd = data["showDetailsTransformed"]

        for ev in sd.get("Event", []):
            for child in ev.get("ChildEvents", []):
                if child.get("EventCode", "").upper() != EVENT_CODE:
                    continue
                for show in child.get("ShowTimes", []):
                    attrs = show.get("Attributes", "")
                    if SCREEN_FILTER.lower() not in attrs.lower():
                        continue
                    return {
                        "session_id": show["SessionId"],
                        "show_time": show["ShowTime"],
                        "screen_name": show.get("ScreenName", ""),
                        "attributes": attrs,
                        "avail_status": show["AvailStatus"],
                        "date_code": show["ShowDateCode"],
                        "categories": show.get("Categories", []),
                    }
    except Exception as e:
        log.error("Error checking show: %s", e)
    return None


# ── Playwright seat layout checker ───────────────────────────────

def check_rows_hn(session_id: str) -> dict:
    """
    Use Playwright to load the seat layout page and check rows H-N.
    Returns {row: {total, available, seats: [...]}, ...}
    """
    from playwright.sync_api import sync_playwright

    seat_url = (
        f"https://in.bookmyshow.com/movies/hyderabad/seat-layout/"
        f"{EVENT_CODE}/{VENUE_CODE}/{session_id}/{TODAY}"
    )

    row_data = {r: {"total": 0, "available": 0, "seats": []} for r in TARGET_ROWS}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
            page = context.new_page()
            page.goto(seat_url, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            # Click "Select Seats"
            try:
                btn = page.wait_for_selector(
                    'button:has-text("Select Seats")', timeout=10000
                )
                btn.click()
                time.sleep(8)
            except Exception:
                log.warning("Could not click Select Seats button")
                browser.close()
                return row_data

            # Extract seat data from Konva canvas
            result = page.evaluate(
                """() => {
                const stages = window.Konva?.stages;
                if (!stages || !stages[0]) return JSON.stringify({error: "No stage"});

                const stage = stages[0];
                const targetRows = ['H', 'I', 'J', 'K', 'L', 'M', 'N'];

                // Get row labels sorted by y position (top = N, bottom = A)
                const rowLabels = [];
                stage.find('Text').forEach(t => {
                    if (/^[A-Z]$/.test(t.text())) {
                        rowLabels.push({letter: t.text(), y: Math.round(t.y())});
                    }
                });
                rowLabels.sort((a, b) => a.y - b.y);

                // Get seats grouped by row number
                const rowsByNum = {};
                stage.find('Group').forEach(g => {
                    const id = g.id();
                    if (!id || !id.startsWith('Seat-')) return;
                    const parts = id.split('-');
                    const rowNum = parts[2];
                    const seatNum = parts[3];
                    const rect = g.findOne('Rect');
                    if (!rect) return;
                    const y = Math.round(rect.y());
                    const fill = rect.fill();
                    const stroke = rect.stroke();
                    const isAvailable = fill === '#FFFFFF' || stroke === '#1FAD3E' || stroke === '#1EA83C';
                    if (!rowsByNum[rowNum]) rowsByNum[rowNum] = {y, total: 0, available: 0, seats: []};
                    rowsByNum[rowNum].total++;
                    if (isAvailable) {
                        rowsByNum[rowNum].available++;
                        rowsByNum[rowNum].seats.push(seatNum);
                    }
                });

                // Map row numbers to letters by y position
                const numEntries = Object.entries(rowsByNum).sort((a, b) => a[1].y - b[1].y);
                const result = {};
                for (let i = 0; i < Math.min(rowLabels.length, numEntries.length); i++) {
                    const letter = rowLabels[i].letter;
                    if (targetRows.includes(letter)) {
                        result[letter] = numEntries[i][1];
                    }
                }
                return JSON.stringify(result);
            }"""
            )

            parsed = json.loads(result)
            if "error" not in parsed:
                for row in TARGET_ROWS:
                    if row in parsed:
                        row_data[row] = parsed[row]

            browser.close()
    except Exception as e:
        log.error("Playwright error: %s", e)

    return row_data


# ── Main loop ────────────────────────────────────────────────────

def format_alert(show_info: dict, row_data: dict) -> str:
    lines = [
        "🎬 <b>BookMyShow Alert — Prasads PCX!</b>",
        f"🏟 <b>{VENUE_NAME}</b>",
        f"📅 Today ({TODAY}) | 🕐 {show_info['show_time']}",
        f"🎥 {MOVIE_NAME} | {show_info['screen_name']} | {show_info['attributes']}",
        "",
        "<b>Rows H-N availability:</b>",
    ]

    total = 0
    for row in TARGET_ROWS:
        rd = row_data[row]
        total += rd["available"]
        if rd["available"] > 0:
            seat_list = ", ".join(sorted(rd["seats"], key=lambda x: int(x)))
            lines.append(f"  ✅ Row {row}: {rd['available']} seats — {seat_list}")
        else:
            lines.append(f"  ❌ Row {row}: sold out")

    lines.append(f"\n🎫 Total available (H-N): <b>{total}</b>")
    booking_url = (
        f"https://in.bookmyshow.com/cinemas/hyderabad/"
        f"{VENUE_SLUG}/buytickets/{VENUE_CODE}/{TODAY}"
    )
    lines.append(f'\n🔗 <a href="{booking_url}">Book Now</a>')
    return "\n".join(lines)


def main():
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.error("Fill in TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in config.py")
        raise SystemExit(1)

    log.info("=" * 55)
    log.info("Prasads PCX Checker Started")
    log.info("  Movie:  %s (%s)", MOVIE_NAME, EVENT_CODE)
    log.info("  Venue:  %s (%s)", VENUE_NAME, VENUE_CODE)
    log.info("  Screen: %s", SCREEN_FILTER)
    log.info("  Rows:   %s", ", ".join(TARGET_ROWS))
    log.info("  Date:   %s (today)", TODAY)
    log.info("  Stops at: %02d:%02d", STOP_HOUR, STOP_MINUTE)
    log.info("  Interval: %ds", config.CHECK_INTERVAL)
    log.info("=" * 55)

    notified = False

    while True:
        now = datetime.now()
        if now.hour > STOP_HOUR or (now.hour == STOP_HOUR and now.minute >= STOP_MINUTE):
            log.info("Reached %02d:%02d. Stopping.", STOP_HOUR, STOP_MINUTE)
            send_telegram(
                "⏹ Prasads PCX checker stopped (8:30 PM). "
                "No seats found in rows H-N today."
            )
            break

        log.info("Checking Prasads PCX show...")

        try:
            show = check_show_exists()

            if not show:
                log.info("PCX show not found for today. Will retry.")
                time.sleep(config.CHECK_INTERVAL)
                continue

            log.info(
                "PCX show found: %s, Session %s, AvailStatus: %s",
                show["show_time"],
                show["session_id"],
                show["avail_status"],
            )

            # Check if any category is available
            any_category_avail = any(
                c.get("AvailStatus") == "1" for c in show.get("categories", [])
            )

            if show["avail_status"] == "0" and not any_category_avail:
                log.info("Show is sold out / unavailable. Checking again in %ds.", config.CHECK_INTERVAL)
                time.sleep(config.CHECK_INTERVAL)
                continue

            # Show is available — check rows H-N with Playwright
            log.info("Show is available! Checking rows H-N via seat layout...")
            row_data = check_rows_hn(show["session_id"])

            total_avail = sum(rd["available"] for rd in row_data.values())
            log.info("Rows H-N: %d seats available", total_avail)

            if total_avail > 0 and not notified:
                msg = format_alert(show, row_data)
                log.info("SEATS FOUND in rows H-N! Notifying...")
                print(msg)
                if send_telegram(msg):
                    notified = True
            elif total_avail > 0:
                log.info("Already notified about available seats.")
            else:
                log.info("No seats in H-N. Will keep checking.")

        except Exception as e:
            log.error("Error: %s", e, exc_info=True)

        time.sleep(config.CHECK_INTERVAL)


if __name__ == "__main__":
    main()
