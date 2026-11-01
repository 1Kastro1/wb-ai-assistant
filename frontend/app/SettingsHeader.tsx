'use client';
import AppIcon from './AppIcon';
const tabs=[
 ['connections','Подключения','Маркеты и сервер','Товары'],
 ['answers','Ответы AI','Правила и качество','Память'],
 ['automation','Автоматизация','Расписание и выгрузка','История действий'],
 ['voice','Голос','Общение с ассистентом','Ассистент'],
 ['security','Безопасность','Режимы и копии','Настройки'],
 ['data','Данные и магазины','Хранение и синхронизация','Товары'],
 ['team','Команда','Участники и доступ','Ассистент'],
];
export default function SettingsHeader({active,onChange,owner}:{active:string;onChange:(id:string)=>void;owner:boolean}){
 return <div className="settings-overview"><div className="settings-intro"><div><p className="eyebrow">ВАШЕ РАБОЧЕЕ ПРОСТРАНСТВО</p><h1>Настройки</h1><p>Подключения, качество ответов и работа по расписанию — в одном месте.</p></div><span className="settings-role">{owner?'Доступ владельца':'Доступ участника'}</span></div><nav className="settings-navigation" aria-label="Разделы настроек">{tabs.filter(([id])=>owner||['connections','answers','voice'].includes(id)).map(([id,title,description,icon])=><button type="button" key={id} aria-current={active===id?'page':undefined} className={active===id?'is-active':''} onClick={()=>onChange(id)}><span className="settings-nav-icon"><AppIcon name={icon}/></span><span><b>{title}</b><small>{description}</small></span><span className="settings-nav-arrow" aria-hidden="true">↗</span></button>)}</nav></div>;
}
