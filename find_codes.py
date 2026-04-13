"""
Helper script to find BookMyShow region codes and venue codes.

Usage:
    python find_codes.py regions              # List all regions/cities
    python find_codes.py venues BANG          # List cinemas in Bengaluru
    python find_codes.py movies BANG          # List currently showing movies in Bengaluru
"""

import sys
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def list_regions():
    print("Fetching regions...")
    resp = requests.get(
        "https://in.bookmyshow.com/api/explore/v1/discover/regions",
        headers=HEADERS, timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    # The structure may vary — try common shapes
    regions = data if isinstance(data, list) else data.get("TopCities", []) + data.get("OtherCities", [])
    if not regions and isinstance(data, dict):
        # Try nested
        for key in data:
            if isinstance(data[key], list):
                regions = data[key]
                break

    print(f"\n{'Code':<10} {'Name':<25} {'Alias'}")
    print("-" * 60)
    for r in regions:
        code = r.get("RegionCode", "") or r.get("code", "")
        name = r.get("RegionName", "") or r.get("name", "") or r.get("Alias", "")
        alias = r.get("Alias", "") or r.get("RegionSlug", "")
        if code:
            print(f"{code:<10} {name:<25} {alias}")


def list_venues(region_code: str):
    print(f"Fetching venues for region: {region_code}...")
    resp = requests.get(
        "https://in.bookmyshow.com/pwa/api/de/venues",
        params={"regionCode": region_code, "eventType": "MT"},
        headers=HEADERS, timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    venues = data.get("BookMyShow", {}).get("arrVenue", [])
    if not venues:
        # Try flat list
        venues = data if isinstance(data, list) else []

    print(f"\n{'Code':<10} {'Venue Name'}")
    print("-" * 60)
    for v in venues:
        code = v.get("VenueCode", "")
        name = v.get("VenueName", "") or v.get("VenueInfoName", "")
        if code:
            print(f"{code:<10} {name}")

    if not venues:
        print("No venues found. Check if the region code is correct.")


def list_movies(region_code: str):
    print(f"Fetching movies for region: {region_code}...")
    cookies = {"Rgn": f"|Code={region_code}|text=Region|"}
    resp = requests.get(
        "https://in.bookmyshow.com/serv/getData",
        params={"cmd": "QUICKBOOK", "type": "MT"},
        headers=HEADERS, cookies=cookies, timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    events = (
        data.get("moviesData", {})
        .get("BookMyShow", {})
        .get("arrEvents", [])
    )

    print(f"\n{'Event Code':<15} {'Title'}")
    print("-" * 60)
    for ev in events:
        code = ev.get("EventCode", "")
        title = ev.get("EventTitle", "")
        if title:
            print(f"{code:<15} {title}")

    if not events:
        print("No movies found. Check if the region code is correct.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    command = sys.argv[1].lower()

    if command == "regions":
        list_regions()
    elif command == "venues":
        if len(sys.argv) < 3:
            print("Usage: python find_codes.py venues <REGION_CODE>")
            return
        list_venues(sys.argv[2].upper())
    elif command == "movies":
        if len(sys.argv) < 3:
            print("Usage: python find_codes.py movies <REGION_CODE>")
            return
        list_movies(sys.argv[2].upper())
    else:
        print(f"Unknown command: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
