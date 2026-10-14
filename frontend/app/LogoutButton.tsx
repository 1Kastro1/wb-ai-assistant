'use client';
export default function LogoutButton({pending=false,onLogout}:{pending?:boolean;onLogout:()=>void}){
 return <button className="account-logout" type="button" onClick={onLogout} disabled={pending} aria-label="Выйти из аккаунта" aria-busy={pending} title="Выйти из аккаунта"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4M13 8l4 4-4 4m-5-4h12"/></svg><span>{pending?'Выходим…':'Выйти'}</span></button>;
}
