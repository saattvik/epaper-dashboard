"""
Spotify statistics dashboard generator for a 960x540 LilyGo T5 e-paper display.

Required environment variables:
    SPOTIFY_CLIENT_ID
    SPOTIFY_CLIENT_SECRET
    SPOTIFY_REFRESH_TOKEN
"""

import os
import time
from io import BytesIO
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageEnhance

WIDTH, HEIGHT = 960, 540
OUTPUT_PATH = "docs/current.bmp"
TIMEZONE = "Asia/Kolkata"
TIME_RANGE = "short_term"   # LAST 4 WEEKS

SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
FONT_DIR = os.path.join(REPO_DIR, "fonts")

#FONT_REGULAR = os.path.join(FONT_DIR, "OpenSans-Regular.ttf")
#FONT_BOLD = os.path.join(FONT_DIR, "OpenSans-Bold.ttf")
#FONT_DISPLAY = os.path.join(FONT_DIR, "BebasNeue-Regular.ttf")

FONT_REGULAR = os.path.join(FONT_DIR, "Inter_24pt-Regular.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "Inter_24pt-Bold.ttf")
FONT_DISPLAY = os.path.join(FONT_DIR, "BebasNeue-Regular.ttf")

SPOTIFY_LOGO_PATH = os.path.join(
    REPO_DIR,
    "assets",
    "spotify",
    "logo.png",
)

WHITE = 255
BLACK = 0
GRAY_DARK = 70
GRAY_MED = 145
GRAY_LIGHT = 220


def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        print(f"Could not load font {path}; using fallback")
        return ImageFont.load_default()


def load_fonts():
    return {
        "title": load_font(FONT_DISPLAY, 38),
        "section": load_font(FONT_DISPLAY, 23),
        "period": load_font(FONT_BOLD, 18),
        "hero_rank": load_font(FONT_BOLD, 28),
        "rank": load_font(FONT_BOLD, 22),
        "name": load_font(FONT_BOLD, 18),
        "small_name": load_font(FONT_BOLD, 18),
        "secondary": load_font(FONT_REGULAR, 15),
        "tiny": load_font(FONT_REGULAR, 12),
        "hero_name": load_font(FONT_DISPLAY, 30),
        "library_label": load_font(FONT_REGULAR, 16),
        "library_value": load_font(FONT_BOLD, 28),
        "footer": load_font(FONT_REGULAR, 12),
    }


def text_w(draw, text, font):
    box = draw.textbbox((0, 0), str(text), font=font)
    return box[2] - box[0]


def fit_text(draw, text, font, max_width):
    text = str(text)
    if text_w(draw, text, font) <= max_width:
        return text

    ellipsis = "…"
    while text and text_w(draw, text + ellipsis, font) > max_width:
        text = text[:-1]
    return (text.rstrip() + ellipsis) if text else ellipsis


def wrap_text(draw, text, font, max_width, max_lines=2):
    words = str(text).split()
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

    if len(lines) <= max_lines:
        return lines

    lines = lines[:max_lines]
    lines[-1] = fit_text(draw, lines[-1], font, max_width)
    return lines


def draw_centered_text(draw, cx, y, text, font, fill=BLACK):
    draw.text((cx - text_w(draw, text, font) / 2, y), text, font=font, fill=fill)


def rounded_image(img, radius=12):
    img = img.convert("L")
    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = Image.new("L", img.size, WHITE)
    out.paste(img, (0, 0), mask)
    return out


def env_required(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing environment variable {name}")
    return value


def get_access_token():
    r = requests.post(
        SPOTIFY_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": env_required("SPOTIFY_REFRESH_TOKEN"),
        },
        auth=(
            env_required("SPOTIFY_CLIENT_ID"),
            env_required("SPOTIFY_CLIENT_SECRET"),
        ),
        timeout=30,
    )
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError("Spotify token response did not contain access_token")
    return token


def spotify_get(access_token, path, params=None, allow_204=False):
    headers = {"Authorization": f"Bearer {access_token}"}
    url = SPOTIFY_API + path

    for _ in range(4):
        r = requests.get(url, headers=headers, params=params, timeout=30)

        if allow_204 and r.status_code == 204:
            return None

        if r.status_code == 429:
            wait = int(r.headers.get("Retry-After", "2"))
            time.sleep(wait)
            continue

        r.raise_for_status()
        return r.json()

    raise RuntimeError(f"Spotify request failed repeatedly: {path}")


def fetch_image(url):
    if not url:
        return None
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception as e:
        print(f"Image fetch failed: {e}")
        return None

def brighten_for_epaper(img, brightness=1.15, gamma=0.85, autocontrast_cutoff=1):
    """Lightens a photo before quantizing to the panel's 16 gray levels.
    Driver photos (dark suits/helmets, sometimes dark backgrounds) tend to
    render darker on the physical e-paper than they look in a monitor
    preview, so we stretch contrast to use the full range, lift shadows/
    midtones via a gamma curve, then apply a uniform brightness boost.
    Only meant for photographic content - not flags, logos, or text."""
    if img.mode != "L":
        img = img.convert("L")
 
    img = ImageOps.autocontrast(img, cutoff=autocontrast_cutoff)
 
    if gamma != 1.0:
        lut = [min(255, int((i / 255) ** gamma * 255)) for i in range(256)]
        img = img.point(lut)
 
    if brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness)
 
    return img

