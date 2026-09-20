"""
Random fact dashboard for LilyGo T5 4.7" e-paper display.

Each time the script runs:
    1. Randomly selects a category.
    2. Fetches a random fact from FactFacts.
    3. Renders it as a 960x540 BMP.

Output:
    docs/current.bmp

Dependencies:
    requests
    Pillow
"""

import os
import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import json
import hashlib

import requests
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# CONFIG
# ============================================================
WIDTH, HEIGHT = 960, 540

OUTPUT_PATH = "docs/current.bmp"

TIMEZONE = "Asia/Kolkata"

FACT_API = "https://factfacts.com/api.php"

FACT_CATEGORIES = [
    "history",
    "science",
    "animals",
    "space",
    "nature",
    "technology",
    "ocean",
    "food",
    "general",
    "dinosaur",
]


# ============================================================
# PATHS
# ============================================================
SCRIPT_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

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


# ============================================================
# COLOURS
# ============================================================
WHITE = 255
BLACK = 0

GRAY_DARK = 70
GRAY_MED = 125
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
            "using Pillow fallback font."
        )

        return ImageFont.load_default()


def load_fonts():
    return {
        "header": load_font(
            FONT_DISPLAY,
            38,
        ),

        "category": load_font(
            FONT_BOLD,
            17,
        ),

        "label": load_font(
            FONT_REGULAR,
            17,
        ),

        "fact_large": load_font(
            FONT_BOLD,
            36,
        ),

        "fact_medium": load_font(
            FONT_BOLD,
            31,
        ),

        "fact_small": load_font(
            FONT_BOLD,
            27,
        ),

        "footer": load_font(
            FONT_REGULAR,
            13,
        ),

        "date": load_font(
            FONT_REGULAR,
            20,
        ),
    }


# ============================================================
# TEXT HELPERS
# ============================================================
def text_w(draw, text, font):
    box = draw.textbbox(
        (0, 0),
        str(text),
        font=font,
    )

    return box[2] - box[0]


def wrap_text(
    draw,
    text,
    font,
    max_width,
):
    """
    Wrap text to fit max_width.
    """

    words = str(text).split()

    if not words:
        return [""]

    lines = []
    current = words[0]

    for word in words[1:]:

        trial = (
            current
            + " "
            + word
        )

        if text_w(
            draw,
            trial,
            font,
        ) <= max_width:

            current = trial

        else:

            lines.append(
                current
            )

            current = word

    lines.append(
        current
    )

    return lines


def centered_text(
    draw,
    center_x,
    y,
    text,
    font,
    fill=BLACK,
):
    width = text_w(
        draw,
        text,
        font,
    )

    draw.text(
        (
            center_x
            - width / 2,
            y,
        ),
        text,
        font=font,
        fill=fill,
    )


# ============================================================
# FACT API
# ============================================================
def fetch_fact():
    """
    Randomly selects one category and requests one fact.
    """

    category = random.choice(
        FACT_CATEGORIES
    )

    print(
        f"Selected category: {category}"
    )

    print(
        "Fetching fact from FactFacts..."
    )

    params = {
        "cat": category,
        "count": 1,
    }

    response = requests.get(
        FACT_API,
        params=params,
        timeout=30,
        headers={
            "Accept": "application/json",
            "User-Agent": "epaper-dashboard/1.0",
        },
    )

    response.raise_for_status()

    data = response.json()

    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------
    # Useful if FactFacts changes its response format.
    #
    # Uncomment temporarily if necessary:
    #
    # print(data)

    if not data.get("ok"):
        raise RuntimeError(
            "FactFacts returned an unsuccessful response: "
            f"{data}"
        )

    fact_data = data.get(
        "fact"
    )

    # Depending on count/API behaviour, tolerate a list too.
    if isinstance(
        fact_data,
        list,
    ):
        if not fact_data:
            raise RuntimeError(
                "FactFacts returned an empty fact list."
            )

        fact_data = fact_data[0]

    if not fact_data:
        raise RuntimeError(
            "FactFacts returned no fact."
        )

    fact_text = fact_data.get(
        "text"
    )

    if not fact_text:
        raise RuntimeError(
            "FactFacts response did not contain fact text."
        )

    returned_category = fact_data.get(
        "category",
        category,
    )

    return {
        "text": fact_text,
        "category": returned_category,
        "id": fact_data.get(
            "id",
            "",
        ),
    }


# ============================================================
# AUTOMATIC FACT FONT SIZE
# ============================================================
def choose_fact_layout(
    draw,
    text,
    fonts,
):
    """
    Automatically reduce the font size if a particularly
    long fact is returned.
    """

    max_width = 800

    candidates = [
        (
            fonts["fact_large"],
            48,
        ),
        (
            fonts["fact_medium"],
            43,
        ),
        (
            fonts["fact_small"],
            38,
        ),
    ]

    for font, line_height in candidates:

        lines = wrap_text(
            draw,
            text,
            font,
            max_width,
        )

        # Prefer no more than 5 lines.
        if len(lines) <= 5:
            return (
                font,
                line_height,
                lines,
            )

    # Final fallback
    font = fonts[
        "fact_small"
    ]

    lines = wrap_text(
        draw,
        text,
        font,
        max_width,
    )

    return (
        font,
        38,
        lines,
    )


