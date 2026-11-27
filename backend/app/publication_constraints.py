"""Marketplace limits, separate from reply quality and rule verification."""
OZON_EMPTY_REVIEW = 'Ozon не принимает ответы на оценки без текста. Черновик сохранён; текстовые отзывы отправляются дальше.'
SELLER_COMMENT_UNKNOWN = 'Яндекс Маркет сообщил о комментариях. Проверяем, есть ли среди них ответ продавца.'


def seller_comment_unknown(review):
    return (review.marketplace == 'yandex'
            and (getattr(review, 'sections', None) or {}).get('_seller_reply_unverified') is True)


def publication_restriction(review):
    if seller_comment_unknown(review):
        return SELLER_COMMENT_UNKNOWN
    if review.marketplace == 'ozon' and not (review.text or '').strip():
        return OZON_EMPTY_REVIEW
    return ''
