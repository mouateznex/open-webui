<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { getContext, onMount } from 'svelte';
	const i18n = getContext('i18n');

	import { config } from '$lib/stores';
	import { removeMembersById, getChannelFederationInfo } from '$lib/apis/channels';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import Modal from '$lib/components/common/Modal.svelte';

	import XMark from '$lib/components/icons/XMark.svelte';
	import Hashtag from '../icons/Hashtag.svelte';
	import Lock from '../icons/Lock.svelte';
	import UserList from './ChannelInfoModal/UserList.svelte';
	import AddMembersModal from './ChannelInfoModal/AddMembersModal.svelte';

	export let show = false;
	export let channel: any = null;

	export let onUpdate = () => {};

	let showAddMembersModal = false;
	let federationInfo: any = null;

	const submitHandler = async () => {};

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

	const removeMemberHandler = async (userId) => {
		const res = await removeMembersById(localStorage.token, channel.id, {
			user_ids: [userId]
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			toast.success($i18n.t('Member removed successfully'));
			onUpdate();
		} else {
			toast.error($i18n.t('Failed to remove member'));
		}
	};

	const init = async () => {
		federationInfo = null;
		if (channel && channel.type !== 'dm' && $config?.features?.matrix_homeserver_domain) {
			federationInfo = await getChannelFederationInfo(localStorage.token, channel.id).catch(
				() => null
			);
		}
	};

	$: if (show) {
		init();
	}

	onMount(() => {
		init();
	});
</script>

{#if channel}
	<AddMembersModal bind:show={showAddMembersModal} {channel} {onUpdate} />
	<Modal size="sm" bind:show>
		<div>
			<div class=" flex justify-between dark:text-gray-100 px-5 pt-4 mb-1.5">
				<div class="self-center text-base">
					<div class="flex items-center gap-0.5 shrink-0">
						{#if channel?.type === 'dm'}
							<div class=" text-left self-center overflow-hidden w-full line-clamp-1 flex-1">
								{$i18n.t('Direct Message')}
							</div>
						{:else}
							<div class=" size-4 justify-center flex items-center">
								{#if isPublicChannel(channel)}
									<Hashtag className="size-3.5" strokeWidth="2.5" />
								{:else}
									<Lock className="size-5.5" strokeWidth="2" />
								{/if}
							</div>

							<div class=" text-left self-center overflow-hidden w-full line-clamp-1 flex-1">
								{channel.name}
							</div>
						{/if}
					</div>
				</div>
				<button
					class="self-center"
					on:click={() => {
						show = false;
					}}
				>
					<XMark className={'size-5'} />
				</button>
			</div>

			<div class="flex flex-col md:flex-row w-full px-3 pb-4 md:space-x-4 dark:text-gray-200">
				<div class=" flex flex-col w-full sm:flex-row sm:justify-center sm:space-x-6">
					<form
						class="flex flex-col w-full"
						on:submit={(e) => {
							e.preventDefault();
							submitHandler();
						}}
					>
						<div class="flex flex-col w-full h-full pb-2">
							<UserList
								{channel}
								onAdd={channel?.type === 'group' && channel?.is_manager
									? () => {
											showAddMembersModal = true;
										}
									: null}
								onRemove={channel?.type === 'group' && channel?.is_manager
									? (userId) => {
											removeMemberHandler(userId);
										}
									: null}
								search={channel?.type !== 'dm'}
								sort={channel?.type !== 'dm'}
							/>
						</div>

						{#if federationInfo?.matrix_room_alias}
							<div
								class="mt-2 mb-1 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 px-3 py-2.5 text-xs"
							>
								<div class="flex items-center gap-1.5 mb-1.5">
									<!-- Matrix logo mark (simplified M) -->
									<svg
										viewBox="0 0 32 32"
										fill="none"
										xmlns="http://www.w3.org/2000/svg"
										class="size-3.5 shrink-0"
									>
										<path
											d="M1 1h3v30H1V1zm29 0h-3v30h3V1zM7 8h3v3.5A7 7 0 0 1 16 9a7 7 0 0 1 6-3v3a4 4 0 0 0-4 4v10h-3V13a4 4 0 0 0-4-4 4 4 0 0 0-4 4v10H7V8z"
											fill="currentColor"
										/>
									</svg>
									<span class="font-medium text-gray-700 dark:text-gray-300"
										>{$i18n.t('Matrix Room Address')}</span
									>
									{#if federationInfo.federation_active}
										<span
											class="ml-auto text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400"
										>
											{$i18n.t('Active')}
										</span>
									{/if}
								</div>

								<div class="flex items-center gap-2">
									<code
										class="flex-1 font-mono text-[11px] text-gray-600 dark:text-gray-400 truncate select-all"
										title={federationInfo.matrix_room_alias}
									>
										{federationInfo.matrix_room_alias}
									</code>
									<button
										class="shrink-0 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
										title={$i18n.t('Copy')}
										on:click={() => {
											navigator.clipboard
												.writeText(federationInfo.matrix_room_alias)
												.then(() => toast.success($i18n.t('Copied to clipboard')));
										}}
									>
										<svg
											xmlns="http://www.w3.org/2000/svg"
											viewBox="0 0 20 20"
											fill="currentColor"
											class="size-4"
										>
											<path
												d="M7 3.5A1.5 1.5 0 0 1 8.5 2h3.879a1.5 1.5 0 0 1 1.06.44l3.122 3.12A1.5 1.5 0 0 1 17 6.622V12.5a1.5 1.5 0 0 1-1.5 1.5h-1v-3.379a3 3 0 0 0-.879-2.121L10.5 5.379A3 3 0 0 0 8.379 4.5H7v-1Z"
											/>
											<path
												d="M4.5 6A1.5 1.5 0 0 0 3 7.5v9A1.5 1.5 0 0 0 4.5 18h7a1.5 1.5 0 0 0 1.5-1.5v-5.879a1.5 1.5 0 0 0-.44-1.06L9.44 6.439A1.5 1.5 0 0 0 8.378 6H4.5Z"
											/>
										</svg>
									</button>
								</div>

								<p class="mt-1.5 text-[10px] text-gray-400 dark:text-gray-500">
									{$i18n.t('Share this address with Matrix users to let them join from any Matrix server.')}
								</p>
							</div>
						{/if}
					</form>
				</div>
			</div>
		</div>
	</Modal>
{/if}
