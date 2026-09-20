"""
VALORANT current-Act dashboard for 960x540 e-paper.

Required environment variables:
    VALORANT_NAME
    VALORANT_TAG
    HENRIK_API_KEY

Optional:
    VALORANT_REGION   default = "ap"

Output:
    docs/current.bmp
"""

import os
import time
from io import BytesIO
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont


# ============================================================
# DISPLAY CONFIG
# ============================================================

WIDTH = 960
HEIGHT = 540

OUTPUT_PATH = "docs/current.bmp"
TIMEZONE = "Asia/Kolkata"


# ============================================================
# PLAYER CONFIG
# ============================================================

VALORANT_NAME = os.getenv(
    "VALORANT_NAME",
    "",
).strip()

VALORANT_TAG = os.getenv(
    "VALORANT_TAG",
    "",
).strip()

VALORANT_REGION = os.getenv(
    "VALORANT_REGION",
    "ap",
).strip()

HENRIK_API_KEY = os.getenv(
    "HENRIK_API_KEY",
    "",
).strip()

PLATFORM = "pc"


# ============================================================
# API CONFIG
# ============================================================

HENRIK_API_BASE = "https://api.henrikdev.xyz"

VALORANT_SEASONS_API = (
    "https://valorant-api.com/v1/seasons"
)

VALORANT_AGENTS_API = (
    "https://valorant-api.com/v1/agents"
    "?isPlayableCharacter=true"
)

VALORANT_TIERS_API = (
    "https://valorant-api.com/v1/competitivetiers"
)


# ============================================================
# PAGINATION / RATE LIMIT
# ============================================================

PAGE_SIZE = 10
MAX_PAGES = 50

REQUEST_DELAY_SECONDS = 2.5

MAX_RATE_LIMIT_RETRIES = 5
RATE_LIMIT_BASE_WAIT = 15


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
# GRAYSCALE COLORS
# ============================================================

WHITE = 255
BLACK = 0

GRAY_DARK = 70
GRAY_MED = 145
GRAY_LIGHT = 205
GRAY_VERY_LIGHT = 235

PIE_WIN = 55
PIE_LOSS = 205


# ============================================================
# REMOTE CACHE
# ============================================================

_AGENT_LOOKUP = None
_TIER_LOOKUP_BY_NAME = None
_TIER_LOOKUP_BY_NUMBER = None

_REMOTE_IMAGE_CACHE = {}


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
            f"Warning: could not load font {path}"
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
            23,
        ),

        "rank": load_font(
            FONT_BOLD,
            29,
        ),

        "level": load_font(
            FONT_BOLD,
            26,
        ),

        "upper_label": load_font(
            FONT_BOLD,
            12,
        ),

        "metric_label": load_font(
            FONT_REGULAR,
            12,
        ),

        "metric_value": load_font(
            FONT_BOLD,
            20,
        ),

        "body": load_font(
            FONT_REGULAR,
            13,
        ),

        "body_bold": load_font(
            FONT_BOLD,
            15,
        ),

        "recent_stat_label": load_font(
            FONT_REGULAR,
            11,
        ),

        "recent_stat_value": load_font(
            FONT_BOLD,
            14,
        ),

        "small": load_font(
            FONT_REGULAR,
            11,
        ),

        "small_bold": load_font(
            FONT_BOLD,
            12,
        ),

        "tiny": load_font(
            FONT_REGULAR,
            10,
        ),

        "match_map": load_font(
            FONT_BOLD,
            18,
        ),

        "match_score": load_font(
            FONT_BOLD,
            19,
        ),

        "map": load_font(
            FONT_BOLD,
            15,
        ),

        "map_record": load_font(
            FONT_BOLD,
            13,
        ),

        "map_percent": load_font(
            FONT_BOLD,
            15,
        ),

        "pie": load_font(
            FONT_BOLD,
            16,
        ),
    }


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_get(
    data,
    *keys,
    default=None,
):
    value = data

    for key in keys:

        if not isinstance(
            value,
            dict,
        ):
            return default

        value = value.get(
            key
        )

        if value is None:
            return default

    return value


def text_width(
    draw,
    text,
    font,
):
    box = draw.textbbox(
        (0, 0),
        str(text),
        font=font,
    )

    return (
        box[2]
        - box[0]
    )


def pct(
    numerator,
    denominator,
):
    if not denominator:
        return 0.0

    return (
        numerator
        / denominator
        * 100.0
    )


def to_int(
    value,
    default=0,
):
    try:
        return int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


def to_float(
    value,
    default=0.0,
):
    try:
        return float(
            value
        )

    except (
        TypeError,
        ValueError,
    ):
        return default


# ============================================================
# CONFIG CHECK
# ============================================================

def require_config():
    missing = []

    if not VALORANT_NAME:
        missing.append(
            "VALORANT_NAME"
        )

    if not VALORANT_TAG:
        missing.append(
            "VALORANT_TAG"
        )

    if not HENRIK_API_KEY:
        missing.append(
            "HENRIK_API_KEY"
        )

    if missing:
        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


# ============================================================
# DATETIME
# ============================================================

def parse_datetime(value):
    if value is None:
        return None

    if isinstance(
        value,
        (int, float),
    ):

        timestamp = float(
            value
        )

        if timestamp > 10_000_000_000:
            timestamp /= 1000.0

        try:
            return datetime.fromtimestamp(
                timestamp,
                tz=timezone.utc,
            )

        except (
            OSError,
            ValueError,
        ):
            return None

    text = str(
        value
    ).strip()

    try:

        numeric = float(
            text
        )

        if numeric > 10_000_000_000:
            numeric /= 1000.0

        if numeric > 1_000_000_000:

            return datetime.fromtimestamp(
                numeric,
                tz=timezone.utc,
            )

    except ValueError:
        pass

    try:

        dt = datetime.fromisoformat(
            text.replace(
                "Z",
                "+00:00",
            )
        )

        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except ValueError:

        return None


# ============================================================
# CURRENT ACT
# ============================================================

