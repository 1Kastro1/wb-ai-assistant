// Group only adjacent successful rule operations by the same actor and scope.
// Keep every original entry, including pagination boundaries, in expandable details.
export function activityGroups(items){
 const groups=[];
 for(const item of items){
  const event=item.details?.event||'';
  const rule=/^rule_(added|deleted|enabled|disabled)$/.test(event)&&Number(item.status)<400;
  const key=rule?[item.user_id||item.username,event,item.details?.scope||''].join(':'):null;
  const previous=groups.at(-1);
  const interval=previous?new Date(previous.entries.at(-1).timestamp).getTime()-new Date(item.timestamp).getTime():Infinity;
  if(key&&previous?.groupKey===key&&interval>=0&&interval<=120000&&new Date(previous.timestamp).toDateString()===new Date(item.timestamp).toDateString())previous.entries.push(item);
  else groups.push({...item,groupKey:key,entries:[item]});
 }
 return groups;
}
