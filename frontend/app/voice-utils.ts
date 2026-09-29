export function wavFromAudio(chunks:Float32Array[],sourceRate:number){
  const length=chunks.reduce((sum,item)=>sum+item.length,0),merged=new Float32Array(length);let offset=0;chunks.forEach(item=>{merged.set(item,offset);offset+=item.length});
  const targetRate=16000,ratio=sourceRate/targetRate,samples=new Int16Array(Math.ceil(merged.length/ratio));
  for(let i=0;i<samples.length;i++){const start=Math.floor(i*ratio),end=Math.min(merged.length,Math.floor((i+1)*ratio));let sum=0;for(let j=start;j<end;j++)sum+=merged[j];const value=Math.max(-1,Math.min(1,sum/Math.max(1,end-start)));samples[i]=value<0?value*32768:value*32767}
  const buffer=new ArrayBuffer(44+samples.length*2),view=new DataView(buffer);const write=(at:number,value:string)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i))};
  write(0,'RIFF');view.setUint32(4,36+samples.length*2,true);write(8,'WAVE');write(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,targetRate,true);view.setUint32(28,targetRate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);write(36,'data');view.setUint32(40,samples.length*2,true);samples.forEach((value,index)=>view.setInt16(44+index*2,value,true));return new Blob([buffer],{type:'audio/wav'});
}

export function speechChunks(text:string){
  const sentences=text.replace(/\s+/g,' ').trim().split(/(?<=[.!?])\s+/),chunks:string[]=[];let current='';
  for(const sentence of sentences){if(current&&current.length+sentence.length>280){chunks.push(current);current=sentence}else current=(current+' '+sentence).trim()}if(current)chunks.push(current);return chunks;
}

export async function createAudioCapture(context:AudioContext,onChunk:(chunk:Float32Array)=>void):Promise<AudioNode>{
  if(context.audioWorklet){
    await context.audioWorklet.addModule('/audio-capture.worklet.js');const node=new AudioWorkletNode(context,'local-audio-capture');node.port.onmessage=event=>onChunk(new Float32Array(event.data));return node;
  }
  const node=context.createScriptProcessor(4096,1,1);node.onaudioprocess=event=>onChunk(new Float32Array(event.inputBuffer.getChannelData(0)));return node;
}
