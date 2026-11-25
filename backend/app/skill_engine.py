"""Registry and execution trace for the existing customer reply pipeline."""
from dataclasses import asdict, dataclass
from time import monotonic

from .db import setting

ENABLED = 'ai_skill_engine_enabled'
VERSION = '1.0.0'


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    description: str
    stage: str
    algorithm: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    version: str = VERSION
    required: bool = True


REGISTRY = {skill.id: skill for skill in (
    Skill('review_classifier', 'Классификация отзывов', 'Определяет ситуацию по тексту отзыва.',
          'Анализ', 'Классификация по содержанию', ('Отзыв',), ('Категория',)),
    Skill('question_classifier', 'Классификация вопросов', 'Определяет, что покупатель хочет узнать.',
          'Анализ', 'Классификация по содержанию', ('Вопрос',), ('Категория',)),
    Skill('product_knowledge', 'Сведения о товаре', 'Подбирает подтверждённые сведения для ответа.',
          'Контекст', 'Существующая база знаний и карточка товара', ('Товар',), ('Факты',)),
    Skill('model_generation', 'Подготовка текста моделью', 'Предлагает ответ, когда нужен AI-вызов.',
          'Подготовка', 'Текущий маршрутизатор Ollama и общий лимит времени', ('Обращение', 'Факты'), ('Черновик',),
          ('product_knowledge',), required=False),
    Skill('quality_control', 'Контроль качества', 'Проверяет факты и соответствие обращения ответу.',
          'Проверка', 'Действующие проверки текста и фактов', ('Ответ', 'Факты'), ('Оценка качества',),
          ('product_knowledge',)),
    Skill('policy_checker', 'Проверка правил магазина', 'Применяет правила и проверяет исправленный текст.',
          'Проверка', 'Действующий механизм проверки правил магазина', ('Ответ', 'Правила'), ('Результат проверки',),
          ('product_knowledge',)),
)}


def catalog(active):
    return [{**asdict(skill), 'active': active} for skill in REGISTRY.values()]


class ReplySkills:
    def __init__(self, db, kind):
        self.enabled = bool(setting(db, ENABLED, False))
        self.kind = kind
        self.steps = {}

    def run(self, skill_id, operation, *args, **kwargs):
        if not self.enabled:
            return operation(*args, **kwargs)
        started = monotonic()
        status = 'error'
        try:
            value = operation(*args, **kwargs)
            status = 'completed'
            return value
        finally:
            self._record(skill_id, started, status)

    async def run_async(self, skill_id, operation, *args, **kwargs):
        if not self.enabled:
            return await operation(*args, **kwargs)
        started = monotonic()
        status = 'error'
        try:
            value = await operation(*args, **kwargs)
            status = 'completed'
            return value
        finally:
            self._record(skill_id, started, status)

    def _record(self, skill_id, started, status):
        skill = REGISTRY[skill_id]
        step = self.steps.setdefault(skill_id, {
            'id': skill.id, 'version': skill.version, 'elapsed_ms': 0, 'calls': 0,
        })
        step['elapsed_ms'] += round((monotonic() - started) * 1000)
        step['calls'] += 1
        step['status'] = status

    def save(self, quality, model, route):
        if self.enabled:
            quality['skill_engine'] = {
                'version': VERSION, 'kind': self.kind, 'steps': list(self.steps.values()),
                'model': model, 'route': route, 'passed': quality.get('passed') is True,
            }
