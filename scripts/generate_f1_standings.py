import os
import time
from io import BytesIO
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ============================================================
# Config
# ============================================================
WIDTH = 960
HEIGHT = 540

TIMEZONE = "Asia/Kolkata"

OPENF1_BASE = "https://api.openf1.org/v1"
JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"

OUTPUT_PATH = "docs/current.bmp"

ASSET_DIR = Path("assets/f1")
TEAM_DIR = ASSET_DIR / "teams"
F1_LOGO_PATH = ASSET_DIR / "f1_logo.png"

FONT_REGULAR = "fonts/OpenSans-Regular.ttf"
FONT_BOLD = "fonts/OpenSans-Bold.ttf"

WHITE = 255
BLACK = 0
GRAY_LIGHT = 225
GRAY_MED = 145
GRAY_DARK = 70


TEAM_LOGOS = {
    "Mercedes": "mercedes.png",
    "Ferrari": "ferrari.png",
    "McLaren": "mclaren.png",

    "Red Bull": "red_bull.png",
    "Red Bull Racing": "red_bull.png",

    "RB F1 Team": "racing_bulls.png",
    "Racing Bulls": "racing_bulls.png",

    "Alpine F1 Team": "alpine.png",
    "Alpine": "alpine.png",

    "Haas F1 Team": "haas.png",
    "Haas": "haas.png",

    "Audi": "audi.png",
    "Williams": "williams.png",
    "Aston Martin": "aston_martin.png",
    "Cadillac": "cadillac.png",
}


# ============================================================
# Helpers
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


def fetch_json(url, params=None, headers=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=30,
            )

            r.raise_for_status()
            return r.json()

        except requests.RequestException as e:
            print(
                f"Request failed "
                f"{attempt + 1}/{retries}: {e}"
            )

            if attempt == retries - 1:
                raise

            time.sleep(3)


def format_points(points):
    points = float(points)
    return str(int(points)) if points.is_integer() else str(points)


