'use client';
import {useEffect, useState} from 'react';
import {api} from './client';

type Skill = {id:string; name:string; description:string; version:string; stage:string; active:boolean; required:boolean; inputs:string[]; outputs:string[]; dependencies:string[]; algorithm:string};
type Report = {enabled:boolean; skills:Skill[]; model:string; performance_mode:string};

export default function AiSkills() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function load() {
    try { setError(''); setReport(await api('/ai-skills')); }
    catch (err) { setError(err instanceof Error ? err.message : 'Не удалось загрузить навыки.'); }
  }
  useEffect(() => { void load(); }, []);
  async function toggle(enabled:boolean) {
    setBusy(true);
    try { setError(''); setReport(await api('/ai-skills', 'PATCH', {enabled})); }
    catch (err) { setError(err instanceof Error ? err.message : 'Не удалось сохранить настройку.'); }
    finally { setBusy(false); }
  }
  return <section className="settings-workspace">
    <div className="settings-intro"><div><p className="eyebrow">ОБРАБОТКА ОБРАЩЕНИЙ</p><h1>AI Skills</h1><p>Этапы обработки отзывов и вопросов покупателей.</p></div></div>
    {error && <div className="card" role="alert"><p>{error}</p><button onClick={() => void load()}>Попробовать снова</button></div>}
    {!report && !error && <p role="status">Загружаем навыки…</p>}
    {report && <>
      <div className="card"><h2>Управление навыками</h2><label className="check"><input type="checkbox" checked={report.enabled} disabled={busy} onChange={event => void toggle(event.target.checked)}/> Использовать AI Skill Engine</label><p className="muted">Настройки публикации и проверок управляются в разделе «Настройки».</p><p>{report.skills.filter(skill => skill.active).length} активных навыков из {report.skills.length}</p></div>
      <div className="card"><h2>Модель ответов</h2><p>{report.model} · {({economy:'экономичный', normal:'обычный', quality:'повышенное качество'} as Record<string,string>)[report.performance_mode] || report.performance_mode}</p><p className="muted">Профиль модели можно изменить в разделе «Настройки → Ответы и модель».</p></div>
      <h2>Каталог навыков</h2>
      <div className="library-metrics">{report.skills.map(skill => <article className="card" key={skill.id}><h3>{skill.name}</h3><p>{skill.description}</p><p className="muted">{skill.active ? 'Активен' : 'Выключен'} · версия {skill.version}{skill.required ? ' · обязательный' : ''}</p><details><summary>Подробнее</summary><p>Этап: {skill.stage}</p><p>Алгоритм: {skill.algorithm}</p><p>Вход: {skill.inputs.join(', ') || '—'}</p><p>Результат: {skill.outputs.join(', ') || '—'}</p><p>Зависимости: {skill.dependencies.join(', ') || '—'}</p></details></article>)}</div>
    </>}
  </section>;
}
