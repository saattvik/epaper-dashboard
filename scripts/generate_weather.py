"""
Weather dashboard image generator for the LilyGo T5 4.7" e-paper display.

Fetches current + hourly + daily weather from Open-Meteo (no API key needed),
draws a full dashboard, and saves it as an uncompressed 8-bit grayscale BMP
at 960x540 - matching the panel resolution.

Run directly to test:  python generate_weather.py
Output:                weather.bmp (in the same folder)
"""

import math
import os
from datetime import datetime

import requests
from PIL import Image, ImageDraw, ImageFont

# =====================================================================
# CONFIG - edit these for your location
# =====================================================================
CITY_NAME = "Bengaluru"
LAT, LON  = 12.9716, 77.5946
TIMEZONE  = "Asia/Kolkata"   # Open-Meteo timezone name

WIDTH, HEIGHT = 960, 540
OUTPUT_PATH   = "docs/weather.bmp"

FONT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "fonts"
)
FONT_REGULAR = os.path.join(FONT_DIR, "OpenSans-Regular.ttf")
FONT_BOLD    = os.path.join(FONT_DIR, "OpenSans-Bold.ttf")

BLACK = 0
WHITE = 255
GRAY_LIGHT = 210   # for the precipitation shading in the graph
GRAY_MED   = 140   # for secondary/footer text


# =====================================================================
# Fonts (falls back to PIL's built-in font if the .ttf files aren't
# present yet, so the script still runs for a first test)
# =====================================================================
def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except (OSError, IOError):
        print(f"  (!) Could not load {path} at size {size} - using fallback font. "
              f"Download OpenSans-Regular.ttf / OpenSans-Bold.ttf into ./fonts/ for proper sizing.")
        return ImageFont.load_default()


def load_fonts():
    return {
        "city":     load_font(FONT_BOLD, 40),
        "date":     load_font(FONT_REGULAR, 22),
        "temp_big": load_font(FONT_BOLD, 90),
        "temp_unit": load_font(FONT_REGULAR, 32),
        "feels":    load_font(FONT_REGULAR, 24),
        "day_name": load_font(FONT_BOLD, 20),
        "day_temp": load_font(FONT_REGULAR, 18),
        "stat_label": load_font(FONT_REGULAR, 17),
        "stat_value": load_font(FONT_BOLD, 22),
        "axis":     load_font(FONT_REGULAR, 15),
        "footer":   load_font(FONT_REGULAR, 16),
    }


# =====================================================================
# Weather fetch (Open-Meteo - free, no API key)
# =====================================================================
def fetch_weather():
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT}&longitude={LON}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        "is_day,weather_code,pressure_msl,wind_speed_10m"
        "&hourly=temperature_2m,precipitation_probability,visibility,uv_index"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset"
        f"&timezone={TIMEZONE}"
        "&forecast_days=6"
    )
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_aqi():
    """Best-effort AQI fetch - returns None if it fails, so a bad/rate-limited
    air quality call never breaks the main weather image."""
    try:
        url = (
            "https://air-quality-api.open-meteo.com/v1/air-quality"
            f"?latitude={LAT}&longitude={LON}&current=us_aqi"
        )
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        return r.json()["current"]["us_aqi"]
    except Exception as e:
        print(f"  (!) AQI fetch failed, skipping: {e}")
        return None


# =====================================================================
# WMO weather code -> icon type
# https://open-meteo.com/en/docs (weather_code table)
# =====================================================================
def icon_type_for_code(code, is_day=True):
    if code == 0:
        return "clear" if is_day else "clear_night"
    if code in (1, 2):
        return "partly_cloudy" if is_day else "partly_cloudy_night"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57):
        return "rain_light"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "thunder"
    return "cloudy"


# =====================================================================
# Icon drawing primitives (simple line-art style, drawn with PIL shapes)
# =====================================================================
def draw_sun(draw, cx, cy, r, fill=BLACK, bg=WHITE, rays=True, width=4):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=bg, outline=fill, width=width)
    if rays:
        for angle in range(0, 360, 45):
            rad = math.radians(angle)
            x1 = cx + (r + 5) * math.cos(rad)
            y1 = cy + (r + 5) * math.sin(rad)
            x2 = cx + (r + 15) * math.cos(rad)
            y2 = cy + (r + 15) * math.sin(rad)
            draw.line([x1, y1, x2, y2], fill=fill, width=width)


