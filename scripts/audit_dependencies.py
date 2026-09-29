"""Development-only advisory check against public PyPI metadata (no shop data)."""
import asyncio
import json
from pathlib import Path
import httpx


async def main():
    root=Path(__file__).resolve().parents[1]
    packages=[line.strip().split('==') for line in (root/'backend/requirements.lock.txt').read_text().splitlines() if '==' in line]
    gate=asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
        async def check(name,version):
            async with gate:
                try:
                    response=await client.get(f'https://pypi.org/pypi/{name}/{version}/json')
                    response.raise_for_status()
                    return {'name':name,'version':version,'advisories':response.json().get('vulnerabilities',[])}
                except Exception:
                    return {'name':name,'version':version,'error':'metadata unavailable'}
        results=await asyncio.gather(*(check(*p) for p in packages))
    (root/'DEPENDENCY_AUDIT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'checked':len(results),'findings':[{'name':r['name'],'version':r['version'],'ids':[a['id'] for a in r.get('advisories',[])],'error':r.get('error')} for r in results if r.get('advisories') or r.get('error')]},indent=2))


if __name__=='__main__':asyncio.run(main())
