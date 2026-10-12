process.env.NEXT_TELEMETRY_DISABLED = '1';
const backendOrigin = process.env.WB_CONTAINER === '1' ? 'http://backend:8000' : 'http://127.0.0.1:8000';
export default {
  poweredByHeader: false,
  async rewrites() { return [{ source: '/api/:path*', destination: `${backendOrigin}/:path*` }]; },
  async headers() { return [{ source: '/(.*)', headers: [
    {key:'Content-Security-Policy',value:"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"},
    {key:'Permissions-Policy',value:'microphone=(self)'}, {key:'X-Content-Type-Options',value:'nosniff'}, {key:'Referrer-Policy',value:'no-referrer'}, {key:'X-Frame-Options',value:'DENY'}
  ]}]; }
};
