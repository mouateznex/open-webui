/**
 * Web Push subscription helper.
 *
 * Subscribes the active service worker to Web Push using the server's VAPID
 * public key, then forwards the resulting subscription to Open WebUI's
 * /rocketchat/push/register endpoint so Rocket.Chat's push gateway can
 * deliver server-side notifications.
 *
 * No-op when:
 *  - Service workers / Push API are not supported
 *  - VAPID public key is not configured on the server
 *  - The user denies the Notification permission
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

function urlBase64ToUint8Array(base64String: string): Uint8Array {
	const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
	const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
	const rawData = atob(base64);
	const out = new Uint8Array(rawData.length);
	for (let i = 0; i < rawData.length; ++i) {
		out[i] = rawData.charCodeAt(i);
	}
	return out;
}

export async function subscribeAndRegisterPush(opts: {
	userId: string;
	vapidPublicKey?: string | null;
}): Promise<boolean> {
	if (typeof window === 'undefined') return false;
	if (!('serviceWorker' in navigator)) return false;
	if (!('PushManager' in window)) return false;
	if (!opts.userId) return false;

	let registration: ServiceWorkerRegistration | null = null;
	try {
		registration = await navigator.serviceWorker.ready;
	} catch (_) {
		return false;
	}
	if (!registration) return false;

	// Notification permission is required before subscribing.
	if (Notification.permission === 'default') {
		try {
			const perm = await Notification.requestPermission();
			if (perm !== 'granted') return false;
		} catch (_) {
			return false;
		}
	} else if (Notification.permission !== 'granted') {
		return false;
	}

	let subscription: PushSubscription | null = null;
	try {
		subscription = await registration.pushManager.getSubscription();
		if (!subscription && opts.vapidPublicKey) {
			subscription = await registration.pushManager.subscribe({
				userVisibleOnly: true,
				applicationServerKey: urlBase64ToUint8Array(opts.vapidPublicKey)
			});
		}
	} catch (err) {
		console.warn('Push subscribe failed:', err);
		return false;
	}

	if (!subscription) return false;

	// Encode the subscription endpoint as the device "token". Rocket.Chat's
	// push gateway accepts the URL itself as the unique token; the keys are
	// only needed when OW (or its gateway) actually pushes to the endpoint.
	const sub = subscription.toJSON();
	const token = sub.endpoint || subscription.endpoint;
	if (!token) return false;

	// Detect the platform — gcm covers Chrome/Android; apn used for Safari/iOS.
	const ua = navigator.userAgent || '';
	const isApple = /iPad|iPhone|iPod|Macintosh/.test(ua) && /Safari/.test(ua);
	const platform = isApple ? 'apn' : 'gcm';

	try {
		const res = await fetch(
			`${WEBUI_API_BASE_URL}/users/${opts.userId}/rocketchat/push/register`,
			{
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.token}`
				},
				body: JSON.stringify({
					token,
					platform,
					app_name: 'open-webui-web'
				})
			}
		);
		return res.ok;
	} catch (err) {
		console.warn('Push register call failed:', err);
		return false;
	}
}

/**
 * Push notification preferences for the user (mirrored to Rocket.Chat
 * via /users/{id}/rocketchat/preferences -> users.setPreferences).
 *
 * Pass any subset of fields RC accepts; unknown fields are ignored on the
 * RC side. Common keys:
 *   - desktopNotifications / desktopNotificationRequireInteraction
 *   - mobileNotifications / pushNotifications
 *   - emailNotificationMode
 *   - notificationsSoundVolume
 */
export async function syncPushPreferences(userId: string, prefs: Record<string, unknown>): Promise<boolean> {
	if (!userId) return false;
	try {
		const res = await fetch(
			`${WEBUI_API_BASE_URL}/users/${userId}/rocketchat/preferences`,
			{
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.token}`
				},
				body: JSON.stringify({ preferences: prefs })
			}
		);
		return res.ok;
	} catch (_) {
		return false;
	}
}
