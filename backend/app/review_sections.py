"""Keep marketplace-provided field boundaries; never guess them from flat text."""
from .safety import mask_vin


def review_sections(source, marketplace):
    fields = {
        'wb': ('pros', 'cons', 'text'),
        'ozon': ('positive', 'negative', 'text'),
        'yandex': ('advantages', 'disadvantages', 'comment'),
    }[marketplace]
    if not isinstance(source, dict) or not any(key in source for key in fields):
        return {}
    return {name: mask_vin(str(source.get(key) or '').strip())
            for name, key in zip(('advantages', 'disadvantages', 'comment'), fields)}
