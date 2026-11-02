import asyncio
from types import SimpleNamespace as NS
from unittest.mock import patch
import pytest
from app.rule_execution import positive_template
from app.owner_reply_policy import enforce_owner_rules

WARM_RULE = "Стиль и формат ответа на отзыв. Начинай ответ с «Добрый день!». Для скрытого или отсутствующего имени действует отдельное правило нейтрального приветствия. Пиши по-русски, уважительно на «Вы», тепло и доброжелательно. Для положительного отзыва без жалобы и вопроса используй одну благодарность, отклик на одно-два явно названных достоинства и при необходимости одно короткое пожелание. Обычно достаточно 2–4 предложений без подписи. Для высокой оценки без текста достаточно одной короткой благодарности без выдуманных впечатлений. При жалобе или вопросе отвечай по существу независимо от количества звёзд; при нескольких существенных замечаниях допустимо больше предложений. Не придумывай имя, опыт использования, эффект, свойства товара, скорость доставки или выгодность цены. Имя используй только из подтверждённых данных. Упоминание суммы само по себе не означает низкую цену. К обычной похвале не добавляй инструкции, рекламу и рекомендации по применению. Используй одну благодарность и не повторяй извинения. Пожелание необязательно. Обязательную подпись сохраняй один раз отдельной строкой в конце. Выводи только ответ покупателю: без HTML, служебной разметки, заголовков, списков, эмодзи, внутреннего анализа и комментариев администратору. Не переписывай отзыв и полное название товара. Варианты благодарности и пожеланий можно менять, сохраняя смысл, факты и обязательный формат."
GREETING = 'Если имя покупателя скрыто или отсутствует, начинай ответ с «Здравствуйте.». '
SIGNATURE = 'В конце каждого ответа на отзыв добавляй подпись «С уважением ГК Юником.».'

@pytest.mark.parametrize('market',['wb','ozon','yandex'])
def test_reviewed_warm_style_verifies_without_model(market):
    r=NS(id='scent-warm',text='приятный запах.',rating=5,risk='NORMAL',marketplace=market)
    p=NS(name='Ароматизатор EIKOSHA',brand='EIKOSHA',category='ароматизатор')
    rules=[WARM_RULE,GREETING,SIGNATURE]
    answer=positive_template(r,rules,product=p)
    assert answer and answer.startswith('Здравствуйте.')
    assert answer.endswith('С уважением ГК Юником.')
    assert answer.count('С уважением')==1
    with patch('app.integrations.ollama.chat',side_effect=AssertionError('simple praise must not call the model')):
        checked,quality=asyncio.run(enforce_owner_rules(answer,r,p,rules,[],lambda value:{'passed':True,'score':100},allow_fast_positive=True))
    assert checked==answer and quality['passed'] and quality['checked']
    assert positive_template(r,[WARM_RULE+' Всегда обещай подарок.',GREETING,SIGNATURE],product=p) is None
    r.marketplace=market+'_question'
    assert positive_template(r,rules,product=p) is None
