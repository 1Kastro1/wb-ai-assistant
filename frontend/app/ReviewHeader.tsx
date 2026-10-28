'use client';
type Row=Record<string,any>;
export default function ReviewHeader({automation,count,total}:{automation:Row;count:number;total:number}){
  const state=automation.state==='running'?'Обрабатывают отзывы':automation.state==='queued'?'Ожидают запуска':automation.enabled?'Включены':'На паузе';
  return <div className="reviews-heading">
    <div className="reviews-title-row"><div className="reviews-title"><h1>Отзывы</h1><span className="review-total" aria-label={'Отзывов по выбранному фильтру: '+total}>{total.toLocaleString('ru-RU')}</span></div><div className="automation-control"><span className={'automation-state '+(automation.enabled?'on':'')}><i/>Автоответы: {state.toLowerCase()}</span></div></div>
    <div className="reviews-heading-footer"><p>Показано {count} · Ответы отправляются после Вашего подтверждения</p></div>
    <details className="automation-details"><summary>Расписание автоответов</summary><div><span>Последний запуск: {automation.last_job?.created_at?new Date(automation.last_job.created_at).toLocaleString('ru-RU'):'ещё не было'}</span><span>Следующая проверка: {automation.next_run_at?new Date(automation.next_run_at).toLocaleString('ru-RU'):'после запуска'}</span></div></details>
  </div>;
}

