"""
Weather dashboard image generator for the LilyGo T5 4.7" e-paper display.

Fetches current + hourly + daily weather from Open-Meteo (no API key needed),
uses Google PNG icons for weather conditions and stats, draws the dashboard,
and saves it as an uncompressed 8-bit grayscale BMP
at 960x540 - matching the panel resolution.

Run directly to test:  python generate_weather.py
Output:                weather.bmp (in the same folder)
"""

import time
import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

# =====================================================================
# CONFIG - edit these for your location
# =====================================================================
CITY_NAME = "Bengaluru"
LAT, LON  = 12.9716, 77.5946
TIMEZONE  = "Asia/Kolkata"   # Open-Meteo timezone name

WIDTH, HEIGHT = 960, 540
OUTPUT_PATH   = "docs/current.bmp"

FONT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "fonts"
)
FONT_REGULAR = os.path.join(FONT_DIR, "OpenSans-Regular.ttf")
FONT_BOLD    = os.path.join(FONT_DIR, "OpenSans-Bold.ttf")

# Google weather/stat icon PNGs.
# Expected repo layout:
#   assets/weather/icons/<filename>.png
WEATHER_ICON_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "assets",
    "weather",
    "icons",
)

WEATHER_ICON_FILES = {
    "clear_day": "clear_day.png",
    "clear_night": "clear_night.png",
    "partly_cloudy_day": "partly_cloudy_day.png",
    "partly_cloudy_night": "partly_cloudy_night.png",
    "cloudy": "cloudy.png",
    "fog": "fog.png",
    "drizzle": "rainy.png",
    "rainy": "rainy.png",
    "snow": "snow.png",
    "thunderstorm": "thunderstorm.png",
}

STAT_ICON_FILES = {
    "sunrise": "sunrise.png",
    "sunset": "sunset.png",
    "wind": "wind.png",
    "humidity": "humidity.png",
    "uv": "uv.png",
    "pressure": "pressure.png",
    "aqi": "air_quality.png",
    "visibility": "visibility.png",
}

BLACK = 0
WHITE = 255
GRAY_LIGHT = 200  # for the precipitation shading in the graph
GRAY_MED   = 110   # for secondary/footer text


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

    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            return r.json()
    
        except requests.exceptions.RequestException as e:
            print(f"Weather fetch attempt {attempt + 1}/3 failed: {e}")
    
            if attempt == 2:
                raise
    
            time.sleep(5)


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
# WMO weather code -> Google icon key
# https://open-meteo.com/en/docs (weather_code table)
# =====================================================================
def icon_type_for_code(code, is_day=True):
    if code == 0:
        return "clear_day" if is_day else "clear_night"
    if code in (1, 2):
        return "partly_cloudy_day" if is_day else "partly_cloudy_night"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57):
        return "drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rainy"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code in (95, 96, 99):
        return "thunderstorm"
    return "cloudy"


# =====================================================================
# PNG icon helpers
# =====================================================================
_ICON_CACHE = {}


def load_icon(filename):
    """Load one transparent PNG icon and cache it for the rest of the run."""
    if filename in _ICON_CACHE:
        return _ICON_CACHE[filename]

    path = os.path.join(WEATHER_ICON_DIR, filename)

    if not os.path.exists(path):
        print(f"  (!) Icon not found: {path}")
        _ICON_CACHE[filename] = None
        return None

    try:
        icon = Image.open(path).convert("RGBA")

        # Crop transparent padding so every icon uses its requested box well.
        alpha = icon.getchannel("A")
        bbox = alpha.getbbox()
        if bbox:
            icon = icon.crop(bbox)

        _ICON_CACHE[filename] = icon
        return icon

    except Exception as e:
        print(f"  (!) Could not load icon {path}: {e}")
        _ICON_CACHE[filename] = None
        return None


def paste_icon(base, filename, box, fill=BLACK):
    """
    Paste a transparent icon into a box while preserving aspect ratio.

    The icon's RGB colour is ignored deliberately; its alpha mask is used to
    draw a clean monochrome glyph. That makes the downloaded Google PNG colour
    irrelevant and gives consistent black icons on the e-paper dashboard.
    """
    icon = load_icon(filename)
    if icon is None:
        return False

    x0, y0, x1, y1 = [int(round(v)) for v in box]
    max_w = max(1, x1 - x0)
    max_h = max(1, y1 - y0)

    scale = min(max_w / icon.width, max_h / icon.height)
    new_w = max(1, int(round(icon.width * scale)))
    new_h = max(1, int(round(icon.height * scale)))

    alpha = icon.getchannel("A").resize((new_w, new_h), Image.LANCZOS)

    x = x0 + (max_w - new_w) // 2
    y = y0 + (max_h - new_h) // 2

    glyph = Image.new("L", (new_w, new_h), color=fill)
    base.paste(glyph, (x, y), alpha)
    return True


