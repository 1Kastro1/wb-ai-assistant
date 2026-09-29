import './style.css';
export const metadata = { title: 'WB · Личный ассистент', description: 'Локальный помощник магазина Wildberries', manifest: '/manifest.webmanifest' };
export default function RootLayout({children}:Readonly<{children:React.ReactNode}>){return <html lang="ru"><body>{children}</body></html>}
