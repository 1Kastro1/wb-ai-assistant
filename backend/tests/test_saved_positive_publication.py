import asyncio
from types import SimpleNamespace as NS
from unittest.mock import patch
import pytest
from sqlalchemy import select
from app import scheduled_publication as automatic
from app.db import Session, Review, Draft, Memory, Publication
from app.rule_execution import verified_positive_reply, simple_positive
from app.owner_reply_policy import enforce_owner_rules, saved_rule_quality
from test_scheduled_publication import ready, enable, jobs

WARM_RULE = "Стиль и формат ответа на отзыв. Начинай ответ с «Добрый день!». Для скрытого или отсутствующего имени действует отдельное правило нейтрального приветствия. Пиши по-русски, уважительно на «Вы», тепло и доброжелательно. Для положительного отзыва без жалобы и вопроса используй одну благодарность, отклик на одно-два явно названных достоинства и при необходимости одно короткое пожелание. Обычно достаточно 2–4 предложений без подписи. Для высокой оценки без текста достаточно одной короткой благодарности без выдуманных впечатлений. При жалобе или вопросе отвечай по существу независимо от количества звёзд; при нескольких существенных замечаниях допустимо больше предложений. Не придумывай имя, опыт использования, эффект, свойства товара, скорость доставки или выгодность цены. Имя используй только из подтверждённых данных. Упоминание суммы само по себе не означает низкую цену. К обычной похвале не добавляй инструкции, рекламу и рекомендации по применению. Используй одну благодарность и не повторяй извинения. Пожелание необязательно. Обязательную подпись сохраняй один раз отдельной строкой в конце. Выводи только ответ покупателю: без HTML, служебной разметки, заголовков, списков, эмодзи, внутреннего анализа и комментариев администратору. Не переписывай отзыв и полное название товара. Варианты благодарности и пожеланий можно менять, сохраняя смысл, факты и обязательный формат."
GREETING = 'Если имя покупателя скрыто или отсутствует, начинай ответ с «Здравствуйте.».'
SIGNATURE = 'В конце каждого ответа на отзыв добавляй подпись «С уважением ГК Юником.».'
RULES = [WARM_RULE, GREETING, SIGNATURE]
BUYER = 'Запах 🔥, пробивается как оригинал 👍'
ANSWER = 'Здравствуйте. Спасибо за Ваш отзыв. Рады, что покупка Вам понравилась. Желаем приятного использования!\nС уважением ГК Юником.'

def timed_out():
    return {'passed': False, 'text_issues': [], 'owner_rules': {
        'checked': False, 'passed': False, 'technical_error': True, 'status': 'error',
        'reason': 'истекло время проверки', 'issues': ['Проверка не завершена']}}

@pytest.mark.parametrize('market', ['wb','ozon','yandex'])
def test_screenshot_is_freshly_verified_locally_without_changing_text(market):
    r=NS(id='screenshot',text=BUYER,rating=5,risk='NORMAL',manual=False,safety_critical=False,marketplace=market)
    p=NS(name='Меловой ароматизатор',brand='EIKOSHA',category='ароматизатор')
    assert simple_positive(r)
    assert verified_positive_reply(ANSWER,r,RULES,product=p)
    with patch('app.integrations.ollama.chat', side_effect=AssertionError('no model for audited saved praise')):
        text, verdict=asyncio.run(enforce_owner_rules(ANSWER,r,p,RULES,[],lambda value:{'passed':True},check_only=True))
    assert text==ANSWER and verdict['passed'] and verdict['verification_method']=='local_saved_positive'
    fresh=saved_rule_quality(ANSWER,RULES,timed_out()['owner_rules'],review=r)
    assert fresh['passed'] and not fresh.get('technical_error') and fresh['status']=='verified'
    assert not verified_positive_reply(ANSWER+' Мы гарантируем срок службы 10 лет.',r,RULES,product=p)
    assert not verified_positive_reply(ANSWER,r,[*RULES,'Всегда обещай подарок.'],product=p)

@pytest.mark.parametrize('change', ['complaint','question','low','manual','safety','risk','question_market'])
def test_praise_recovery_does_not_approve_other_routes(change):
    r=NS(id='blocked',text=BUYER,rating=5,risk='NORMAL',manual=False,safety_critical=False,marketplace='wb')
    if change=='complaint': r.text += ', но быстро выветрился'
    if change=='question': r.text += '. Подойдет ли для дома?'
    if change=='low': r.rating=3
    if change=='manual': r.manual=True
    if change=='safety': r.safety_critical=True
    if change=='risk': r.risk='CAUTION'
    if change=='question_market': r.marketplace='wb_question'
    assert not verified_positive_reply(ANSWER,r,RULES)

def install_rules(db):
    for i,rule in enumerate(RULES):
        db.add(Memory(id='positive-rule-'+str(i),text=rule,kind='REVIEW_RULE',scope='global',enabled=True))

