type Scope='all'|'positive';
export default function PublicationChoices({loading,working,disabled,statuses,onLaunch}:{loading:boolean;working:string|null;disabled:boolean;statuses:Record<string,any>;onLaunch:(scope:Scope)=>void}){
 return <div className="export-choices" aria-label="Выберите вариант выгрузки">
  {(['all','positive'] as const).map(scope=>{
   const row=statuses[scope],running=row?.status==='sending'||(row?.status==='preparing'&&row?.auto_send);
   return <button className={'export-choice '+scope} key={scope} disabled={loading||disabled||Boolean(working)||running} onClick={()=>onLaunch(scope)}>
    <span className="export-choice-top"><span className="export-choice-symbol" aria-hidden="true">{scope==='positive'?'★':'▤'}</span><span className="export-choice-arrow" aria-hidden="true">↗</span></span>
    <strong>{scope==='positive'?'Выгрузить положительные отзывы':'Выгрузить все отзывы'}</strong>
    <span className="export-choice-description">{scope==='positive'?'4–5 звёзд без выявленных жалоб':'Включая ранее исключённые ответы'}</span>
    <span className="export-choice-action">{loading?'Проверяем список…':working===scope?'Запускаем…':running?row.status==='sending'?'Выгрузка выполняется':'Сборка → затем отправка':'Собрать и отправить →'}</span>
   </button>;
  })}
 </div>;
}