def fetch_current_act_window():

    print(
        "Resolving current Valorant Act..."
    )

    response = requests.get(
        VALORANT_SEASONS_API,
        timeout=30,
        headers={
            "User-Agent":
                "epaper-dashboard/1.0",
        },
    )

    response.raise_for_status()

    seasons = (
        response.json()
        .get(
            "data",
            [],
        )
    )

    now = datetime.now(
        timezone.utc
    )

    active_acts = []

    for season in seasons:

        display_name = str(
            season.get(
                "displayName",
                "",
            )
        ).strip()

        season_type = str(
            season.get(
                "type",
                "",
            )
        ).strip()

        start = parse_datetime(
            season.get(
                "startTime"
            )
        )

        end = parse_datetime(
            season.get(
                "endTime"
            )
        )

        if (
            start is None
            or end is None
        ):
            continue

        is_act = (
            "act"
            in display_name.lower()
            or
            "act"
            in season_type.lower()
        )

        if not is_act:
            continue

        if (
            start
            <= now
            < end
        ):

            active_acts.append(
                {
                    "uuid":
                        season.get(
                            "uuid"
                        ),

                    "display_name":
                        display_name,

                    "start":
                        start,

                    "end":
                        end,
                }
            )

    if not active_acts:

        raise RuntimeError(
            "Could not determine current Valorant Act."
        )

    active_acts.sort(
        key=lambda x:
            x["start"],
        reverse=True,
    )

    act = active_acts[0]

    print(
        f"Current Act: {act['display_name']}"
    )

    print(
        f"Act UUID: {act['uuid']}"
    )

    print(
        f"Start: {act['start']}"
    )

    print(
        f"End:   {act['end']}"
    )

    return act


# ============================================================
# HENRIK API
# ============================================================

def henrik_get(
    endpoint,
    params=None,
):

    headers = {
        "Authorization":
            HENRIK_API_KEY,

        "User-Agent":
            "epaper-dashboard/1.0",
    }

    for attempt in range(
        MAX_RATE_LIMIT_RETRIES + 1
    ):

        response = requests.get(
            HENRIK_API_BASE
            + endpoint,

            params=params,

            headers=headers,

            timeout=30,
        )

        if (
            response.status_code
            == 401
        ):

            raise RuntimeError(
                "HenrikDev returned 401. "
                "Check HENRIK_API_KEY."
            )

        if (
            response.status_code
            == 429
        ):

            if (
                attempt
                >= MAX_RATE_LIMIT_RETRIES
            ):

                raise RuntimeError(
                    "Henrik rate limit remained active "
                    "after retries."
                )

            retry_after = (
                response.headers.get(
                    "Retry-After"
                )
            )

            wait_time = None

            if retry_after:

                try:
                    wait_time = float(
                        retry_after
                    )

                except ValueError:
                    pass

            if wait_time is None:

                wait_time = (
                    RATE_LIMIT_BASE_WAIT
                    * (
                        attempt + 1
                    )
                )

            print(
                f"Henrik rate limit reached. "
                f"Waiting {wait_time:.0f}s..."
            )

            time.sleep(
                wait_time
            )

            continue

        response.raise_for_status()

        result = response.json()

        if (
            isinstance(
                result,
                dict,
            )
            and
            result.get(
                "status"
            )
            not in (
                None,
                200,
            )
        ):

            raise RuntimeError(
                f"Henrik API error: {result}"
            )

        return result

    raise RuntimeError(
        "Henrik request failed."
    )


# ============================================================
# MMR / RANK
# ============================================================

def fetch_mmr():

    print(
        "Fetching current rank / MMR..."
    )

    name = quote(
        VALORANT_NAME,
        safe="",
    )

    tag = quote(
        VALORANT_TAG,
        safe="",
    )

    endpoint = (
        f"/valorant/v3/mmr/"
        f"{VALORANT_REGION}/"
        f"{PLATFORM}/"
        f"{name}/"
        f"{tag}"
    )

    result = henrik_get(
        endpoint
    )

    return result.get(
        "data",
        {},
    )


def parse_rank(mmr):

    current = (
        mmr.get(
            "current"
        )
        or {}
    )

    tier = (
        current.get(
            "tier"
        )
        or {}
    )

    if isinstance(
        tier,
        dict,
    ):

        rank_name = (
            tier.get(
                "name"
            )
            or "Unrated"
        )

        tier_number = (
            tier.get(
                "id"
            )
            or tier.get(
                "tier"
            )
        )

    else:

        rank_name = (
            str(tier)
            if tier
            else "Unrated"
        )

        tier_number = None

    rr = (
        current.get(
            "rr"
        )
        or current.get(
            "ranking_in_tier"
        )
        or 0
    )

    return (
        rank_name,
        rr,
        tier_number,
    )


# ============================================================
# REMOTE IMAGES
# ============================================================

def fetch_remote_image(url):

    if not url:
        return None

    if (
        url
        in _REMOTE_IMAGE_CACHE
    ):

        return (
            _REMOTE_IMAGE_CACHE[
                url
            ].copy()
        )

    try:

        response = requests.get(
            url,
            timeout=20,
            headers={
                "User-Agent":
                    "epaper-dashboard/1.0",
            },
        )

        response.raise_for_status()

        image = Image.open(
            BytesIO(
                response.content
            )
        ).convert(
            "RGBA"
        )

        _REMOTE_IMAGE_CACHE[
            url
        ] = image

        return image.copy()

    except Exception as e:

        print(
            f"Warning: image download failed: {e}"
        )

        return None


def paste_icon(
    base_image,
    icon,
    box,
):

    if icon is None:
        return

    x0, y0, x1, y1 = box

    max_w = max(
        1,
        x1 - x0,
    )

    max_h = max(
        1,
        y1 - y0,
    )

    icon = icon.copy()

    alpha = (
        icon.getchannel(
            "A"
        )
    )

    gray = (
        icon.convert(
            "L"
        )
    )

    rgba = Image.merge(
        "RGBA",
        (
            gray,
            gray,
            gray,
            alpha,
        ),
    )

    rgba.thumbnail(
        (
            max_w,
            max_h,
        ),
        Image.LANCZOS,
    )

    x = (
        x0
        + (
            max_w
            - rgba.width
        )
        // 2
    )

    y = (
        y0
        + (
            max_h
            - rgba.height
        )
        // 2
    )

    base_image.paste(
        rgba,
        (
            x,
            y,
        ),
        rgba,
    )


# ============================================================
# AGENT LOOKUP
# ============================================================

