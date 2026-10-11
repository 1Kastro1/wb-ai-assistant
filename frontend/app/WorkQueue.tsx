'use client';

import {useEffect,useState} from 'react';
import {api} from './client';

type Store = {id:string;name:string;provider:string};
type Item = {id:string;store_id:string;store_name:string;provider:string;kind:'reviews'|'questions';product:string;text:string;rating:number;created_at:string;manual:boolean;status:string;has_draft:boolean};

export default function WorkQueue({stores,onOpen}:{stores:Store[];onOpen:(item:Item)=>void}) {
  const [kind,setKind]=useState(''),[store,setStore]=useState(''),[items,setItems]=useState<Item[]>([]),[total,setTotal]=useState(0),[offset,setOffset]=useState(0),[loading,setLoading]=useState(false),[error,setError]=useState('');
  useEffect(()=>{let active=true;setLoading(true);setError('');api(`/work-queue?kind=${kind}&store_id=${encodeURIComponent(store)}&limit=50&offset=${offset}`).then(result=>{if(active){setItems(result.items);setTotal(result.total)}}).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:'Не удалось загрузить обращения')}).finally(()=>{if(active)setLoading(false)});return()=>{active=false}},[kind,store,offset]);
  return <section><div className="section-title"><div><p className="eyebrow">ВСЕ МАГАЗИНЫ</p><h1>Очередь ответов</h1><p className="muted">Отзывы и вопросы без ответа в одном месте. Сложные обращения показаны первыми.</p></div><span className="pill amber">Ждут ответа: {total}</span></div>
    <div className="toolbar"><select value={kind} onChange={e=>{setKind(e.target.value);setOffset(0)}}><option value="">Отзывы и вопросы</option><option value="reviews">Только отзывы</option><option value="questions">Только вопросы</option></select><select value={store} onChange={e=>{setStore(e.target.value);setOffset(0)}}><option value="">Все доступные магазины</option>{stores.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></div>
    {error&&<div className="error" role="alert">{error}</div>}{loading&&<p className="muted">Загружаю обращения…</p>}
    <div className="queue-list">{items.map(item=><article className="card queue-item" key={item.id}><div><span className="pill">{item.provider==='wb'?'Wildberries':item.provider==='ozon'?'Ozon':'Яндекс Маркет'} · {item.kind==='questions'?'Вопрос':'Отзыв'}</span>{item.manual&&<span className="pill amber">Ручная проверка</span>}<h3>{item.product}</h3><p>{item.text||'Покупатель оставил оценку без текста.'}</p><small>{item.store_name} · {new Date(item.created_at).toLocaleDateString('ru')} · {item.has_draft?'Черновик готов':'Черновика нет'}</small></div><button onClick={()=>onOpen(item)}>Открыть →</button></article>)}</div>
    {!loading&&!error&&!items.length&&<div className="empty"><h3>Очередь пуста</h3><p>Новых обращений без ответа нет или магазины ещё не синхронизированы.</p></div>}
    {total>50&&<div className="button-row"><button disabled={loading||offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>Назад</button><span className="muted">{offset+1}–{Math.min(offset+50,total)} из {total}</span><button disabled={loading||offset+50>=total} onClick={()=>setOffset(offset+50)}>Далее</button></div>}
  </section>;
}
