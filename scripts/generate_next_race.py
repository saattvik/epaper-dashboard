"""
Next F1 race dashboard generator.

Race data is fetched live from Jolpica/OpenF1, circuit layouts are selected
automatically from julesr0y/f1-circuits-svg, and circuit/race facts are read
from the latest F1DB GitHub release.

No hardcoded circuit length/corner table, local flag images, track PNGs, or
manually re-entered race calendar are required. Race time and the footer
timestamp are shown in IST (Asia/Kolkata) rather than circuit-local time.
"""

import os
import math
import re
import json
import zipfile
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import pycountry
    print("pycountry loaded OK")
except ImportError as e:
    pycountry = None
    print(f"pycountry NOT available: {e}")


# ============================================================
# Config
# ============================================================
WIDTH, HEIGHT = 960, 540

IST = ZoneInfo("Asia/Kolkata")

JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"
OPENF1_BASE = "https://api.openf1.org/v1"
FLAG_CDN = "https://flagcdn.com/w160"

F1DB_LATEST_RELEASE_API = (
    "https://api.github.com/repos/f1db/f1db/releases/latest"
)
F1DB_JSON_ASSET_NAME = "f1db-json-single.zip"

CIRCUITS_JSON_URL = (
    "https://raw.githubusercontent.com/"
    "julesr0y/f1-circuits-svg/main/circuits.json"
)

CIRCUIT_SVG_BASE = (
    "https://raw.githubusercontent.com/"
    "julesr0y/f1-circuits-svg/main/"
    "circuits/detailed/black-outline"
)

_CIRCUITS_CACHE = None
_F1DB_CACHE = None

OUTPUT_PATH = "docs/current.bmp"

ASSET_DIR = Path("assets/f1")
TEAM_DIR = ASSET_DIR / "teams"
ICON_DIR = ASSET_DIR / "icons"
F1_LOGO_PATH = ASSET_DIR / "f1_logo.png"

FONT_REGULAR = "fonts/OpenSans-Regular.ttf"
FONT_BOLD = "fonts/BebasNeue-Regular.ttf"
FONT_BEBAS = "fonts/BebasNeue-Regular.ttf"

WHITE, BLACK = 255, 0
GRAY_LIGHT, GRAY_MED, GRAY_DARK = 225, 145, 70

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


COUNTRY_NAME_OVERRIDES = {
    "USA": "US",
    "UK": "GB",
    "UAE": "AE",
    "Korea": "KR",
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
        return COUNTRY_NAME_OVERRIDES[country_name].lower()

    if pycountry is None:
        print(f"pycountry unavailable, can't resolve '{country_name}'")
        return None

    try:
        match = pycountry.countries.lookup(country_name)
        return match.alpha_2.lower()
    except LookupError:
        try:
            results = pycountry.countries.search_fuzzy(country_name)
            return results[0].alpha_2.lower()
        except LookupError:
            print(f"Could not resolve country '{country_name}'")
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


# ============================================================
# F1DB helpers
# ============================================================
def get_f1db_data():
    """
    Download the latest F1DB single-JSON release once per script run.

    The GitHub release metadata is queried first, then the
    f1db-json-single.zip asset is downloaded and read entirely in memory.
    """
    global _F1DB_CACHE

    if _F1DB_CACHE is not None:
        print("Using cached F1DB data")
        return _F1DB_CACHE

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "epaper-dashboard/1.0",
    }

    print("Fetching latest F1DB release metadata...")
    release = fetch_json(
        F1DB_LATEST_RELEASE_API,
        headers=headers,
        timeout=30,
    )

    print(f"Latest F1DB release: {release.get('tag_name', 'unknown')}")

    asset = None
    for candidate in release.get("assets", []):
        if candidate.get("name") == F1DB_JSON_ASSET_NAME:
            asset = candidate
            break

    if asset is None:
        available = [
            item.get("name")
            for item in release.get("assets", [])
            if item.get("name")
        ]
        raise RuntimeError(
            f"F1DB release does not contain {F1DB_JSON_ASSET_NAME}. "
            f"Available assets: {available}"
        )

    download_url = asset.get("browser_download_url")
    if not download_url:
        raise RuntimeError("F1DB JSON release asset has no download URL")

    print(f"Downloading {F1DB_JSON_ASSET_NAME}...")
    r = requests.get(
        download_url,
        headers={"User-Agent": "epaper-dashboard/1.0"},
        timeout=90,
    )
    r.raise_for_status()

    print(
        f"Downloaded F1DB archive: "
        f"{len(r.content) / (1024 * 1024):.1f} MiB"
    )

    with zipfile.ZipFile(BytesIO(r.content)) as zf:
        json_names = [
            name
            for name in zf.namelist()
            if name.lower().endswith(".json")
            and not name.lower().endswith(".schema.json")
        ]

        if not json_names:
            raise RuntimeError("No JSON data file found inside F1DB archive")

        preferred = sorted(
            json_names,
            key=lambda name: (
                0 if Path(name).name.lower() == "f1db.json" else 1,
                -zf.getinfo(name).file_size,
            ),
        )

        data = None
        chosen_name = None

        for name in preferred:
            try:
                candidate_data = json.loads(
                    zf.read(name).decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            if (
                isinstance(candidate_data, dict)
                and isinstance(candidate_data.get("races"), list)
            ):
                data = candidate_data
                chosen_name = name
                break

        if data is None:
            raise RuntimeError(
                "Could not find the F1DB single JSON dataset "
                "inside the downloaded archive"
            )

    print(f"Loaded F1DB dataset from: {chosen_name}")
    print(f"F1DB races available: {len(data.get('races', []))}")

    _F1DB_CACHE = data
    return _F1DB_CACHE


def get_f1db_race(year, round_num):
    """Return the F1DB Race object matching the season year and round."""
    year = int(year)
    round_num = int(round_num)

    data = get_f1db_data()

    for f1db_race in data.get("races", []):
        if (
            int(f1db_race.get("year", -1)) == year
            and int(f1db_race.get("round", -1)) == round_num
        ):
            print(
                f"F1DB race matched: {year} round {round_num} "
                f"({f1db_race.get('officialName', 'unknown race')})"
            )

            print(
                "F1DB race facts: "
                f"courseLength={f1db_race.get('courseLength')}, "
                f"turns={f1db_race.get('turns')}, "
                f"laps={f1db_race.get('laps')}, "
                f"distance={f1db_race.get('distance')}, "
                f"scheduledLaps={f1db_race.get('scheduledLaps')}, "
                f"scheduledDistance={f1db_race.get('scheduledDistance')}"
            )

            return f1db_race

    raise RuntimeError(
        f"No F1DB race found for {year} round {round_num}"
    )


def format_number(value, decimals=3):
    """Format numbers without unnecessary trailing zeroes."""
    if value is None:
        return "N/A"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)

    if number.is_integer():
        return str(int(number))

    return f"{number:.{decimals}f}".rstrip("0").rstrip(".")


