const destinations={wb:{name:'Wildberries',short:'WB',logo:'WB'},ozon:{name:'Ozon',short:'Ozon',logo:'O'},yandex:{name:'Яндекс Маркет',short:'Яндекс Маркет',logo:'Я'}};
export function publicationDestination(provider){return destinations[provider]||{name:'Маркетплейс',short:'Маркетплейс',logo:'М'}}