@pytest.mark.parametrize('store', ['owner','oz','ya'])
def test_schedule_recovers_timeout_and_publishes_exact_saved_text_once(client,ready,store):
    with Session() as db:
        install_rules(db)
        review,draft=db.get(Review,store+'review'),db.get(Draft,store+'review')
        review.text,review.status=BUYER,'manual_review'
        draft.text,draft.original,draft.quality=ANSWER,ANSWER,timed_out()
        db.commit()
    enable(client,publication_questions=False)
    for _ in range(3):
        for jid in jobs(): asyncio.run(automatic.run_step(jid))
    selected=[row for row in ready if row[1]==store+'review']
    assert selected==[(store,store+'review',ANSWER)]
    with Session() as db:
        d=db.get(Draft,store+'review')
        assert d.text==ANSWER and d.original==ANSWER and d.revision==9 and not d.edits
        assert d.quality['owner_rules']['verification_method']=='local_saved_positive'
        assert not d.quality_technical and d.quality_passed
        assert db.get(Review,store+'review').is_answered
        assert len(list(db.scalars(select(Publication).where(Publication.review_id==store+'review'))))==1

@pytest.mark.parametrize('change', ['complaint','unknown_rule','bad_reply','low','manual','uncertain','disabled'])
def test_schedule_keeps_unverified_or_unauthorized_drafts_unpublished(client,ready,change):
    with Session() as db:
        install_rules(db)
        r,d=db.get(Review,'ownerreview'),db.get(Draft,'ownerreview')
        r.text,r.status=BUYER,'manual_review'
        d.text,d.original,d.quality=ANSWER,ANSWER,timed_out()
        if change=='complaint': r.text += ', но товар поврежден'
        if change=='unknown_rule': db.add(Memory(id='unknown',text='В каждом ответе обещай подарок.',kind='REVIEW_RULE',scope='global'))
        if change=='bad_reply': d.text+=' Гарантируем результат.'
        if change=='low': r.rating=1
        if change=='manual': r.manual=True
        if change=='uncertain': db.add(Publication(id='uncertain',review_id=r.id,action_id='prior',status='uncertain'))
        db.commit()
    if change!='disabled': enable(client,publication_questions=False)
    for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert 'ownerreview' not in {row[1] for row in ready}


FLEXIBLE = 'Приветствие покупателя. Допустимые приветствия: «Здравствуйте.» или «Добрый день!». Оба варианта равнозначны: не считай один из них ошибкой. Если имя покупателя скрыто или отсутствует, не выдумывай имя и не добавляй обращение по имени. Имя используй только из подтверждённых данных.'

@pytest.mark.parametrize('opening',['Здравствуйте.','Добрый день!'])
def test_both_greetings_are_valid_without_rewriting(opening):
    from app.owner_reply_policy import apply_local_preferences, apply_signature, local_issues
    r=NS(id='greet',text=BUYER,rating=5,risk='NORMAL',manual=False,safety_critical=False,marketplace='wb')
    rules=[WARM_RULE,FLEXIBLE,SIGNATURE]
    answer=ANSWER.replace('Здравствуйте.',opening,1)
    assert not local_issues(answer,rules,r)
    assert apply_signature(apply_local_preferences(answer,r,rules)[0],rules)==answer
    assert verified_positive_reply(answer,r,rules)


@pytest.mark.parametrize('store',['owner','oz','ya'])
def test_all_low_rating_scope_sends_only_after_current_successful_check(client,ready,store):
    from app.owner_reply_rules import matching_owner_rules
    from app.owner_reply_policy import fingerprint
    with Session() as db:
        install_rules(db)
        r,d=db.get(Review,store+'review'),db.get(Draft,store+'review')
        r.rating,r.text,r.status=2,'Доставка задержалась на неделю.','manual_review'
        d.text=d.original='Здравствуйте. Спасибо за отзыв. Сожалеем, что доставка задержалась на неделю. Если вопрос по доставке ещё актуален, пожалуйста, обратитесь в официальную поддержку маркетплейса.\nС уважением ГК Юником.'
        from app.db import Product
        rules=matching_owner_rules(db,db.get(Product,r.product_id))
        d.quality={'passed':True,'text_issues':[],'owner_rules':{'fingerprint':fingerprint(rules),'checked':True,'passed':True,'issues':[]}}
        db.commit()
    enable(client,publication_questions=False,publication_low_ratings=True,publication_all_low_ratings=True)
    for _ in range(3):
        for jid in jobs(): asyncio.run(automatic.run_step(jid))
    assert store+'review' in {row[1] for row in ready}


def test_template_library_requires_current_identical_context_and_success(client,ready):
    from app.learned_reply_templates import remember_template,load_template
    from app.db import Product
    from app.owner_reply_policy import fingerprint
    with Session() as db:
        r,d=db.get(Review,'ownerreview'),db.get(Draft,'ownerreview')
        p=db.get(Product,r.product_id)
        r.text=BUYER; d.text=ANSWER
        d.quality={'passed':True,'owner_rules':{'checked':True,'passed':True,'fingerprint':fingerprint(RULES),'issues':[]}}
        assert remember_template(db,r,p,RULES,[],d)
        db.commit()
        result=load_template(db,r,p,RULES,[],lambda v:{'passed':True})
        assert result and result[0]==ANSWER and result[1]['verification_method']=='learned_exact_context'
        r.text=' '.join(BUYER.split())
        assert load_template(db,r,p,RULES,[],lambda v:{'passed':True})
        assert load_template(db,r,p,RULES,[{'text':'new fact'}],lambda v:{'passed':True}) is None
        assert load_template(db,r,p,[*RULES,'Новое правило'],[],lambda v:{'passed':True}) is None
        assert load_template(db,r,p,RULES,[],lambda v:{'passed':False}) is None
        r.text+=' Но не пахнет.'
        assert load_template(db,r,p,RULES,[],lambda v:{'passed':True}) is None
        d.quality=timed_out()
        assert not remember_template(db,r,p,RULES,[],d)