# ============================================================
# Circuit layout helpers
# ============================================================
def get_circuits_data():
    global _CIRCUITS_CACHE

    if _CIRCUITS_CACHE is None:
        print("Fetching circuits.json...")
        _CIRCUITS_CACHE = fetch_json(
            CIRCUITS_JSON_URL,
            headers={"User-Agent": "epaper-dashboard/1.0"},
            timeout=20,
        )
    else:
        print("Using cached circuits.json")

    return _CIRCUITS_CACHE


def season_in_range(season, season_string):
    season = int(season)

    if not season_string:
        return False

    for part in season_string.split(","):
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            start, end = part.split("-", 1)
            if int(start) <= season <= int(end):
                return True
        elif season == int(part):
            return True

    return False


def get_circuit_layout_id(circuit_id, season):
    circuits = get_circuits_data()

    for circuit in circuits:
        if circuit.get("id") == circuit_id:
            for layout in circuit.get("layouts", []):
                if season_in_range(season, layout.get("seasons", "")):
                    layout_id = layout.get("layoutId")
                    print(f"Circuit layout found: {circuit_id} -> {layout_id} for {season}")
                    return layout_id

            print(f"No layout found for {circuit_id} in {season}")
            return None

        for layout in circuit.get("layouts", []):
            if (
                layout.get("otherId") == circuit_id
                and season_in_range(season, layout.get("seasons", ""))
            ):
                layout_id = layout.get("layoutId")
                print(f"Circuit layout found through otherId: {circuit_id} -> {layout_id} for {season}")
                return layout_id

    print(f"Circuit '{circuit_id}' not found in circuits.json")
    return None


def get_circuit_svg_url(circuit_id, season):
    layout_id = get_circuit_layout_id(circuit_id, season)
    if not layout_id:
        return None
    return f"{CIRCUIT_SVG_BASE}/{layout_id}.svg"


