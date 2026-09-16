"""
Trending news dashboard generator for the LilyGo T5 4.7" e-paper display.

Displays:
    - 3 India headlines
    - 3 World headlines

Output:
    docs/current.bmp

Required environment variable:
    NEWS_API_KEY
"""

import os
from io import BytesIO
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ============================================================
# CONFIG
# ============================================================
WIDTH, HEIGHT = 960, 540
OUTPUT_PATH = "docs/current.bmp"

TIMEZONE = "Asia/Kolkata"

NEWS_API_BASE = "https://newsapi.org/v2"
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "").strip()

# Fetch more than we display so junk filtering still leaves enough stories.
INDIA_PAGE_SIZE = 20
WORLD_PAGE_SIZE = 20

INDIA_COUNTRY = "in"

# Used as the final India fallback.
INDIA_DOMAINS = ",".join([
    "ndtv.com",
    "indianexpress.com",
    "hindustantimes.com",
    "thehindu.com",
    "timesofindia.indiatimes.com",
    "news18.com",
])

# Avoid CNN because its feed was returning utility / login pages.
WORLD_SOURCES = ",".join([
    "bbc-news",
    "reuters",
    "al-jazeera-english",
    "abc-news",
])

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.abspath(
    os.path.join(
        SCRIPT_DIR,
        "..",
    )
)

FONT_DIR = os.path.join(
    REPO_DIR,
    "fonts",
)

FONT_REGULAR = os.path.join(
    FONT_DIR,
    "OpenSans-Regular.ttf",
)

FONT_BOLD = os.path.join(
    FONT_DIR,
    "OpenSans-Bold.ttf",
)

FONT_DISPLAY = os.path.join(
    FONT_DIR,
    "BebasNeue-Regular.ttf",
)

WHITE = 255
BLACK = 0

GRAY_DARK = 70
GRAY_MED = 145
GRAY_LIGHT = 220


# ============================================================
# FONT HELPERS
# ============================================================
def load_font(path, size):
    try:
        return ImageFont.truetype(
            path,
            size,
        )

    except OSError:
        print(
            f"Could not load {path}; "
            "using Pillow fallback font"
        )

        return ImageFont.load_default()


def load_fonts():
    return {
        "title": load_font(
            FONT_DISPLAY,
            34,
        ),

        "section": load_font(
            FONT_DISPLAY,
            24,
        ),

        "headline": load_font(
            FONT_BOLD,
            16,
        ),

        "meta": load_font(
            FONT_REGULAR,
            12,
        ),

        "footer": load_font(
            FONT_REGULAR,
            12,
        ),

        "placeholder": load_font(
            FONT_BOLD,
            14,
        ),
    }


# ============================================================
# TEXT HELPERS
# ============================================================
def text_w(draw, text, font):
    bbox = draw.textbbox(
        (0, 0),
        str(text),
        font=font,
    )

    return bbox[2] - bbox[0]


def fit_text(
    draw,
    text,
    font,
    max_width,
):
    text = str(text)

    if text_w(
        draw,
        text,
        font,
    ) <= max_width:
        return text

    ellipsis = "…"

    while (
        text
        and text_w(
            draw,
            text + ellipsis,
            font,
        ) > max_width
    ):
        text = text[:-1]

    if text:
        return text.rstrip() + ellipsis

    return ellipsis


def wrap_text(
    draw,
    text,
    font,
    max_width,
    max_lines=3,
):
    words = str(text).split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:
        test_line = current + " " + word

        if text_w(
            draw,
            test_line,
            font,
        ) <= max_width:
            current = test_line

        else:
            lines.append(current)
            current = word

    lines.append(current)

    if len(lines) <= max_lines:
        return lines

    lines = lines[:max_lines]

    lines[-1] = fit_text(
        draw,
        lines[-1],
        font,
        max_width,
    )

    return lines


