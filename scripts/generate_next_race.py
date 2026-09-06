"""
Next F1 race dashboard generator.

Everything except two circuit facts (length_km, corners) is fetched live -
no per-circuit timezone table, no local flag images, no manually re-entered
race calendar. See CIRCUIT_FACTS below for why those two numbers specifically
can't be sourced from a free API.
"""

import os
from io import BytesIO
from pathlib import Path
from datetime import datetime, date
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import pycountry
    print("pycountry loaded OK")
except ImportError as e:
    pycountry = None
    print(f"pycountry NOT available: {e}")

try:
    from timezonefinder import TimezoneFinder
    _TF = TimezoneFinder()
    print("timezonefinder loaded OK")
except Exception as e:
    _TF = None
    print(f"timezonefinder NOT available: {e}")


# ============================================================
# Config
# ============================================================
WIDTH, HEIGHT = 960, 540

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
OPENF1_BASE = "https://api.openf1.org/v1"
FLAG_CDN = "https://flagcdn.com/w160"  # ISO alpha-2 code, e.g. .../w160/es.png

OUTPUT_PATH = "docs/current.bmp"

ASSET_DIR = Path("assets/f1")
TEAM_DIR = ASSET_DIR / "teams"
TRACK_DIR = ASSET_DIR / "tracks"       # pre-converted track outline PNGs, one per circuitId
ICON_DIR = ASSET_DIR / "icons"         # small black-glyph-on-transparent PNGs (calendar, clock, pin, etc.)
F1_LOGO_PATH = ASSET_DIR / "f1_logo.png"

FONT_REGULAR = "fonts/OpenSans-Regular.ttf"
FONT_BOLD = "fonts/OpenSans-Bold.ttf"

WHITE, BLACK = 255, 0
GRAY_LIGHT, GRAY_MED, GRAY_DARK = 225, 145, 70

TEAM_LOGOS = {
    "Mercedes": "mercedes.png", "Ferrari": "ferrari.png", "McLaren": "mclaren.png",
    "Red Bull": "red_bull.png", "Red Bull Racing": "red_bull.png",
    "RB F1 Team": "racing_bulls.png", "Racing Bulls": "racing_bulls.png",
    "Alpine F1 Team": "alpine.png", "Alpine": "alpine.png",
    "Haas F1 Team": "haas.png", "Haas": "haas.png",
    "Audi": "audi.png", "Williams": "williams.png",
    "Aston Martin": "aston_martin.png", "Cadillac": "cadillac.png",
}

# ------------------------------------------------------------
# The ONE genuinely unavoidable hardcoded table.
# Circuit length and corner count aren't exposed by Jolpica, OpenF1, or
# Wikidata in a consistently structured way - every other field on this
# page is fetched live. Fill in as you add each new circuit; only two
# numbers needed per entry.
# ------------------------------------------------------------
CIRCUIT_FACTS = {
    "catalunya": {"length_km": 4.657, "corners": 14},
    "monza": {"length_km": 5.793, "corners": 11},
    # add more circuitId -> {length_km, corners} as the season progresses
}

# Country-name overrides where Jolpica's naming doesn't match what pycountry
# expects. Everything not listed here is resolved automatically.
COUNTRY_NAME_OVERRIDES = {
    "USA": "US", "UK": "GB", "UAE": "AE", "Korea": "KR",
}


# ============================================================
# Generic helpers
# ============================================================
def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        print(f"Could not load font {path}, using fallback")
        return ImageFont.load_default()