def fetch_image(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


def paste_contain(base, overlay, box, scale=1.0, align_bottom=False):
    x0, y0, x1, y1 = box

    max_w = x1 - x0
    max_h = y1 - y0

    img = overlay.copy()

    fit_scale = min(
        max_w / img.width,
        max_h / img.height
    )

    final_scale = fit_scale * scale

    new_w = int(img.width * final_scale)
    new_h = int(img.height * final_scale)

    img = img.resize(
        (new_w, new_h),
        Image.LANCZOS
    )

    # Center horizontally
    x = x0 + (max_w - new_w) // 2

    # Either center vertically or align to bottom
    if align_bottom:
        y = y1 - new_h
    else:
        y = y0 + (max_h - new_h) // 2

    gray = ImageOps.grayscale(img)
    alpha = img.getchannel("A")

    base.paste(
        gray,
        (x, y),
        alpha
    )


def trim_transparent(img):
    if img.mode != "RGBA":
        return img

    alpha = img.getchannel("A")
    bbox = alpha.getbbox()

    if bbox:
        return img.crop(bbox)

    return img


def wrap_text(draw, text, font, max_width):
    words = text.split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        trial = current + " " + word

        if text_w(draw, trial, font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word

    lines.append(current)

    return lines


# ============================================================
# Data
# ============================================================
def get_standings():
    season = datetime.now(ZoneInfo(TIMEZONE)).year

    url = f"{JOLPICA_BASE}/{season}/driverstandings/"

    headers = {
        "User-Agent": "epaper-dashboard/1.0"
    }

    data = fetch_json(
        url,
        headers=headers,
    )

    standings_lists = (
        data["MRData"]
        ["StandingsTable"]
        ["StandingsLists"]
    )

    if not standings_lists:
        raise RuntimeError("No standings returned by Jolpica")

    standings = standings_lists[0]["DriverStandings"]

    rows = []

    for item in standings:
        driver = item["Driver"]
        constructors = item.get("Constructors", [])

        team_name = (
            constructors[0]["name"]
            if constructors
            else ""
        )

        rows.append({
            "position": int(item["position"]),
            "points": float(item["points"]),
            "number": driver.get("permanentNumber"),
            "name": (
                f"{driver['givenName']} "
                f"{driver['familyName']}"
            ),
            "acronym": driver.get("code", ""),
            "team": team_name,
            "headshot_url": None,
        })

    rows.sort(key=lambda r: r["position"])

    return rows


def get_leader_headshot_url(leader_row):
    drivers = fetch_json(
        f"{OPENF1_BASE}/drivers",
        params={"session_key": "latest"},
    )

    # First try matching by driver number
    for d in drivers:
        if str(d.get("driver_number")) == str(
            leader_row.get("number")
        ):
            return d.get("headshot_url")

    # Fallback: match by 3-letter acronym
    leader_acr = (
        leader_row.get("acronym") or ""
    ).upper()

    for d in drivers:
        if (
            d.get("name_acronym") or ""
        ).upper() == leader_acr:
            return d.get("headshot_url")

    return None


# ============================================================
# Rendering
# ============================================================
def build_image(rows):
    img = Image.new(
        "L",
        (WIDTH, HEIGHT),
        WHITE,
    )

    draw = ImageDraw.Draw(img)

    title_font = load_font(FONT_BOLD, 28)

    leader_font = load_font(FONT_BOLD, 31)
    leader_team_font = load_font(FONT_REGULAR, 18)
    points_big = load_font(FONT_BOLD, 38)

    row_name_font = load_font(FONT_BOLD, 18)
    row_team_font = load_font(FONT_REGULAR, 13)
    row_points_font = load_font(FONT_BOLD, 18)

    position_font = load_font(FONT_BOLD, 18)
    footer_font = load_font(FONT_REGULAR, 14)

    # ========================================================
    # Header
    # ========================================================
    if F1_LOGO_PATH.exists():
        logo = Image.open(
            F1_LOGO_PATH
        ).convert("RGBA")

        paste_contain(
            img,
            logo,
            (20, 10, 95, 48),
        )
    else:
        draw.text(
            (20, 12),
            "F1",
            font=title_font,
            fill=BLACK,
        )

    season = datetime.now(
        ZoneInfo(TIMEZONE)
    ).year

    draw.text(
        (110, 14),
        f"{season} DRIVER STANDINGS",
        font=title_font,
        fill=BLACK,
    )

    draw.line(
        (20, 58, 940, 58),
        fill=GRAY_LIGHT,
        width=2,
    )

    leader = rows[0]

    # ========================================================
    # Leader section
    # ========================================================
    left_x0 = 20
    left_x1 = 335

    draw.rounded_rectangle(
        (left_x0, 78, left_x1, 505),
        radius=15,
        outline=GRAY_LIGHT,
        width=2,
    )

    draw.text(
        (40, 92),
        "CHAMPIONSHIP LEADER",
        font=leader_team_font,
        fill=GRAY_DARK,
    )

    # -------------------------
    # Leader image
    # -------------------------
    if leader.get("headshot_url"):
        try:
            headshot = fetch_image(
                leader["headshot_url"]
            )
    
            headshot = trim_transparent(headshot)
    
            paste_contain(
                img,
                headshot,
                (20, 100, 335, 325),
                scale=0.8,
                align_bottom=True,
            )
    
        except Exception as e:
            print(
                f"Leader image failed: {e}"
            )
    
            draw.text(
                (115, 190),
                "No image",
                font=leader_team_font,
                fill=GRAY_MED,
            )

    else:
        draw.text(
            (115, 190),
            "No image",
            font=leader_team_font,
            fill=GRAY_MED,
        )

    # -------------------------
    # Wrapped leader name
    # -------------------------
    name_max_width = (
        left_x1 - left_x0 - 40
    )

    name_lines = wrap_text(
        draw,
        leader["name"].title(),
        leader_font,
        name_max_width,
    )

    name_y = 334
    line_gap = 34

    for i, line in enumerate(name_lines):
        draw.text(
            (
                40,
                name_y + i * line_gap,
            ),
            line,
            font=leader_font,
            fill=BLACK,
        )

    # -------------------------
    # Team name
    # -------------------------
    team_y = (
        name_y
        + len(name_lines) * line_gap
        + 6
    )

    draw.text(
        (40, team_y),
        leader["team"],
        font=leader_team_font,
        fill=GRAY_DARK,
    )

    # -------------------------
    # Leader points
    # -------------------------
    points_label_y = team_y + 32
    points_value_y = points_label_y + 16

    draw.text(
        (40, points_label_y),
        "PTS",
        font=footer_font,
        fill=GRAY_MED,
    )

    draw.text(
        (40, points_value_y),
        format_points(
            leader["points"]
        ),
        font=points_big,
        fill=BLACK,
    )

    # ========================================================
    # Standings list
    # ========================================================
    start_x = 355
    row_y = 78
    row_h = 41

    for row in rows[:10]:

        draw.line(
            (
                start_x,
                row_y + row_h,
                940,
                row_y + row_h,
            ),
            fill=GRAY_LIGHT,
            width=1,
        )

        # ---------------------
        # Position
        # ---------------------
        draw.text(
            (
                start_x + 10,
                row_y + 8,
            ),
            str(row["position"]),
            font=position_font,
            fill=BLACK,
        )

        # ---------------------
        # Team logo
        # ---------------------
        logo_filename = TEAM_LOGOS.get(
            row["team"]
        )

        if logo_filename:
            logo_path = (
                TEAM_DIR /
                logo_filename
            )

            if logo_path.exists():
                team_logo = Image.open(
                    logo_path
                ).convert("RGBA")

                paste_contain(
                    img,
                    team_logo,
                    (
                        start_x + 50,
                        row_y + 5,
                        start_x + 90,
                        row_y + 35,
                    ),
                )

        # ---------------------
        # Driver name
        # ---------------------
        draw.text(
            (
                start_x + 105,
                row_y + 3,
            ),
            row["name"].title(),
            font=row_name_font,
            fill=BLACK,
        )

        # ---------------------
        # Team name
        # ---------------------
        draw.text(
            (
                start_x + 105,
                row_y + 23,
            ),
            row["team"],
            font=row_team_font,
            fill=GRAY_MED,
        )

        # ---------------------
        # Points
        # ---------------------
        pts = format_points(
            row["points"]
        )

        pts_w = text_w(
            draw,
            pts,
            row_points_font,
        )

        draw.text(
            (
                920 - pts_w,
                row_y + 10,
            ),
            pts,
            font=row_points_font,
            fill=BLACK,
        )

        row_y += row_h

    # ========================================================
    # Footer
    # ========================================================
    now = datetime.now(
        ZoneInfo(TIMEZONE)
    )

    footer = (
        "Updated "
        + now.strftime(
            "%d %b %Y, %H:%M"
        )
    )

    footer_w = text_w(
        draw,
        footer,
        footer_font,
    )

    draw.text(
        (
            940 - footer_w,
            518,
        ),
        footer,
        font=footer_font,
        fill=GRAY_MED,
    )

    return img


# ============================================================
# Main
# ============================================================
def main():

    print(
        "Fetching current F1 driver standings..."
    )

    rows = get_standings()

    if not rows:
        raise RuntimeError(
            "No standings returned"
        )

    print(
        f"Fetched {len(rows)} drivers"
    )

    print(
        "Fetching leader headshot..."
    )

    try:
        rows[0]["headshot_url"] = (
            get_leader_headshot_url(
                rows[0]
            )
        )

        if rows[0]["headshot_url"]:
            print(
                "Leader headshot found."
            )
        else:
            print(
                "Leader headshot not found."
            )

    except Exception as e:
        print(
            f"Leader headshot lookup failed: {e}"
        )

        rows[0]["headshot_url"] = None

    print(
        "Rendering image..."
    )

    img = build_image(rows)

    os.makedirs(
        os.path.dirname(
            OUTPUT_PATH
        ),
        exist_ok=True,
    )

    img.save(
        OUTPUT_PATH,
        format="BMP",
    )

    print(
        f"Saved {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
