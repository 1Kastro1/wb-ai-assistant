import { NextRequest, NextResponse } from 'next/server';

export function middleware(request: NextRequest) {
  const host = request.headers.get('host')?.split(':')[0].toLowerCase();
  if (host !== 'assistant.blackmindworks.com') return NextResponse.next();

  const forwardedProtocol = request.headers.get('x-forwarded-proto')?.split(',')[0].trim().toLowerCase();
  if (forwardedProtocol === 'https') return NextResponse.next();
  if (forwardedProtocol !== 'http' && request.nextUrl.protocol !== 'http:') return NextResponse.next();

  const secureUrl = new URL(request.nextUrl.pathname + request.nextUrl.search, 'https://assistant.blackmindworks.com');
  const response = NextResponse.redirect(secureUrl, 307);
  response.headers.set('Cache-Control', 'no-store');
  return response;
}
