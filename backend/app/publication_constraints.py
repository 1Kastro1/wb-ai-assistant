"""Marketplace limits, separate from reply quality and rule verification."""
OZON_EMPTY_REVIEW = 'Ozon не принимает ответы на оценки без текста. Черновик сохранён; текстовые отзывы отправляются дальше.'


def publication_restriction(review):
    if review.marketplace == 'ozon' and not (review.text or '').strip():
        return OZON_EMPTY_REVIEW
    return ''