def load_agent_lookup():

    global _AGENT_LOOKUP

    if (
        _AGENT_LOOKUP
        is not None
    ):

        return _AGENT_LOOKUP

    print(
        "Fetching Valorant agent assets..."
    )

    response = requests.get(
        VALORANT_AGENTS_API,
        timeout=30,
        headers={
            "User-Agent":
                "epaper-dashboard/1.0",
        },
    )

    response.raise_for_status()

    agents = (
        response.json()
        .get(
            "data",
            [],
        )
    )

    lookup = {}

    for agent in agents:

        name = str(
            agent.get(
                "displayName",
                "",
            )
        ).strip()

        uuid = str(
            agent.get(
                "uuid",
                "",
            )
        ).strip()

        icon = (
            agent.get(
                "displayIconSmall"
            )
            or agent.get(
                "displayIcon"
            )
        )

        if name:

            lookup[
                name.lower()
            ] = icon

        if uuid:

            lookup[
                uuid.lower()
            ] = icon

    _AGENT_LOOKUP = lookup

    return lookup


# ============================================================
# RANK LOOKUP
# ============================================================

def load_tier_lookups():

    global _TIER_LOOKUP_BY_NAME
    global _TIER_LOOKUP_BY_NUMBER

    if (
        _TIER_LOOKUP_BY_NAME
        is not None
        and
        _TIER_LOOKUP_BY_NUMBER
        is not None
    ):

        return (
            _TIER_LOOKUP_BY_NAME,
            _TIER_LOOKUP_BY_NUMBER,
        )

    print(
        "Fetching Valorant rank assets..."
    )

    response = requests.get(
        VALORANT_TIERS_API,
        timeout=30,
        headers={
            "User-Agent":
                "epaper-dashboard/1.0",
        },
    )

    response.raise_for_status()

    tier_sets = (
        response.json()
        .get(
            "data",
            [],
        )
    )

    by_name = {}
    by_number = {}

    for tier_set in tier_sets:

        for tier in (
            tier_set.get(
                "tiers",
                [],
            )
        ):

            name = str(
                tier.get(
                    "tierName",
                    "",
                )
            ).strip()

            number = (
                tier.get(
                    "tier"
                )
            )

            icon = (
                tier.get(
                    "smallIcon"
                )
                or tier.get(
                    "largeIcon"
                )
            )

            if not icon:
                continue

            if name:

                by_name[
                    name.lower()
                ] = icon

            if (
                number
                is not None
            ):

                by_number[
                    to_int(
                        number
                    )
                ] = icon

    _TIER_LOOKUP_BY_NAME = (
        by_name
    )

    _TIER_LOOKUP_BY_NUMBER = (
        by_number
    )

    return (
        by_name,
        by_number,
    )


def get_rank_icon_url(
    rank_name,
    rank_number=None,
):

    (
        by_name,
        by_number,
    ) = load_tier_lookups()

    icon = None

    if rank_name:

        icon = (
            by_name.get(
                rank_name.lower()
            )
        )

    if (
        icon is None
        and
        rank_number is not None
    ):

        icon = (
            by_number.get(
                to_int(
                    rank_number
                )
            )
        )

    return icon


# ============================================================
# MATCH BASICS
# ============================================================

def get_match_start_datetime(
    match,
):

    metadata = (
        match.get(
            "metadata",
            {},
        )
        or {}
    )

    candidates = [
        metadata.get(
            "started_at"
        ),

        metadata.get(
            "game_start"
        ),

        metadata.get(
            "game_start_patched"
        ),

        metadata.get(
            "start_time"
        ),

        match.get(
            "started_at"
        ),
    ]

    for value in candidates:

        result = parse_datetime(
            value
        )

        if result is not None:

            return result

    return None


def get_match_id(match):

    metadata = (
        match.get(
            "metadata",
            {},
        )
        or {}
    )

    return (
        metadata.get(
            "match_id"
        )
        or metadata.get(
            "matchid"
        )
        or metadata.get(
            "id"
        )
        or match.get(
            "match_id"
        )
        or match.get(
            "id"
        )
    )


# ============================================================
# PLAYER LOOKUP
# ============================================================

def flatten_players(players):

    if isinstance(
        players,
        list,
    ):

        return players

    result = []

    if isinstance(
        players,
        dict,
    ):

        for value in (
            players.values()
        ):

            if isinstance(
                value,
                list,
            ):

                result.extend(
                    value
                )

            elif isinstance(
                value,
                dict,
            ):

                result.append(
                    value
                )

    return result


def is_target_player(player):

    name = (
        player.get(
            "name"
        )
        or player.get(
            "game_name"
        )
        or safe_get(
            player,
            "account",
            "name",
            default="",
        )
    )

    tag = (
        player.get(
            "tag"
        )
        or player.get(
            "tag_line"
        )
        or safe_get(
            player,
            "account",
            "tag",
            default="",
        )
    )

    return (
        str(name).lower()
        == VALORANT_NAME.lower()

        and

        str(tag).lower()
        == VALORANT_TAG.lower()
    )


def find_player(match):

    players = flatten_players(
        match.get(
            "players",
            [],
        )
    )

    for player in players:

        if is_target_player(
            player
        ):

            return player

    return None


def get_player_puuid(player):

    return (
        player.get(
            "puuid"
        )
        or safe_get(
            player,
            "account",
            "puuid",
            default="",
        )
    )


# ============================================================
# PLAYER STATS
# ============================================================

def get_player_stats(player):

    stats = (
        player.get(
            "stats"
        )
        or {}
    )

    return {
        "kills":
            to_int(
                stats.get(
                    "kills",
                    player.get(
                        "kills",
                        0,
                    ),
                )
            ),

        "deaths":
            to_int(
                stats.get(
                    "deaths",
                    player.get(
                        "deaths",
                        0,
                    ),
                )
            ),

        "assists":
            to_int(
                stats.get(
                    "assists",
                    player.get(
                        "assists",
                        0,
                    ),
                )
            ),

        "score":
            to_int(
                stats.get(
                    "score",
                    player.get(
                        "score",
                        0,
                    ),
                )
            ),

        "headshots":
            to_int(
                stats.get(
                    "headshots",
                    stats.get(
                        "head_shots",
                        player.get(
                            "headshots",
                            0,
                        ),
                    ),
                )
            ),

        "bodyshots":
            to_int(
                stats.get(
                    "bodyshots",
                    stats.get(
                        "body_shots",
                        player.get(
                            "bodyshots",
                            0,
                        ),
                    ),
                )
            ),

        "legshots":
            to_int(
                stats.get(
                    "legshots",
                    stats.get(
                        "leg_shots",
                        player.get(
                            "legshots",
                            0,
                        ),
                    ),
                )
            ),
    }


