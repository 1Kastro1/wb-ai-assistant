"""Product photos received from marketplace card data."""
from urllib.parse import urlsplit


def card_image_url(card):
    photos = card.get('photos') or []
    if not isinstance(photos, list):
        return ''
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        for key in ('c246x328', 'square', 'c516x688', 'big'):
            url = photo.get(key)
            if not isinstance(url, str) or len(url) > 2000:
                continue
            try:
                parsed = urlsplit(url)
                if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
                    return url
            except ValueError:
                continue
    return ''
