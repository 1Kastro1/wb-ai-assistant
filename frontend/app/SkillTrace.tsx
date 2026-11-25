type Step = {id: string; version: string; elapsed_ms: number; calls: number; status: string};
type Trace = {version: string; model: string; route: string; passed: boolean; steps: Step[]};

const names: Record<string, string> = {
  review_classifier: 'Классификация отзыва',
  question_classifier: 'Классификация вопроса',
  product_knowledge: 'Подбор сведений о товаре',
  model_generation: 'Генерация моделью',
  quality_control: 'Проверка качества',
  policy_checker: 'Проверка правил магазина',
};
const routes: Record<string, string> = {
  fallback: 'Резервный сценарий',
  model: 'Подготовка моделью',
  rules: 'Программный сценарий',
};

export default function SkillTrace({trace}: {trace?: Trace}) {
  if (!Array.isArray(trace?.steps) || !trace.steps.length) return null;
  const steps = trace.steps;
  return <details>
    <summary>Этапы подготовки ответа ({steps.length})</summary>
    <p className="muted">
      {routes[trace.route] ?? 'Подготовка ответа'} · {trace.model}
      {' · '}{trace.passed ? 'Проверка при подготовке пройдена' : 'При подготовке требовалась проверка'}
    </p>
    <ul>
      {steps.map(step => <li key={step.id}>
        {names[step.id] ?? step.id} · {step.status === 'completed' ? 'завершён' : 'ошибка'}
        {' · '}{step.elapsed_ms} мс{step.calls > 1 ? ` · вызовов: ${step.calls}` : ''}
      </li>)}
    </ul>
  </details>;
}