def clean_headline(title):
    if not title:
        return ""

    title = title.strip()

    # NewsAPI titles often include:
    # "Headline text - Publication Name"
    parts = title.rsplit(
        " - ",
        1,
    )

    if (
        len(parts) == 2
        and len(parts[1]) < 35
    ):
        return parts[0].strip()

    return title


def normalize_text(text):
    return (
        text
        or ""
    ).strip().lower()


# ============================================================
# JUNK FILTERING
# ============================================================
def is_junk_article(article):
    title = normalize_text(
        article.get("title")
    )

    source = normalize_text(
        (
            article.get("source")
            or {}
        ).get("name")
    )

    description = normalize_text(
        article.get("description")
    )

    combined = " ".join([
        title,
        source,
        description,
    ])

    # Missing title
    if not title:
        return True

    # Generic source landing pages rather than actual articles.
    exact_junk_titles = {
        "google news",
        "cnn",
        "bbc news",
        "reuters",
        "al jazeera english",
        "abc news",
    }

    if title in exact_junk_titles:
        return True

    # Utility pages / non-news pages.
    junk_phrases = [
        "log in",
        "login",
        "sign in",
        "sign up",
        "create an account",
        "your account",
        "manage account",

        "subscribe",
        "subscription",
        "newsletter",

        "cookie policy",
        "privacy policy",
        "terms of use",

        "listen live",
        "watch live",

        "photo gallery",
        "image gallery",

        "all over the map with john king",

        "advertisement",
        "advertising",

        "download our app",
        "download the app",
    ]

    for phrase in junk_phrases:
        if phrase in combined:
            return True

    # Generic source name used as the entire headline.
    if (
        source
        and title == source
    ):
        return True

    # Very short strings tend to be navigation / landing pages.
    if len(title) < 12:
        return True

    return False


# ============================================================
# TIME HELPERS
# ============================================================
def relative_time(iso_time):
    if not iso_time:
        return ""

    try:
        published = datetime.fromisoformat(
            iso_time.replace(
                "Z",
                "+00:00",
            )
        )

        now = datetime.now(
            timezone.utc
        )

        diff = (
            now
            - published.astimezone(
                timezone.utc
            )
        )

        minutes = max(
            0,
            int(
                diff.total_seconds()
                // 60
            ),
        )

        if minutes < 1:
            return "just now"

        if minutes < 60:
            return f"{minutes}m ago"

        hours = minutes // 60

        if hours < 24:
            return f"{hours}h ago"

        days = hours // 24

        return f"{days}d ago"

    except Exception:
        return ""


# ============================================================
# NEWS API HELPERS
# ============================================================
def require_api_key():
    if not NEWS_API_KEY:
        raise RuntimeError(
            "NEWS_API_KEY is not set.\n"
            "\n"
            "In Git Bash run:\n"
            'export NEWS_API_KEY="your_actual_api_key"\n'
        )


def news_get(
    endpoint,
    params,
):
    require_api_key()

    params = dict(params)
    params["apiKey"] = NEWS_API_KEY

    url = (
        NEWS_API_BASE
        + endpoint
    )

    response = requests.get(
        url,
        params=params,
        timeout=30,
    )

    # More useful error than requests' generic 401.
    if response.status_code == 401:
        raise RuntimeError(
            "NewsAPI returned 401 Unauthorized. "
            "Check that NEWS_API_KEY contains only "
            "your actual API key."
        )

    response.raise_for_status()

    data = response.json()

    if data.get("status") != "ok":
        raise RuntimeError(
            f"NewsAPI error: {data}"
        )

    return data


# ============================================================
# ARTICLE PROCESSING
# ============================================================
def normalize_article(article):
    source_name = (
        article.get("source")
        or {}
    ).get(
        "name"
    ) or "Unknown"

    return {
        "title": clean_headline(
            article.get("title")
            or ""
        ),

        "source": source_name,

        "image_url": article.get(
            "urlToImage"
        ),

        "published_at": article.get(
            "publishedAt"
        ) or "",

        "url": article.get(
            "url"
        ) or "",
    }