def draw_moon(draw, cx, cy, r, fill=BLACK, bg=WHITE, width=4):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=width)
    # bite out a crescent using a background-filled circle offset to the right
    draw.ellipse([cx - r * 0.4, cy - r * 0.75, cx + r * 1.3, cy + r * 0.75], fill=bg)


def draw_cloud(draw, cx, cy, w, h, fill=BLACK, bg=WHITE, width=4):
    body_h = h * 0.55
    top = cy + h * 0.5 - body_h
    draw.rounded_rectangle(
        [cx - w / 2, top + body_h * 0.35, cx + w / 2, cy + h / 2],
        radius=body_h * 0.45, fill=bg, outline=fill, width=width
    )
    bump_r = h * 0.34
    for dx_frac, dy_frac in [(-0.28, -0.05), (0.02, -0.24), (0.30, -0.03)]:
        bx, by = cx + dx_frac * w, cy + dy_frac * h
        draw.ellipse([bx - bump_r, by - bump_r, bx + bump_r, by + bump_r],
                     fill=bg, outline=fill, width=width)


def draw_rain_drops(draw, cx, cy, size, fill=BLACK, width=4, count=4):
    for i in range(count):
        x = cx - size * 0.4 + i * (size * 0.8 / (count - 1))
        y1 = cy + size * 0.32
        y2 = y1 + size * 0.26
        draw.line([x, y1, x - size * 0.08, y2], fill=fill, width=width)


def draw_snow_marks(draw, cx, cy, size, fill=BLACK, width=3, count=4):
    for i in range(count):
        x = cx - size * 0.4 + i * (size * 0.8 / (count - 1))
        y = cy + size * 0.42
        r = size * 0.06
        draw.line([x - r, y, x + r, y], fill=fill, width=width)
        draw.line([x, y - r, x, y + r], fill=fill, width=width)
        draw.line([x - r * 0.7, y - r * 0.7, x + r * 0.7, y + r * 0.7], fill=fill, width=width)
        draw.line([x - r * 0.7, y + r * 0.7, x + r * 0.7, y - r * 0.7], fill=fill, width=width)


def draw_bolt(draw, cx, cy, size, fill=BLACK):
    pts = [
        (cx + size * 0.05, cy + size * 0.15),
        (cx - size * 0.12, cy + size * 0.45),
        (cx + size * 0.0, cy + size * 0.45),
        (cx - size * 0.08, cy + size * 0.75),
        (cx + size * 0.18, cy + size * 0.35),
        (cx + size * 0.0, cy + size * 0.35),
    ]
    draw.polygon(pts, fill=fill)


def draw_fog_lines(draw, cx, cy, size, fill=BLACK, width=4, count=4):
    for i in range(count):
        y = cy + size * 0.15 + i * size * 0.16
        spread = size * (0.5 - 0.06 * (i % 2))
        draw.line([cx - spread, y, cx + spread, y], fill=fill, width=width)


def draw_icon(draw, icon_type, cx, cy, size, fill=BLACK, bg=WHITE, width=4):
    """size = roughly the icon's half-width in pixels."""
    if icon_type == "clear":
        draw_sun(draw, cx, cy, size * 0.6, fill, bg, rays=True, width=width)
    elif icon_type == "clear_night":
        draw_moon(draw, cx, cy, size * 0.55, fill, bg, width=width)
    elif icon_type == "partly_cloudy":
        draw_sun(draw, cx + size * 0.28, cy - size * 0.32, size * 0.4, fill, bg, rays=True, width=width - 1)
        draw_cloud(draw, cx - size * 0.05, cy + size * 0.18, size * 1.3, size * 0.85, fill, bg, width)
    elif icon_type == "partly_cloudy_night":
        draw_moon(draw, cx + size * 0.28, cy - size * 0.32, size * 0.38, fill, bg, width=width - 1)
        draw_cloud(draw, cx - size * 0.05, cy + size * 0.18, size * 1.3, size * 0.85, fill, bg, width)
    elif icon_type == "cloudy":
        draw_cloud(draw, cx, cy, size * 1.5, size * 0.95, fill, bg, width)
    elif icon_type == "fog":
        draw_cloud(draw, cx, cy - size * 0.15, size * 1.3, size * 0.7, fill, bg, width)
        draw_fog_lines(draw, cx, cy + size * 0.35, size, fill, width - 1)
    elif icon_type in ("rain", "rain_light"):
        draw_cloud(draw, cx, cy - size * 0.15, size * 1.3, size * 0.85, fill, bg, width)
        draw_rain_drops(draw, cx, cy, size, fill, width - 1,
                         count=4 if icon_type == "rain" else 3)
    elif icon_type == "snow":
        draw_cloud(draw, cx, cy - size * 0.2, size * 1.3, size * 0.85, fill, bg, width)
        draw_snow_marks(draw, cx, cy, size, fill, width - 1)
    elif icon_type == "thunder":
        draw_cloud(draw, cx, cy - size * 0.25, size * 1.3, size * 0.8, fill, bg, width)
        draw_bolt(draw, cx, cy + size * 0.1, size, fill)
    else:
        draw_cloud(draw, cx, cy, size * 1.3, size * 0.85, fill, bg, width)