def draw_weather_icon(img, icon_type, cx, cy, size):
    """Draw a weather-condition PNG centered at cx/cy."""
    filename = WEATHER_ICON_FILES.get(
        icon_type,
        WEATHER_ICON_FILES["cloudy"],
    )

    return paste_icon(
        img,
        filename,
        (
            cx - size,
            cy - size,
            cx + size,
            cy + size,
        ),
    )


def draw_stat_icon_png(img, kind, cx, cy, size):
    """Draw a stat icon such as wind/humidity/sunrise from the PNG assets."""
    filename = STAT_ICON_FILES.get(kind)
    if not filename:
        return False

    half = size / 2
    return paste_icon(
        img,
        filename,
        (
            cx - half,
            cy - half,
            cx + half,
            cy + half,
        ),
    )


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


def smooth_series(values, passes=2):
    """Lightly smooth hourly values without significantly changing the trend."""
    vals = [float(v) for v in values]
    if len(vals) < 3:
        return vals

    for _ in range(passes):
        vals = (
            [vals[0]]
            + [
                (vals[i - 1] + 2 * vals[i] + vals[i + 1]) / 4
                for i in range(1, len(vals) - 1)
            ]
            + [vals[-1]]
        )
    return vals


def catmull_rom_spline(points, samples_per_segment=16):
    """Create a smooth Catmull-Rom curve passing through the supplied points."""
    if len(points) < 3:
        return points

    def interp(p0, p1, p2, p3, t):
        t2 = t * t
        t3 = t2 * t

        x = 0.5 * (
            (2 * p1[0])
            + (-p0[0] + p2[0]) * t
            + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
            + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
        )

        y = 0.5 * (
            (2 * p1[1])
            + (-p0[1] + p2[1]) * t
            + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
            + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
        )
        return (x, y)

    padded = [points[0]] + list(points) + [points[-1]]
    curve = []

    for i in range(1, len(padded) - 2):
        p0, p1, p2, p3 = (
            padded[i - 1],
            padded[i],
            padded[i + 1],
            padded[i + 2],
        )
        for sample in range(samples_per_segment):
            t = sample / samples_per_segment
            curve.append(interp(p0, p1, p2, p3, t))

    curve.append(points[-1])
    return curve


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

    # Shared right edge for the header, forecast, divider and footer.
    page_right = 930

    # -----------------------------------------------------------------
    # Header: city + date
    # -----------------------------------------------------------------
    now_dt = datetime.now(ZoneInfo("Asia/Kolkata"))
    date_str = now_dt.strftime("%A, %d %B")

    city_w = text_w(draw, CITY_NAME, fonts["city"])
    draw.text((page_right - city_w, 8), CITY_NAME, font=fonts["city"], fill=BLACK)

    date_w = text_w(draw, date_str, fonts["date"])
    draw.text((page_right - date_w, 60), date_str, font=fonts["date"], fill=BLACK)

    # -----------------------------------------------------------------
    # Current conditions: icon + big temp + feels like + today's high/low
    # -----------------------------------------------------------------
    draw_weather_icon(img, now_icon, cx=105, cy=140, size=55)

    temp_val = round(current["temperature_2m"])
    temp_str = f"{temp_val}"
    draw.text((210, 55), temp_str, font=fonts["temp_big"], fill=BLACK)

    tw = text_w(draw, temp_str, fonts["temp_big"])
    draw.text((210 + tw + 5, 60), "\u00b0C", font=fonts["temp_unit"], fill=BLACK)

    feels = round(current["apparent_temperature"])
    draw.text((212, 165), f"Feels like {feels}\u00b0", font=fonts["feels"], fill=BLACK)

    today_hi = round(daily["temperature_2m_max"][0])
    today_lo = round(daily["temperature_2m_min"][0])
    draw.text(
        (212, 198),
        f"H: {today_hi}\u00b0   L: {today_lo}\u00b0",
        font=fonts["day_temp"],
        fill=BLACK,
    )

    # -----------------------------------------------------------------
    # 5-day forecast row
    # -----------------------------------------------------------------
    forecast_x0, forecast_x1 = 485, page_right
    col_w = (forecast_x1 - forecast_x0) / 5
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for i in range(5):
        day_idx = i + 1
        cx = forecast_x0 + col_w * i + col_w / 2

        date_iso = daily["time"][day_idx]
        weekday = datetime.strptime(date_iso, "%Y-%m-%d").weekday()
        draw_centered_text(draw, cx, 100, day_names[weekday], fonts["day_name"])

        code = daily["weather_code"][day_idx]
        draw_weather_icon(img, icon_type_for_code(code, True), cx, 165, size=25)

        hi = round(daily["temperature_2m_max"][day_idx])
        lo = round(daily["temperature_2m_min"][day_idx])
        draw_centered_text(
            draw,
            cx,
            205,
            f"{hi}\u00b0|{lo}\u00b0",
            fonts["day_temp"],
        )

    # -----------------------------------------------------------------
    # Divider
    # -----------------------------------------------------------------
    draw.line([20, 260, page_right, 260], fill=GRAY_MED, width=1)

    # -----------------------------------------------------------------
    # Stat grid
    # -----------------------------------------------------------------
    sunrise_str = datetime.fromisoformat(daily["sunrise"][0]).strftime("%H:%M")
    sunset_str = datetime.fromisoformat(daily["sunset"][0]).strftime("%H:%M")
    wind_kmh = round(current["wind_speed_10m"], 1)
    humidity = round(current["relative_humidity_2m"])
    pressure = round(current["pressure_msl"])

    hour_now_str = now_dt.strftime("%Y-%m-%dT%H:00")
    try:
        cur_idx = hourly["time"].index(hour_now_str)
    except ValueError:
        cur_idx = 0

    uv_val = round(hourly["uv_index"][cur_idx], 1)
    uv_level = (
        "Low" if uv_val < 3
        else "Moderate" if uv_val < 6
        else "High" if uv_val < 8
        else "Very High"
    )

    vis_km = round(hourly["visibility"][cur_idx] / 1000, 1)
    aqi_str = f"{aqi}" if aqi is not None else "N/A"

    stats = [
        ("sunrise", "Sunrise", sunrise_str),
        ("sunset", "Sunset", sunset_str),
        ("wind", "Wind", f"{wind_kmh} km/h"),
        ("humidity", "Humidity", f"{humidity}%"),
        ("uv", "UV Index", f"{uv_val} {uv_level}"),
        ("pressure", "Pressure", f"{pressure} hPa"),
        ("aqi", "Air Quality", aqi_str),
        ("visibility", "Visibility", f"{vis_km} km"),
    ]

    row_h = 58
    col0_x, col1_x = 20, 205
    start_y = 278

    for i, (kind, label, value) in enumerate(stats):
        row = i // 2
        col = i % 2
        x = col0_x if col == 0 else col1_x
        y = start_y + row * row_h

        draw_stat_icon_png(img, kind, x + 18, y + 30, size=32)
        draw.text((x + 45, y + 4), label, font=fonts["stat_label"], fill=GRAY_MED)
        draw.text((x + 45, y + 24), value, font=fonts["stat_value"], fill=BLACK)

    # -----------------------------------------------------------------
    # Hourly temperature + precipitation-probability graph
    # -----------------------------------------------------------------
    gx0, gx1 = 400, 890
    gy0, gy1 = 280, 468
    rain_axis_right = page_right

    hrs = 24
    temps = hourly["temperature_2m"][cur_idx: cur_idx + hrs]
    precs = hourly["precipitation_probability"][cur_idx: cur_idx + hrs]
    times = hourly["time"][cur_idx: cur_idx + hrs]

    point_count = min(len(temps), len(precs), len(times), hrs)
    temps = temps[:point_count]
    precs = precs[:point_count]
    times = times[:point_count]

    if point_count >= 2:
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
            rain_label = 100 - i * 100 / steps

            dashed_hline(draw, gx0, gx1, y)

            left_text = f"{round(temp_label)}\u00b0"
            left_x = gx0 - 8 - text_w(draw, left_text, fonts["axis"])
            draw.text(
                (left_x, y - 8),
                left_text,
                font=fonts["axis"],
                fill=GRAY_MED,
            )

            right_text = f"{round(rain_label)}%"
            right_x = rain_axis_right - text_w(draw, right_text, fonts["axis"])
            draw.text(
                (right_x, y - 8),
                right_text,
                font=fonts["axis"],
                fill=GRAY_MED,
            )

        x_step = (gx1 - gx0) / (point_count - 1)

        for i, p in enumerate(precs):
            if p is None or p <= 0:
                continue

            x = gx0 + i * x_step
            bar_h = (p / 100) * (gy1 - gy0)
            bar_half_w = x_step * 0.48
            left = max(gx0, x - bar_half_w)
            right = min(gx1, x + bar_half_w)

            draw.rectangle(
                [left, gy1 - bar_h, right, gy1],
                fill=GRAY_LIGHT,
            )

        smooth_temps = smooth_series(temps, passes=0)
        raw_points = [
            (gx0 + i * x_step, temp_to_y(t))
            for i, t in enumerate(smooth_temps)
        ]
        smooth_points = catmull_rom_spline(
            raw_points,
            samples_per_segment=8,
        )

        draw.line(
            smooth_points,
            fill=BLACK,
            width=4,
            joint="curve",
        )

        for i in range(0, point_count, 3):
            hour_label = datetime.fromisoformat(times[i]).strftime("%H")
            x = gx0 + i * x_step
            draw_centered_text(
                draw,
                x,
                gy1 + 8,
                hour_label,
                fonts["axis"],
                fill=GRAY_MED,
            )

    # -----------------------------------------------------------------
    # Footer
    # -----------------------------------------------------------------
    footer = "Updated " + now_dt.strftime("%d %b %Y, %H:%M")
    fw = text_w(draw, footer, fonts["footer"])
    draw.text(
        (page_right - fw, HEIGHT - 26),
        footer,
        font=fonts["footer"],
        fill=GRAY_MED,
    )

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