/* eslint-env serviceworker */
/*
 * Open WebUI service worker.
 *
 * Caches the static shell of the app and the new feature routes
 * (/teams, /search, /channels, /admin/users) so the PWA can render the
 * UI shell offline. API responses are NOT cached — they always go to
 * the network because they are user-specific and change frequently.
 */

const CACHE_VERSION = 'open-webui-shell-v3';
const SHELL_URLS = [
	'/',
	'/teams',
	'/search',
	'/channels',
	'/admin',
	'/admin/users',
	'/static/web-app-manifest-192x192.png',
	'/static/web-app-manifest-512x512.png',
	'/static/favicon.png',
	'/static/site.webmanifest'
];

self.addEventListener('install', (event) => {
	event.waitUntil(
		caches
			.open(CACHE_VERSION)
			.then((cache) =>
				Promise.all(
					SHELL_URLS.map((url) =>
						cache
							.add(new Request(url, { credentials: 'same-origin' }))
							.catch(() => null)
					)
				)
			)
			.then(() => self.skipWaiting())
	);
});

self.addEventListener('activate', (event) => {
	event.waitUntil(
		caches
			.keys()
			.then((keys) =>
				Promise.all(
					keys
						.filter((key) => key !== CACHE_VERSION)
						.map((stale) => caches.delete(stale))
				)
			)
			.then(() => self.clients.claim())
	);
});

self.addEventListener('fetch', (event) => {
	const req = event.request;

	// Bypass: anything that isn't GET, anything cross-origin, anything API.
	if (req.method !== 'GET') return;

	const url = new URL(req.url);

	if (url.origin !== self.location.origin) return;

	const isApi =
		url.pathname.startsWith('/api/') ||
		url.pathname.startsWith('/ws/') ||
		url.pathname.startsWith('/oauth/') ||
		url.pathname.startsWith('/.well-known/') ||
		url.pathname.startsWith('/rocketchat/');

	if (isApi) return;

	// Network-first for navigations so the user always sees the freshest shell;
	// fall back to cache when the network is gone (PWA offline mode).
	if (req.mode === 'navigate') {
		event.respondWith(
			fetch(req)
				.then((res) => {
					const copy = res.clone();
					caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy)).catch(() => null);
					return res;
				})
				.catch(() => caches.match(req).then((cached) => cached || caches.match('/')))
		);
		return;
	}

	// Cache-first for static assets.
	const isStatic =
		url.pathname.startsWith('/static/') ||
		url.pathname.startsWith('/_app/') ||
		url.pathname.endsWith('.css') ||
		url.pathname.endsWith('.js') ||
		url.pathname.endsWith('.svg') ||
		url.pathname.endsWith('.png') ||
		url.pathname.endsWith('.webp') ||
		url.pathname.endsWith('.woff2');

	if (isStatic) {
		event.respondWith(
			caches.match(req).then(
				(cached) =>
					cached ||
					fetch(req).then((res) => {
						const copy = res.clone();
						caches.open(CACHE_VERSION).then((cache) => cache.put(req, copy)).catch(() => null);
						return res;
					})
			)
		);
	}
});
