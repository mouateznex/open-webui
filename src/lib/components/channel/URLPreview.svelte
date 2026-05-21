<script lang="ts">
	import { onMount } from 'svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	export let channelId: string;
	export let content: string = '';

	type Preview = {
		url: string;
		title?: string;
		description?: string;
		image?: string;
		host?: string;
	};

	let previews: Preview[] = [];

	const URL_REGEX = /https?:\/\/[^\s<>"'`]+/gi;

	const extractUrls = (text: string): string[] => {
		if (!text) return [];
		const seen = new Set<string>();
		const out: string[] = [];
		const matches = text.match(URL_REGEX) ?? [];
		for (const raw of matches) {
			// Strip trailing punctuation that markdown often leaves behind
			const cleaned = raw.replace(/[)>,.;:!?]+$/, '');
			if (!seen.has(cleaned)) {
				seen.add(cleaned);
				out.push(cleaned);
				if (out.length >= 3) break; // cap to avoid request storms
			}
		}
		return out;
	};

	async function loadPreviews() {
		const urls = extractUrls(content);
		if (!urls.length || !channelId) return;
		const fetched: Preview[] = [];
		for (const u of urls) {
			try {
				const res = await fetch(`${WEBUI_API_BASE_URL}/rocketchat/messages/url-preview`, {
					method: 'POST',
					headers: {
						'Content-Type': 'application/json',
						Authorization: `Bearer ${localStorage.token}`
					},
					body: JSON.stringify({ channel_id: channelId, url: u })
				});
				if (!res.ok) continue;
				const body = await res.json();
				// RC's payload shape varies — normalise the common fields
				const meta = body?.meta ?? body ?? {};
				fetched.push({
					url: u,
					title: meta.pageTitle || meta.ogTitle || meta.title,
					description: meta.ogDescription || meta.description || meta.metaDescription,
					image: meta.ogImage || meta.oembedThumbnailUrl || meta.image,
					host: (() => {
						try {
							return new URL(u).host;
						} catch (_) {
							return '';
						}
					})()
				});
			} catch (_) {
				// silently skip — URL preview is non-critical
			}
		}
		previews = fetched.filter((p) => p.title || p.description || p.image);
	}

	$: if (channelId && content) loadPreviews();
</script>

{#if previews.length > 0}
	<div class="mt-2 flex flex-col gap-2">
		{#each previews as p}
			<a
				href={p.url}
				target="_blank"
				rel="noopener noreferrer"
				class="border rounded-lg p-3 flex gap-3 hover:bg-gray-50 dark:hover:bg-gray-900 dark:border-gray-700 transition no-underline text-inherit"
			>
				{#if p.image}
					<img
						src={p.image}
						alt=""
						class="size-16 rounded object-cover shrink-0"
						loading="lazy"
					/>
				{/if}
				<div class="min-w-0 flex-1">
					{#if p.host}
						<div class="text-xs text-gray-500 truncate">{p.host}</div>
					{/if}
					{#if p.title}
						<div class="text-sm font-medium truncate">{p.title}</div>
					{/if}
					{#if p.description}
						<div class="text-xs text-gray-600 dark:text-gray-400 line-clamp-2">{p.description}</div>
					{/if}
				</div>
			</a>
		{/each}
	</div>
{/if}
