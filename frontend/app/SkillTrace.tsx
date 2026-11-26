type Step = {id: string; version: string; elapsed_ms: number; calls: number; status: string};
type Trace = {version: string; model: string; route: string; passed: boolean; steps: Step[]; generation_model?: string; policy_model?: string};
type Supervisor = {model: string; status: 'clear' | 'attention' | 'unavailable'; issues?: string[]};
type Repair = {status: 'pending' | 'rechecked' | 'blocked'; issue?: string};
type Quality = {owner_rules?: {technical_error?: boolean; failure_code?: string; retryable?: boolean}; fitment?: {status?: string};
  timing?: {fallback_reason?: string}; confirmed_repair?: Repair};

const names: Record<string, string> = {
  review_classifier: 'Классификация отзыва',
  question_classifier: 'Классификация вопроса',
  product_knowledge: 'Подбор сведений о товаре',
  verified_template: 'Подбор проверенного шаблона',
  model_generation: 'Генерация моделью',
  quality_control: 'Проверка качества',
  policy_checker: 'Проверка правил магазина',
  reply_supervisor: 'Независимая проверка',
};
const routes: Record<string, string> = {
  fallback: 'Резервный сценарий',
  model: 'Подготовка моделью',
  rules: 'Программный сценарий',
  template: 'Проверенный шаблон без вызова модели',
};

export default function SkillTrace({trace, supervisor, quality}: {trace?: Trace; supervisor?: Supervisor; quality?: Quality}) {
  const repair = quality?.confirmed_repair;
  const fitment = quality?.fitment?.status;
  const rules = quality?.owner_rules;
  const fallback = quality?.timing?.fallback_reason;
  const steps = trace?.steps ?? [];
  if (!steps.length && !repair && !supervisor) return null;
  return <details>
    <summary>Этапы подготовки ответа{steps.length > 0 && ` (${steps.length})`}</summary>
    {trace && <p className="muted">
      {routes[trace.route] ?? 'Подготовка ответа'} · {trace.model}
      {' · '}{trace.passed ? 'Проверка при подготовке пройдена' : 'При подготовке требовалась проверка'}
    </p>}
    {(fitment === 'NEEDS_CATALOG' || fitment === 'CONFLICT') && <p>Нужны подтверждённые данные для подбора по каталогу.</p>}
    {rules?.technical_error && <p>{rules.retryable === false
      ? 'Сервис проверки недоступен. Для отправки нужна успешная перепроверка.'
      : 'Проверка прервалась. Черновик ждёт продолжения, отправка закрыта.'}</p>}
    {(fallback === 'timeout' || fallback === 'unavailable') && <p>
      {fallback === 'timeout' ? 'Модель не успела подготовить ответ.' : 'Модель подготовки ответа была недоступна.'}
    </p>}
    {fallback === 'missing_data' && <p>Для точного ответа не хватило подтверждённых сведений о товаре.</p>}
    {repair && <p>{repair.issue} {repair.status === 'rechecked'
      ? 'Ответ исправлен и повторно проверен.'
      : repair.status === 'pending'
        ? 'Исправленный черновик проходит повторную проверку.'
        : 'Исправление требует внимания. Отправка заблокирована.'}</p>}
    {steps.length > 0 && <ul>
      {steps.map(step => <li key={step.id}>
        {names[step.id] ?? step.id} · {step.status === 'completed' ? 'завершён' : 'ошибка'}
        {step.id === 'model_generation' && trace?.generation_model && ` · Ollama / ${trace.generation_model}`}
        {step.id === 'policy_checker' && trace?.policy_model && ` · Ollama / ${trace.policy_model}`}
        {' · '}{step.elapsed_ms} мс{step.calls > 1 ? ` · вызовов: ${step.calls}` : ''}
      </li>)}
    </ul>}
    {supervisor && <div>
      <p>Независимая проверка · Ollama / {supervisor.model} · режим наблюдения</p>
      <p>{supervisor.status === 'clear' ? 'Замечаний нет' : supervisor.status === 'attention' ? 'Есть замечания' : 'Результат недоступен'}</p>
      {Boolean(supervisor.issues?.length) && <ul>{supervisor.issues?.map(issue => <li key={issue}>{issue}</li>)}</ul>}
    </div>}
  </details>;
}