def pick_best_articles(
    articles,
    count=3,
):
    selected = []
    seen_titles = set()
    seen_urls = set()

    for raw_article in articles:
        if is_junk_article(
            raw_article
        ):
            continue

        article = normalize_article(
            raw_article
        )

        title = normalize_text(
            article["title"]
        )

        url = normalize_text(
            article["url"]
        )

        if not title:
            continue

        # Prevent duplicate / near-identical API records.
        if title in seen_titles:
            continue

        if (
            url
            and url in seen_urls
        ):
            continue

        seen_titles.add(
            title
        )

        if url:
            seen_urls.add(
                url
            )

        selected.append(
            article
        )

        if len(selected) >= count:
            break

    return selected


# ============================================================
# INDIA FETCHING
# ============================================================
def fetch_india_news():
    combined = []

    # --------------------------------------------------------
    # 1. Normal India top-headlines feed
    # --------------------------------------------------------
    print(
        "Fetching India country headlines..."
    )

    try:
        data = news_get(
            "/top-headlines",
            {
                "country": INDIA_COUNTRY,
                "pageSize": INDIA_PAGE_SIZE,
            },
        )

        country_articles = data.get(
            "articles",
            [],
        )

        print(
            "India country feed returned "
            f"{len(country_articles)} raw articles"
        )

        combined.extend(
            country_articles
        )

        usable = pick_best_articles(
            combined,
            count=3,
        )

        if len(usable) >= 3:
            print(
                "Enough usable India articles "
                "from country feed."
            )

            return combined

    except Exception as e:
        print(
            "India country feed failed: "
            f"{e}"
        )

    # --------------------------------------------------------
    # 2. Google News India fallback
    # --------------------------------------------------------
    print(
        "Trying Google News India fallback..."
    )

    try:
        data = news_get(
            "/top-headlines",
            {
                "sources": "google-news-in",
                "pageSize": INDIA_PAGE_SIZE,
            },
        )

        google_articles = data.get(
            "articles",
            [],
        )

        print(
            "Google News India returned "
            f"{len(google_articles)} raw articles"
        )

        combined.extend(
            google_articles
        )

        usable = pick_best_articles(
            combined,
            count=3,
        )

        if len(usable) >= 3:
            print(
                "Enough usable India articles "
                "after Google News fallback."
            )

            return combined

    except Exception as e:
        print(
            "Google News India fallback failed: "
            f"{e}"
        )

    # --------------------------------------------------------
    # 3. Major Indian news domains fallback
    # --------------------------------------------------------
    print(
        "Trying Indian news domains fallback..."
    )

    try:
        data = news_get(
            "/everything",
            {
                "domains": INDIA_DOMAINS,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": INDIA_PAGE_SIZE,
            },
        )

        domain_articles = data.get(
            "articles",
            [],
        )

        print(
            "Indian domains feed returned "
            f"{len(domain_articles)} raw articles"
        )

        combined.extend(
            domain_articles
        )

    except Exception as e:
        print(
            "Indian domains fallback failed: "
            f"{e}"
        )

    return combined


# ============================================================
# WORLD FETCHING
# ============================================================
def fetch_world_news():
    print(
        "Fetching world headlines..."
    )

    try:
        data = news_get(
            "/top-headlines",
            {
                "sources": WORLD_SOURCES,
                "pageSize": WORLD_PAGE_SIZE,
            },
        )

        articles = data.get(
            "articles",
            [],
        )

        print(
            "World feed returned "
            f"{len(articles)} raw articles"
        )

        return articles

    except Exception as e:
        print(
            "World headline feed failed: "
            f"{e}"
        )

        return []


# ============================================================
# IMAGE HELPERS
# ============================================================
def fetch_image(url):
    if not url:
        return None

    try:
        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent":
                "Mozilla/5.0 epaper-dashboard/1.0"
            },
        )

        response.raise_for_status()

        return Image.open(
            BytesIO(
                response.content
            )
        ).convert(
            "RGB"
        )

    except Exception as e:
        print(
            "Image fetch failed: "
            f"{e}"
        )

        return None


