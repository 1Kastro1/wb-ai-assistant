import {ReactNode} from 'react';
const paths:Record<string,ReactNode>={
 'Главная':<><path d="m3 10 9-7 9 7v10H3Z"/><path d="M9 20v-7h6v7"/></>,
 'Ассистент':<><path d="M4 4h16v12H9l-5 4Z"/><path d="M8 8h8M8 12h5"/></>,
 'Очередь ответов':<><rect x="7" y="3" width="13" height="15" rx="2"/><path d="M4 7v14h12M11 7h5M11 11h5"/></>,
 'Действия':<><rect x="7" y="3" width="13" height="15" rx="2"/><path d="M4 7v14h12M11 7h5M11 11h5"/></>,
 'Отзывы':<><path d="M4 4h16v14H9l-5 3Z"/><path d="m12 7 1.2 2.4 2.8.4-2 1.9.5 2.7-2.5-1.3-2.5 1.3.5-2.7-2-1.9 2.8-.4Z"/></>,
 'Вопросы':<><path d="M4 4h16v14H9l-5 3Z"/><path d="M9.5 8a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4M12 15h.01"/></>,
 'Товары':<><path d="m3 7 9-4 9 4v10l-9 4-9-4Zm0 0 9 4 9-4M12 11v10M7.5 5 17 9"/></>,
 'История действий':<><path d="M3 11a9 9 0 1 1 2 7M3 4v7h7M12 7v5l3 2"/></>,
 'Уведомления':<><path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5ZM10 21h4"/></>,
 'Память':<><rect x="5" y="3" width="14" height="18" rx="2"/><path d="m8 9 1 1 2-3M13 9h3m-8 6 1 1 2-3M13 15h3"/></>,
 'Настройки':<><path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/></>,
 filters:<><path d="M3 6h18M3 12h18M3 18h18"/><circle cx="8" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="9" cy="18" r="2"/></>,
 search:<><circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/></>,
 close:<path d="m6 6 12 12M6 18 18 6"/>,
 logout:<><path d="M10 4H4v16h6M9 12h12m-5-5 5 5-5 5"/></>,
 chevron:<path d="m8 10 4 4 4-4"/>,
 panel:<><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16m7-7-3-3 3-3"/></>
};
export default function AppIcon({name}:{name:string}){return <svg className="app-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||<rect x="4" y="4" width="16" height="16" rx="4"/>}</svg>}
export function BrandMark(){return <svg className="brand-mark" viewBox="0 0 40 40" width="40" height="40" aria-hidden="true"><rect width="40" height="40" rx="12" fill="#6941c6"/><path d="M10 11h20v14H19l-6 5v-5h-3Z" fill="none" stroke="white" strokeWidth="2" strokeLinejoin="round"/><path d="M15 17h10m-10 4h6" stroke="white" strokeWidth="2" strokeLinecap="round"/><circle cx="30" cy="10" r="4" fill="#a7edce" stroke="#6941c6" strokeWidth="2"/></svg>}