def get_account_level(player):

    for value in (
        player.get(
            "account_level"
        ),

        player.get(
            "level"
        ),

        safe_get(
            player,
            "account",
            "level",
        ),
    ):

        if value is not None:

            return to_int(
                value
            )

    return 0


# ============================================================
# AGENT
# ============================================================

def get_player_agent(player):

    value = (
        player.get(
            "agent"
        )
        or player.get(
            "character"
        )
        or player.get(
            "character_name"
        )
    )

    if isinstance(
        value,
        dict,
    ):

        return str(
            value.get(
                "name"
            )
            or value.get(
                "displayName"
            )
            or value.get(
                "id"
            )
            or value.get(
                "uuid"
            )
            or ""
        )

    if value:

        return str(
            value
        )

    return ""


# ============================================================
# ROUND STATS
# ============================================================

def get_round_player_stats(
    round_data,
    target_puuid,
):

    entries = (
        round_data.get(
            "stats"
        )
        or round_data.get(
            "player_stats"
        )
        or round_data.get(
            "playerStats"
        )
        or []
    )

    if isinstance(
        entries,
        dict,
    ):

        entries = list(
            entries.values()
        )

    if not isinstance(
        entries,
        list,
    ):

        return None

    for entry in entries:

        if not isinstance(
            entry,
            dict,
        ):

            continue

        puuid = (
            entry.get(
                "puuid"
            )
            or entry.get(
                "subject"
            )
            or entry.get(
                "player_puuid"
            )
            or entry.get(
                "playerPuuid"
            )
            or safe_get(
                entry,
                "player",
                "puuid",
            )
        )

        if (
            str(puuid)
            == str(target_puuid)
        ):

            return entry

    return None


def get_match_damage_from_rounds(
    match,
    target_puuid,
):

    total = 0.0

    rounds = (
        match.get(
            "rounds",
            [],
        )
    )

    if not isinstance(
        rounds,
        list,
    ):

        return 0.0

    for round_data in rounds:

        stats = (
            get_round_player_stats(
                round_data,
                target_puuid,
            )
        )

        if not stats:

            continue

        damage_events = (
            stats.get(
                "damage"
            )
            or stats.get(
                "damage_events"
            )
            or stats.get(
                "damageEvents"
            )
            or []
        )

        if isinstance(
            damage_events,
            dict,
        ):

            damage_events = [
                damage_events
            ]

        for event in damage_events:

            if not isinstance(
                event,
                dict,
            ):

                continue

            total += to_float(
                event.get(
                    "damage"
                )
                or event.get(
                    "amount"
                )
                or event.get(
                    "damage_dealt"
                )
                or 0
            )

    return total


def get_match_hits_from_rounds(
    match,
    target_puuid,
):

    head = 0
    body = 0
    leg = 0

    rounds = (
        match.get(
            "rounds",
            [],
        )
    )

    if not isinstance(
        rounds,
        list,
    ):

        return (
            0,
            0,
            0,
        )

    for round_data in rounds:

        stats = (
            get_round_player_stats(
                round_data,
                target_puuid,
            )
        )

        if not stats:

            continue

        events = (
            stats.get(
                "damage"
            )
            or stats.get(
                "damage_events"
            )
            or []
        )

        if isinstance(
            events,
            dict,
        ):

            events = [
                events
            ]

        for event in events:

            if not isinstance(
                event,
                dict,
            ):

                continue

            head += to_int(
                event.get(
                    "headshots",
                    event.get(
                        "head_shots",
                        0,
                    ),
                )
            )

            body += to_int(
                event.get(
                    "bodyshots",
                    event.get(
                        "body_shots",
                        0,
                    ),
                )
            )

            leg += to_int(
                event.get(
                    "legshots",
                    event.get(
                        "leg_shots",
                        0,
                    ),
                )
            )

    return (
        head,
        body,
        leg,
    )


def match_round_count(match):

    rounds = (
        match.get(
            "rounds"
        )
    )

    if isinstance(
        rounds,
        list,
    ):

        return len(
            rounds
        )

    return to_int(
        safe_get(
            match,
            "metadata",
            "rounds_played",
            default=0,
        )
    )


# ============================================================
# TEAM RESULT
# ============================================================

def player_team_id(player):

    team = (
        player.get(
            "team"
        )
    )

    if isinstance(
        team,
        dict,
    ):

        return (
            team.get(
                "id"
            )
            or team.get(
                "team_id"
            )
            or team.get(
                "name"
            )
        )

    return (
        player.get(
            "team_id"
        )
        or team
    )


def normalize_teams(match):

    teams = (
        match.get(
            "teams",
            [],
        )
    )

    if isinstance(
        teams,
        list,
    ):

        return teams

    result = []

    if isinstance(
        teams,
        dict,
    ):

        for key, value in (
            teams.items()
        ):

            if not isinstance(
                value,
                dict,
            ):

                continue

            item = dict(
                value
            )

            item.setdefault(
                "_team_key",
                key,
            )

            result.append(
                item
            )

    return result


def team_identifier(team):

    return (
        team.get(
            "team_id"
        )
        or team.get(
            "id"
        )
        or team.get(
            "team"
        )
        or team.get(
            "name"
        )
        or team.get(
            "_team_key"
        )
    )


def team_round_score(team):

    if not team:

        return None

    for value in (
        team.get(
            "rounds_won"
        ),

        team.get(
            "roundsWon"
        ),

        team.get(
            "score"
        ),

        safe_get(
            team,
            "rounds",
            "won",
        ),
    ):

        if value is None:

            continue

        try:

            return int(
                value
            )

        except (
            ValueError,
            TypeError,
        ):

            pass

    return None


def determine_match_result(
    match,
    player,
):

    target = str(
        player_team_id(
            player
        )
    ).lower()

    teams = normalize_teams(
        match
    )

    our_team = None
    opponents = []

    for team in teams:

        if (
            str(
                team_identifier(
                    team
                )
            ).lower()
            == target
        ):

            our_team = team

        else:

            opponents.append(
                team
            )

    if our_team is None:

        return None

    opponent = (
        opponents[0]
        if opponents
        else None
    )

    if (
        our_team.get(
            "won"
        )
        is True
    ):

        return "win"

    if (
        opponent
        and opponent.get(
            "won"
        )
        is True
    ):

        return "loss"

    ours = team_round_score(
        our_team
    )

    theirs = team_round_score(
        opponent
    )

    if (
        ours is not None
        and theirs is not None
    ):

        if ours > theirs:

            return "win"

        if ours < theirs:

            return "loss"

        return "draw"

    return None