def paste_fit(
    base,
    overlay,
    box,
):
    x0, y0, x1, y1 = [
        int(round(v))
        for v in box
    ]

    width = max(
        1,
        x1 - x0,
    )

    height = max(
        1,
        y1 - y0,
    )

    image = ImageOps.fit(
        overlay.convert("L"),
        (
            width,
            height,
        ),
        method=Image.LANCZOS,
    )

    base.paste(
        image,
        (
            x0,
            y0,
        ),
    )


def draw_placeholder_image(
    img,
    draw,
    box,
    section_name,
    fonts,
):
    x0, y0, x1, y1 = [
        int(round(v))
        for v in box
    ]

    draw.rectangle(
        (
            x0,
            y0,
            x1,
            y1,
        ),
        fill=GRAY_LIGHT,
        outline=GRAY_MED,
        width=1,
    )

    text = section_name.upper()

    width = text_w(
        draw,
        text,
        fonts["placeholder"],
    )

    draw.text(
        (
            (x0 + x1) / 2
            - width / 2,

            (y0 + y1) / 2
            - 8,
        ),
        text,
        font=fonts["placeholder"],
        fill=GRAY_DARK,
    )


# ============================================================
# NEWS COLUMN
# ============================================================
def draw_news_column(
    img,
    draw,
    x0,
    x1,
    top_y,
    section_title,
    articles,
    fonts,
):
    # Section heading
    draw.text(
        (
            x0,
            top_y,
        ),
        section_title,
        font=fonts["section"],
        fill=BLACK,
    )

    draw.line(
        (
            x0,
            top_y + 28,
            x1,
            top_y + 28,
        ),
        fill=GRAY_LIGHT,
        width=1,
    )

    # Three large article rows.
    article_top = top_y + 46

    row_height = 118
    row_gap = 18

    image_width = 110
    image_height = 78

    for index, article in enumerate(
        articles[:3]
    ):
        y = (
            article_top
            + index
            * (
                row_height
                + row_gap
            )
        )

        image_box = (
            x0,
            y,
            x0 + image_width,
            y + image_height,
        )

        # ----------------------------------------------------
        # Image
        # ----------------------------------------------------
        article_image = fetch_image(
            article["image_url"]
        )

        if article_image:
            paste_fit(
                img,
                article_image,
                image_box,
            )

        else:
            draw_placeholder_image(
                img,
                draw,
                image_box,
                section_title,
                fonts,
            )

        # ----------------------------------------------------
        # Headline
        # ----------------------------------------------------
        text_x = (
            x0
            + image_width
            + 14
        )

        max_text_width = (
            x1
            - text_x
        )

        headline_lines = wrap_text(
            draw,
            article["title"],
            fonts["headline"],
            max_text_width,
            max_lines=3,
        )

        line_y = y - 2

        for line in headline_lines:
            draw.text(
                (
                    text_x,
                    line_y,
                ),
                line,
                font=fonts["headline"],
                fill=BLACK,
            )

            line_y += 18

        # ----------------------------------------------------
        # Source + age
        # ----------------------------------------------------
        meta = article["source"]

        age = relative_time(
            article["published_at"]
        )

        if age:
            meta += (
                " • "
                + age
            )

        meta = fit_text(
            draw,
            meta,
            fonts["meta"],
            max_text_width,
        )

        draw.text(
            (
                text_x,
                y + 64,
            ),
            meta,
            font=fonts["meta"],
            fill=GRAY_DARK,
        )

        # Divider
        if index < 2:
            divider_y = (
                y
                + row_height
            )

            draw.line(
                (
                    x0,
                    divider_y,
                    x1,
                    divider_y,
                ),
                fill=GRAY_LIGHT,
                width=1,
            )


