'use client';
export default function ScopeField({value,onChange,disabled=false}:{value:string;onChange:(value:string)=>void;disabled?:boolean}){
 const [kind,...parts]=value.split(':'),names:Record<string,string>={brand:'Бренд',category:'Категория',product:'Товар'};
 return <div className="scope-fields"><label>Область правила<select disabled={disabled} value={names[kind]?kind:'brand'} onChange={e=>onChange(e.target.value+':')}><option value="brand">Бренд</option><option value="category">Категория</option><option value="product">Товар</option></select></label><label>{names[kind]||'Бренд'}<input disabled={disabled} value={parts.join(':')} onChange={e=>onChange((names[kind]?kind:'brand')+':'+e.target.value)} placeholder={kind==='product'?'Артикул или идентификатор товара':'Название'}/></label></div>;
}