def get_our_and_enemy_scores(
    match,
    player,
):

    target = str(
        player_team_id(
            player
        )
    ).lower()

    teams = normalize_teams(
        match
    )

    ours = None
    theirs = None

    for team in teams:

        if (
            str(
                team_identifier(
                    team
                )
            ).lower()
            == target
        ):

            ours = team

        else:

            theirs = team

    return (
        team_round_score(
            ours
        ),

        team_round_score(
            theirs
        ),
    )


# ============================================================
# MAP
# ============================================================

def get_map_name(match):

    map_data = (
        safe_get(
            match,
            "metadata",
            "map",
        )
        or match.get(
            "map"
        )
    )

    if isinstance(
        map_data,
        dict,
    ):

        return (
            map_data.get(
                "name"
            )
            or map_data.get(
                "displayName"
            )
            or map_data.get(
                "id"
            )
            or "Unknown"
        )

    return (
        str(
            map_data
        )
        if map_data
        else "Unknown"
    )


# ============================================================
# FETCH ACT MATCHES
# ============================================================

def fetch_act_matches(act):

    name = quote(
        VALORANT_NAME,
        safe="",
    )

    tag = quote(
        VALORANT_TAG,
        safe="",
    )

    endpoint = (
        f"/valorant/v4/matches/"
        f"{VALORANT_REGION}/"
        f"{PLATFORM}/"
        f"{name}/"
        f"{tag}"
    )

    target_matches = []

    seen = set()

    start_index = 0

    found_current_act = False

    print()
    print(
        "Fetching Competitive matches..."
    )

    for _ in range(
        MAX_PAGES
    ):

        print(
            f"Fetching matches "
            f"{start_index}–"
            f"{start_index + PAGE_SIZE - 1}..."
        )

        response = henrik_get(
            endpoint,
            params={
                "mode":
                    "competitive",

                "size":
                    PAGE_SIZE,

                "start":
                    start_index,
            },
        )

        matches = (
            response.get(
                "data",
                [],
            )
        )

        if not matches:

            break

        inside = 0
        older = False

        for match in matches:

            match_id = (
                get_match_id(
                    match
                )
            )

            if (
                match_id
                and match_id in seen
            ):

                continue

            if match_id:

                seen.add(
                    match_id
                )

            start = (
                get_match_start_datetime(
                    match
                )
            )

            if start is None:

                continue

            if (
                act["start"]
                <= start
                < act["end"]
            ):

                target_matches.append(
                    match
                )

                inside += 1
                found_current_act = True

            elif (
                start
                < act["start"]
            ):

                older = True

        print(
            f"  matches inside Act: {inside}"
        )

        if (
            found_current_act
            and older
        ):

            print(
                "Reached matches older than current Act."
            )

            break

        if (
            len(matches)
            < PAGE_SIZE
        ):

            break

        start_index += PAGE_SIZE

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    print()
    print(
        f"Total Act matches: "
        f"{len(target_matches)}"
    )

    return target_matches


# ============================================================
# RECENT MATCHES
# ============================================================

def days_ago_label(
    match_time,
    now_local,
):

    match_local = (
        match_time.astimezone(
            ZoneInfo(
                TIMEZONE
            )
        )
    )

    days = (
        now_local.date()
        - match_local.date()
    ).days

    if days <= 0:

        return "Today"

    if days == 1:

        return "1d ago"

    return f"{days}d ago"


def build_recent_matches(
    matches,
):

    agent_lookup = (
        load_agent_lookup()
    )

    now = datetime.now(
        ZoneInfo(
            TIMEZONE
        )
    )

    result = []

    for match in matches:

        player = find_player(
            match
        )

        if not player:

            continue

        match_time = (
            get_match_start_datetime(
                match
            )
        )

        if not match_time:

            continue

        stats = get_player_stats(
            player
        )

        puuid = get_player_puuid(
            player
        )

        rounds = match_round_count(
            match
        )

        head = stats[
            "headshots"
        ]

        body = stats[
            "bodyshots"
        ]

        leg = stats[
            "legshots"
        ]

        if (
            head
            + body
            + leg
            == 0
        ):

            (
                head,
                body,
                leg,
            ) = get_match_hits_from_rounds(
                match,
                puuid,
            )

        hit_total = (
            head
            + body
            + leg
        )

        kd = (
            stats["kills"]
            / stats["deaths"]
            if stats["deaths"]
            else float(
                stats["kills"]
            )
        )

        acs = (
            stats["score"]
            / rounds
            if rounds
            else 0
        )

        (
            our_score,
            enemy_score,
        ) = get_our_and_enemy_scores(
            match,
            player,
        )

        agent = (
            get_player_agent(
                player
            )
        )

        agent_icon = (
            agent_lookup.get(
                agent.lower()
            )
            if agent
            else None
        )

        result.append(
            {
                "datetime":
                    match_time,

                "days_ago":
                    days_ago_label(
                        match_time,
                        now,
                    ),

                "map":
                    get_map_name(
                        match
                    ),

                "our_score":
                    our_score,

                "enemy_score":
                    enemy_score,

                "kills":
                    stats[
                        "kills"
                    ],

                "deaths":
                    stats[
                        "deaths"
                    ],

                "assists":
                    stats[
                        "assists"
                    ],

                "kd":
                    kd,

                "hs":
                    pct(
                        head,
                        hit_total,
                    ),

                "acs":
                    acs,

                "agent_icon":
                    agent_icon,
            }
        )

    result.sort(
        key=lambda x:
            x["datetime"],
        reverse=True,
    )

    return result[:3]


# ============================================================
# ACT AGGREGATION
# ============================================================

def map_win_rate(stats):

    total = (
        stats["wins"]
        + stats["losses"]
        + stats["draws"]
    )

    return pct(
        stats["wins"],
        total,
    )