# ============================================================
# MAIN PAGE RENDERING
# ============================================================
def build_image(
    india_articles,
    world_articles,
):
    img = Image.new(
        "L",
        (
            WIDTH,
            HEIGHT,
        ),
        WHITE,
    )

    draw = ImageDraw.Draw(
        img
    )

    fonts = load_fonts()

    # --------------------------------------------------------
    # Header
    # --------------------------------------------------------
    draw.text(
        (
            20,
            10,
        ),
        "TRENDING NEWS",
        font=fonts["title"],
        fill=BLACK,
    )

    now = datetime.now(
        ZoneInfo(
            TIMEZONE
        )
    )

    header_right = now.strftime(
        "%d %b %Y  %H:%M"
    )

    header_width = text_w(
        draw,
        header_right,
        fonts["meta"],
    )

    draw.text(
        (
            940 - header_width,
            22,
        ),
        header_right,
        font=fonts["meta"],
        fill=GRAY_DARK,
    )

    draw.line(
        (
            20,
            58,
            940,
            58,
        ),
        fill=GRAY_LIGHT,
        width=2,
    )

    # --------------------------------------------------------
    # Two news columns
    # --------------------------------------------------------
    left_x0 = 20
    left_x1 = 465

    right_x0 = 495
    right_x1 = 940

    top_y = 72

    draw_news_column(
        img,
        draw,
        left_x0,
        left_x1,
        top_y,
        "INDIA",
        india_articles,
        fonts,
    )

    draw_news_column(
        img,
        draw,
        right_x0,
        right_x1,
        top_y,
        "WORLD",
        world_articles,
        fonts,
    )

    # --------------------------------------------------------
    # Footer
    # --------------------------------------------------------
    footer = "Source: NewsAPI"

    footer_width = text_w(
        draw,
        footer,
        fonts["footer"],
    )

    draw.text(
        (
            940 - footer_width,
            522,
        ),
        footer,
        font=fonts["footer"],
        fill=GRAY_MED,
    )

    return img


# ============================================================
# SAVE
# ============================================================
def save_bmp(
    img,
    path,
):
    output_dir = os.path.dirname(
        path
    )

    if output_dir:
        os.makedirs(
            output_dir,
            exist_ok=True,
        )

    img.save(
        path,
        format="BMP",
    )


# ============================================================
# MAIN
# ============================================================
def main():
    print()
    print("==============================")
    print("TRENDING NEWS DASHBOARD")
    print("==============================")
    print()

    india_raw = fetch_india_news()
    world_raw = fetch_world_news()

    print()
    print(
        f"Raw India articles: "
        f"{len(india_raw)}"
    )

    print(
        f"Raw World articles: "
        f"{len(world_raw)}"
    )

    india_articles = pick_best_articles(
        india_raw,
        count=3,
    )

    world_articles = pick_best_articles(
        world_raw,
        count=3,
    )

    print()
    print(
        f"India articles selected: "
        f"{len(india_articles)}"
    )

    for index, article in enumerate(
        india_articles,
        start=1,
    ):
        print(
            f"  INDIA {index}: "
            f"{article['title']}"
        )

    print()

    print(
        f"World articles selected: "
        f"{len(world_articles)}"
    )

    for index, article in enumerate(
        world_articles,
        start=1,
    ):
        print(
            f"  WORLD {index}: "
            f"{article['title']}"
        )

    print()

    if len(india_articles) < 3:
        print(
            "(!) Warning: fewer than 3 usable "
            "India articles were returned."
        )

    if len(world_articles) < 3:
        print(
            "(!) Warning: fewer than 3 usable "
            "World articles were returned."
        )

    print(
        "Rendering dashboard..."
    )

    img = build_image(
        india_articles,
        world_articles,
    )

    save_bmp(
        img,
        OUTPUT_PATH,
    )

    print(
        f"Saved {OUTPUT_PATH} "
        f"({WIDTH}x{HEIGHT})"
    )


if __name__ == "__main__":
    main()