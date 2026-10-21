import {vehicleIdentifiers} from './vehicleIdentifiers';

export function uniqomVehicleUrl(text:string){
 const ids=vehicleIdentifiers(text||'');
 return ids.length===1?'https://uniqom.ru/original-catalogs?identString='+encodeURIComponent(ids[0]):'https://uniqom.ru/original-catalogs';
}
export function uniqomOemUrl(value:string){
 const oem=String(value||'').trim().toUpperCase();
 return /^(?=.*[0-9])[A-Z0-9][A-Z0-9./-]{3,39}$/.test(oem)&&!vehicleIdentifiers(oem).length
  ?'https://uniqom.ru/search?term='+encodeURIComponent(oem)+'&only_parts=true'
  :'https://uniqom.ru/original-catalogs';
}