# ============================================================
# Lightweight SVG renderer using Pillow only
# ============================================================
_NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")
_CMD_OR_NUM_RE = re.compile(r"[AaCcHhLlMmQqSsTtVvZz]|[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _svg_local_name(tag):
    return tag.split("}")[-1]


def _parse_length(val, default=0.0):
    if val is None:
        return float(default)
    m = _NUM_RE.search(str(val))
    return float(m.group(0)) if m else float(default)


def _parse_style(style_str):
    out = {}
    if not style_str:
        return out

    for part in style_str.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()

    return out


def _get_svg_attr(el, name, default=None):
    if name in el.attrib:
        return el.attrib[name]
    style = _parse_style(el.attrib.get("style"))
    return style.get(name, default)


def _is_none_color(value):
    if value is None:
        return False
    return str(value).strip().lower() == "none"


def _affine_identity():
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _affine_multiply(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _affine_apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _affine_scale(sx, sy=None):
    if sy is None:
        sy = sx
    return (sx, 0.0, 0.0, sy, 0.0, 0.0)


def _affine_translate(tx, ty):
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def _affine_rotate(deg, cx=0.0, cy=0.0):
    rad = math.radians(deg)
    cos_t = math.cos(rad)
    sin_t = math.sin(rad)
    rot = (cos_t, sin_t, -sin_t, cos_t, 0.0, 0.0)
    if cx == 0 and cy == 0:
        return rot
    return _affine_multiply(
        _affine_translate(cx, cy),
        _affine_multiply(rot, _affine_translate(-cx, -cy)),
    )


def _parse_transform(transform_str):
    if not transform_str:
        return _affine_identity()

    transform_str = transform_str.strip()
    pattern = re.compile(r"([a-zA-Z]+)\(([^)]*)\)")
    result = _affine_identity()

    for name, args_str in pattern.findall(transform_str):
        nums = [float(x) for x in _NUM_RE.findall(args_str)]
        name = name.strip()

        if name == "translate":
            tx = nums[0] if len(nums) >= 1 else 0.0
            ty = nums[1] if len(nums) >= 2 else 0.0
            local = _affine_translate(tx, ty)

        elif name == "scale":
            sx = nums[0] if len(nums) >= 1 else 1.0
            sy = nums[1] if len(nums) >= 2 else sx
            local = _affine_scale(sx, sy)

        elif name == "rotate":
            ang = nums[0] if len(nums) >= 1 else 0.0
            if len(nums) >= 3:
                local = _affine_rotate(ang, nums[1], nums[2])
            else:
                local = _affine_rotate(ang)

        elif name == "matrix" and len(nums) >= 6:
            local = tuple(nums[:6])

        else:
            local = _affine_identity()

        result = _affine_multiply(result, local)

    return result


def _matrix_scale_factor(m):
    a, b, c, d, _, _ = m
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    if sx == 0 and sy == 0:
        return 1.0
    if sx == 0:
        return sy
    if sy == 0:
        return sx
    return (sx + sy) / 2.0


def _sample_cubic(p0, p1, p2, p3, n=24):
    pts = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        x = (
            mt ** 3 * p0[0]
            + 3 * mt ** 2 * t * p1[0]
            + 3 * mt * t ** 2 * p2[0]
            + t ** 3 * p3[0]
        )
        y = (
            mt ** 3 * p0[1]
            + 3 * mt ** 2 * t * p1[1]
            + 3 * mt * t ** 2 * p2[1]
            + t ** 3 * p3[1]
        )
        pts.append((x, y))
    return pts


def _sample_quadratic(p0, p1, p2, n=20):
    pts = []
    for i in range(1, n + 1):
        t = i / n
        mt = 1 - t
        x = mt ** 2 * p0[0] + 2 * mt * t * p1[0] + t ** 2 * p2[0]
        y = mt ** 2 * p0[1] + 2 * mt * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def _vector_angle(u, v):
    ux, uy = u
    vx, vy = v
    dot = ux * vx + uy * vy
    det = ux * vy - uy * vx
    return math.atan2(det, dot)


def _sample_arc(p0, rx, ry, phi_deg, large_arc, sweep, p1, n=28):
    x1, y1 = p0
    x2, y2 = p1

    if rx == 0 or ry == 0:
        return [p1]

    phi = math.radians(phi_deg % 360.0)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0

    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    rx = abs(rx)
    ry = abs(ry)

    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    num = (rx ** 2) * (ry ** 2) - (rx ** 2) * (y1p ** 2) - (ry ** 2) * (x1p ** 2)
    den = (rx ** 2) * (y1p ** 2) + (ry ** 2) * (x1p ** 2)
    if den == 0:
        return [p1]

    coef = math.sqrt(max(0.0, num / den))
    if large_arc == sweep:
        coef = -coef

    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    u = ((x1p - cxp) / rx, (y1p - cyp) / ry)
    v = ((-x1p - cxp) / rx, (-y1p - cyp) / ry)

    theta1 = _vector_angle((1, 0), u)
    delta_theta = _vector_angle(u, v)

    if not sweep and delta_theta > 0:
        delta_theta -= 2 * math.pi
    elif sweep and delta_theta < 0:
        delta_theta += 2 * math.pi

    pts = []
    for i in range(1, n + 1):
        t = theta1 + delta_theta * (i / n)
        ct = math.cos(t)
        st = math.sin(t)
        x = cx + rx * ct * cos_phi - ry * st * sin_phi
        y = cy + rx * ct * sin_phi + ry * st * cos_phi
        pts.append((x, y))

    return pts


def _parse_points_attr(points_str):
    nums = [float(x) for x in _NUM_RE.findall(points_str or "")]
    pts = []
    for i in range(0, len(nums) - 1, 2):
        pts.append((nums[i], nums[i + 1]))
    return pts


def _parse_path_subpaths(d):
    tokens = _CMD_OR_NUM_RE.findall((d or "").replace(",", " "))
    if not tokens:
        return []

    def is_cmd(tok):
        return len(tok) == 1 and tok.isalpha()

    i = 0
    cmd = None
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    prev_ctrl_cubic = None
    prev_ctrl_quad = None
    prev_cmd_upper = None

    subpaths = []
    current = []

    def next_num():
        nonlocal i
        val = float(tokens[i])
        i += 1
        return val

    def flush_current():
        nonlocal current
        if len(current) >= 2:
            subpaths.append(current)
        current = []

    while i < len(tokens):
        if is_cmd(tokens[i]):
            cmd = tokens[i]
            i += 1
        elif cmd is None:
            raise ValueError("Path data started without a command")

        absolute = cmd.isupper()
        cu = cmd.upper()

        if cu == "M":
            x = next_num()
            y = next_num()
            if not absolute:
                x += cur[0]
                y += cur[1]

            flush_current()
            cur = (x, y)
            start = cur
            current = [cur]

            while i < len(tokens) and not is_cmd(tokens[i]):
                x = next_num()
                y = next_num()
                if not absolute:
                    x += cur[0]
                    y += cur[1]
                cur = (x, y)
                current.append(cur)

            prev_ctrl_cubic = None
            prev_ctrl_quad = None
            prev_cmd_upper = "M"

        elif cu == "L":
            while i < len(tokens) and not is_cmd(tokens[i]):
                x = next_num()
                y = next_num()
                if not absolute:
                    x += cur[0]
                    y += cur[1]
                cur = (x, y)
                current.append(cur)
            prev_ctrl_cubic = None
            prev_ctrl_quad = None
            prev_cmd_upper = "L"

        elif cu == "H":
            while i < len(tokens) and not is_cmd(tokens[i]):
                x = next_num()
                if not absolute:
                    x += cur[0]
                cur = (x, cur[1])
                current.append(cur)
            prev_ctrl_cubic = None
            prev_ctrl_quad = None
            prev_cmd_upper = "H"

        elif cu == "V":
            while i < len(tokens) and not is_cmd(tokens[i]):
                y = next_num()
                if not absolute:
                    y += cur[1]
                cur = (cur[0], y)
                current.append(cur)
            prev_ctrl_cubic = None
            prev_ctrl_quad = None
            prev_cmd_upper = "V"

        elif cu == "C":
            while i < len(tokens) and not is_cmd(tokens[i]):
                x1, y1 = next_num(), next_num()
                x2, y2 = next_num(), next_num()
                x, y = next_num(), next_num()

                if not absolute:
                    x1 += cur[0]
                    y1 += cur[1]
                    x2 += cur[0]
                    y2 += cur[1]
                    x += cur[0]
                    y += cur[1]

                pts = _sample_cubic(cur, (x1, y1), (x2, y2), (x, y))
                current.extend(pts)
                cur = (x, y)
                prev_ctrl_cubic = (x2, y2)
                prev_ctrl_quad = None
            prev_cmd_upper = "C"

        elif cu == "S":
            while i < len(tokens) and not is_cmd(tokens[i]):
                x2, y2 = next_num(), next_num()
                x, y = next_num(), next_num()

                if prev_cmd_upper in ("C", "S") and prev_ctrl_cubic is not None:
                    x1 = 2 * cur[0] - prev_ctrl_cubic[0]
                    y1 = 2 * cur[1] - prev_ctrl_cubic[1]
                else:
                    x1, y1 = cur

                if not absolute:
                    x2 += cur[0]
                    y2 += cur[1]
                    x += cur[0]
                    y += cur[1]

                pts = _sample_cubic(cur, (x1, y1), (x2, y2), (x, y))
                current.extend(pts)
                cur = (x, y)
                prev_ctrl_cubic = (x2, y2)
                prev_ctrl_quad = None
            prev_cmd_upper = "S"

        elif cu == "Q":
            while i < len(tokens) and not is_cmd(tokens[i]):
                x1, y1 = next_num(), next_num()
                x, y = next_num(), next_num()

                if not absolute:
                    x1 += cur[0]
                    y1 += cur[1]
                    x += cur[0]
                    y += cur[1]

                pts = _sample_quadratic(cur, (x1, y1), (x, y))
                current.extend(pts)
                cur = (x, y)
                prev_ctrl_quad = (x1, y1)
                prev_ctrl_cubic = None
            prev_cmd_upper = "Q"

        elif cu == "T":
            while i < len(tokens) and not is_cmd(tokens[i]):
                x, y = next_num(), next_num()

                if prev_cmd_upper in ("Q", "T") and prev_ctrl_quad is not None:
                    x1 = 2 * cur[0] - prev_ctrl_quad[0]
                    y1 = 2 * cur[1] - prev_ctrl_quad[1]
                else:
                    x1, y1 = cur

                if not absolute:
                    x += cur[0]
                    y += cur[1]

                pts = _sample_quadratic(cur, (x1, y1), (x, y))
                current.extend(pts)
                cur = (x, y)
                prev_ctrl_quad = (x1, y1)
                prev_ctrl_cubic = None
            prev_cmd_upper = "T"

        elif cu == "A":
            while i < len(tokens) and not is_cmd(tokens[i]):
                rx = next_num()
                ry = next_num()
                rot = next_num()
                large_arc = int(next_num())
                sweep = int(next_num())
                x = next_num()
                y = next_num()

                if not absolute:
                    x += cur[0]
                    y += cur[1]

                pts = _sample_arc(cur, rx, ry, rot, large_arc, sweep, (x, y))
                current.extend(pts)
                cur = (x, y)
                prev_ctrl_cubic = None
                prev_ctrl_quad = None
            prev_cmd_upper = "A"

        elif cu == "Z":
            if current and current[-1] != start:
                current.append(start)
            flush_current()
            cur = start
            prev_ctrl_cubic = None
            prev_ctrl_quad = None
            prev_cmd_upper = "Z"

        else:
            print(f"Unsupported SVG path command: {cmd}")
            break

    flush_current()
    return subpaths


def _svg_global_matrix(root, out_w, out_h, margin_frac=0.06):
    view_box = root.attrib.get("viewBox")

    if view_box:
        vals = [float(x) for x in _NUM_RE.findall(view_box)]
        if len(vals) == 4:
            vb_x, vb_y, vb_w, vb_h = vals
        else:
            vb_x, vb_y, vb_w, vb_h = 0.0, 0.0, float(out_w), float(out_h)
    else:
        vb_x = 0.0
        vb_y = 0.0
        vb_w = _parse_length(root.attrib.get("width"), out_w)
        vb_h = _parse_length(root.attrib.get("height"), out_h)

    if vb_w == 0 or vb_h == 0:
        vb_w, vb_h = float(out_w), float(out_h)

    inner_w = out_w * (1.0 - 2 * margin_frac)
    inner_h = out_h * (1.0 - 2 * margin_frac)
    scale = min(inner_w / vb_w, inner_h / vb_h)
    dx = (out_w - vb_w * scale) / 2.0 - vb_x * scale
    dy = (out_h - vb_h * scale) / 2.0 - vb_y * scale

    return (scale, 0.0, 0.0, scale, dx, dy)


def _svg_color_to_rgba(value, default=None):
    if value is None:
        return default

    value = str(value).strip().lower()

    if value == "none":
        return None

    if value in ("black", "#000", "#000000"):
        return (0, 0, 0, 255)

    if value in ("white", "#fff", "#ffffff"):
        return (255, 255, 255, 255)

    # #RRGGBB
    if value.startswith("#") and len(value) == 7:
        try:
            return (
                int(value[1:3], 16),
                int(value[3:5], 16),
                int(value[5:7], 16),
                255,
            )
        except ValueError:
            return default

    # #RGB
    if value.startswith("#") and len(value) == 4:
        try:
            return (
                int(value[1] * 2, 16),
                int(value[2] * 2, 16),
                int(value[3] * 2, 16),
                255,
            )
        except ValueError:
            return default

    # rgb(0, 0, 0)
    if value.startswith("rgb(") and value.endswith(")"):
        try:
            parts = value[4:-1].split(",")

            return (
                int(parts[0].strip()),
                int(parts[1].strip()),
                int(parts[2].strip()),
                255,
            )
        except (ValueError, IndexError):
            return default

    return default


def _render_svg_element(draw, el, matrix):
    tag = _svg_local_name(el.tag)

    style = _parse_style(el.attrib.get("style", ""))
    if style.get("display") == "none" or el.attrib.get("display") == "none":
        return

    local_transform = _parse_transform(el.attrib.get("transform"))
    m = _affine_multiply(matrix, local_transform)

    if tag in ("svg", "g"):
        for child in el:
            _render_svg_element(draw, child, m)
        return

    stroke_value = _get_svg_attr(
        el,
        "stroke",
        None,
    )

    fill_value = _get_svg_attr(
        el,
        "fill",
        None,
    )

    stroke_color = _svg_color_to_rgba(
        stroke_value,
        default=None,
    )

    fill_color = _svg_color_to_rgba(
        fill_value,
        default=None,
    )

    stroke_width = _parse_length(
        _get_svg_attr(
            el,
            "stroke-width",
            "1",
        ),
        1.0,
    )

    width_px = max(
        1,
        int(round(
            stroke_width *
            _matrix_scale_factor(m)
        )),
    )

    def draw_polyline(points, closed=False):
        if len(points) < 2:
            return

        tpts = [
            _affine_apply(m, x, y)
            for x, y in points
        ]

        # Respect SVG fill colour
        if (
            fill_color is not None
            and closed
            and len(tpts) >= 3
        ):
            draw.polygon(
                tpts,
                fill=fill_color,
            )

        # Respect SVG stroke colour
        if stroke_color is not None:
            draw.line(
                tpts,
                fill=stroke_color,
                width=width_px,
                joint="curve",
            )

    if tag == "path":
        d = el.attrib.get("d", "")
        for subpath in _parse_path_subpaths(d):
            closed = len(subpath) >= 2 and subpath[0] == subpath[-1]
            draw_polyline(subpath, closed=closed)

    elif tag == "polyline":
        draw_polyline(_parse_points_attr(el.attrib.get("points", "")), closed=False)

    elif tag == "polygon":
        pts = _parse_points_attr(el.attrib.get("points", ""))
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        draw_polyline(pts, closed=True)

    elif tag == "line":
        x1 = _parse_length(el.attrib.get("x1"))
        y1 = _parse_length(el.attrib.get("y1"))
        x2 = _parse_length(el.attrib.get("x2"))
        y2 = _parse_length(el.attrib.get("y2"))
        draw_polyline([(x1, y1), (x2, y2)], closed=False)

    elif tag == "rect":
        x = _parse_length(el.attrib.get("x"))
        y = _parse_length(el.attrib.get("y"))
        w = _parse_length(el.attrib.get("width"))
        h = _parse_length(el.attrib.get("height"))
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
        draw_polyline(pts, closed=True)

    elif tag == "circle":
        cx = _parse_length(el.attrib.get("cx"))
        cy = _parse_length(el.attrib.get("cy"))
        r = _parse_length(el.attrib.get("r"))
        pts = []
        for i in range(65):
            t = 2 * math.pi * i / 64
            pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
        draw_polyline(pts, closed=True)

    elif tag == "ellipse":
        cx = _parse_length(el.attrib.get("cx"))
        cy = _parse_length(el.attrib.get("cy"))
        rx = _parse_length(el.attrib.get("rx"))
        ry = _parse_length(el.attrib.get("ry"))
        pts = []
        for i in range(65):
            t = 2 * math.pi * i / 64
            pts.append((cx + rx * math.cos(t), cy + ry * math.sin(t)))
        draw_polyline(pts, closed=True)

    # silently ignore unsupported element types


def render_svg_to_rgba(svg_bytes, out_size=(700, 700)):
    root = ET.fromstring(svg_bytes)
    out_w, out_h = out_size

    img = Image.new("RGBA", (out_w, out_h), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)

    global_matrix = _svg_global_matrix(root, out_w, out_h, margin_frac=0.04)
    _render_svg_element(draw, root, global_matrix)

    return img


def fetch_circuit_svg(circuit_id, season):
    svg_url = get_circuit_svg_url(circuit_id, season)
    if not svg_url:
        return None

    print(f"Fetching track SVG: {svg_url}")
    r = requests.get(
        svg_url,
        timeout=20,
        headers={"User-Agent": "epaper-dashboard/1.0"},
    )
    r.raise_for_status()

    return render_svg_to_rgba(r.content, out_size=(700, 700))


# ============================================================
# Data fetching
# ============================================================
def get_next_race():
    season = datetime.now(ZoneInfo("UTC")).year
    url = f"{JOLPICA_BASE}/{season}.json"

    data = fetch_json(
        url,
        headers={"User-Agent": "epaper-dashboard/1.0"},
        params={"limit": 40},
    )

    races = data["MRData"]["RaceTable"]["Races"]
    total_rounds = len(races)

    now_utc = datetime.now(ZoneInfo("UTC"))

    print(f"Current UTC time: {now_utc}")

    for race in races:
        race_date = race["date"]
        race_time = race.get("time")

        if race_time:
            race_dt = datetime.strptime(
                f"{race_date} {race_time.replace('Z', '')}",
                "%Y-%m-%d %H:%M:%S",
            ).replace(tzinfo=ZoneInfo("UTC"))

            # Keep the race as current for roughly 3 hours after lights out.
            race_end_estimate = race_dt + timedelta(hours=3)

            print(
                f"Checking {race['raceName']}: "
                f"start={race_dt}, estimated_end={race_end_estimate}"
            )

            if race_end_estimate > now_utc:
                race["_total_rounds"] = total_rounds
                return race

        else:
            # Fallback if Jolpica does not provide a race start time.
            race_dt = datetime.strptime(
                race_date,
                "%Y-%m-%d",
            ).replace(tzinfo=ZoneInfo("UTC"))

            if race_dt.date() >= now_utc.date():
                race["_total_rounds"] = total_rounds
                return race

    # Season over: show final race instead of producing an empty page.
    if races:
        races[-1]["_total_rounds"] = total_rounds
        return races[-1]

    raise RuntimeError("No races returned by Jolpica")


def get_last_year_result(circuit_id, year):
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
# Small icons
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
        (cx - size, cy + size * 0.6),
        (cx - size * 0.3, cy + size * 0.6),
        (cx - size * 0.1, cy - size * 0.2),
        (cx + size * 0.4, cy - size * 0.2),
        (cx + size * 0.6, cy + size * 0.5),
        (cx + size, cy + size * 0.5),
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
def build_image(race, winner, f1db_race):
    img = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    f_tag = load_font(FONT_BOLD, 33)
    f_title = load_font(FONT_BEBAS, 45)
    f_section = load_font(FONT_BOLD, 20)
    f_row_label = load_font(FONT_REGULAR, 15)
    f_row_value = load_font(FONT_BOLD, 24)
    f_circuit = load_font(FONT_BOLD, 18)
    f_city = load_font(FONT_REGULAR, 15)
    f_round = load_font(FONT_BOLD, 33)
    f_winner_name = load_font(FONT_BOLD, 25)
    f_winner_team = load_font(FONT_REGULAR, 15)
    f_stat_label = load_font(FONT_REGULAR, 15)
    f_stat_value = load_font(FONT_BOLD, 26)
    f_footer = load_font(FONT_REGULAR, 14)

    circuit = race["Circuit"]
    circuit_id = circuit["circuitId"]
    circuit_name = circuit["circuitName"]
    locality = circuit["Location"]["locality"]
    country = circuit["Location"]["country"]

    race_name = race["raceName"].upper()
    race_dt_utc = datetime.strptime(
        f"{race['date']} {race.get('time', '12:00:00Z').replace('Z', '')}",
        "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=ZoneInfo("UTC"))

    round_num = int(race["round"])
    total_rounds = race["_total_rounds"]

    # ------------------------------------------------------------
    # Header - (4) bigger F1 logo, aligned with "NEXT RACE"
    # ------------------------------------------------------------
    logo_box = (20, 8, 130, 58)
    if F1_LOGO_PATH.exists():
        logo = Image.open(F1_LOGO_PATH).convert("RGBA")
        alpha = logo.getchannel("A")
        bbox = alpha.getbbox()
        if bbox:
            logo = logo.crop(bbox)   # trim any transparent padding baked into the source file, so the box isn't mostly empty space
        paste_contain(img, logo, logo_box)
    else:
        draw.text((20, 15), "F1", font=f_title, fill=BLACK)

    next_race_y = centered_text_y(draw, "NEXT RACE", f_tag, (logo_box[1] + logo_box[3]) / 2)
    draw.text((165, next_race_y), "NEXT RACE", font=f_tag, fill=BLACK)

    round_str = f"ROUND {round_num} OF {total_rounds}"
    rs_w = text_w(draw, round_str, f_round)
    draw.text((940 - rs_w, next_race_y), round_str, font=f_round, fill=BLACK)

    draw.line((20, 65, 940, 65), fill=BLACK, width=2)   # reverted to original position

    # ------------------------------------------------------------
    # Left column: race name
    # ------------------------------------------------------------
    left_x = 20
    name_lines = wrap_text(draw, race_name, f_title, 300)
    ny = 78
    for line in name_lines:
        draw.text((left_x, ny), line, font=f_title, fill=BLACK)
        ny += 44

    ny += 18

    # ------------------------------------------------------------
    # (1) Flag centered + same width as the row icon badges
    # (2) Only the location (not circuit name) shown beside the flag, in black
    # ------------------------------------------------------------
    badge_size = 44   # icon badge size (date/time/circuit rows below)
    flag_row_top = ny
    flag_w = 56       # no longer tied to badge_size - bigger, standalone
    flag_h = 35       # fallback if the flag fetch fails, keeps spacing consistent either way

    flag_img = fetch_flag(country)
    if flag_img:
        aspect = flag_img.width / max(1, flag_img.height)
        flag_h = max(1, int(round(flag_w / aspect)))
        flag_gray = flag_img.convert("L").resize((flag_w, flag_h), Image.LANCZOS)
        img.paste(flag_gray, (left_x, flag_row_top))

    flag_row_center_y = flag_row_top + flag_h / 2
    location_text = f"{locality}, {country}"
    loc_text_x = left_x + flag_w + 14
    loc_text_y = centered_text_y(draw, location_text, f_row_value, flag_row_center_y)
    draw.text((loc_text_x, loc_text_y), location_text, font=f_row_value, fill=BLACK)

    row_y = flag_row_top + flag_h + 14   # was flag_row_top + badge_size(44) + 24 = a much bigger gap
    row_h = 58   # was 54 - a bit taller to give the larger value text room

    # ------------------------------------------------------------
    # (5) Race time in IST instead of circuit-local time
    # ------------------------------------------------------------
    if race.get("time"):
        ist_dt = race_dt_utc.astimezone(IST)
        time_str = ist_dt.strftime("%H:%M")
    else:
        time_str = "TBD"

    date_display = race_dt_utc.astimezone(IST).strftime("%A, %d %B %Y")

    # ------------------------------------------------------------
    # Date + time rows (fixed height)
    # ------------------------------------------------------------
    simple_rows = [
        ("calendar.png", draw_calendar_icon, "RACE DATE", date_display),
        ("clock.png", draw_clock_icon, "RACE TIME (IST)", time_str),
    ]

    for icon_file, fallback_fn, label, value in simple_rows:
        by0 = row_y + row_h / 2 - badge_size / 2
        draw_badge_icon(img, draw, (left_x, by0, left_x + badge_size, by0 + badge_size), icon_file, fallback_fn)
        draw.text((left_x + badge_size + 14, row_y + 6), label, font=f_row_label, fill=GRAY_MED)
        draw.text((left_x + badge_size + 14, row_y + 24), value, font=f_row_value, fill=BLACK)
        draw.line((left_x + badge_size + 14, row_y + row_h - 4, left_x + 305, row_y + row_h - 4), fill=GRAY_LIGHT, width=1)
        row_y += row_h

    # ------------------------------------------------------------
    # (3) Circuit row - label is now "CIRCUIT", value is the circuit name
    # (wraps to a second line automatically if the name is long)
    # ------------------------------------------------------------
    value_max_w = 305 - (badge_size + 14)
    circuit_lines = wrap_text(draw, circuit_name, f_row_value, value_max_w)
    circuit_row_h = row_h if len(circuit_lines) <= 1 else row_h + 20

    by0 = row_y + circuit_row_h / 2 - badge_size / 2
    draw_badge_icon(img, draw, (left_x, by0, left_x + badge_size, by0 + badge_size), "pin.png", draw_pin_icon)
    draw.text((left_x + badge_size + 14, row_y + 6), "CIRCUIT", font=f_row_label, fill=GRAY_MED)

    cy = row_y + 24
    for line in circuit_lines:
        draw.text((left_x + badge_size + 14, cy), line, font=f_row_value, fill=BLACK)
        cy += 22

    draw.line(
        (left_x + badge_size + 14, row_y + circuit_row_h - 4, left_x + 305, row_y + circuit_row_h - 4),
        fill=GRAY_LIGHT, width=1,
    )
    row_y += circuit_row_h

    # ------------------------------------------------------------
    # Middle column: track layout
    # ------------------------------------------------------------
    mid_x0, mid_x1 = 350, 655
    draw.text((mid_x0, 83), "TRACK LAYOUT", font=f_section, fill=BLACK)
    draw.line((mid_x0, 105, mid_x1, 105), fill=GRAY_DARK, width=1)

    season = int(race["season"])
    try:
        track_img = fetch_circuit_svg(circuit_id, season)
        if track_img is None:
            raise RuntimeError(f"No matching circuit layout for {circuit_id} in {season}")

        paste_contain(img, track_img, (mid_x0, 110, mid_x1, 400))
    except Exception as e:
        print(f"Track layout fetch failed: {e}")
        draw_centered_text(draw, (mid_x0 + mid_x1) / 2, 240, "Track layout", f_row_value, fill=GRAY_MED)
        draw_centered_text(draw, (mid_x0 + mid_x1) / 2, 265, "not available", f_row_value, fill=GRAY_MED)

    # ------------------------------------------------------------
    # Right column: last year's winner
    # (6) Photo/name/team/logo all shifted down by WINNER_SHIFT, spacing unchanged
    # ------------------------------------------------------------
    right_x0, right_x1 = 695, 940
    winner_cx = (right_x0 + right_x1) / 2
    WINNER_SHIFT = 18

    draw.text(
        (right_x0, 83),
        "LAST YEAR'S WINNER",
        font=f_section,
        fill=BLACK,
    )
    draw.line((right_x0, 105, right_x1, 105), fill=GRAY_DARK, width=1)

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
                paste_x = int(right_x0 + (box_w - new_size[0]) // 2)
                img.paste(dithered, (paste_x, 110 + WINNER_SHIFT))

            except Exception as e:
                print(f"Winner headshot render failed: {e}")
                draw_centered_text(draw, winner_cx, 190 + WINNER_SHIFT, "No image", f_row_value, fill=GRAY_MED)
        else:
            draw_centered_text(draw, winner_cx, 190 + WINNER_SHIFT, "No image", f_row_value, fill=GRAY_MED)

        wy = 295 + WINNER_SHIFT
        draw_centered_text(draw, winner_cx, wy, winner["name"].upper(), f_winner_name)
        wy += 26
        draw_centered_text(draw, winner_cx, wy, winner["team"], f_winner_team, fill=GRAY_DARK)
        wy += 24

        logo_filename = TEAM_LOGOS.get(winner["team"])
        if logo_filename:
            logo_path = TEAM_DIR / logo_filename
            if logo_path.exists():
                team_logo = Image.open(logo_path).convert("RGBA")
                paste_contain(img, team_logo, (winner_cx - 70, wy, winner_cx + 70, wy + 52))
    else:
        draw_centered_text(draw, winner_cx, 200 + WINNER_SHIFT, "Not available", f_row_value, fill=GRAY_MED)
        draw_centered_text(draw, winner_cx, 224 + WINNER_SHIFT, "(new to calendar)", f_row_label, fill=GRAY_MED)

    # ------------------------------------------------------------
    # Bottom stats
    # ------------------------------------------------------------
    draw.line((20, 445, 940, 445), fill=BLACK, width=2)

    course_length = f1db_race.get("courseLength")
    turns = f1db_race.get("turns")

    race_laps = f1db_race.get("laps")
    if race_laps in (None, 0):
        race_laps = f1db_race.get("scheduledLaps")

    race_distance = f1db_race.get("distance")
    if race_distance in (None, 0):
        race_distance = f1db_race.get("scheduledDistance")

    corners_str = format_number(turns, decimals=0)

    if course_length is None:
        length_str = "N/A"
    else:
        length_str = f"{format_number(course_length, decimals=3)} km"

    if race_laps is not None and race_distance is not None:
        distance_str = (
            f"{format_number(race_laps, decimals=0)} LAPS / "
            f"{format_number(race_distance, decimals=3)} km"
        )
    elif race_laps is not None:
        distance_str = f"{format_number(race_laps, decimals=0)} LAPS"
    elif race_distance is not None:
        distance_str = f"{format_number(race_distance, decimals=3)} km"
    else:
        distance_str = "N/A"

    stat_cols = [
        ("corners.png", draw_corners_icon, "NUMBER OF CORNERS", corners_str, 20),
        ("ruler.png", draw_ruler_icon, "CIRCUIT LENGTH", length_str, 350),
        ("speedo.png", draw_speedo_icon, "RACE DISTANCE", distance_str, 620),
    ]

    stat_badge_size = 50
    for icon_file, fallback_fn, label, value, x in stat_cols:
        by0 = 465
        draw_badge_icon(img, draw, (x, by0, x + stat_badge_size, by0 + stat_badge_size), icon_file, fallback_fn)
        draw.text((x + stat_badge_size + 14, by0 + 2), label, font=f_stat_label, fill=GRAY_DARK)
        draw.text((x + stat_badge_size + 14, by0 + 20), value, font=f_stat_value, fill=BLACK)

    # ------------------------------------------------------------
    # Footer - (5) also shown in IST
    # ------------------------------------------------------------
    footer = "Updated " + datetime.now(IST).strftime("%d %b %Y, %H:%M IST")
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

    season = int(race["season"])
    round_num = int(race["round"])

    print(f"Fetching F1DB facts for {season} round {round_num}...")
    f1db_race = get_f1db_race(season, round_num)

    last_year = season - 1
    print(f"Fetching {last_year} result at {circuit_id}...")
    winner, last_year_laps = get_last_year_result(circuit_id, last_year)

    if winner:
        print(
            f"Last year's winner: {winner['name']} "
            f"({winner['team']}), {last_year_laps} laps"
        )
        winner["headshot_url"] = get_driver_headshot(
            winner["number"],
            winner["acronym"],
        )
    else:
        print("No previous result found for this circuit (likely new to the calendar).")

    print("Rendering image...")
    img = build_image(race, winner, f1db_race)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    img.save(OUTPUT_PATH, format="BMP")
    print(f"Saved {OUTPUT_PATH}")


if __name__ == "__main__":
    main()