# =====================================================================
# Small stat icons (sunrise, sunset, wind, humidity, uv, pressure, aqi, eye)
# =====================================================================
def draw_stat_icon(draw, kind, cx, cy, r, fill=BLACK, width=3):
    if kind in ("sunrise", "sunset"):
        horizon_y = cy + r * 0.3
        draw.line([cx - r * 1.3, horizon_y, cx + r * 1.3, horizon_y], fill=fill, width=width)
        draw.pieslice([cx - r, horizon_y - r, cx + r, horizon_y + r], 180, 360, outline=fill, width=width)
        for angle in range(200, 341, 35):
            rad = math.radians(angle)
            x1 = cx + (r * 0.55) * math.cos(rad); y1 = horizon_y + (r * 0.55) * math.sin(rad)
            x2 = cx + (r * 0.9) * math.cos(rad);  y2 = horizon_y + (r * 0.9) * math.sin(rad)
            draw.line([x1, y1, x2, y2], fill=fill, width=max(1, width - 1))
        ax = cx + r * 1.55
        if kind == "sunrise":
            draw.line([ax, horizon_y + r * 0.5, ax, horizon_y - r * 0.55], fill=fill, width=width)
            draw.polygon([(ax - 5, horizon_y - r * 0.35), (ax + 5, horizon_y - r * 0.35), (ax, horizon_y - r * 0.65)], fill=fill)
        else:
            draw.line([ax, horizon_y - r * 0.55, ax, horizon_y + r * 0.5], fill=fill, width=width)
            draw.polygon([(ax - 5, horizon_y + r * 0.3), (ax + 5, horizon_y + r * 0.3), (ax, horizon_y + r * 0.6)], fill=fill)
    elif kind == "wind":
        for i, dy in enumerate([-r * 0.5, 0, r * 0.5]):
            length = r * (1.3 - i * 0.15)
            draw.line([cx - r, cy + dy, cx - r + length, cy + dy], fill=fill, width=width)
            draw.arc([cx - r + length - r * 0.3, cy + dy - r * 0.3,
                      cx - r + length + r * 0.3, cy + dy + r * 0.3], 270, 90, fill=fill, width=width)
    elif kind == "humidity":
        draw.polygon([(cx, cy - r), (cx - r * 0.75, cy + r * 0.6), (cx + r * 0.75, cy + r * 0.6)], outline=fill, width=width)
        draw.pieslice([cx - r * 0.75, cy - r * 0.2, cx + r * 0.75, cy + r * 1.2], 0, 360, outline=fill, width=width)
    elif kind == "uv":
        draw_sun(draw, cx, cy, r * 0.55, fill, WHITE, rays=True, width=width)
    elif kind == "pressure":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fill, width=width)
        draw.line([cx, cy, cx + r * 0.55, cy - r * 0.45], fill=fill, width=width)
        draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=fill)
    elif kind == "aqi":
        for i in range(3):
            x = cx - r * 0.5 + i * r * 0.5
            h = r * (0.6 + i * 0.35)
            draw.line([x, cy + r * 0.7, x, cy + r * 0.7 - h], fill=fill, width=width)
    elif kind == "visibility":
        draw.ellipse([cx - r, cy - r * 0.5, cx + r, cy + r * 0.5], outline=fill, width=width)
        draw.ellipse([cx - r * 0.35, cy - r * 0.35, cx + r * 0.35, cy + r * 0.35], outline=fill, width=width)