def aggregate_matches(matches):

    kills = 0
    deaths = 0

    damage = 0
    rounds = 0

    head = 0
    body = 0
    leg = 0

    wins = 0
    losses = 0
    draws = 0

    level = 0

    maps = defaultdict(
        lambda: {
            "wins": 0,
            "losses": 0,
            "draws": 0,
        }
    )

    valid = 0

    for match in matches:

        player = find_player(
            match
        )

        if not player:

            continue

        valid += 1

        puuid = (
            get_player_puuid(
                player
            )
        )

        level = max(
            level,
            get_account_level(
                player
            ),
        )

        stats = (
            get_player_stats(
                player
            )
        )

        kills += stats[
            "kills"
        ]

        deaths += stats[
            "deaths"
        ]

        mh = stats[
            "headshots"
        ]

        mb = stats[
            "bodyshots"
        ]

        ml = stats[
            "legshots"
        ]

        if (
            mh
            + mb
            + ml
            == 0
        ):

            (
                mh,
                mb,
                ml,
            ) = get_match_hits_from_rounds(
                match,
                puuid,
            )

        head += mh
        body += mb
        leg += ml

        damage += (
            get_match_damage_from_rounds(
                match,
                puuid,
            )
        )

        rounds += (
            match_round_count(
                match
            )
        )

        map_name = (
            get_map_name(
                match
            )
        )

        result = (
            determine_match_result(
                match,
                player,
            )
        )

        if result == "win":

            wins += 1
            maps[
                map_name
            ]["wins"] += 1

        elif result == "loss":

            losses += 1
            maps[
                map_name
            ]["losses"] += 1

        elif result == "draw":

            draws += 1
            maps[
                map_name
            ]["draws"] += 1

    total_hits = (
        head
        + body
        + leg
    )

    total_games = (
        wins
        + losses
        + draws
    )

    sorted_maps = sorted(
        maps.items(),

        key=lambda x: (
            map_win_rate(
                x[1]
            ),

            (
                x[1]["wins"]
                + x[1]["losses"]
                + x[1]["draws"]
            ),
        ),

        reverse=True,
    )

    return {
        "matches":
            valid,

        "level":
            level,

        "wins":
            wins,

        "losses":
            losses,

        "draws":
            draws,

        "kd":
            (
                kills
                / deaths
                if deaths
                else float(
                    kills
                )
            ),

        "damage_round":
            (
                damage
                / rounds
                if rounds
                else 0
            ),

        "hs":
            pct(
                head,
                total_hits,
            ),

        "win_rate":
            pct(
                wins,
                total_games,
            ),

        "maps":
            sorted_maps[:7],

        "recent":
            build_recent_matches(
                matches
            ),
    }


# ============================================================
# DRAW PIE
# ============================================================

def draw_record_pie(
    draw,
    cx,
    cy,
    radius,
    wins,
    losses,
    fonts,
):

    box = (
        cx - radius,
        cy - radius,
        cx + radius,
        cy + radius,
    )

    decisive = (
        wins
        + losses
    )

    if decisive:

        win_angle = (
            360.0
            * wins
            / decisive
        )

    else:

        win_angle = 0

    start = -90

    draw.pieslice(
        box,
        start=start,
        end=start + win_angle,
        fill=PIE_WIN,
    )

    draw.pieslice(
        box,
        start=start + win_angle,
        end=start + 360,
        fill=PIE_LOSS,
    )

    inner = int(
        radius * 0.64
    )

    draw.ellipse(
        (
            cx - inner,
            cy - inner,
            cx + inner,
            cy + inner,
        ),
        fill=WHITE,
    )

    win_text = (
        f"{wins} W"
    )

    loss_text = (
        f"{losses} L"
    )

    width = text_width(
        draw,
        win_text,
        fonts[
            "pie"
        ],
    )

    draw.text(
        (
            cx - width / 2,
            cy - 19,
        ),
        win_text,
        font=fonts[
            "pie"
        ],
        fill=BLACK,
    )

    width = text_width(
        draw,
        loss_text,
        fonts[
            "pie"
        ],
    )

    draw.text(
        (
            cx - width / 2,
            cy + 3,
        ),
        loss_text,
        font=fonts[
            "pie"
        ],
        fill=BLACK,
    )


# ============================================================
# DRAW METRIC
# ============================================================

def draw_metric(
    draw,
    x,
    y,
    width,
    height,
    label,
    value,
    fonts,
):

    draw.rounded_rectangle(
        (
            x,
            y,
            x + width,
            y + height,
        ),
        radius=8,
        outline=GRAY_LIGHT,
        width=1,
    )

    draw.text(
        (
            x + 10,
            y + 7,
        ),
        label,
        font=fonts[
            "metric_label"
        ],
        fill=GRAY_DARK,
    )

    draw.text(
        (
            x + 10,
            y + 26,
        ),
        value,
        font=fonts[
            "metric_value"
        ],
        fill=BLACK,
    )


# ============================================================
# DRAW RECENT MATCH
# ============================================================

def draw_recent_match_row(
    image,
    draw,
    x,
    y,
    width,
    row,
    fonts,
):

    height = 74

    draw.rounded_rectangle(
        (
            x,
            y,
            x + width,
            y + height,
        ),
        radius=10,
        outline=GRAY_LIGHT,
        width=1,
    )

    # Agent portrait
    agent_image = (
        fetch_remote_image(
            row[
                "agent_icon"
            ]
        )
    )

    paste_icon(
        image,
        agent_image,
        (
            x + 6,
            y + 5,
            x + 62,
            y + 69,
        ),
    )

    # Age
    draw.text(
        (
            x + 68,
            y + 7,
        ),
        row[
            "days_ago"
        ],
        font=fonts[
            "small"
        ],
        fill=GRAY_DARK,
    )

    # Map
    draw.text(
        (
            x + 68,
            y + 27,
        ),
        row[
            "map"
        ],
        font=fonts[
            "match_map"
        ],
        fill=BLACK,
    )

    # Score
    if (
        row[
            "our_score"
        ]
        is None
        or
        row[
            "enemy_score"
        ]
        is None
    ):

        score = "?:?"

    else:

        score = (
            f"{row['our_score']}:"
            f"{row['enemy_score']}"
        )

    draw.text(
        (
            x + 165,
            y + 27,
        ),
        score,
        font=fonts[
            "match_score"
        ],
        fill=BLACK,
    )

    label_y = (
        y + 8
    )

    value_y = (
        y + 37
    )

    # K/D
    col = (
        x + 275
    )

    draw.text(
        (
            col,
            label_y,
        ),
        "K/D",
        font=fonts[
            "recent_stat_label"
        ],
        fill=GRAY_DARK,
    )

    draw.text(
        (
            col,
            value_y,
        ),
        f"{row['kd']:.1f}",
        font=fonts[
            "recent_stat_value"
        ],
        fill=BLACK,
    )

    # K/D/A
    col = (
        x + 330
    )

    draw.text(
        (
            col,
            label_y,
        ),
        "K/D/A",
        font=fonts[
            "recent_stat_label"
        ],
        fill=GRAY_DARK,
    )

    draw.text(
        (
            col,
            value_y,
        ),
        (
            f"{row['kills']}/"
            f"{row['deaths']}/"
            f"{row['assists']}"
        ),
        font=fonts[
            "recent_stat_value"
        ],
        fill=BLACK,
    )

    # HS
    col = (
        x + 425
    )

    draw.text(
        (
            col,
            label_y,
        ),
        "HS%",
        font=fonts[
            "recent_stat_label"
        ],
        fill=GRAY_DARK,
    )

    draw.text(
        (
            col,
            value_y,
        ),
        f"{row['hs']:.0f}",
        font=fonts[
            "recent_stat_value"
        ],
        fill=BLACK,
    )

    # ACS
    col = (
        x + 485
    )

    draw.text(
        (
            col,
            label_y,
        ),
        "ACS",
        font=fonts[
            "recent_stat_label"
        ],
        fill=GRAY_DARK,
    )

    draw.text(
        (
            col,
            value_y,
        ),
        f"{row['acs']:.0f}",
        font=fonts[
            "recent_stat_value"
        ],
        fill=BLACK,
    )


