import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Ассистент маркетплейсов',
    short_name: 'Маркетплейсы',
    description: 'Локальный помощник для Wildberries, Ozon и Яндекс Маркета',
    start_url: '/',
    display: 'standalone',
    background_color: '#f7f8fa',
    theme_color: '#7259df',
    lang: 'ru',
    icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
  };
}
