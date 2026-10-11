import './style.css';
export const metadata = { title: 'Ассистент маркетплейсов', description: 'Отзывы, вопросы и ответы для Wildberries, Ozon и Яндекс Маркета', manifest: '/manifest.webmanifest' };
export default function RootLayout({children}:Readonly<{children:React.ReactNode}>){return <html lang="ru"><body>{children}</body></html>}
