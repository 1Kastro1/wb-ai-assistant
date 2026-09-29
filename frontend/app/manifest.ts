import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'WB Assistant',
    short_name: 'WB Assistant',
    description: 'Локальный голосовой помощник магазина Wildberries',
    start_url: '/',
    display: 'standalone',
    background_color: '#f7f8fa',
    theme_color: '#7259df',
    lang: 'ru',
  };
}
