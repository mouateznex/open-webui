<script lang="ts">
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	let q = '';
	let loading = false;
	let error: string | null = null;
	let results: { open_webui: any[]; rocketchat: any } = { open_webui: [], rocketchat: {} };

	async function runSearch() {
		if (!q.trim()) return;
		loading = true;
		error = null;
		try {
			const res = await fetch(
				`${WEBUI_API_BASE_URL}/search/?q=${encodeURIComponent(q.trim())}`,
				{ headers: { Authorization: `Bearer ${localStorage.token}` } }
			);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const body = await res.json();
			results = {
				open_webui: body.open_webui ?? [],
				rocketchat: body.rocketchat ?? {}
			};
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>Search · Open WebUI</title>
</svelte:head>

<div class="px-6 py-8 max-w-3xl mx-auto">
	<h1 class="text-2xl font-semibold mb-2">Federated search</h1>
	<p class="text-sm text-gray-500 mb-4">Searches Open WebUI channels and Rocket.Chat rooms together.</p>

	<form on:submit|preventDefault={runSearch} class="flex gap-2 mb-6">
		<input
			class="px-3 py-2 rounded border flex-1 dark:bg-gray-800 dark:border-gray-700"
			type="search"
			placeholder="Search messages, rooms, users…"
			bind:value={q}
		/>
		<button class="px-4 py-2 rounded bg-blue-600 text-white" disabled={loading || !q.trim()}>
			{loading ? 'Searching…' : 'Search'}
		</button>
	</form>

	{#if error}
		<p class="text-red-500 text-sm">{error}</p>
	{/if}

	{#if results.open_webui.length || (results.rocketchat?.messages ?? []).length || (results.rocketchat?.rooms ?? []).length || (results.rocketchat?.users ?? []).length}
		<div class="space-y-6">
			{#if results.open_webui.length}
				<section>
					<h2 class="font-medium mb-2">Open WebUI messages</h2>
					<ul class="space-y-2">
						{#each results.open_webui as m}
							<li class="p-3 border rounded dark:border-gray-700">
								<a href="/channels/{m.channel_id}" class="text-blue-600 underline text-sm">#{m.channel_id}</a>
								<p class="mt-1 text-sm">{m.content}</p>
							</li>
						{/each}
					</ul>
				</section>
			{/if}
			{#if (results.rocketchat?.rooms ?? []).length}
				<section>
					<h2 class="font-medium mb-2">Rocket.Chat rooms</h2>
					<ul class="text-sm">
						{#each results.rocketchat.rooms as r}
							<li class="p-2 border rounded mb-1 dark:border-gray-700">{r.name ?? r.fname ?? r._id}</li>
						{/each}
					</ul>
				</section>
			{/if}
			{#if (results.rocketchat?.users ?? []).length}
				<section>
					<h2 class="font-medium mb-2">Rocket.Chat users</h2>
					<ul class="text-sm">
						{#each results.rocketchat.users as u}
							<li class="p-2 border rounded mb-1 dark:border-gray-700">{u.name ?? u.username}</li>
						{/each}
					</ul>
				</section>
			{/if}
			{#if (results.rocketchat?.messages ?? []).length}
				<section>
					<h2 class="font-medium mb-2">Rocket.Chat messages</h2>
					<ul class="space-y-2">
						{#each results.rocketchat.messages as m}
							<li class="p-3 border rounded dark:border-gray-700">
								<p class="text-sm">{m.msg}</p>
								<p class="text-xs text-gray-500 mt-1">room: {m.rid}</p>
							</li>
						{/each}
					</ul>
				</section>
			{/if}
		</div>
	{:else if !loading && q}
		<p class="text-gray-500 text-sm">No results.</p>
	{/if}
</div>