# =====================================================================
# Layout helpers
# =====================================================================
def text_w(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def draw_centered_text(draw, cx, y, text, font, fill=BLACK):
    w = text_w(draw, text, font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def dashed_hline(draw, x0, x1, y, fill=GRAY_MED, dash=4, gap=4, width=1):
    x = x0
    while x < x1:
        draw.line([x, y, min(x + dash, x1), y], fill=fill, width=width)
        x += dash + gap


# =====================================================================
# Main drawing routine
# =====================================================================
def build_image(data, aqi):
    img = Image.new("L", (WIDTH, HEIGHT), color=WHITE)
    draw = ImageDraw.Draw(img)
    fonts = load_fonts()

    current = data["current"]
    daily = data["daily"]
    hourly = data["hourly"]

    is_day = bool(current.get("is_day", 1))
    now_code = current["weather_code"]
    now_icon = icon_type_for_code(now_code, is_day)

    # -----------------------------------------------------------------
    # Header: city + date (top right)
    # -----------------------------------------------------------------
    now_dt = datetime.now()
    date_str = now_dt.strftime("%A, %d %B")

    city_w = text_w(draw, CITY_NAME, fonts["city"])
    draw.text((WIDTH - 20 - city_w, 12), CITY_NAME, font=fonts["city"], fill=BLACK)
    date_w = text_w(draw, date_str, fonts["date"])
    draw.text((WIDTH - 20 - date_w, 58), date_str, font=fonts["date"], fill=BLACK)

    # -----------------------------------------------------------------
    # Current conditions: icon + big temp + feels like (top left)
    # -----------------------------------------------------------------
    draw_icon(draw, now_icon, cx=105, cy=115, size=75, width=5)

    temp_val = round(current["temperature_2m"])
    temp_str = f"{temp_val}"
    draw.text((210, 55), temp_str, font=fonts["temp_big"], fill=BLACK)
    tw = text_w(draw, temp_str, fonts["temp_big"])
    draw.text((210 + tw + 5, 60), "\u00b0C", font=fonts["temp_unit"], fill=BLACK)

    feels = round(current["apparent_temperature"])
    draw.text((212, 165), f"Feels like {feels}\u00b0", font=fonts["feels"], fill=BLACK)

    # -----------------------------------------------------------------
    # 5-day forecast row (top right, below the date)
    # -----------------------------------------------------------------
    forecast_x0, forecast_x1 = 480, 940
    col_w = (forecast_x1 - forecast_x0) / 5
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for i in range(5):
        day_idx = i + 1  # skip today (index 0), show next 5 days
        cx = forecast_x0 + col_w * i + col_w / 2

        date_iso = daily["time"][day_idx]
        weekday = datetime.strptime(date_iso, "%Y-%m-%d").weekday()
        draw_centered_text(draw, cx, 100, day_names[weekday], fonts["day_name"])

        code = daily["weather_code"][day_idx]
        draw_icon(draw, icon_type_for_code(code, True), cx, 155, size=32, width=3)

        hi = round(daily["temperature_2m_max"][day_idx])
        lo = round(daily["temperature_2m_min"][day_idx])
        draw_centered_text(draw, cx, 205, f"{hi}\u00b0|{lo}\u00b0", fonts["day_temp"])

    # -----------------------------------------------------------------
    # Divider
    # -----------------------------------------------------------------
    draw.line([20, 260, 940, 260], fill=GRAY_MED, width=1)

    # -----------------------------------------------------------------
    # Stat grid (left side, 4 rows x 2 columns)
    # -----------------------------------------------------------------
    sunrise_str = datetime.fromisoformat(daily["sunrise"][0]).strftime("%H:%M")
    sunset_str  = datetime.fromisoformat(daily["sunset"][0]).strftime("%H:%M")
    wind_kmh    = round(current["wind_speed_10m"], 1)
    humidity    = round(current["relative_humidity_2m"])
    pressure    = round(current["pressure_msl"])

    # find current hour index in the hourly arrays
    hour_now_str = now_dt.strftime("%Y-%m-%dT%H:00")
    try:
        cur_idx = hourly["time"].index(hour_now_str)
    except ValueError:
        cur_idx = 0

    uv_val = round(hourly["uv_index"][cur_idx], 1)
    uv_level = "Low" if uv_val < 3 else "Moderate" if uv_val < 6 else "High" if uv_val < 8 else "Very High"

    vis_km = round(hourly["visibility"][cur_idx] / 1000, 1)
    aqi_str = f"{aqi}" if aqi is not None else "N/A"

    stats = [
        ("sunrise", "Sunrise", sunrise_str), ("sunset", "Sunset", sunset_str),
        ("wind", "Wind", f"{wind_kmh} km/h"), ("humidity", "Humidity", f"{humidity}%"),
        ("uv", "UV Index", f"{uv_val} {uv_level}"), ("pressure", "Pressure", f"{pressure} hPa"),
        ("aqi", "Air Quality", aqi_str), ("visibility", "Visibility", f"{vis_km} km"),
    ]

    row_h = 58
    col0_x, col1_x = 20, 200
    start_y = 278

    for i, (kind, label, value) in enumerate(stats):
        row = i // 2
        col = i % 2
        x = col0_x if col == 0 else col1_x
        y = start_y + row * row_h

        draw_stat_icon(draw, kind, x + 18, y + 20, 16)
        draw.text((x + 45, y + 4), label, font=fonts["stat_label"], fill=GRAY_MED)
        draw.text((x + 45, y + 24), value, font=fonts["stat_value"], fill=BLACK)

    # -----------------------------------------------------------------
    # Hourly temperature + precipitation-probability graph (right side)
    # -----------------------------------------------------------------
    gx0, gx1 = 400, 890
    gy0, gy1 = 280, 468

    hrs = 24
    temps = hourly["temperature_2m"][cur_idx: cur_idx + hrs]
    precs = hourly["precipitation_probability"][cur_idx: cur_idx + hrs]
    times = hourly["time"][cur_idx: cur_idx + hrs]

    tmin, tmax = min(temps), max(temps)
    axis_min = math.floor((tmin - 2) / 5) * 5
    axis_max = math.ceil((tmax + 2) / 5) * 5
    if axis_max == axis_min:
        axis_max += 5

    def temp_to_y(t):
        return gy1 - (t - axis_min) / (axis_max - axis_min) * (gy1 - gy0)

    steps = 5
    for i in range(steps + 1):
        y = gy0 + i * (gy1 - gy0) / steps
        temp_label = axis_max - i * (axis_max - axis_min) / steps
        pct_label = 100 - i * 100 / steps
        dashed_hline(draw, gx0, gx1, y)
        draw.text((gx0 - 38, y - 7), f"{round(temp_label)}\u00b0", font=fonts["axis"], fill=GRAY_MED)
        draw.text((gx1 + 6, y - 7), f"{round(pct_label)}%", font=fonts["axis"], fill=GRAY_MED)

    # precipitation probability as light-gray shading, bottom-aligned in the box
    bar_w = (gx1 - gx0) / (hrs - 1)
    for i, p in enumerate(precs):
        if p <= 0:
            continue
        x = gx0 + i * bar_w
        bar_h = (p / 100) * (gy1 - gy0)
        draw.rectangle([x - bar_w / 2, gy1 - bar_h, x + bar_w / 2, gy1], fill=GRAY_LIGHT)

    # temperature line
    points = [(gx0 + i * bar_w, temp_to_y(t)) for i, t in enumerate(temps)]
    draw.line(points, fill=BLACK, width=3, joint="curve")

    # axis legend labels (top of graph, above the highest gridline)
    draw.text((gx0 - 38, gy0 - 22), "Temp", font=fonts["axis"], fill=BLACK)
    rain_label = "Rain %"
    rw = text_w(draw, rain_label, fonts["axis"])
    draw.text((gx1 + 6 - max(0, rw - 34), gy0 - 22), rain_label, font=fonts["axis"], fill=GRAY_MED)

    # x-axis hour labels every 3 hours
    for i in range(0, hrs, 3):
        hour_label = datetime.fromisoformat(times[i]).strftime("%H")
        x = gx0 + i * bar_w
        draw_centered_text(draw, x, gy1 + 8, hour_label, fonts["axis"], fill=GRAY_MED)

    # -----------------------------------------------------------------
    # Footer: last updated timestamp
    # -----------------------------------------------------------------
    footer = "Updated " + now_dt.strftime("%d %b %Y, %H:%M")
    fw = text_w(draw, footer, fonts["footer"])
    draw.text((WIDTH - 20 - fw, HEIGHT - 26), footer, font=fonts["footer"], fill=GRAY_MED)

    return img


def save_bmp(img, path):
    """Save the image as an uncompressed 8-bit grayscale BMP.

    Creates the output directory first if it does not already exist.
    This is needed on GitHub Actions because the generated docs/ folder
    is not stored in the Git repository.
    """
    output_dir = os.path.dirname(path)

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    img.save(path, format="BMP")


def main():
    print(f"Fetching weather for {CITY_NAME} ({LAT}, {LON})...")
    data = fetch_weather()
    print("Weather fetched.")

    print("Fetching AQI (best-effort)...")
    aqi = fetch_aqi()

    print("Rendering image...")
    img = build_image(data, aqi)

    save_bmp(img, OUTPUT_PATH)
    print(f"Saved {OUTPUT_PATH} ({img.size[0]}x{img.size[1]}, mode={img.mode})")


if __name__ == "__main__":
    main()
