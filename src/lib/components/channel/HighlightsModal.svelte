<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';

	const i18n = getContext('i18n');

	export let show = false;
	export let channel: any = null;

	type Tab = 'starred' | 'threads';
	let activeTab: Tab = 'starred';

	let starred: any[] = [];
	let threads: any[] = [];
	let loading = false;
	let error: string | null = null;

	async function loadTab(tab: Tab) {
		if (!channel) return;
		loading = true;
		error = null;
		try {
			if (tab === 'starred') {
				const res = await fetch(
					`${WEBUI_API_BASE_URL}/rocketchat/messages/${channel.id}/starred`,
					{ headers: { Authorization: `Bearer ${localStorage.token}` } }
				);
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				starred = (await res.json()) ?? [];
			} else if (tab === 'threads') {
				const res = await fetch(
					`${WEBUI_API_BASE_URL}/rocketchat/messages/${channel.id}/threads`,
					{ headers: { Authorization: `Bearer ${localStorage.token}` } }
				);
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				threads = (await res.json()) ?? [];
			}
		} catch (err) {
			error = err instanceof Error ? err.message : String(err);
		} finally {
			loading = false;
		}
	}

	$: if (show && channel) loadTab(activeTab);

	const switchTab = (tab: Tab) => {
		activeTab = tab;
		loadTab(tab);
	};

	const formatTs = (ts: any) => {
		if (!ts) return '';
		const v =
			typeof ts === 'object' && ts.$date
				? Number(ts.$date)
				: typeof ts === 'string'
					? Date.parse(ts)
					: Number(ts);
		if (!v || Number.isNaN(v)) return '';
		return new Date(v).toLocaleString();
	};
</script>

<Modal bind:show size="lg">
	<div class="flex justify-between items-center px-5 pt-4">
		<h2 class="text-lg font-medium">{$i18n.t('Channel Highlights')}</h2>
		<button class="p-1" on:click={() => (show = false)} aria-label="Close">
			<XMark className="size-5" />
		</button>
	</div>

	<nav class="flex gap-1 px-5 mt-3 border-b dark:border-gray-800">
		<button
			class="px-3 py-2 text-sm border-b-2 -mb-px {activeTab === 'starred'
				? 'border-blue-500 text-blue-600'
				: 'border-transparent text-gray-500 hover:text-gray-700'}"
			on:click={() => switchTab('starred')}
		>
			{$i18n.t('Starred')}
		</button>
		<button
			class="px-3 py-2 text-sm border-b-2 -mb-px {activeTab === 'threads'
				? 'border-blue-500 text-blue-600'
				: 'border-transparent text-gray-500 hover:text-gray-700'}"
			on:click={() => switchTab('threads')}
		>
			{$i18n.t('Threads')}
		</button>
	</nav>

	<div class="p-5 max-h-[60vh] overflow-y-auto">
		{#if loading}
			<div class="flex justify-center"><Spinner /></div>
		{:else if error}
			<p class="text-red-500 text-sm">{error}</p>
		{:else if activeTab === 'starred'}
			{#if starred.length === 0}
				<p class="text-sm text-gray-500">{$i18n.t('No starred messages.')}</p>
			{:else}
				<ul class="space-y-3">
					{#each starred as m}
						<li class="border rounded p-3 dark:border-gray-700">
							<div class="text-xs text-gray-500 mb-1">
								{m?.u?.username ?? '?'} · {formatTs(m.ts)}
							</div>
							<div class="text-sm whitespace-pre-wrap">{m.msg ?? ''}</div>
						</li>
					{/each}
				</ul>
			{/if}
		{:else if activeTab === 'threads'}
			{#if threads.length === 0}
				<p class="text-sm text-gray-500">{$i18n.t('No threads in this room yet.')}</p>
			{:else}
				<ul class="space-y-3">
					{#each threads as t}
						<li class="border rounded p-3 dark:border-gray-700">
							<div class="text-xs text-gray-500 mb-1">
								{t?.u?.username ?? '?'} · {formatTs(t.ts)} · {t.tcount ?? 0} replies
							</div>
							<div class="text-sm whitespace-pre-wrap">{t.msg ?? ''}</div>
						</li>
					{/each}
				</ul>
			{/if}
		{/if}
	</div>
</Modal>