def text_w(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def text_h_and_top_offset(draw, text, font):
    box = draw.textbbox((0, 0), text, font=font)
    return box[3] - box[1], box[1]


def centered_text_y(draw, text, font, center_y):
    h, top = text_h_and_top_offset(draw, text, font)
    return center_y - h / 2 - top


def draw_centered_text(draw, cx, y, text, font, fill=BLACK):
    w = text_w(draw, text, font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def wrap_text(draw, text, font, max_width):
    words = text.split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for word in words[1:]:
        trial = current + " " + word
        if text_w(draw, trial, font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def fetch_json(url, params=None, headers=None, timeout=20):
    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_image(url, timeout=20):
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


def paste_contain(base, overlay, box, scale=1.0, align_bottom=False):
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))
    max_w, max_h = x1 - x0, y1 - y0

    img = overlay.copy()
    fit_scale = min(max_w / img.width, max_h / img.height)
    final_scale = fit_scale * scale
    new_w, new_h = int(img.width * final_scale), int(img.height * final_scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    x = x0 + (max_w - new_w) // 2
    y = (y1 - new_h) if align_bottom else y0 + (max_h - new_h) // 2

    if img.mode == "RGBA":
        gray = ImageOps.grayscale(img)
        alpha = img.getchannel("A")
        base.paste(gray, (int(x), int(y)), alpha)
    else:
        base.paste(img, (int(x), int(y)))


def draw_badge_icon(img, draw, box, icon_filename, fallback_fn, badge_color=BLACK, icon_frac=0.85):
    """Draws a solid rounded-square badge with either a real icon asset
    (white glyph pasted on top, if ICON_DIR/icon_filename exists) or a
    hand-drawn fallback glyph (in white) so the page still works with zero
    assets provided."""
    x0, y0, x1, y1 = box
    radius = (x1 - x0) * 0.1
    draw.rounded_rectangle(box, radius=radius, fill=badge_color)

    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    size = min(x1 - x0, y1 - y0)

    icon_path = ICON_DIR / icon_filename
    if icon_path.exists():
        icon = Image.open(icon_path).convert("RGBA")
        alpha = icon.getchannel("A")
        white_icon = Image.merge("RGBA", (Image.new("L", icon.size, 255),) * 3 + (alpha,))
        half = size * icon_frac / 2
        paste_contain(img, white_icon, (cx - half, cy - half, cx + half, cy + half))
    else:
        fallback_fn(draw, cx, cy, size * icon_frac / 2, fill=WHITE)


def dither_to_epaper_gray(img):
    """Floyd-Steinberg dither to the panel's 16 gray levels (spaced so they
    survive the ESP32's >>4 truncation losslessly). Use for photos only -
    headshots, flags - not for flat text/lines/logos."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    pal_img = Image.new("P", (1, 1))
    levels = [i * 17 for i in range(16)]
    palette = []
    for lvl in levels:
        palette += [lvl, lvl, lvl]
    palette += [0, 0, 0] * (256 - 16)
    pal_img.putpalette(palette)
    return img.quantize(palette=pal_img, dither=Image.FLOYDSTEINBERG).convert("L")


def country_to_iso2(country_name):
    if country_name in COUNTRY_NAME_OVERRIDES:
        return COUNTRY_NAME_OVERRIDES[country_name]
    if pycountry is None:
        print(f"country_to_iso2: pycountry not available, can't resolve '{country_name}'")
        return None
    try:
        match = pycountry.countries.lookup(country_name)
        return match.alpha_2.lower()
    except LookupError:
        try:
            results = pycountry.countries.search_fuzzy(country_name)
            return results[0].alpha_2.lower()
        except LookupError:
            print(f"Could not resolve country '{country_name}' to an ISO code")
            return None


def fetch_flag(country_name):
    iso2 = country_to_iso2(country_name)
    if not iso2:
        return None
    try:
        url = f"{FLAG_CDN}/{iso2}.png"
        img = fetch_image(url)
        print(f"Flag fetched OK for {country_name} ({iso2})")
        return img
    except Exception as e:
        print(f"Flag fetch failed for {country_name} ({iso2}): {e}")
        return None


def local_time_at(lat, lon, utc_dt):
    """Given a circuit's lat/long and a UTC datetime, returns the circuit's
    local wall-clock time - no per-circuit timezone table needed."""
    if _TF is None:
        print("local_time_at: TimezoneFinder not available, falling back to UTC")
        return None
    if lat is None or lon is None:
        print(f"local_time_at: missing lat/lon (lat={lat}, lon={lon}), falling back to UTC")
        return None
    tz_name = _TF.timezone_at(lat=float(lat), lng=float(lon))
    if not tz_name:
        print(f"local_time_at: no timezone found for lat={lat}, lon={lon}, falling back to UTC")
        return None
    print(f"local_time_at: resolved lat={lat}, lon={lon} -> {tz_name}")
    return utc_dt.astimezone(ZoneInfo(tz_name)), tz_name


# ============================================================
# Data fetching
# ============================================================
def get_next_race():
    season = datetime.now(ZoneInfo("UTC")).year
    url = f"{JOLPICA_BASE}/{season}.json"
    data = fetch_json(url, headers={"User-Agent": "epaper-dashboard/1.0"}, params={"limit": 40})

    races = data["MRData"]["RaceTable"]["Races"]
    total_rounds = len(races)
    today = date.today()

    for race in races:
        race_date = datetime.strptime(race["date"], "%Y-%m-%d").date()
        if race_date >= today:
            race["_total_rounds"] = total_rounds
            return race

    # season over - fall back to the last race so the page isn't empty
    if races:
        races[-1]["_total_rounds"] = total_rounds
        return races[-1]

    raise RuntimeError("No races returned by Jolpica")


def get_last_year_result(circuit_id, year):
    """Returns (winner_dict, laps) for the given circuit/year, or (None, None)
    if unavailable (e.g. circuit new to the calendar this season)."""
    try:
        url = f"{JOLPICA_BASE}/{year}/circuits/{circuit_id}/results.json"
        data = fetch_json(
            url,
            headers={"User-Agent": "epaper-dashboard/1.0"},
            params={"limit": 1},
        )
        race_list = data["MRData"]["RaceTable"]["Races"]
        if not race_list:
            return None, None

        result = race_list[0]["Results"][0]
        driver = result["Driver"]
        constructor = result["Constructor"]

        winner = {
            "name": f"{driver['givenName']} {driver['familyName']}",
            "number": driver.get("permanentNumber"),
            "acronym": driver.get("code", ""),
            "team": constructor["name"],
        }
        laps = int(result.get("laps", 0)) or None
        return winner, laps

    except Exception as e:
        print(f"Last-year result fetch failed: {e}")
        return None, None


def get_driver_headshot(driver_number, acronym):
    try:
        drivers = fetch_json(f"{OPENF1_BASE}/drivers", params={"session_key": "latest"})
        for d in drivers:
            if str(d.get("driver_number")) == str(driver_number):
                return d.get("headshot_url")
        for d in drivers:
            if (d.get("name_acronym") or "").upper() == (acronym or "").upper():
                return d.get("headshot_url")
    except Exception as e:
        print(f"Headshot lookup failed: {e}")
    return None


# ============================================================
# Small icons (consistent line-art style with the other dashboards)
# ============================================================
def draw_calendar_icon(draw, cx, cy, size, fill=BLACK, width=3):
    x0, y0 = cx - size, cy - size * 0.8
    x1, y1 = cx + size, cy + size * 0.8
    draw.rounded_rectangle([x0, y0, x1, y1], radius=size * 0.2, outline=fill, width=width)
    draw.line([x0, y0 + size * 0.5, x1, y0 + size * 0.5], fill=fill, width=width)
    draw.line([cx - size * 0.5, y0 - size * 0.25, cx - size * 0.5, y0 + size * 0.25], fill=fill, width=width)
    draw.line([cx + size * 0.5, y0 - size * 0.25, cx + size * 0.5, y0 + size * 0.25], fill=fill, width=width)


def draw_clock_icon(draw, cx, cy, r, fill=BLACK, width=3):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=width)
    draw.line([cx, cy, cx, cy - r * 0.55], fill=fill, width=width)
    draw.line([cx, cy, cx + r * 0.4, cy + r * 0.2], fill=fill, width=width)


def draw_pin_icon(draw, cx, cy, size, fill=BLACK, width=3):
    r = size * 0.55
    draw.ellipse([cx - r, cy - size, cx + r, cy - size + 2 * r], outline=fill, width=width)
    draw.polygon(
        [(cx - r * 0.6, cy - size + 1.7 * r), (cx + r * 0.6, cy - size + 1.7 * r), (cx, cy + size * 0.3)],
        fill=fill,
    )
    draw.ellipse([cx - r * 0.4, cy - size + r * 0.6, cx + r * 0.4, cy - size + r * 1.4], fill=WHITE)


def draw_corners_icon(draw, cx, cy, size, fill=BLACK, width=4):
    pts = [
        (cx - size, cy + size * 0.6), (cx - size * 0.3, cy + size * 0.6),
        (cx - size * 0.1, cy - size * 0.2), (cx + size * 0.4, cy - size * 0.2),
        (cx + size * 0.6, cy + size * 0.5), (cx + size, cy + size * 0.5),
    ]
    draw.line(pts, fill=fill, width=width, joint="curve")


def draw_ruler_icon(draw, cx, cy, size, fill=BLACK, width=3):
    draw.rectangle([cx - size, cy - size * 0.35, cx + size, cy + size * 0.35], outline=fill, width=width)
    for i in range(-3, 4):
        x = cx + i * size / 3.5
        draw.line([x, cy - size * 0.35, x, cy - size * 0.05], fill=fill, width=2)


def draw_speedo_icon(draw, cx, cy, r, fill=BLACK, width=3):
    draw.arc([cx - r, cy - r, cx + r, cy + r], 200, 340, fill=fill, width=width)
    draw.line([cx, cy, cx + r * 0.5, cy - r * 0.5], fill=fill, width=width)


# ============================================================
# Rendering
# ============================================================
def build_image(race, winner, laps):
    img = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    f_tag = load_font(FONT_BOLD, 20)
    f_title = load_font(FONT_BOLD, 40)
    f_section = load_font(FONT_BOLD, 17)
    f_row_label = load_font(FONT_REGULAR, 15)
    f_row_value = load_font(FONT_BOLD, 20)
    f_circuit = load_font(FONT_BOLD, 18)
    f_city = load_font(FONT_REGULAR, 15)
    f_round = load_font(FONT_BOLD, 17)
    f_daterange = load_font(FONT_REGULAR, 15)
    f_winner_name = load_font(FONT_BOLD, 20)
    f_winner_team = load_font(FONT_REGULAR, 15)
    f_stat_label = load_font(FONT_REGULAR, 15)
    f_stat_value = load_font(FONT_BOLD, 26)
    f_footer = load_font(FONT_REGULAR, 14)

    circuit = race["Circuit"]
    circuit_id = circuit["circuitId"]
    circuit_name = circuit["circuitName"]
    locality = circuit["Location"]["locality"]
    country = circuit["Location"]["country"]
    lat, lon = circuit["Location"].get("lat"), circuit["Location"].get("long")

    race_name = race["raceName"].upper()
    race_date_str = race["date"]
    race_dt_utc = datetime.strptime(f"{race['date']} {race.get('time', '12:00:00Z').replace('Z','')}",
                                     "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("UTC"))

    round_num = int(race["round"])
    total_rounds = race["_total_rounds"]

    # ------------------------------------------------------------
    # Header
    # ------------------------------------------------------------
    logo_box = (20, 10, 135, 62)   # was (20, 10, 110, 50) - bigger, matching the reference's proportions
    if F1_LOGO_PATH.exists():
        logo = Image.open(F1_LOGO_PATH).convert("RGBA")
        paste_contain(img, logo, logo_box)
    else:
        draw.text((20, 15), "F1", font=f_title, fill=BLACK)

    next_race_y = centered_text_y(draw, "NEXT RACE", f_tag, (logo_box[1] + logo_box[3]) / 2)
    draw.text((150, next_race_y), "NEXT RACE", font=f_tag, fill=BLACK)

    round_str = f"ROUND {round_num} OF {total_rounds}"
    rs_w = text_w(draw, round_str, f_round)
    draw.text((940 - rs_w, 30), round_str, font=f_round, fill=GRAY_DARK)

    draw.line((20, 65, 940, 65), fill=GRAY_LIGHT, width=2)

    # ------------------------------------------------------------
    # Left column: race name + circuit/country + date/time/location rows
    # ------------------------------------------------------------
    left_x = 20
    name_lines = wrap_text(draw, race_name, f_title, 300)
    ny = 78
    for line in name_lines:
        draw.text((left_x, ny), line, font=f_title, fill=BLACK)
        ny += 44

    ny += 6
    flag_img = fetch_flag(country)
    if flag_img:
        flag_gray = flag_img.convert("L")  # flat color bands - plain grayscale looks crisper than dithering here
        flag_box_w, flag_box_h = 40, 26
        flag_resized = flag_gray.resize(
            (flag_box_w, flag_box_h),
            Image.LANCZOS,
        )
        img.paste(flag_resized, (left_x, int(ny)))
    draw.text((left_x + 50, ny - 2), circuit_name.upper(), font=f_circuit, fill=BLACK)
    draw.text((left_x + 50, ny + 18), f"{locality}, {country}", font=f_city, fill=GRAY_DARK)

    row_y = ny + 55
    row_h = 54

    local_result = local_time_at(lat, lon, race_dt_utc) if race.get("time") else None
    if local_result:
        local_dt, tz_name = local_result
        time_str = local_dt.strftime("%H:%M") + " " + local_dt.strftime("%Z")
    else:
        time_str = race.get("time", "TBD").replace("Z", " UTC")

    date_display = race_dt_utc.strftime("%A, %d %B %Y")

    rows = [
        ("calendar.png", draw_calendar_icon, "RACE DATE", date_display),
        ("clock.png", draw_clock_icon, "RACE TIME (LOCAL)", time_str),
        ("pin.png", draw_pin_icon, "LOCATION", f"{locality}, {country}"),
    ]

    badge_size = 44
    for icon_file, fallback_fn, label, value in rows:
        by0 = row_y + row_h / 2 - badge_size / 2
        draw_badge_icon(img, draw, (left_x, by0, left_x + badge_size, by0 + badge_size), icon_file, fallback_fn)
        draw.text((left_x + badge_size + 14, row_y + 6), label, font=f_row_label, fill=GRAY_MED)
        draw.text((left_x + badge_size + 14, row_y + 24), value, font=f_row_value, fill=BLACK)
        draw.line((left_x, row_y + row_h - 4, left_x + 305, row_y + row_h - 4), fill=GRAY_LIGHT, width=1)
        row_y += row_h

    # ------------------------------------------------------------
    # Middle column: track layout
    # ------------------------------------------------------------
    mid_x0, mid_x1 = 350, 630
    draw.text((mid_x0, 78), "TRACK LAYOUT", font=f_section, fill=BLACK)
    draw.line((mid_x0, 100, mid_x1, 100), fill=GRAY_LIGHT, width=1)

    track_path = TRACK_DIR / f"{circuit_id}.png"
    if track_path.exists():
        track_img = Image.open(track_path).convert("RGBA")
        paste_contain(img, track_img, (mid_x0, 110, mid_x1, 400))
    else:
        draw_centered_text(draw, (mid_x0 + mid_x1) / 2, 240, "Track layout", f_row_value, fill=GRAY_MED)
        draw_centered_text(draw, (mid_x0 + mid_x1) / 2, 265, "not added yet", f_row_value, fill=GRAY_MED)

    # ------------------------------------------------------------
    # Right column: last year's winner
    # ------------------------------------------------------------
    right_x0, right_x1 = 660, 940
    draw.text((right_x0, 78), "LAST YEAR'S WINNER", font=f_section, fill=BLACK)
    draw.line((right_x0, 100, right_x1, 100), fill=GRAY_LIGHT, width=1)

    if winner:
        headshot_url = winner.get("headshot_url")
        if headshot_url:
            try:
                high_res = headshot_url.replace(".transform/1col/image.png", ".transform/6col/image.png")
                headshot = fetch_image(high_res)
                alpha = headshot.getchannel("A")
                bbox = alpha.getbbox()
                if bbox:
                    headshot = headshot.crop(bbox)
                composited = Image.new("RGB", headshot.size, (255, 255, 255))
                composited.paste(headshot, (0, 0), headshot.getchannel("A"))
                dithered = dither_to_epaper_gray(composited)
                box_w, box_h = right_x1 - right_x0, 170
                fit_scale = min(box_w / dithered.width, box_h / dithered.height)
                new_size = (int(dithered.width * fit_scale), int(dithered.height * fit_scale))
                dithered = dithered.resize(new_size, Image.LANCZOS)
                paste_x = right_x0 + (box_w - new_size[0]) // 2
                img.paste(dithered, (paste_x, 110))
            except Exception as e:
                print(f"Winner headshot render failed: {e}")
                draw_centered_text(draw, (right_x0 + right_x1) / 2, 190, "No image", f_row_value, fill=GRAY_MED)
        else:
            draw_centered_text(draw, (right_x0 + right_x1) / 2, 190, "No image", f_row_value, fill=GRAY_MED)

        wy = 295
        draw_centered_text(draw, (right_x0 + right_x1) / 2, wy, winner["name"].upper(), f_winner_name)
        wy += 26
        draw_centered_text(draw, (right_x0 + right_x1) / 2, wy, winner["team"], f_winner_team, fill=GRAY_DARK)
        wy += 24

        logo_filename = TEAM_LOGOS.get(winner["team"])
        if logo_filename:
            logo_path = TEAM_DIR / logo_filename
            if logo_path.exists():
                team_logo = Image.open(logo_path).convert("RGBA")
                cx = (right_x0 + right_x1) / 2
                paste_contain(img, team_logo, (cx - 40, wy, cx + 40, wy + 35))
    else:
        draw_centered_text(draw, (right_x0 + right_x1) / 2, 200, "Not available", f_row_value, fill=GRAY_MED)
        draw_centered_text(draw, (right_x0 + right_x1) / 2, 224, "(new to calendar)", f_row_label, fill=GRAY_MED)

    # ------------------------------------------------------------
    # Bottom stat bar
    # ------------------------------------------------------------
    draw.line((20, 445, 940, 445), fill=BLACK, width=2)

    facts = CIRCUIT_FACTS.get(circuit_id)
    corners_str = str(facts["corners"]) if facts else "N/A"
    length_str = f"{facts['length_km']}" if facts else "N/A"
    if facts and laps:
        distance_str = f"{laps} LAPS / {round(facts['length_km'] * laps, 3)} km"
    elif laps:
        distance_str = f"{laps} LAPS"
    else:
        distance_str = "N/A"

    stat_cols = [
        ("corners.png", draw_corners_icon, "NUMBER OF CORNERS", corners_str, 20),
        ("ruler.png", draw_ruler_icon, "CIRCUIT LENGTH", f"{length_str} km" if facts else length_str, 350),
        ("speedo.png", draw_speedo_icon, "RACE DISTANCE", distance_str, 620),
    ]

    badge_size = 50
    for icon_file, fallback_fn, label, value, x in stat_cols:
        by0 = 465
        draw_badge_icon(img, draw, (x, by0, x + badge_size, by0 + badge_size), icon_file, fallback_fn)
        draw.text((x + badge_size + 14, by0 + 2), label, font=f_stat_label, fill=GRAY_DARK)
        draw.text((x + badge_size + 14, by0 + 20), value, font=f_stat_value, fill=BLACK)

    # ------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------
    footer = "Updated " + datetime.now(ZoneInfo("UTC")).strftime("%d %b %Y, %H:%M UTC")
    fw = text_w(draw, footer, f_footer)
    draw.text((940 - fw, 520), footer, font=f_footer, fill=GRAY_MED)

    return img


# ============================================================
# Main
# ============================================================
def main():
    print("Fetching next race from Jolpica...")
    race = get_next_race()
    circuit_id = race["Circuit"]["circuitId"]
    print(f"Next race: {race['raceName']} ({circuit_id}) on {race['date']}")

    last_year = int(race["season"]) - 1
    print(f"Fetching {last_year} result at {circuit_id}...")
    winner, laps = get_last_year_result(circuit_id, last_year)

    if winner:
        print(f"Last year's winner: {winner['name']} ({winner['team']}), {laps} laps")
        winner["headshot_url"] = get_driver_headshot(winner["number"], winner["acronym"])
    else:
        print("No previous result found for this circuit (likely new to the calendar).")

    print("Rendering image...")
    img = build_image(race, winner, laps)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    img.save(OUTPUT_PATH, format="BMP")
    print(f"Saved {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
