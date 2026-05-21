<script lang="ts">
	import { onMount } from 'svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	let teams: any[] = [];
	let loading = true;
	let error: string | null = null;

	// Form state for creating a new team
	let newTeamName = '';
	let newTeamPrivate = false;
	let creating = false;

	async function loadTeams() {
		loading = true;
		error = null;
		try {
			const res = await fetch(`${WEBUI_API_BASE_URL}/teams/`, {
				headers: { Authorization: `Bearer ${localStorage.token}` }
			});
			if (!res.ok) {
				throw new Error(`HTTP ${res.status}`);
			}
			teams = await res.json();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			teams = [];
		} finally {
			loading = false;
		}
	}

	async function createTeam() {
		if (!newTeamName.trim()) return;
		creating = true;
		try {
			const res = await fetch(`${WEBUI_API_BASE_URL}/teams/create`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					Authorization: `Bearer ${localStorage.token}`
				},
				body: JSON.stringify({ name: newTeamName, private: newTeamPrivate, members: [] })
			});
			if (res.ok) {
				newTeamName = '';
				newTeamPrivate = false;
				await loadTeams();
			} else {
				error = `Create failed: HTTP ${res.status}`;
			}
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			creating = false;
		}
	}

	async function deleteTeam(teamId: string) {
		if (!confirm('Delete this team?')) return;
		const res = await fetch(`${WEBUI_API_BASE_URL}/teams/${teamId}`, {
			method: 'DELETE',
			headers: { Authorization: `Bearer ${localStorage.token}` }
		});
		if (res.ok) {
			await loadTeams();
		}
	}

	onMount(loadTeams);
</script>

<svelte:head>
	<title>Teams · Open WebUI</title>
</svelte:head>

<div class="px-6 py-8 max-w-3xl mx-auto">
	<header class="mb-6">
		<h1 class="text-2xl font-semibold">Teams</h1>
		<p class="text-sm text-gray-500 mt-1">
			Rocket.Chat teams created here are visible in both Open WebUI and Rocket.Chat.
		</p>
	</header>

	<section class="mb-6 p-4 rounded border bg-white dark:bg-gray-900 dark:border-gray-700">
		<h2 class="text-lg font-medium mb-3">Create a team</h2>
		<form
			class="flex flex-wrap items-center gap-3"
			on:submit|preventDefault={createTeam}
		>
			<input
				class="px-3 py-2 rounded border dark:bg-gray-800 dark:border-gray-700 flex-1 min-w-[200px]"
				type="text"
				bind:value={newTeamName}
				placeholder="Team name"
				required
			/>
			<label class="text-sm flex items-center gap-2">
				<input type="checkbox" bind:checked={newTeamPrivate} />
				Private
			</label>
			<button
				type="submit"
				disabled={creating || !newTeamName.trim()}
				class="px-4 py-2 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
			>
				{creating ? 'Creating…' : 'Create'}
			</button>
		</form>
	</section>

	{#if loading}
		<p class="text-sm text-gray-500">Loading teams…</p>
	{:else if error}
		<p class="text-sm text-red-500">Error: {error}</p>
	{:else if teams.length === 0}
		<p class="text-sm text-gray-500">No teams yet.</p>
	{:else}
		<ul class="divide-y border rounded dark:border-gray-700">
			{#each teams as team}
				<li class="px-4 py-3 flex items-center justify-between">
					<div>
						<div class="font-medium">{team.name}</div>
						<div class="text-xs text-gray-500">
							{team.type === 1 ? 'Private' : 'Public'} · {team.usersCount ?? 0} members
						</div>
					</div>
					<button
						class="text-sm text-red-600 hover:text-red-700"
						on:click={() => deleteTeam(team._id)}
					>
						Delete
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
