export function rendererContentSecurityPolicy(isPackaged: boolean): string {
  const connectSource = isPackaged
    ? "'none'"
    : "'self' ws://127.0.0.1:5173";
  const styleSource = isPackaged
    ? "'self'"
    : "'self' 'unsafe-inline'";
  return [
    "default-src 'self'",
    "script-src 'self'",
    `style-src ${styleSource}`,
    "img-src 'self' data:",
    "font-src 'self'",
    `connect-src ${connectSource}`,
    "media-src blob:",
    "object-src 'none'",
    "frame-src 'none'",
    "worker-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'"
  ].join("; ");
}