# ============================================================
# RENDER
# ============================================================
def build_image(fact):
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

    now = datetime.now(
        ZoneInfo(
            TIMEZONE
        )
    )


    # ========================================================
    # HEADER
    # ========================================================
    draw.text(
        (
            20,
            14,
        ),
        "RANDOM FACT",
        font=fonts["header"],
        fill=BLACK,
    )


    # Date on right
    date_text = now.strftime(
        "%A, %d %B %Y"
    )

    date_width = text_w(
        draw,
        date_text,
        fonts["date"],
    )

    draw.text(
        (
            940
            - date_width,
            28,
        ),
        date_text,
        font=fonts["date"],
        fill=GRAY_DARK,
    )


    # Header separator
    draw.line(
        (
            20,
            68,
            940,
            68,
        ),
        fill=GRAY_LIGHT,
        width=2,
    )


    # ========================================================
    # CATEGORY
    # ========================================================
    category = (
        fact["category"]
        .replace(
            "_",
            " ",
        )
        .upper()
    )

    centered_text(
        draw,
        WIDTH / 2,
        101,
        category,
        fonts["category"],
        fill=GRAY_DARK,
    )


    # ========================================================
    # DID YOU KNOW LABEL
    # ========================================================
    centered_text(
        draw,
        WIDTH / 2,
        137,
        "DID YOU KNOW?",
        fonts["label"],
        fill=GRAY_MED,
    )


    # Small decorative line
    draw.line(
        (
            420,
            171,
            540,
            171,
        ),
        fill=GRAY_MED,
        width=1,
    )


    # ========================================================
    # FACT
    # ========================================================
    (
        fact_font,
        line_height,
        lines,
    ) = choose_fact_layout(
        draw,
        fact["text"],
        fonts,
    )


    total_height = (
        len(lines)
        * line_height
    )


    # Centre the block vertically around y ~= 300
    center_y = 305

    start_y = (
        center_y
        - total_height / 2
    )


    for index, line in enumerate(
        lines
    ):

        y = (
            start_y
            + index
            * line_height
        )

        centered_text(
            draw,
            WIDTH / 2,
            y,
            line,
            fact_font,
            fill=BLACK,
        )


    # ========================================================
    # LOWER DECORATIVE DIVIDER
    # ========================================================
    draw.line(
        (
            365,
            442,
            595,
            442,
        ),
        fill=GRAY_LIGHT,
        width=1,
    )


    # ========================================================
    # ATTRIBUTION
    # ========================================================
    attribution = (
        "Facts from FactFacts.com"
    )

    centered_text(
        draw,
        WIDTH / 2,
        459,
        attribution,
        fonts["footer"],
        fill=GRAY_MED,
    )


    # ========================================================
    # UPDATE TIME
    # ========================================================
    footer = (
        "Updated "
        + now.strftime(
            "%d %b %Y, %H:%M"
        )
    )

    footer_width = text_w(
        draw,
        footer,
        fonts["footer"],
    )

    draw.text(
        (
            940
            - footer_width,
            516,
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
# JSON creation
# ============================================================

def write_page_manifest(page_name, bmp_path="docs/current.bmp"):
    with open(bmp_path, "rb") as f:
        bmp_data = f.read()

    sha256 = hashlib.sha256(bmp_data).hexdigest()

    with Image.open(bmp_path) as bmp:
        width, height = bmp.size

    manifest = {
        "page": page_name,
        "file": "current.bmp",
        "sha256": sha256,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "width": width,
        "height": height,
    }

    os.makedirs("docs", exist_ok=True)

    with open(
        "docs/current.json",
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            manifest,
            f,
            indent=2,
        )

    print(f"Saved manifest for page: {page_name}")

# ============================================================
# MAIN
# ============================================================
def main():

    print()
    print(
        "============================="
    )
    print(
        "RANDOM FACT DASHBOARD"
    )
    print(
        "============================="
    )
    print()


    fact = fetch_fact()


    print()
    print(
        f"Category: {fact['category']}"
    )

    print(
        f"Fact: {fact['text']}"
    )

    print()


    print(
        "Rendering fact page..."
    )


    img = build_image(
        fact
    )


    save_bmp(
        img,
        OUTPUT_PATH,
    )

    write_page_manifest(
    "fact",
    OUTPUT_PATH,
    )

    print(
        f"Saved {OUTPUT_PATH} "
        f"({WIDTH}x{HEIGHT})"
    )


if __name__ == "__main__":
    main()