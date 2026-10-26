/** Restore server-owned progress independently of browser storage or page selection. */
/** @param {any[]} items @param {string} storeId @param {any} current */
export function batchProgressJob(items, storeId, current = null) {
 const scoped = items.filter(job => job.kind === 'drafts' && job.result?.account_id === storeId);
 const active = job => ['queued', 'running'].includes(job.status);
 const allReviews = job => !job.result?.selected_ids?.length && !job.result?.since;
 const full = scoped.filter(job => active(job) && allReviews(job));
 return full.find(job => job.status === 'running') || full[0]
  || scoped.find(job => job.id === current?.id)
  || scoped.find(active)
  || scoped.find(job => job.result?.manual_batch && allReviews(job)) || null;
}

/** Count attempted reviews separately from successfully saved answers. */
export function batchProgress(job) {
 const result = job.result || {};
 const processed = Math.max(0, Number(result.processed) || 0);
 const failed = Math.max(0, Number(result.failed) || 0);
 const skipped = Math.max(0, Number(result.skipped) || 0);
 const attention = Math.max(0, Number(result.attention) || 0);
 const manualErrors = Math.max(0, Number(result.manual_errors) || 0);
 const total = result.total == null ? null : Math.max(0, Number(result.total) || 0);
 const attempted = processed + failed + skipped + manualErrors;
 const percent = job.status === 'completed' ? 100 : total > 0
  ? Math.min(100, Math.round(attempted / total * 1000) / 10) : 0;
 return {processed, failed, skipped, attention, total, attempted, percent,
  remaining: total == null ? null : Math.max(0, total - attempted)};
}

export function batchWaitReason(job, jobs = [], now = Date.now()) {
 if (!job || job.status !== 'queued') return '';
 if (Number(job.result?.retry_at || 0) * 1000 > now)
  return 'Задание сохранено. Повтор после временной ошибки: ' + new Date(job.result.retry_at * 1000).toLocaleTimeString('ru-RU') + '.';
 const labels={products:'обновление товаров',reviews:'обновление отзывов',questions:'обновление вопросов',drafts:'ответы на отзывы',question_drafts:'ответы на вопросы',single_draft:'ответ на отзыв',single_question_draft:'ответ на вопрос',draft_audit:'перепроверка черновиков',draft_recheck:'проверка правил',knowledge_research:'поиск инструкций'};
 const running=jobs.filter(item=>item.id!==job.id&&item.status==='running');
 const names=[...new Set(running.map(item=>labels[item.kind]||'другое задание'))];
 return names.length?'Сейчас выполняется: '+names.join(', ')+'. Ваш прогресс сохранён; пакет продолжится при следующем переходе очереди.':'Задание сохранено и ожидает обработчика. Повторно запускать подготовку не нужно.';
}
