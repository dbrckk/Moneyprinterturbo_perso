/** Public static-shell cache policy. Never cache API calls or authentication data. */
export const SHELL_CACHE = 'mpt-android-shell-v4';
export const SHELL_FILES = Object.freeze([
  './', './index.html', './style.css', './main.mjs',
  './core.mjs', './downloads.mjs', './draft_backup.mjs',
  './run_lifecycle.mjs', './offline_cache.mjs',
  './manifest.webmanifest', './icon.svg',
]);

export function cacheableStaticRequest(request, scope) {
  try {
    if (!request || request.method !== 'GET') return false;
    const url = new URL(request.url);
    const root = new URL(scope);
    if (url.origin !== root.origin || url.search || url.hash) return false;
    return SHELL_FILES.some(file => new URL(file, root).pathname === url.pathname);
  } catch {
    return false;
  }
}
