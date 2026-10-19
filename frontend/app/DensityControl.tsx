'use client';
export default function DensityControl({value,onChange}:{value:string;onChange:(value:string)=>void}){
 return <div className="view-controls"><span>Вид списков</span><div role="group" aria-label="Плотность списков" className="density-control">{[['comfortable','Просторный'],['compact','Компактный']].map(([id,label])=><button type="button" key={id} aria-pressed={value===id} onClick={()=>onChange(id)}><span aria-hidden="true">{id==='compact'?'☰':'▤'}</span>{label}</button>)}</div></div>;
}
