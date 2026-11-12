"""Personal decisions are separate from recoverable verification outages."""
from sqlalchemy import and_, or_, func, select
from .db import Review, Draft, Publication


def automatically_checked():
    """Low stars alone need no personal decision after the explicit audit."""
    return func.coalesce(and_(Draft.quality_passed == True,
        Draft.quality['automatic_audit']['input_key'].as_string().is_not(None),
        Draft.quality['owner_rules']['checked'].as_boolean() == True,
        Draft.quality['owner_rules']['passed'].as_boolean() == True,
        func.coalesce(Draft.quality['owner_rules']['technical_error'].as_boolean(), False) == False), False)


def personal_condition(schedule):
    pending = func.coalesce(Draft.quality_pending, False)
    low_requires_person = not (schedule.get('enabled') and schedule.get('publication_enabled') and
        schedule.get('publication_low_ratings') and schedule.get('publication_all_low_ratings'))
    if not low_requires_person:
        allowed=[p for p in ('wb','yandex','ozon') if schedule.get('publication_markets',{}).get(p,True)]
        low_requires_person=~Review.marketplace.in_(allowed)
    return func.coalesce(or_(Review.manual == True, Review.safety_critical == True, Review.risk != 'NORMAL',
        Review.id.in_(select(Publication.review_id).where(Publication.status.in_(('uncertain','mismatch')))),
        and_(~pending, or_(Draft.quality_attention == True,
            and_(Review.status == 'manual_review', func.coalesce(Draft.quality_passed, False) == False))),
        and_(low_requires_person, ~Review.marketplace.like('%_question'), Review.rating.between(1,3), ~automatically_checked())),False)


def automatic_publication(schedule, provider, question=False, rating=5):
    return bool(schedule.get('enabled') and schedule.get('publication_enabled') and
        schedule.get('publication_markets',{}).get(provider,True) and
        (schedule.get('publication_questions',True) if question else
         schedule.get('publication_reviews',True) if rating>=4 else
         schedule.get('publication_low_ratings',False) and schedule.get('publication_all_low_ratings',False)))
