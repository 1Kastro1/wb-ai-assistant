// Keep aligned with backend/app/vehicle_identifiers.py.
export function vehicleIdentifiers(text:string):string[]{
 const value=text||'', found=new Set<string>((value.match(/(?<![A-Z0-9])[A-HJ-NPR-Z0-9]{17}(?![A-Z0-9])/gi)||[]).map(v=>v.toUpperCase()));
 for(const m of value.matchAll(/(?<![A-Z0-9])([A-Z]{1,4}[0-9]{1,3}[A-Z]?)\s*[-‐‑–—]\s*([0-9]{5,8})(?![A-Z0-9])/gi)){
  if(/(?:OEM|OE|ОЕМ|артикул|номер детали)\s*[:#№]?\s*$/i.test(value.slice(Math.max(0,(m.index||0)-24),m.index)))continue;
  found.add(m[1].toUpperCase()+'-'+m[2]);
 }
 for(const m of value.matchAll(/(?<![A-Z0-9])([A-Z]{2,4}[0-9]{2}[A-Z]?)([0-9]{6,7})(?![A-Z0-9])/gi)){
  const start=m.index||0;
  if(/(?:OEM|OE|ОЕМ|артикул|номер детали)\s*[:#№]?\s*$/i.test(value.slice(Math.max(0,start-24),start)))continue;
  if(!/(?<![\p{L}\p{N}])(?:frame|фрейм\p{L}*|кузов\p{L}*|под[оа]йд\p{L}*|совместим\p{L}*)(?![\p{L}\p{N}])/iu.test(value.slice(Math.max(0,start-100),start+m[0].length+80)))continue;
  found.add(m[1].toUpperCase()+'-'+m[2]);
 }
 return Array.from(found).sort();
}
