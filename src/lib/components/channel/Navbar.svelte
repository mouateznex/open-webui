<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { mobile, showArchivedChats, showSidebar, user, config } from '$lib/stores';

	import { slide } from 'svelte/transition';
	import { page } from '$app/stores';

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import UserMenu from '$lib/components/layout/Sidebar/UserMenu.svelte';
	import PencilSquare from '../icons/PencilSquare.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Sidebar from '../icons/Sidebar.svelte';
	import Hashtag from '../icons/Hashtag.svelte';
	import Lock from '../icons/Lock.svelte';
	import UserAlt from '../icons/UserAlt.svelte';
	import ChannelInfoModal from './ChannelInfoModal.svelte';
	import Users from '../icons/Users.svelte';
	import Pin from '../icons/Pin.svelte';
	import PinnedMessagesModal from './PinnedMessagesModal.svelte';
	import SearchModal from './SearchModal.svelte';
	import HighlightsModal from './HighlightsModal.svelte';

	import { sendMessage } from '$lib/apis/channels';

	const i18n = getContext('i18n');

	let showChannelPinnedMessagesModal = false;
	let showChannelInfoModal = false;
	let showSearchModal = false;
	let showHighlightsModal = false;

	const startVideoCall = async () => {
		if (!channel) return;
		// Hit the backend endpoint — it tries RC's video plugin first
		// (Jitsi/BBB through video-conference.start) and falls back to a
		// deterministic Jitsi URL when RC isn't configured. Either way we get
		// a real URL to open and the room participants get a notification
		// inside Rocket.Chat.
		try {
			const res = await fetch(
				`${WEBUI_API_BASE_URL}/channels/${channel.id}/video-call`,
				{
					method: 'POST',
					headers: { Authorization: `Bearer ${localStorage.token}` }
				}
			);
			if (!res.ok) {
				const detail = (await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`;
				throw new Error(detail);
			}
			const data = await res.json();
			const callUrl = data?.url || data?.rc_call?.url;
			if (!callUrl) {
				toast.error($i18n.t('No video call URL was returned. Configure JITSI_URL or RC video conferencing.'));
				return;
			}
			// Drop a chat message with the URL so other members can join.
			await sendMessage(localStorage.token, channel.id, {
				content: `📹 **Video call started** — [Join here](${callUrl})`
			}).catch((err) => toast.error(`${err}`));
			window.open(callUrl, '_blank');
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const hasPublicReadGrant = (grants: any) =>
		Array.isArray(grants) &&
		grants.some(
			(grant) =>
				grant?.principal_type === 'user' &&
				grant?.principal_id === '*' &&
				grant?.permission === 'read'
		);

	const isPublicChannel = (channel: any): boolean => {
		if (channel?.type === 'group') {
			if (typeof channel?.is_private === 'boolean') {
				return !channel.is_private;
			}
			return hasPublicReadGrant(channel?.access_grants);
		}
		return hasPublicReadGrant(channel?.access_grants);
	};

	export let channel;

	export let onPin = (messageId, pinned) => {};
	export let onUpdate = () => {};
</script>

<PinnedMessagesModal bind:show={showChannelPinnedMessagesModal} {channel} {onPin} />
<ChannelInfoModal bind:show={showChannelInfoModal} {channel} {onUpdate} />
<SearchModal bind:show={showSearchModal} {channel} />
<HighlightsModal bind:show={showHighlightsModal} {channel} />
<nav class="sticky top-0 z-30 w-full px-1.5 py-1 -mb-8 flex items-center drag-region flex flex-col">
	<div
		id="navbar-bg-gradient-to-b"
		class=" bg-linear-to-b via-50% from-white via-white to-transparent dark:from-gray-900 dark:via-gray-900 dark:to-transparent pointer-events-none absolute inset-0 -bottom-7 z-[-1]"
	></div>

	<div class=" flex max-w-full w-full mx-auto px-1 pt-0.5 bg-transparent">
		<div class="flex items-center w-full max-w-full">
			{#if $mobile}
				<div
					class="{$showSidebar
						? 'md:hidden'
						: ''} mr-1.5 mt-0.5 self-start flex flex-none items-center text-gray-600 dark:text-gray-400"
				>
					<Tooltip
						content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
						interactive={true}
					>
						<button
							id="sidebar-toggle-button"
							class=" cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition cursor-"
							on:click={() => {
								showSidebar.set(!$showSidebar);
							}}
						>
							<div class=" self-center p-1.5">
								<Sidebar />
							</div>
						</button>
					</Tooltip>
				</div>
			{/if}

			<div
				class="flex-1 overflow-hidden max-w-full py-0.5 flex items-center
			{$showSidebar ? 'ml-1' : ''}
			"
			>
				{#if channel}
					<div class="flex items-center gap-0.5 shrink-0">
						{#if channel?.type === 'dm'}
							{#if channel?.users}
								{@const channelMembers = channel.users.filter((u) => u.id !== $user?.id)}
								<div class="flex mr-1.5 relative">
									{#each channelMembers.slice(0, 2) as u, index}
										<img
											src={`${WEBUI_API_BASE_URL}/users/${u.id}/profile/image`}
											alt={u.name}
											class=" size-6.5 rounded-full border-2 border-white dark:border-gray-900 {index ===
											1
												? '-ml-3'
												: ''}"
										/>
									{/each}

									{#if channelMembers.length === 1}
										<div class="absolute bottom-0 right-0">
											<span class="relative flex size-2">
												{#if channelMembers[0]?.is_active}
													<span
														class="absolute inline-flex h-full w-full animate-ping rounded-full bg-green-400 opacity-75"
													></span>
												{/if}
												<span
													class="relative inline-flex size-2 rounded-full {channelMembers[0]
														?.is_active
														? 'bg-green-500'
														: 'bg-gray-300 dark:bg-gray-700'} border-[1.5px] border-white dark:border-gray-900"
												></span>
											</span>
										</div>
									{/if}
								</div>
							{:else}
								<Users className="size-4 ml-1 mr-0.5" strokeWidth="2" />
							{/if}
						{:else}
							<div class=" size-4.5 justify-center flex items-center">
								{#if isPublicChannel(channel)}
									<Hashtag className="size-3.5" strokeWidth="2.5" />
								{:else}
									<Lock className="size-5" strokeWidth="2" />
								{/if}
							</div>
						{/if}

						<div class=" text-left self-center overflow-hidden w-full line-clamp-1 flex-1">
							{#if channel?.name}
								{channel.name}
							{:else}
								{channel?.users
									?.filter((u) => u.id !== $user?.id)
									.map((u) => u.name)
									.join(', ')}
							{/if}
						</div>
					</div>
				{/if}
			</div>

			<div
				class="self-start flex flex-none items-center text-gray-600 dark:text-gray-400 gap-1 shrink-0"
			>
				{#if channel}
					{#if (($config?.features?.jitsi_url) || $config?.features?.rocketchat_enabled) && channel?.type !== 'dm'}
						<Tooltip content={$i18n.t('Start Video Call')}>
							<button
								class=" flex cursor-pointer py-1.5 px-1.5 border dark:border-gray-850 border-gray-50 rounded-xl text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								aria-label="Start Video Call"
								type="button"
								on:click={startVideoCall}
							>
								<div class=" flex items-center gap-0.5 m-auto self-center shrink-0">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-4"
									>
										<path
											d="M4.5 4.5a3 3 0 0 0-3 3v9a3 3 0 0 0 3 3h8.25a3 3 0 0 0 3-3v-9a3 3 0 0 0-3-3H4.5ZM19.94 18.75l-2.69-2.69V7.94l2.69-2.69c.944-.945 2.56-.276 2.56 1.06v11.38c0 1.336-1.616 2.005-2.56 1.06Z"
										/>
									</svg>
								</div>
							</button>
						</Tooltip>
					{/if}

					<Tooltip content={$i18n.t('Search Messages')}>
						<button
							class=" flex cursor-pointer py-1.5 px-1.5 border dark:border-gray-850 border-gray-50 rounded-xl text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
							aria-label="Search Messages"
							type="button"
							on:click={() => {
								showSearchModal = true;
							}}
						>
							<div class=" flex items-center gap-0.5 m-auto self-center shrink-0">
								<svg
									xmlns="http://www.w3.org/2000/svg"
									viewBox="0 0 20 20"
									fill="currentColor"
									class="size-4"
								>
									<path
										fill-rule="evenodd"
										d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z"
										clip-rule="evenodd"
									/>
								</svg>
							</div>
						</button>
					</Tooltip>

					<Tooltip content={$i18n.t('Pinned Messages')}>
						<button
							class=" flex cursor-pointer py-1.5 px-1.5 border dark:border-gray-850 border-gray-50 rounded-xl text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
							aria-label="Pinned Messages"
							type="button"
							on:click={() => {
								showChannelPinnedMessagesModal = true;
							}}
						>
							<div class=" flex items-center gap-0.5 m-auto self-center shrink-0">
								<Pin className=" size-4" strokeWidth="1.5" />
							</div>
						</button>
					</Tooltip>

					{#if $config?.features?.rocketchat_enabled}
						<Tooltip content={$i18n.t('Starred & Threads')}>
							<button
								class=" flex cursor-pointer py-1.5 px-1.5 border dark:border-gray-850 border-gray-50 rounded-xl text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								aria-label="Starred & Threads"
								type="button"
								on:click={() => {
									showHighlightsModal = true;
								}}
							>
								<div class=" flex items-center gap-0.5 m-auto self-center shrink-0">
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 24 24"
										fill="currentColor"
										class="size-4"
									>
										<path
											fill-rule="evenodd"
											d="M10.788 3.21c.448-1.077 1.976-1.077 2.424 0l2.082 5.007 5.404.434c1.164.093 1.636 1.545.749 2.305l-4.117 3.527 1.257 5.273c.271 1.136-.964 2.033-1.96 1.425L12 18.354 5.373 22.18c-.996.608-2.231-.29-1.96-1.425l1.257-5.273-4.117-3.527c-.887-.76-.415-2.212.749-2.305l5.404-.434 2.082-5.006Z"
											clip-rule="evenodd"
										/>
									</svg>
								</div>
							</button>
						</Tooltip>
					{/if}

					{#if channel?.user_count !== undefined}
						<Tooltip content={$i18n.t('Users')}>
							<button
								class=" flex cursor-pointer shrink-0 py-1 px-1.5 border dark:border-gray-850 border-gray-50 rounded-xl text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								aria-label="User Count"
								type="button"
								on:click={() => {
									showChannelInfoModal = true;
								}}
							>
								<div class=" flex items-center gap-0.5 m-auto self-center shrink-0">
									<UserAlt className=" size-4" strokeWidth="1.5" />

									<div class="text-sm shrink-0">
										{channel.user_count}
									</div>
								</div>
							</button>
						</Tooltip>
					{/if}
				{/if}

				{#if $user !== undefined}
					<div>
						<UserMenu
							className="w-[240px]"
							role={$user?.role}
							help={true}
							on:show={(e) => {
								if (e.detail === 'archived-chat') {
									showArchivedChats.set(true);
								}
							}}
						>
							<button
								class="select-none flex rounded-xl p-1.5 w-full hover:bg-gray-50 dark:hover:bg-gray-850 transition"
								aria-label="User Menu"
							>
								<div class=" self-center">
									<img
										src={`${WEBUI_API_BASE_URL}/users/${$user?.id}/profile/image`}
										class="size-6 object-cover rounded-full"
										alt="User profile"
										draggable="false"
									/>
								</div>
							</button>
						</UserMenu>
					</div>
				{/if}
			</div>
		</div>
	</div>
</nav>
