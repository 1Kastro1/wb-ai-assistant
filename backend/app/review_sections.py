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


async def wb_review_context(item, get_linked, *, allow_incomplete=False):
    """A WB addition may be a separate review with its own identifier."""
    def text(data):
        return mask_vin('\n'.join(str(data.get(field) or '').strip()
            for field in ('text', 'pros', 'cons') if str(data.get(field) or '').strip()))

    sections = review_sections(item, 'wb')
    primary = text(item)
    parent, child = item.get('parentFeedbackId'), item.get('childFeedbackId')
    linked_id = parent or child
    if not linked_id or str(linked_id) == str(item.get('id')):
        return primary, sections
    try:
        linked = await get_linked(str(linked_id))
    except ValueError:
        if not allow_incomplete:
            raise
        return primary, {**sections, '_context_pending': str(linked_id)}
    original, supplement = (linked, item) if parent else (item, linked)
    later = text(supplement)
    sections = review_sections(original, 'wb')
    if later:
        sections['supplement'] = later
    return '\n'.join(filter(None, (text(original), 'Дополнение покупателя: ' + later if later else ''))), sections