# ============================================================
# MAIN RENDER
# ============================================================

def build_image(
    stats,
    rank_name,
    rank_number,
    act,
):

    image = Image.new(
        "L",
        (
            WIDTH,
            HEIGHT,
        ),
        WHITE,
    )

    draw = ImageDraw.Draw(
        image
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
            10,
        ),
        "VALORANT STATS",
        font=fonts[
            "title"
        ],
        fill=BLACK,
    )

    draw.text(
        (
            20,
            47,
        ),
        (
            f"{VALORANT_NAME}"
            f"#{VALORANT_TAG}"
            f"  •  "
            f"{act['display_name']}"
        ),
        font=fonts[
            "body"
        ],
        fill=GRAY_DARK,
    )

    date_text = now.strftime(
        "%d %b %Y  %H:%M"
    )

    date_width = text_width(
        draw,
        date_text,
        fonts[
            "small"
        ],
    )

    draw.text(
        (
            940 - date_width,
            25,
        ),
        date_text,
        font=fonts[
            "small"
        ],
        fill=GRAY_DARK,
    )

    draw.line(
        (
            20,
            70,
            940,
            70,
        ),
        fill=GRAY_LIGHT,
        width=2,
    )


    # ========================================================
    # UPPER LEFT — LARGE RANK ICON
    # ========================================================

    upper_top = 80
    upper_bottom = 222

    rank_icon_url = (
        get_rank_icon_url(
            rank_name,
            rank_number,
        )
    )

    rank_icon = (
        fetch_remote_image(
            rank_icon_url
        )
    )

    # Large vertical rank icon.
    paste_icon(
        image,
        rank_icon,
        (
            20,
            upper_top + 4,
            118,
            upper_bottom - 4,
        ),
    )


    # ========================================================
    # RATING + LEVEL STACKED
    # ========================================================

    text_x = 128

    draw.text(
        (
            text_x,
            88,
        ),
        "RATING",
        font=fonts[
            "upper_label"
        ],
        fill=GRAY_DARK,
    )

    draw.text(
        (
            text_x,
            108,
        ),
        rank_name,
        font=fonts[
            "rank"
        ],
        fill=BLACK,
    )

    draw.text(
        (
            text_x,
            156,
        ),
        "LEVEL",
        font=fonts[
            "upper_label"
        ],
        fill=GRAY_DARK,
    )

    draw.text(
        (
            text_x,
            175,
        ),
        str(
            stats[
                "level"
            ]
        ),
        font=fonts[
            "level"
        ],
        fill=BLACK,
    )


    # ========================================================
    # LARGE RECORD DONUT — NO LABEL, NO DRAW COUNT
    # ========================================================

    draw_record_pie(
        draw,
        cx=500,
        cy=150,
        radius=52,
        wins=stats[
            "wins"
        ],
        losses=stats[
            "losses"
        ],
        fonts=fonts,
    )


    # ========================================================
    # RIGHT — 2x2 METRIC GRID
    # ========================================================

    grid_x = 625
    grid_y = 84

    card_w = 150
    card_h = 61

    gap_x = 12
    gap_y = 10

    draw_metric(
        draw,
        grid_x,
        grid_y,
        card_w,
        card_h,
        "Damage / Round",
        f"{stats['damage_round']:.1f}",
        fonts,
    )

    draw_metric(
        draw,
        grid_x
        + card_w
        + gap_x,
        grid_y,
        card_w,
        card_h,
        "K/D Ratio",
        f"{stats['kd']:.2f}",
        fonts,
    )

    draw_metric(
        draw,
        grid_x,
        grid_y
        + card_h
        + gap_y,
        card_w,
        card_h,
        "Headshot %",
        f"{stats['hs']:.1f}%",
        fonts,
    )

    draw_metric(
        draw,
        grid_x
        + card_w
        + gap_x,
        grid_y
        + card_h
        + gap_y,
        card_w,
        card_h,
        "Win %",
        f"{stats['win_rate']:.1f}%",
        fonts,
    )


    # ========================================================
    # LOWER SECTION
    # ========================================================

    # Moved down compared with previous version.
    section_y = 245

    # Wider Recent Matches section.
    recent_x0 = 20
    recent_x1 = 575

    # Narrower Maps section, moved right.
    maps_x0 = 600
    maps_x1 = 940

    draw.text(
        (
            recent_x0,
            section_y,
        ),
        "RECENT MATCHES",
        font=fonts[
            "section"
        ],
        fill=BLACK,
    )

    draw.text(
        (
            maps_x0,
            section_y,
        ),
        "MAPS",
        font=fonts[
            "section"
        ],
        fill=BLACK,
    )

    divider_y = (
        section_y + 29
    )

    draw.line(
        (
            recent_x0,
            divider_y,
            recent_x1,
            divider_y,
        ),
        fill=GRAY_LIGHT,
        width=1,
    )

    draw.line(
        (
            maps_x0,
            divider_y,
            maps_x1,
            divider_y,
        ),
        fill=GRAY_LIGHT,
        width=1,
    )


    # ========================================================
    # RECENT MATCHES
    # ========================================================

    recent_top = (
        divider_y + 10
    )

    row_h = 74
    row_gap = 8

    recent_width = (
        recent_x1
        - recent_x0
    )

    for index, row in enumerate(
        stats[
            "recent"
        ]
    ):

        y = (
            recent_top
            + index
            * (
                row_h
                + row_gap
            )
        )

        draw_recent_match_row(
            image,
            draw,
            recent_x0,
            y,
            recent_width,
            row,
            fonts,
        )

    recent_bottom = (
        recent_top
        + (
            3
            * row_h
        )
        + (
            2
            * row_gap
        )
    )


    # ========================================================
    # MAPS
    # ========================================================

    map_top = recent_top
    map_bottom = recent_bottom

    map_count = len(
        stats[
            "maps"
        ]
    )

    if map_count:

        map_row_height = (
            (
                map_bottom
                - map_top
            )
            / map_count
        )

        # Three logical columns:
        # map name | record | win %
        map_name_x = (
            maps_x0 + 10
        )

        record_center_x = (
            maps_x0 + 190
        )

        percent_right_x = (
            maps_x1 - 10
        )

        for index, (
            map_name,
            map_stats,
        ) in enumerate(
            stats[
                "maps"
            ]
        ):

            row_top = int(
                round(
                    map_top
                    + index
                    * map_row_height
                )
            )

            row_bottom = int(
                round(
                    map_top
                    + (
                        index + 1
                    )
                    * map_row_height
                )
            )

            played = (
                map_stats[
                    "wins"
                ]
                + map_stats[
                    "losses"
                ]
                + map_stats[
                    "draws"
                ]
            )

            win_rate = pct(
                map_stats[
                    "wins"
                ],
                played,
            )

            center_y = (
                row_top
                + (
                    row_bottom
                    - row_top
                )
                // 2
            )

            # ------------------------------------------------
            # MAP NAME
            # ------------------------------------------------

            map_box = draw.textbbox(
                (
                    0,
                    0,
                ),
                map_name,
                font=fonts[
                    "map"
                ],
            )

            map_h = (
                map_box[3]
                - map_box[1]
            )

            draw.text(
                (
                    map_name_x,
                    center_y
                    - map_h / 2
                    - map_box[1],
                ),
                map_name,
                font=fonts[
                    "map"
                ],
                fill=BLACK,
            )


            # ------------------------------------------------
            # WIN / LOSS / DRAW IN MIDDLE
            # ------------------------------------------------

            record = (
                f"{map_stats['wins']}W-"
                f"{map_stats['losses']}L"
            )

            if (
                map_stats[
                    "draws"
                ]
                > 0
            ):

                record += (
                    f"-"
                    f"{map_stats['draws']}D"
                )

            record_w = (
                text_width(
                    draw,
                    record,
                    fonts[
                        "map_record"
                    ],
                )
            )

            record_box = draw.textbbox(
                (
                    0,
                    0,
                ),
                record,
                font=fonts[
                    "map_record"
                ],
            )

            record_h = (
                record_box[3]
                - record_box[1]
            )

            draw.text(
                (
                    record_center_x
                    - record_w / 2,

                    center_y
                    - record_h / 2
                    - record_box[1],
                ),
                record,
                font=fonts[
                    "map_record"
                ],
                fill=GRAY_DARK,
            )


            # ------------------------------------------------
            # WIN % RIGHT
            # ------------------------------------------------

            percent_text = (
                f"{win_rate:.1f}%"
            )

            percent_w = (
                text_width(
                    draw,
                    percent_text,
                    fonts[
                        "map_percent"
                    ],
                )
            )

            percent_box = draw.textbbox(
                (
                    0,
                    0,
                ),
                percent_text,
                font=fonts[
                    "map_percent"
                ],
            )

            percent_h = (
                percent_box[3]
                - percent_box[1]
            )

            draw.text(
                (
                    percent_right_x
                    - percent_w,

                    center_y
                    - percent_h / 2
                    - percent_box[1],
                ),
                percent_text,
                font=fonts[
                    "map_percent"
                ],
                fill=BLACK,
            )


            # ------------------------------------------------
            # ROW DIVIDER
            # ------------------------------------------------

            if (
                index
                < map_count - 1
            ):

                draw.line(
                    (
                        maps_x0,
                        row_bottom - 1,
                        maps_x1,
                        row_bottom - 1,
                    ),
                    fill=GRAY_VERY_LIGHT,
                    width=1,
                )

    return image


