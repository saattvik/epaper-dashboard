import requests
from io import BytesIO
from PIL import Image


OPENF1_BASE = "https://api.openf1.org/v1"

# Change this if needed
DRIVER_ACRONYM = "ANT"


def fetch_json(url, params=None):
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def download_image(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return Image.open(BytesIO(r.content)).convert("RGBA")


def trim_transparent(img):
    if img.mode != "RGBA":
        return img

    alpha = img.getchannel("A")
    bbox = alpha.getbbox()

    if bbox:
        return img.crop(bbox)

    return img


def get_driver(acronym):
    drivers = fetch_json(
        f"{OPENF1_BASE}/drivers",
        params={"session_key": "latest"}
    )

    acronym = acronym.upper()

    for driver in drivers:
        if (driver.get("name_acronym") or "").upper() == acronym:
            return driver

    return None


def make_variant_urls(base_url):
    variants = []

    if ".transform/1col/image.png" in base_url:
        for col in ["1col", "2col", "3col", "4col", "6col", "8col", "12col"]:
            variants.append(
                base_url.replace(
                    ".transform/1col/image.png",
                    f".transform/{col}/image.png"
                )
            )
    else:
        variants.append(base_url)

    # Remove duplicates while keeping order
    seen = set()
    unique = []
    for url in variants:
        if url not in seen:
            unique.append(url)
            seen.add(url)

    return unique


def main():
    print("Fetching latest OpenF1 driver data...")

    driver = get_driver(DRIVER_ACRONYM)

    if not driver:
        print(f"Could not find driver: {DRIVER_ACRONYM}")
        return

    print()
    print("Driver found:")
    print("Name:", driver.get("full_name"))
    print("Driver number:", driver.get("driver_number"))
    print("Acronym:", driver.get("name_acronym"))
    print("Team:", driver.get("team_name"))

    headshot_url = driver.get("headshot_url")

    if not headshot_url:
        print("No headshot URL available.")
        return

    print()
    print("Original headshot URL:")
    print(headshot_url)

    # Download original URL version
    print()
    print("Downloading original OpenF1/F1 image...")
    original_img = download_image(headshot_url)
    print("Original image resolution:", original_img.size)
    original_img.save("driver_original.png")
    print("Saved: driver_original.png")

    print()
    print("Trying higher-resolution transform variants...")

    best_img = None
    best_url = None
    best_area = -1

    variant_urls = make_variant_urls(headshot_url)

    for url in variant_urls:
        try:
            img = download_image(url)
            w, h = img.size
            area = w * h

            print(f"OK   {url}")
            print(f"     resolution = {w} x {h}")

            img_name = url.split(".transform/")[-1].replace("/", "_")
            img.save(f"test_{img_name}.png")

            if area > best_area:
                best_area = area
                best_img = img
                best_url = url

        except Exception as e:
            print(f"FAIL {url}")
            print(f"     {e}")

    if best_img is None:
        print()
        print("No valid image variants found.")
        return

    print()
    print("Best image variant:")
    print(best_url)
    print("Best image resolution:", best_img.size)

    best_img.save("driver_best.png")
    print("Saved: driver_best.png")

    trimmed = trim_transparent(best_img)
    print("Trimmed best image resolution:", trimmed.size)
    trimmed.save("driver_best_trimmed.png")
    print("Saved: driver_best_trimmed.png")

    print()
    print("Done.")


if __name__ == "__main__":
    main()