def compact_number(n):
    if n is None:
        return "-"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def track_artist_names(track):
    return ", ".join(a["name"] for a in track.get("artists", []))


def period_label():
    return "LAST 4 WEEKS"


def relative_time(iso_time):
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo)
        mins = max(0, int((now - dt).total_seconds() // 60))
        if mins < 1:
            return "just now"
        if mins < 60:
            return f"{mins}m ago"
        hours = mins // 60
        if hours < 24:
            return f"{hours}h ago"
        return f"{hours // 24}d ago"
    except Exception:
        return ""


def fetch_library_counts(access_token):
    saved_tracks = spotify_get(access_token, "/me/tracks", params={"limit": 1})
    playlists = spotify_get(access_token, "/me/playlists", params={"limit": 1})
    followed = spotify_get(access_token, "/me/following", params={"type": "artist", "limit": 1})

    return {
        "saved_tracks": saved_tracks.get("total", 0),
        "playlists": playlists.get("total", 0),
        "followed_artists": followed.get("artists", {}).get("total", 0),
    }


def fetch_spotify_data():
    token = get_access_token()

    artists = spotify_get(
        token,
        "/me/top/artists",
        params={"time_range": TIME_RANGE, "limit": 6},
    )["items"]

    tracks = spotify_get(
        token,
        "/me/top/tracks",
        params={"time_range": TIME_RANGE, "limit": 5},
    )["items"]

    recent = spotify_get(
        token,
        "/me/player/recently-played",
        params={"limit": 5},
    )["items"]

    current = spotify_get(
        token,
        "/me/player/currently-playing",
        allow_204=True,
    )

    library = fetch_library_counts(token)

    return {
        "artists": artists,
        "tracks": tracks,
        "recent": recent,
        "current": current,
        "library": library,
    }


def build_image(data):
    img = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    f = load_fonts()

    artists = data["artists"]
    tracks = data["tracks"]
    recent = data["recent"]
    current = data["current"]
    library = data["library"]


    # ------------------------------------------------------------
    # Header
    # ------------------------------------------------------------
    logo_box = (20, 10, 60, 50)

    if os.path.exists(SPOTIFY_LOGO_PATH):
        logo = Image.open(SPOTIFY_LOGO_PATH).convert("RGBA")

        alpha = logo.getchannel("A")
        bbox = alpha.getbbox()
        if bbox:
            logo = logo.crop(bbox)

        box_w = logo_box[2] - logo_box[0]
        box_h = logo_box[3] - logo_box[1]

        scale = min(box_w / logo.width, box_h / logo.height)
        new_w = max(1, int(logo.width * scale))
        new_h = max(1, int(logo.height * scale))

        logo = logo.resize((new_w, new_h), Image.LANCZOS)

        gray_logo = ImageOps.grayscale(logo)
        alpha = logo.getchannel("A")

        x = logo_box[0] + (box_w - new_w) // 2
        y = logo_box[1] + (box_h - new_h) // 2
        img.paste(gray_logo, (x, y), alpha)

    title_x = 82
    draw.text((title_x, 10), "SPOTIFY", font=f["title"], fill=BLACK)

    period = period_label()
    draw.text(
        (940 - text_w(draw, period, f["period"]), 18),
        period,
        font=f["period"],
        fill=GRAY_DARK,
    )

    draw.line((20, 58, 940, 58), fill=GRAY_LIGHT, width=2)

    # ------------------------------------------------------------
    # Section headers
    # ------------------------------------------------------------
    left_x0, left_x1 = 20, 570
    right_x0, right_x1 = 600, 940

    draw.text((left_x0, 72), "TOP ARTISTS", font=f["section"], fill=BLACK)
    draw.line((left_x0, 98, left_x1, 98), fill=GRAY_LIGHT, width=1)

    draw.text((right_x0, 72), "TOP TRACKS", font=f["section"], fill=BLACK)
    draw.line((right_x0, 98, right_x1, 98), fill=GRAY_LIGHT, width=1)

    # ------------------------------------------------------------
    # Top Artists
    # ------------------------------------------------------------
    featured = artists[0] if artists else None
    others = artists[1:6] if len(artists) > 1 else []

    hero_img_x = 40
    hero_img_y = 118
    hero_img_size = 225
    hero_center_x = hero_img_x + hero_img_size / 2

    if featured:
#        draw.text((25, 122), "1", font=f["hero_rank"], fill=BLACK)

        hero_url = featured.get("images", [{}])[0].get("url") if featured.get("images") else None
        hero_img = fetch_image(hero_url)
        if hero_img:
            hero_img = ImageOps.fit(
                hero_img.convert("L"),
                (hero_img_size, hero_img_size),
                method=Image.LANCZOS,
            )
            hero_img = rounded_image(hero_img, 16)
            hero_img = brighten_for_epaper(hero_img, brightness=1.15, gamma=0.95, autocontrast_cutoff=1)
            img.paste(hero_img, (hero_img_x, hero_img_y))

        name_lines = wrap_text(draw, featured["name"].upper(), f["hero_name"], 230, max_lines=2)
        base_y = hero_img_y + hero_img_size + 12
        for i, line in enumerate(name_lines):
            draw_centered_text(draw, hero_center_x, base_y + i * 28, line, f["hero_name"], BLACK)

    # Smaller artist list (#2 to #6)
    thumb_size = 50
    artist_list_x = 300
    artist_row_y = 110
    row_step = 56

    for idx, artist in enumerate(others, start=2):
        art_url = artist.get("images", [{}])[-1].get("url") if artist.get("images") else None
        art = fetch_image(art_url)
        if art:
            art = ImageOps.fit(art.convert("L"), (thumb_size, thumb_size), method=Image.LANCZOS)
#            art = rounded_image(art, 8)
            art = brighten_for_epaper(art, brightness=1.15, gamma=0.95, autocontrast_cutoff=1)
            img.paste(art, (artist_list_x + 36, artist_row_y))

        draw.text((artist_list_x, artist_row_y + 11), str(idx), font=f["rank"], fill=GRAY_DARK)

        name_x = artist_list_x + 98
        max_w = left_x1 - name_x
        name = fit_text(draw, artist["name"], f["small_name"], max_w)
        draw.text((name_x, artist_row_y + 10), name, font=f["small_name"], fill=BLACK)

        artist_row_y += row_step

    # ------------------------------------------------------------
    # Top Tracks
    # ------------------------------------------------------------
    track_row_y = 110

    for rank, track in enumerate(tracks[:5], 1):
        images = track.get("album", {}).get("images") or []
        cover_url = images[-1].get("url") if images else None
        cover = fetch_image(cover_url)
        if cover:
            cover = ImageOps.fit(cover.convert("L"), (thumb_size, thumb_size), method=Image.LANCZOS)
            cover = brighten_for_epaper(cover, brightness=1.15, gamma=0.95, autocontrast_cutoff=1)
            img.paste(cover, (right_x0 + 32, track_row_y))

        draw.text((right_x0, track_row_y + 11), str(rank), font=f["rank"], fill=GRAY_DARK)

        name_x = right_x0 + 96
        max_w = right_x1 - name_x
        name = fit_text(draw, track["name"], f["small_name"], max_w)
        artist_text = fit_text(draw, track_artist_names(track), f["secondary"], max_w)

        draw.text((name_x, track_row_y + 6), name, font=f["small_name"], fill=BLACK)
        draw.text((name_x, track_row_y + 30), artist_text, font=f["secondary"], fill=GRAY_MED)

        track_row_y += row_step

    # ------------------------------------------------------------
    # Divider
    # ------------------------------------------------------------
    draw.line((20, 408, 940, 408), fill=BLACK, width=2)

    # ------------------------------------------------------------
    # Last Played / Now Playing
    # ------------------------------------------------------------
    bottom_left_x0, bottom_left_x1 = 20, 445

    current_track = None
    currently_playing = False

    if current and current.get("item"):
        current_track = current["item"]
        currently_playing = bool(current.get("is_playing"))
    elif recent:
        current_track = recent[0].get("track")

    label = "NOW PLAYING" if currently_playing else "LAST PLAYED"
    draw.text((bottom_left_x0, 420), label, font=f["section"], fill=BLACK)

    if current_track:
        images = current_track.get("album", {}).get("images") or []
        cover_url = images[-1].get("url") if images else None
        cover = fetch_image(cover_url)
        if cover:
            cover = ImageOps.fit(cover.convert("L"), (76, 76), method=Image.LANCZOS)
            img.paste(cover, (bottom_left_x0, 452))

        max_w = bottom_left_x1 - 112
        track_name = fit_text(draw, current_track.get("name", ""), f["name"], max_w)
        artist_text = fit_text(draw, track_artist_names(current_track), f["secondary"], max_w)

        draw.text((bottom_left_x0 + 92, 458), track_name, font=f["name"], fill=BLACK)
        draw.text((bottom_left_x0 + 92, 486), artist_text, font=f["secondary"], fill=GRAY_DARK)

        if currently_playing and current.get("progress_ms") is not None:
            duration = current_track.get("duration_ms") or 0
            progress = current["progress_ms"]
            if duration > 0:
                bar_x0 = bottom_left_x0 + 92
                bar_x1 = bottom_left_x1 - 10
                bar_y = 515
                frac = max(0.0, min(1.0, progress / duration))
                draw.line((bar_x0, bar_y, bar_x1, bar_y), fill=GRAY_LIGHT, width=4)
                draw.line((bar_x0, bar_y, bar_x0 + (bar_x1 - bar_x0) * frac, bar_y), fill=BLACK, width=4)
        elif recent:
            played_at = recent[0].get("played_at", "")
            when = relative_time(played_at)
            if when:
                draw.text((bottom_left_x0 + 92, 508), when, font=f["tiny"], fill=GRAY_MED)

    # ------------------------------------------------------------
    # Your Library
    # ------------------------------------------------------------
    lib_x0 = 500
    draw.text((lib_x0, 420), "YOUR LIBRARY", font=f["section"], fill=BLACK)

    items = [
        ("Saved Songs", compact_number(library.get("saved_tracks", 0))),
        ("Playlists", compact_number(library.get("playlists", 0))),
        ("Following", compact_number(library.get("followed_artists", 0))),
    ]

    positions = [
        (500, 462),
        (665, 462),
        (810, 462),
    ]

    for (label_text, value_text), (x, y) in zip(items, positions):
        draw.text((x, y), label_text, font=f["library_label"], fill=GRAY_DARK)
        draw.text((x, y + 20), str(value_text), font=f["library_value"], fill=BLACK)

    # ------------------------------------------------------------
    # Footer
    # ------------------------------------------------------------
    now = datetime.now(ZoneInfo(TIMEZONE))
    footer = "Updated " + now.strftime("%d %b %Y, %H:%M")
    draw.text(
        (940 - text_w(draw, footer, f["footer"]), 524),
        footer,
        font=f["footer"],
        fill=GRAY_MED,
    )

    return img


def save_bmp(img, path):
    output_dir = os.path.dirname(path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    img.save(path, format="BMP")


def main():
    print("Fetching Spotify statistics...")
    data = fetch_spotify_data()
    print("Rendering Spotify dashboard...")
    img = build_image(data)
    save_bmp(img, OUTPUT_PATH)
    print(f"Saved {OUTPUT_PATH} ({WIDTH}x{HEIGHT})")


if __name__ == "__main__":
    main()