# ============================================================
# SAVE
# ============================================================

def save_bmp(
    image,
    path,
):

    directory = os.path.dirname(
        path
    )

    if directory:

        os.makedirs(
            directory,
            exist_ok=True,
        )

    image.save(
        path,
        format="BMP",
    )


# ============================================================
# MAIN
# ============================================================

def main():

    require_config()

    print()
    print(
        "=============================="
    )
    print(
        "VALORANT ACT DASHBOARD"
    )
    print(
        "=============================="
    )
    print()

    act = (
        fetch_current_act_window()
    )

    print()

    mmr = (
        fetch_mmr()
    )

    (
        rank_name,
        rr,
        rank_number,
    ) = parse_rank(
        mmr
    )

    time.sleep(
        REQUEST_DELAY_SECONDS
    )

    matches = (
        fetch_act_matches(
            act
        )
    )

    if not matches:

        raise RuntimeError(
            "No competitive matches found "
            "for current Act."
        )

    print()
    print(
        "Aggregating statistics..."
    )

    stats = (
        aggregate_matches(
            matches
        )
    )

    print()
    print(
        f"Rank: {rank_name}"
    )

    print(
        f"Level: {stats['level']}"
    )

    print(
        f"Record: "
        f"{stats['wins']}W "
        f"{stats['losses']}L "
        f"{stats['draws']}D"
    )

    print(
        f"Damage/Round: "
        f"{stats['damage_round']:.1f}"
    )

    print(
        f"K/D: "
        f"{stats['kd']:.2f}"
    )

    print(
        f"HS%: "
        f"{stats['hs']:.1f}%"
    )

    print(
        f"Win%: "
        f"{stats['win_rate']:.1f}%"
    )

    print()
    print(
        "Rendering dashboard..."
    )

    image = (
        build_image(
            stats,
            rank_name,
            rank_number,
            act,
        )
    )

    save_bmp(
        image,
        OUTPUT_PATH,
    )

    print(
        f"Saved {OUTPUT_PATH} "
        f"({WIDTH}x{HEIGHT})"
    )


if __name__ == "__main__":

    main()