<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { searchChannelMessages } from '$lib/apis/channels';

	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import Message from './Messages/Message.svelte';

	export let show = false;
	export let channel = null;

	let query = '';
	let results: any[] | null = null;
	let loading = false;
	let debounceTimer: ReturnType<typeof setTimeout>;

	const runSearch = async () => {
		if (!channel || !query.trim()) {
			results = null;
			return;
		}

		loading = true;
		try {
			const res = await searchChannelMessages(localStorage.token, channel.id, query.trim()).catch(
				(err) => {
					toast.error(`${err}`);
					return null;
				}
			);
			results = res ?? [];
		} finally {
			loading = false;
		}
	};

	const onQueryInput = () => {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(runSearch, 350);
	};

	const reset = () => {
		query = '';
		results = null;
		loading = false;
		clearTimeout(debounceTimer);
	};

	$: if (show) {
		reset();
	}
</script>

{#if channel}
	<Modal size="sm" bind:show>
		<div>
			<div class="flex justify-between dark:text-gray-100 px-5 pt-4 mb-3">
				<div class="self-center text-base">{$i18n.t('Search Messages')}</div>
				<button
					class="self-center"
					on:click={() => {
						show = false;
					}}
				>
					<XMark className="size-5" />
				</button>
			</div>

			<div class="px-5 pb-2">
				<div
					class="flex items-center gap-2 rounded-xl border border-gray-200 dark:border-gray-700 px-3 py-2 bg-gray-50 dark:bg-gray-850"
				>
					<svg
						xmlns="http://www.w3.org/2000/svg"
						viewBox="0 0 20 20"
						fill="currentColor"
						class="size-4 text-gray-400 shrink-0"
					>
						<path
							fill-rule="evenodd"
							d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
							clip-rule="evenodd"
						/>
					</svg>
					<!-- svelte-ignore a11y-autofocus -->
					<input
						autofocus
						class="flex-1 bg-transparent text-sm outline-hidden placeholder-gray-400"
						bind:value={query}
						on:input={onQueryInput}
						placeholder={$i18n.t('Search in {{name}}', { name: channel.name ?? '' })}
					/>
					{#if query}
						<button
							class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
							on:click={() => {
								query = '';
								results = null;
							}}
						>
							<XMark className="size-4" />
						</button>
					{/if}
				</div>
			</div>

			<div class="px-4 pb-4">
				{#if loading}
					<div class="my-8 flex justify-center">
						<Spinner className="size-5" />
					</div>
				{:else if results === null}
					<div class="text-center text-xs text-gray-400 dark:text-gray-500 py-8">
						{$i18n.t('Type to search messages in this channel')}
					</div>
				{:else if results.length === 0}
					<div class="text-center text-xs text-gray-400 dark:text-gray-500 py-8">
						{$i18n.t('No messages found for "{{query}}"', { query })}
					</div>
				{:else}
					<div
						class="flex flex-col gap-2 max-h-[60vh] overflow-y-auto scrollbar-thin scrollbar-thumb-gray-300 dark:scrollbar-thumb-gray-700 scrollbar-track-transparent py-1"
					>
						<div class="text-xs text-gray-400 dark:text-gray-500 px-1 mb-1">
							{results.length === 50
								? $i18n.t('Showing top 50 results')
								: $i18n.t('{{count}} result(s)', { count: results.length })}
						</div>
						{#each results as message (message.id)}
							<Message
								className="rounded-xl px-2"
								{message}
								{channel}
								onPin={false}
								onReaction={false}
								onThread={false}
								onReply={false}
								onEdit={false}
								onDelete={false}
							/>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</Modal>
{/if}
