export default function ReviewAnswerTabs({value,busy,onChange}:{value:string|null;busy:boolean;onChange:(value:'unanswered'|'answered')=>void}){
 return <div className="review-answer-tabs" role="group" aria-label="Ответы продавца">
  <button className={value==='unanswered'?'selected':''} aria-pressed={value==='unanswered'} disabled={busy} onClick={()=>onChange('unanswered')}><span>Ждут ответа</span><i className="waiting" aria-hidden="true"/></button>
  <button className={value==='answered'?'selected':''} aria-pressed={value==='answered'} disabled={busy} onClick={()=>onChange('answered')}><span>Есть ответ</span><i className="answered" aria-hidden="true"/></button>
 </div>;
}

