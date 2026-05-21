/**
 * Direct Rocket.Chat upload helpers.
 *
 * These call the backend wrappers around RC's `rooms.upload` so the file
 * is stored on the Rocket.Chat side natively (proper voice-memo bubble,
 * full-text search, etc.). Use these instead of OW's /api/v1/files when
 * you specifically want the file to live on RC, not Open WebUI.
 *
 * Server-side, post_new_message already mirrors OW attachments to RC for
 * regular text+file messages, so most callers don't need these helpers.
 * They exist for cases where the RC-native rendering matters (audio
 * messages, dropzone-direct uploads).
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';

export type RCUploadResult = {
	rc_message_id?: string;
	room_id?: string;
};

export async function uploadFileToRC(
	channelId: string,
	file: File,
	opts: { description?: string; msg?: string; threadId?: string } = {}
): Promise<RCUploadResult> {
	const fd = new FormData();
	fd.append('file', file);
	if (opts.description) fd.append('description', opts.description);
	if (opts.msg) fd.append('msg', opts.msg);
	if (opts.threadId) fd.append('thread_id', opts.threadId);

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/rocketchat/files/${encodeURIComponent(channelId)}/upload`,
		{
			method: 'POST',
			headers: { Authorization: `Bearer ${localStorage.token}` },
			body: fd
		}
	);
	if (!res.ok) {
		const detail = await res.text().catch(() => '');
		throw new Error(`RC upload failed (${res.status}): ${detail}`);
	}
	return await res.json();
}

export async function uploadAudioToRC(
	channelId: string,
	file: File,
	opts: { description?: string } = {}
): Promise<RCUploadResult> {
	const fd = new FormData();
	fd.append('file', file);
	if (opts.description) fd.append('description', opts.description);

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/rocketchat/files/${encodeURIComponent(channelId)}/upload/audio`,
		{
			method: 'POST',
			headers: { Authorization: `Bearer ${localStorage.token}` },
			body: fd
		}
	);
	if (!res.ok) {
		const detail = await res.text().catch(() => '');
		throw new Error(`RC audio upload failed (${res.status}): ${detail}`);
	}
	return await res.json();
}
