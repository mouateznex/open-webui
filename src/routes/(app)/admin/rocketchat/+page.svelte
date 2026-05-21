<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const i18n = getContext('i18n');

	type Tab = 'queue' | 'webhooks' | 'apps' | 'audit';
	let activeTab: Tab = 'queue';

	// ─────────────────────── queue ───────────────────────
	let queueStats: any = null;
	let queueJobs: any = null;
	let queueLoading = false;

	async function loadQueue() {
		queueLoading = true;
		try {
			const [statsRes, jobsRes] = await Promise.all([
				fetch(`${WEBUI_API_BASE_URL}/users/rocketchat/queue/stats`, {
					headers: { Authorization: `Bearer ${localStorage.token}` }
				}),
				fetch(`${WEBUI_API_BASE_URL}/users/rocketchat/queue/jobs`, {
					headers: { Authorization: `Bearer ${localStorage.token}` }
				})
			]);
			if (statsRes.ok) queueStats = await statsRes.json();
			if (jobsRes.ok) queueJobs = await jobsRes.json();
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			queueLoading = false;
		}
	}

	async function replayDead() {
		const res = await fetch(
			`${WEBUI_API_BASE_URL}/users/rocketchat/queue/replay-dead`,
			{
				method: 'POST',
				headers: { Authorization: `Bearer ${localStorage.token}` }
			}
		);
		if (res.ok) {
			const moved = await res.json();
			toast.success(`Replayed ${moved} dead-letter jobs`);
			await loadQueue();
		} else {
			toast.error(`HTTP ${res.status}`);
		}
	}

	// ─────────────────────── webhooks ───────────────────────
	let integrations: any[] = [];
	let integrationsLoading = false;

	let newIncoming = { name: '', channel: '#general', username: 'rocket.cat' };
	let newOutgoing = { name: '', channel: '#general', urls: '', trigger_words: '' };

	async function loadIntegrations() {
		integrationsLoading = true;
		try {
			const res = await fetch(
				`${WEBUI_API_BASE_URL}/rocketchat/webhooks/integrations`,
				{ headers: { Authorization: `Bearer ${localStorage.token}` } }
			);
			if (res.ok) integrations = await res.json();
			else toast.error(`HTTP ${res.status}`);
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			integrationsLoading = false;
		}
	}

	async function createIncoming() {
		if (!newIncoming.name || !newIncoming.channel) return;
		const res = await fetch(`${WEBUI_API_BASE_URL}/rocketchat/webhooks/incoming/create`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${localStorage.token}`
			},
			body: JSON.stringify(newIncoming)
		});
		if (res.ok) {
			toast.success('Incoming webhook created');
			newIncoming = { name: '', channel: '#general', username: 'rocket.cat' };
			await loadIntegrations();
		} else {
			toast.error(`HTTP ${res.status}`);
		}
	}

	async function createOutgoing() {
		if (!newOutgoing.name || !newOutgoing.channel || !newOutgoing.urls) return;
		const res = await fetch(`${WEBUI_API_BASE_URL}/rocketchat/webhooks/outgoing/create`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${localStorage.token}`
			},
			body: JSON.stringify({
				name: newOutgoing.name,
				channel: newOutgoing.channel,
				urls: newOutgoing.urls.split(',').map((s) => s.trim()).filter(Boolean),
				trigger_words: newOutgoing.trigger_words
					.split(',')
					.map((s) => s.trim())
					.filter(Boolean)
			})
		});
		if (res.ok) {
			toast.success('Outgoing webhook created');
			newOutgoing = { name: '', channel: '#general', urls: '', trigger_words: '' };
			await loadIntegrations();
		} else {
			toast.error(`HTTP ${res.status}`);
		}
	}

	async function deleteIntegration(id: string, type: string) {
		if (!confirm('Delete this integration?')) return;
		const res = await fetch(
			`${WEBUI_API_BASE_URL}/rocketchat/webhooks/${id}?integration_type=${encodeURIComponent(type)}`,
			{
				method: 'DELETE',
				headers: { Authorization: `Bearer ${localStorage.token}` }
			}
		);
		if (res.ok) {
			toast.success('Deleted');
			await loadIntegrations();
		} else {
			toast.error(`HTTP ${res.status}`);
		}
	}

	// ─────────────────────── apps ───────────────────────
	let appsInstalled: any[] = [];
	let appsMarketplace: any[] = [];
	let appsLoading = false;

	async function loadApps() {
		appsLoading = true;
		try {
			const [a, b] = await Promise.all([
				fetch(`${WEBUI_API_BASE_URL}/rocketchat/apps/installed`, {
					headers: { Authorization: `Bearer ${localStorage.token}` }
				}),
				fetch(`${WEBUI_API_BASE_URL}/rocketchat/apps/marketplace`, {
					headers: { Authorization: `Bearer ${localStorage.token}` }
				})
			]);
			if (a.ok) appsInstalled = await a.json();
			if (b.ok) appsMarketplace = await b.json();
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			appsLoading = false;
		}
	}

	// ─────────────────────── audit ───────────────────────
	let auditEntries: any[] = [];
	let auditMessageEntries: any[] = [];
	let auditLoading = false;
	let auditRoomFilter = '';

	async function loadAudit() {
		auditLoading = true;
		try {
			const [a, b] = await Promise.all([
				fetch(`${WEBUI_API_BASE_URL}/rocketchat/audit/`, {
					headers: { Authorization: `Bearer ${localStorage.token}` }
				}),
				fetch(
					`${WEBUI_API_BASE_URL}/rocketchat/audit/messages${
						auditRoomFilter ? `?room_id=${encodeURIComponent(auditRoomFilter)}` : ''
					}`,
					{ headers: { Authorization: `Bearer ${localStorage.token}` } }
				)
			]);
			auditEntries = a.ok ? await a.json() : [];
			auditMessageEntries = b.ok ? await b.json() : [];
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			auditLoading = false;
		}
	}

	function fmtAuditTs(ts: any): string {
		if (!ts) return '';
		if (typeof ts === 'object' && ts.$date) return new Date(Number(ts.$date)).toLocaleString();
		const v = typeof ts === 'string' ? Date.parse(ts) : Number(ts);
		return Number.isFinite(v) ? new Date(v).toLocaleString() : '';
	}

	function switchTab(t: Tab) {
		activeTab = t;
		if (t === 'queue') loadQueue();
		if (t === 'webhooks') loadIntegrations();
		if (t === 'apps') loadApps();
		if (t === 'audit') loadAudit();
	}

	onMount(() => loadQueue());
</script>

<svelte:head>
	<title>{$i18n.t('Rocket.Chat')} · Admin</title>
</svelte:head>

<div class="px-6 py-6 max-w-5xl mx-auto">
	<h1 class="text-2xl font-semibold mb-1">{$i18n.t('Rocket.Chat')}</h1>
	<p class="text-sm text-gray-500 mb-4">
		{$i18n.t('Manage the Rocket.Chat sync queue, webhooks, and Apps Engine.')}
	</p>

	<nav class="flex gap-1 mb-6 border-b dark:border-gray-800">
		<button
			class="px-3 py-2 text-sm border-b-2 -mb-px {activeTab === 'queue'
				? 'border-blue-500 text-blue-600'
				: 'border-transparent text-gray-500 hover:text-gray-700'}"
			on:click={() => switchTab('queue')}
		>
			{$i18n.t('Sync Queue')}
		</button>
		<button
			class="px-3 py-2 text-sm border-b-2 -mb-px {activeTab === 'webhooks'
				? 'border-blue-500 text-blue-600'
				: 'border-transparent text-gray-500 hover:text-gray-700'}"
			on:click={() => switchTab('webhooks')}
		>
			{$i18n.t('Webhooks')}
		</button>
		<button
			class="px-3 py-2 text-sm border-b-2 -mb-px {activeTab === 'apps'
				? 'border-blue-500 text-blue-600'
				: 'border-transparent text-gray-500 hover:text-gray-700'}"
			on:click={() => switchTab('apps')}
		>
			{$i18n.t('Apps & Marketplace')}
		</button>
		<button
			class="px-3 py-2 text-sm border-b-2 -mb-px {activeTab === 'audit'
				? 'border-blue-500 text-blue-600'
				: 'border-transparent text-gray-500 hover:text-gray-700'}"
			on:click={() => switchTab('audit')}
		>
			{$i18n.t('Audit Logs')}
		</button>
	</nav>

	{#if activeTab === 'queue'}
		<section>
			<header class="flex items-center justify-between mb-4">
				<h2 class="text-lg font-medium">{$i18n.t('Persistent sync queue')}</h2>
				<div class="flex gap-2">
					<button
						class="px-3 py-1 rounded border text-sm dark:border-gray-700"
						on:click={loadQueue}
						disabled={queueLoading}
					>
						{$i18n.t('Refresh')}
					</button>
					<button
						class="px-3 py-1 rounded bg-amber-600 text-white text-sm"
						on:click={replayDead}
					>
						{$i18n.t('Replay dead-letter')}
					</button>
				</div>
			</header>

			{#if queueStats}
				<div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
					<div class="border rounded p-3 dark:border-gray-700">
						<div class="text-xs text-gray-500">{$i18n.t('Pending')}</div>
						<div class="text-lg font-semibold">{queueStats?.queue?.pending ?? 0}</div>
					</div>
					<div class="border rounded p-3 dark:border-gray-700">
						<div class="text-xs text-gray-500">{$i18n.t('Dead-letter')}</div>
						<div class="text-lg font-semibold">{queueStats?.dead_letter_count ?? 0}</div>
					</div>
					<div class="border rounded p-3 dark:border-gray-700">
						<div class="text-xs text-gray-500">{$i18n.t('Oldest job (s)')}</div>
						<div class="text-lg font-semibold">
							{Math.round(queueStats?.queue?.oldest_age_seconds ?? 0)}
						</div>
					</div>
					<div class="border rounded p-3 dark:border-gray-700">
						<div class="text-xs text-gray-500">{$i18n.t('Total attempts')}</div>
						<div class="text-lg font-semibold">{queueStats?.queue?.attempts_total ?? 0}</div>
					</div>
				</div>
			{/if}

			<h3 class="font-medium mb-2 text-sm">{$i18n.t('Pending jobs')}</h3>
			{#if (queueJobs?.pending ?? []).length === 0}
				<p class="text-sm text-gray-500 mb-6">{$i18n.t('No pending jobs.')}</p>
			{:else}
				<div class="overflow-x-auto mb-6">
					<table class="w-full text-sm border dark:border-gray-700 rounded">
						<thead class="bg-gray-50 dark:bg-gray-900">
							<tr>
								<th class="px-3 py-2 text-left">Type</th>
								<th class="px-3 py-2 text-left">Attempts</th>
								<th class="px-3 py-2 text-left">Next attempt</th>
								<th class="px-3 py-2 text-left">Last error</th>
							</tr>
						</thead>
						<tbody>
							{#each queueJobs.pending as j}
								<tr class="border-t dark:border-gray-800">
									<td class="px-3 py-2 font-mono">{j.type}</td>
									<td class="px-3 py-2">{j.attempts}</td>
									<td class="px-3 py-2">
										{new Date(j.next_attempt_at * 1000).toLocaleString()}
									</td>
									<td class="px-3 py-2 text-red-500 truncate max-w-xs">{j.last_error ?? ''}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}

			<h3 class="font-medium mb-2 text-sm">{$i18n.t('Dead-letter')}</h3>
			{#if (queueJobs?.dead ?? []).length === 0}
				<p class="text-sm text-gray-500">{$i18n.t('No dead-letter jobs.')}</p>
			{:else}
				<div class="overflow-x-auto">
					<table class="w-full text-sm border dark:border-gray-700 rounded">
						<thead class="bg-gray-50 dark:bg-gray-900">
							<tr>
								<th class="px-3 py-2 text-left">Type</th>
								<th class="px-3 py-2 text-left">Attempts</th>
								<th class="px-3 py-2 text-left">Last error</th>
							</tr>
						</thead>
						<tbody>
							{#each queueJobs.dead as j}
								<tr class="border-t dark:border-gray-800">
									<td class="px-3 py-2 font-mono">{j.type}</td>
									<td class="px-3 py-2">{j.attempts}</td>
									<td class="px-3 py-2 text-red-500 truncate max-w-xs">{j.last_error ?? ''}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{:else if activeTab === 'webhooks'}
		<section class="space-y-6">
			<div class="grid md:grid-cols-2 gap-6">
				<div class="border rounded p-4 dark:border-gray-700">
					<h2 class="text-lg font-medium mb-3">{$i18n.t('Create incoming webhook')}</h2>
					<p class="text-xs text-gray-500 mb-3">
						{$i18n.t('Generates a Rocket.Chat URL that external services POST to.')}
					</p>
					<div class="space-y-2">
						<input
							class="w-full px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700"
							placeholder="Name"
							bind:value={newIncoming.name}
						/>
						<input
							class="w-full px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700"
							placeholder="#channel-name"
							bind:value={newIncoming.channel}
						/>
						<input
							class="w-full px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700"
							placeholder="post-as username"
							bind:value={newIncoming.username}
						/>
						<button
							class="w-full px-3 py-2 rounded bg-blue-600 text-white"
							on:click={createIncoming}
						>
							{$i18n.t('Create')}
						</button>
					</div>
				</div>

				<div class="border rounded p-4 dark:border-gray-700">
					<h2 class="text-lg font-medium mb-3">{$i18n.t('Create outgoing webhook')}</h2>
					<p class="text-xs text-gray-500 mb-3">
						{$i18n.t('Rocket.Chat POSTs to your URL when triggered.')}
					</p>
					<div class="space-y-2">
						<input
							class="w-full px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700"
							placeholder="Name"
							bind:value={newOutgoing.name}
						/>
						<input
							class="w-full px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700"
							placeholder="#channel-name"
							bind:value={newOutgoing.channel}
						/>
						<input
							class="w-full px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700"
							placeholder="https://your.url, https://other.url"
							bind:value={newOutgoing.urls}
						/>
						<input
							class="w-full px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700"
							placeholder="trigger,words,csv"
							bind:value={newOutgoing.trigger_words}
						/>
						<button
							class="w-full px-3 py-2 rounded bg-blue-600 text-white"
							on:click={createOutgoing}
						>
							{$i18n.t('Create')}
						</button>
					</div>
				</div>
			</div>

			<div>
				<header class="flex items-center justify-between mb-2">
					<h2 class="text-lg font-medium">{$i18n.t('Existing integrations')}</h2>
					<button
						class="px-3 py-1 rounded border text-sm dark:border-gray-700"
						on:click={loadIntegrations}
					>
						{$i18n.t('Refresh')}
					</button>
				</header>
				{#if integrationsLoading}
					<p class="text-sm text-gray-500">{$i18n.t('Loading…')}</p>
				{:else if integrations.length === 0}
					<p class="text-sm text-gray-500">{$i18n.t('No integrations configured.')}</p>
				{:else}
					<div class="overflow-x-auto">
						<table class="w-full text-sm border dark:border-gray-700 rounded">
							<thead class="bg-gray-50 dark:bg-gray-900">
								<tr>
									<th class="px-3 py-2 text-left">Name</th>
									<th class="px-3 py-2 text-left">Type</th>
									<th class="px-3 py-2 text-left">Channel</th>
									<th class="px-3 py-2 text-left">Enabled</th>
									<th class="px-3 py-2"></th>
								</tr>
							</thead>
							<tbody>
								{#each integrations as i}
									<tr class="border-t dark:border-gray-800">
										<td class="px-3 py-2">{i.name}</td>
										<td class="px-3 py-2">{i.type}</td>
										<td class="px-3 py-2">
											{Array.isArray(i.channel) ? i.channel.join(', ') : i.channel}
										</td>
										<td class="px-3 py-2">{i.enabled ? '✓' : '—'}</td>
										<td class="px-3 py-2 text-right">
											<button
												class="text-red-600 hover:text-red-700 text-xs"
												on:click={() => deleteIntegration(i._id, i.type)}
											>
												{$i18n.t('Delete')}
											</button>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</div>
		</section>
	{:else if activeTab === 'apps'}
		<section class="space-y-6">
			<div>
				<header class="flex items-center justify-between mb-2">
					<h2 class="text-lg font-medium">{$i18n.t('Installed apps')}</h2>
					<button
						class="px-3 py-1 rounded border text-sm dark:border-gray-700"
						on:click={loadApps}
					>
						{$i18n.t('Refresh')}
					</button>
				</header>
				{#if appsLoading}
					<p class="text-sm text-gray-500">{$i18n.t('Loading…')}</p>
				{:else if appsInstalled.length === 0}
					<p class="text-sm text-gray-500">{$i18n.t('No apps installed.')}</p>
				{:else}
					<ul class="grid md:grid-cols-2 gap-3">
						{#each appsInstalled as a}
							<li class="border rounded p-3 dark:border-gray-700">
								<div class="font-medium">{a.name ?? a.id}</div>
								<div class="text-xs text-gray-500">v{a.version} · {a.author?.name ?? ''}</div>
								{#if a.description}
									<div class="text-sm mt-2 line-clamp-2">{a.description}</div>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<div>
				<h2 class="text-lg font-medium mb-2">{$i18n.t('Marketplace')}</h2>
				{#if appsMarketplace.length === 0}
					<p class="text-sm text-gray-500">
						{$i18n.t('Marketplace is empty or unavailable from this Rocket.Chat instance.')}
					</p>
				{:else}
					<ul class="grid md:grid-cols-2 gap-3">
						{#each appsMarketplace.slice(0, 24) as a}
							<li class="border rounded p-3 dark:border-gray-700">
								<div class="font-medium">{a.name ?? a.appName ?? a.id}</div>
								<div class="text-xs text-gray-500">{a.shortDescription ?? ''}</div>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</section>
	{:else if activeTab === 'audit'}
		<section class="space-y-6">
			<header class="flex flex-wrap items-center gap-3">
				<h2 class="text-lg font-medium flex-1">{$i18n.t('Rocket.Chat audit logs')}</h2>
				<input
					class="px-3 py-1 rounded border text-sm dark:bg-gray-800 dark:border-gray-700"
					placeholder="Filter messages by RC roomId"
					bind:value={auditRoomFilter}
				/>
				<button
					class="px-3 py-1 rounded border text-sm dark:border-gray-700"
					on:click={loadAudit}
					disabled={auditLoading}
				>
					{$i18n.t('Refresh')}
				</button>
			</header>

			<div>
				<h3 class="font-medium mb-2 text-sm">{$i18n.t('General audit')}</h3>
				{#if auditLoading}
					<p class="text-sm text-gray-500">{$i18n.t('Loading…')}</p>
				{:else if auditEntries.length === 0}
					<p class="text-sm text-gray-500">
						{$i18n.t(
							'No audit entries returned. Audit may be disabled in Rocket.Chat or restricted to enterprise editions.'
						)}
					</p>
				{:else}
					<div class="overflow-x-auto">
						<table class="w-full text-sm border dark:border-gray-700 rounded">
							<thead class="bg-gray-50 dark:bg-gray-900">
								<tr>
									<th class="px-3 py-2 text-left">When</th>
									<th class="px-3 py-2 text-left">Actor</th>
									<th class="px-3 py-2 text-left">Action</th>
									<th class="px-3 py-2 text-left">Detail</th>
								</tr>
							</thead>
							<tbody>
								{#each auditEntries as e}
									<tr class="border-t dark:border-gray-800 align-top">
										<td class="px-3 py-2 whitespace-nowrap">{fmtAuditTs(e.ts ?? e.when ?? e._updatedAt)}</td>
										<td class="px-3 py-2">
											{e?.u?.username ?? e.user ?? e.actor ?? '?'}
										</td>
										<td class="px-3 py-2 font-mono text-xs">{e.action ?? e.event ?? ''}</td>
										<td class="px-3 py-2 text-xs">
											<pre class="whitespace-pre-wrap break-all">{JSON.stringify(
													e.details ?? e.payload ?? e,
													null,
													2
												)}</pre>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</div>

			<div>
				<h3 class="font-medium mb-2 text-sm">{$i18n.t('Message audit')}</h3>
				{#if auditMessageEntries.length === 0}
					<p class="text-sm text-gray-500">
						{$i18n.t('No message audit entries.')}
					</p>
				{:else}
					<ul class="space-y-2">
						{#each auditMessageEntries as m}
							<li class="border rounded p-3 dark:border-gray-700 text-xs">
								<div class="text-gray-500">
									{fmtAuditTs(m.ts)} · room <code>{m.rid}</code> · user <code>{m.u?.username ?? m.u?._id ?? ''}</code>
								</div>
								<div class="mt-1 whitespace-pre-wrap">{m.msg ?? '(no body)'}</div>
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		</section>
	{/if}
</div>
