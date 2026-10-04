<script lang="ts">
	import { goto } from '$app/navigation';
	import {
		workspaceList,
		removeWorkspace,
		reorderWorkspaces,
		sidebarOpen,
		activeTab,
		currentWorkspace,
		sleepClosedChatTabs
	} from '$lib/stores';
	import {
		chatEnabled,
		chatStatuses,
		setChatClosed,
		updateChatStatuses
	} from '$lib/stores/chat';
	import { socketStore } from '$lib/stores/socket.svelte';
	import {
		deleteChat as apiDeleteChat,
		getChats,
		updateChatTitle,
		type ChatInfo
	} from '$lib/apis/chat';
	import { getWorkspaceDwell } from '$lib/apis/perf';
	import {
		collapseList,
		DEFAULT_LIMIT,
		loadWorkspacePriority,
		pinnedWorkspaces,
		resurface,
		visibleWorkspaces,
		workspacePriority,
		workspacesExpandedList
	} from '$lib/stores/workspacePriority.svelte';
	import { t } from '$lib/i18n';
	import { tooltip } from '$lib/tooltip';
	import Sortable from 'sortablejs';
	import { onDestroy, onMount } from 'svelte';
	import ChatItem from './common/ChatItem.svelte';
	import DropdownMenu from './DropdownMenu.svelte';
	import Icon from './Icon.svelte';
	import WorkspaceToolServersModal from './WorkspaceToolServersModal.svelte';

	interface Props {
		onaddworkspace: () => void;
	}

	let { onaddworkspace }: Props = $props();
	let wsMenuPath = $state<string | null>(null);
	let wsMenuAnchor = $state<HTMLElement | null>(null);
	let chatMenu = $state<{ chatId: string; wsPath: string; anchor: HTMLElement } | null>(null);
	let wsListEl: HTMLDivElement | undefined = $state();
	let sortable: Sortable | null = null;
	let unbindSocketListener: (() => void) | null = null;
	let workspacesExpanded = $state(true);
	let toolServersPath = $state<string | null>(null);
	// Active-time share per workspace (path → %), from the measured `dwell`
	// samples. Read once on mount: it moves over days, not seconds, so polling
	// it would be pure waste (see the cptr-frontend skill).
	let dwellShare = $state<Map<string, number>>(new Map());

	// Which workspaces are shown, and the tail of low-priority ones behind
	// "Show more". The rank decides the starting set; the user's own clicks then
	// own it (see stores/workspacePriority.svelte.ts).
	let visiblePaths = $derived(
		visibleWorkspaces(
			$workspaceList,
			$workspacePriority,
			$pinnedWorkspaces,
			$workspacesExpandedList,
			DEFAULT_LIMIT
		)
	);
	let visibleSet = $derived(new Set(visiblePaths));
	let hiddenWorkspaces = $derived($workspaceList.length - visiblePaths.length);

	// Workspace folders start expanded (their chat list visible); we only
	// remember the ones the user explicitly collapsed.
	let collapsedWorkspaces = $state<Set<string>>(new Set());
	let wsChatsCache = $state<Map<string, ChatInfo[]>>(new Map());
	let wsChatsLoading = $state<Set<string>>(new Set());
	let currentPath = $derived($currentWorkspace?.path ?? null);
	let currentChatId = $derived($activeTab?.type === 'chat' ? $activeTab.path : null);
	// Every chat that has not been closed is listed, so there is nothing to page:
	// the workspace is asked for its whole open list in one request. The API caps
	// `limit` at 200, which is above every per-workspace chat count on this box.
	const WS_CHATS_LIMIT = 200;

	function isWorkspaceExpanded(path: string): boolean {
		return !collapsedWorkspaces.has(path);
	}

	function toggleWorkspaceExpand(path: string) {
		const next = new Set(collapsedWorkspaces);
		if (next.has(path)) {
			next.delete(path);
		} else {
			next.add(path);
		}
		collapsedWorkspaces = next;
	}

	/**
	 * A chat is listed for as long as it has not been closed. Reading it is not
	 * what removes a row -- only the x closes it, and a chat with no messages at
	 * all is closed automatically, so nothing else can ever empty a workspace's
	 * list. Idle chats are still in the pinned Chat tab's history list.
	 */
	function needsAttention(chat: ChatInfo): boolean {
		return !chat.closed_at;
	}

	/**
	 * Newest first. The server already returns `updated_at` desc, but only some
	 * refreshes come through that path and a socket event can move a chat after
	 * it has been drawn, so the order is re-applied where the rows are drawn.
	 * Attention is no longer an ordering key: a read chat keeps its row and its
	 * place, while a chat the server is working on carries a spinner instead of a
	 * dot.
	 */
	function byRecent(a: ChatInfo, b: ChatInfo): number {
		return b.updated_at - a.updated_at;
	}

	function visibleChatsFor(path: string): ChatInfo[] {
		return (wsChatsCache.get(path) ?? []).filter(needsAttention).sort(byRecent);
	}

	/** Close (conclude) a chat: its row leaves the sidebar for good. */
	function handleCloseChat(chatId: string, wsPath: string) {
		const now = Date.now();
		setChatClosed(chatId, true);
		wsChatsCache = new Map([
			...wsChatsCache,
			[
				wsPath,
				(wsChatsCache.get(wsPath) ?? []).map((chat) =>
					chat.id === chatId ? { ...chat, closed_at: now, last_read_at: now } : chat
				)
			]
		]);
		// Closing the chat you are looking at returns you to its landing page.
		if (currentPath === wsPath && currentChatId === chatId) {
			goto(`/?workspace=${encodeURIComponent(wsPath)}`);
		}
	}

	async function fetchWorkspaceChats(path: string, limit = WS_CHATS_LIMIT) {
		if (wsChatsLoading.has(path)) return;
		wsChatsLoading = new Set([...wsChatsLoading, path]);
		try {
			// include_closed=true and the local `closed_at` filter: the endpoint's
			// own `include_closed=false` is an attention rule -- it lets a closed
			// chat back into the list once new activity makes it unread again --
			// and closing has to be the one and only way a row leaves.
			const data = await getChats(path, limit, 0, 'updated_at', 'desc', true);
			wsChatsCache = new Map([...wsChatsCache, [path, data.chats || []]]);
			updateChatStatuses(data.chats || [], path);
		} catch {
			wsChatsCache = new Map([...wsChatsCache, [path, []]]);
		} finally {
			const next = new Set(wsChatsLoading);
			next.delete(path);
			wsChatsLoading = next;
		}
	}

	function reloadWorkspaceChats(path: string) {
		void fetchWorkspaceChats(path, WS_CHATS_LIMIT);
	}

	function closeMobileSidebar() {
		if (typeof window !== 'undefined' && window.innerWidth < 768) sidebarOpen.set(false);
	}

	function openWorkspace(e: MouseEvent, path: string) {
		if (e.metaKey || e.ctrlKey) return;
		e.preventDefault();
		goto(`/?workspace=${encodeURIComponent(path)}&view=dashboard`);
		closeMobileSidebar();
	}

	// Collapsing gives the ranking back its say: everything it only remembered
	// because it was being used is dropped, and the held/highest-scored set is
	// what remains.
	function toggleMoreWorkspaces() {
		if ($workspacesExpandedList) collapseList();
		else workspacesExpandedList.set(true);
	}

	function openChat(chatId: string, wsPath: string) {
		goto(`/?workspace=${encodeURIComponent(wsPath)}&chatId=${encodeURIComponent(chatId)}`);
		closeMobileSidebar();
	}

	function newChat(wsPath: string) {
		goto(`/?workspace=${encodeURIComponent(wsPath)}&chatId`);
		closeMobileSidebar();
	}

	function openWsMenu(e: MouseEvent, path: string) {
		e.stopPropagation();
		e.preventDefault();
		closeChatMenu();
		wsMenuAnchor = e.currentTarget as HTMLElement;
		wsMenuPath = path;
	}

	function closeWsMenu() {
		wsMenuPath = null;
		wsMenuAnchor = null;
	}

	function openChatMenu(e: MouseEvent, chatId: string, wsPath: string) {
		e.stopPropagation();
		closeWsMenu();
		chatMenu = { chatId, wsPath, anchor: e.currentTarget as HTMLElement };
	}

	function closeChatMenu() {
		chatMenu = null;
	}

	async function handleRemoveWorkspace(path: string) {
		closeWsMenu();
		await removeWorkspace(path);
		if (currentPath === path) goto('/');
	}

	async function handleDeleteChat() {
		if (!chatMenu) return;
		const { chatId, wsPath } = chatMenu;
		closeChatMenu();
		await apiDeleteChat(chatId);
		sleepClosedChatTabs(chatId);
		const chats = wsChatsCache.get(wsPath) ?? [];
		wsChatsCache = new Map([...wsChatsCache, [wsPath, chats.filter((chat) => chat.id !== chatId)]]);
		if (currentPath === wsPath && currentChatId === chatId) {
			goto(`/?workspace=${encodeURIComponent(wsPath)}`);
		}
	}

	async function handleRenameChat() {
		if (!chatMenu) return;
		const { chatId, wsPath } = chatMenu;
		const chat = (wsChatsCache.get(wsPath) ?? []).find((item) => item.id === chatId);
		const title = window.prompt($t('files.rename'), chat?.title)?.trim();
		if (!title || title === chat?.title) return;
		await updateChatTitle(chatId, title);
		const chats = wsChatsCache.get(wsPath) ?? [];
		wsChatsCache = new Map([
			...wsChatsCache,
			[wsPath, chats.map((item) => (item.id === chatId ? { ...item, title } : item))]
		]);
	}

	function copyChatPath() {
		if (!chatMenu) return;
		const { chatId, wsPath } = chatMenu;
		const chat = (wsChatsCache.get(wsPath) ?? []).find((item) => item.id === chatId);
		if (!chat) return;
		navigator.clipboard.writeText(
			`${wsPath.replace(/\/$/, '')}/.cptr/chats/${chat.folder ? `${chat.folder}/` : ''}${chat.id}.json`
		);
	}

	function handleChatEvent(data: {
		type?: string;
		chat_id: string;
		done?: boolean;
		title?: string;
		delta?: string;
		workspace?: string;
		active?: boolean;
		updated_at?: number;
		last_read_at?: number;
		closed_at?: number | null;
		workspace_unread_count?: number;
	}) {
		const hasClosedAt = 'closed_at' in data;
		if (
			!data.title &&
			typeof data.active !== 'boolean' &&
			typeof data.updated_at !== 'number' &&
			typeof data.last_read_at !== 'number' &&
			!hasClosedAt &&
			typeof data.workspace_unread_count !== 'number'
		) {
			return;
		}
		const unreadCount = data.workspace_unread_count;
		if (data.workspace && typeof unreadCount === 'number') {
			workspaceList.update((workspaces) =>
				workspaces.map((workspace) =>
					workspace.path === data.workspace
						? { ...workspace, unread_count: unreadCount }
						: workspace
				)
			);
		}

		let known = false;
		const shouldReorder =
			typeof data.updated_at === 'number' ||
			typeof data.last_read_at === 'number' ||
			typeof data.active === 'boolean';

		wsChatsCache = new Map(
			[...wsChatsCache].map(([path, chats]) => {
				const nextChats = chats.map((chat) => {
					if (chat.id !== data.chat_id) return chat;
					known = true;
					return {
						...chat,
						...(data.title ? { title: data.title } : {}),
						...(typeof data.updated_at === 'number' ? { updated_at: data.updated_at } : {}),
						...(typeof data.last_read_at === 'number' ? { last_read_at: data.last_read_at } : {}),
						...(hasClosedAt ? { closed_at: data.closed_at ?? null } : {}),
						...(typeof data.active === 'boolean' ? { is_active: data.active } : {})
					};
				});
				return [
					path,
					shouldReorder ? nextChats.sort((a, b) => b.updated_at - a.updated_at) : nextChats
				] as [string, ChatInfo[]];
			})
		);

		// A chat created in another session is not yet in this sidebar's page.
		// Refresh only that expanded workspace; all known rows update in place.
		if (!known && data.workspace && isWorkspaceExpanded(data.workspace)) {
			void fetchWorkspaceChats(data.workspace);
			// New activity is its own reason to be visible.
			resurface(data.workspace);
		} else if (
			known &&
			typeof data.last_read_at === 'number' &&
			data.workspace &&
			isWorkspaceExpanded(data.workspace)
		) {
			reloadWorkspaceChats(data.workspace);
		}
	}

	// Workspaces default to expanded, so load each *visible* workspace's chats as
	// soon as it appears (and for any newly added one). Collapsed-tail workspaces
	// are deliberately not fetched: that was 40 chat requests a page load.
	$effect(() => {
		if (!$chatEnabled) return;
		for (const path of visiblePaths) {
			if (!isWorkspaceExpanded(path)) continue;
			if (wsChatsCache.has(path) || wsChatsLoading.has(path)) continue;
			void fetchWorkspaceChats(path);
		}
	});

	// Opening a workspace is the clearest signal there is that it is in use, so
	// its row returns to the visible list even if the ranking had it buried.
	$effect(() => {
		if (currentPath) resurface(currentPath);
	});

	async function loadDwellShare() {
		try {
			const data = await getWorkspaceDwell();
			if (data.total_seconds <= 0) return;
			const next = new Map<string, number>();
			for (const row of data.workspaces) {
				// Anything under 0.5% would render as a meaningless "0%".
				if (row.share_pct >= 0.5) next.set(row.workspace, row.share_pct);
			}
			dwellShare = next;
		} catch {
			/* Telemetry is best-effort: showing no badge is the right fallback. */
		}
	}

	function isTouchDevice(): boolean {
		return (
			typeof window !== 'undefined' && ('ontouchstart' in window || navigator.maxTouchPoints > 0)
		);
	}

	function handleWorkspaceSort(evt: { oldIndex?: number; newIndex?: number }) {
		if (evt.oldIndex == null || evt.newIndex == null || evt.oldIndex === evt.newIndex) return;
		// Sortable sees only the rendered rows, so translate its indices back into
		// the full list before reordering — otherwise a drag while the tail is
		// collapsed moves the wrong workspace.
		const from = visiblePaths[evt.oldIndex];
		const to = visiblePaths[evt.newIndex];
		const oldIndex = $workspaceList.findIndex((ws) => ws.path === from);
		const newIndex = $workspaceList.findIndex((ws) => ws.path === to);
		if (oldIndex >= 0 && newIndex >= 0) reorderWorkspaces(oldIndex, newIndex);
	}

	onMount(() => {
		if (wsListEl && !isTouchDevice()) {
			sortable = Sortable.create(wsListEl, {
				animation: 150,
				ghostClass: 'opacity-30',
				dragClass: 'cursor-grabbing',
				direction: 'vertical',
				onEnd: handleWorkspaceSort
			});
		}

		unbindSocketListener = socketStore.on('events:chat', handleChatEvent);

		// Once, not on a timer — the share moves over days, not seconds.
		loadDwellShare();
		loadWorkspacePriority(currentPath);
	});

	onDestroy(() => {
		sortable?.destroy();
		unbindSocketListener?.();
		unbindSocketListener = null;
	});
</script>

<div class="flex items-center justify-between h-8 pl-3.5 pr-1.5 shrink-0">
	<button
		class="group flex flex-1 h-full items-center gap-1 text-left text-xs text-gray-400 hover:text-gray-500 dark:text-gray-500 dark:hover:text-gray-400 transition-colors duration-100"
		onclick={() => (workspacesExpanded = !workspacesExpanded)}
		aria-expanded={workspacesExpanded}
		aria-controls="workspace-list"
	>
		<span>{$t('sidebar.workspaces')}</span>
		<span
			class="flex opacity-0 group-hover:opacity-100 transition-all duration-100"
			style="transform: rotate({workspacesExpanded ? '90deg' : '0deg'})"
		>
			<Icon name="chevron-right" size={11} />
		</span>
	</button>
	<button
		class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"
		onclick={onaddworkspace}
		aria-label={$t('sidebar.addWorkspace')}
		use:tooltip={$t('sidebar.addWorkspace')}
	>
		<Icon name="plus" size={14} />
	</button>
</div>

<div
	id="workspace-list"
	bind:this={wsListEl}
	class="flex-1 overflow-y-auto px-1.5"
	class:invisible={!workspacesExpanded}
>
	{#each $workspaceList.filter((ws) => visibleSet.has(ws.path)) as ws (ws.path)}
		{@const isExpanded = isWorkspaceExpanded(ws.path)}
		{@const chats = visibleChatsFor(ws.path)}
		{@const chatsLoaded = wsChatsCache.has(ws.path)}
		{@const isLoading = wsChatsLoading.has(ws.path)}
		<div class="ws-item">
			<div
				class="ws-heading group flex items-center gap-1 w-full h-7 px-2 rounded-lg text-xs font-medium transition-colors duration-100"
				class:ws-heading-current={ws.path === currentPath}
			>
				<a
					href="/?workspace={encodeURIComponent(ws.path)}&view=dashboard"
					class="flex items-center gap-1.5 flex-1 min-w-0 no-underline text-inherit"
					onclick={(e) => openWorkspace(e, ws.path)}
				>
					{#if $chatEnabled}
						<span
							class="ws-icon-toggle shrink-0"
							role="button"
							tabindex="-1"
							onclick={(e) => {
								e.stopPropagation();
								e.preventDefault();
								toggleWorkspaceExpand(ws.path);
							}}
							aria-label={isExpanded ? $t('sidebar.collapse') : $t('sidebar.addWorkspace')}
						>
							<span class="ws-icon-folder"><Icon name="folder" size={14} /></span>
							<span
								class="ws-icon-chevron"
								style="transform: rotate({isExpanded ? '90deg' : '0deg'})"
							>
								<Icon name="chevron-right" size={11} />
							</span>
						</span>
					{:else}
						<Icon name="folder" size={14} />
					{/if}
					<span class="min-w-0 truncate text-left">{ws.name}</span>
					{#if dwellShare.has(ws.path)}
						<span
							class="ws-dwell-share shrink-0"
							use:tooltip={$t('sidebar.timeShareTooltip', {
								pct: Math.round(dwellShare.get(ws.path) ?? 0)
							})}
						>
							{Math.round(dwellShare.get(ws.path) ?? 0)}%
						</span>
					{/if}
					{#if ws.unread_count > 0}
						<span
							class="ws-unread inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-md bg-sky-500/10 px-1 text-[0.625rem] font-semibold text-sky-600 dark:bg-sky-400/10 dark:text-sky-300"
						>
							{new Intl.NumberFormat(undefined, {
								notation: 'compact',
								compactDisplay: 'short'
							}).format(ws.unread_count)}
						</span>
					{/if}
				</a>
				<span
					class="ws-heading-action flex items-center justify-center w-4 h-4 shrink-0 text-gray-400 opacity-0 group-hover:opacity-100 hover:text-gray-600 dark:hover:text-gray-300 transition-all duration-75"
					role="button"
					tabindex="-1"
					onclick={(e) => openWsMenu(e, ws.path)}
					aria-label={$t('sidebar.workspaceOptions')}
				>
					<Icon name="three-dots" size={11} />
				</span>
				{#if $chatEnabled}
					<span
						class="ws-heading-action flex items-center justify-center w-4 h-4 shrink-0 text-gray-400 opacity-0 group-hover:opacity-100 hover:text-gray-600 dark:hover:text-gray-300 transition-all duration-75"
						role="button"
						tabindex="-1"
						onclick={() => newChat(ws.path)}
						aria-label={$t('bar.newChat')}
						use:tooltip={$t('bar.newChat')}
					>
						<Icon name="pencil" size={11} />
					</span>
				{/if}
			</div>

			{#if $chatEnabled && isExpanded}
				<div class="ws-chats">
					{#if isLoading && !chatsLoaded}
						<div class="ws-chat-loading">
							<span class="ws-chat-loading-dot"></span>
							<span class="ws-chat-loading-dot"></span>
							<span class="ws-chat-loading-dot"></span>
						</div>
					{:else if chats.length > 0}
						{#each chats as chat (chat.id)}
							<ChatItem
								{chat}
								isSelected={chat.id === currentChatId}
								onclick={() => openChat(chat.id, ws.path)}
								onmenu={(e) => openChatMenu(e, chat.id, ws.path)}
								onclose={() => handleCloseChat(chat.id, ws.path)}
							/>
						{/each}
					{/if}
				</div>
			{/if}
		</div>
	{/each}

	<!-- The low-priority tail. Collapsed, it states the count so the list is
	     honest about what is behind it; expanded, it is the way back down. -->
	{#if hiddenWorkspaces > 0 || $workspacesExpandedList}
		<button
			class="ws-show-more"
			onclick={toggleMoreWorkspaces}
			aria-expanded={$workspacesExpandedList}
			aria-label={$t(
				$workspacesExpandedList ? 'sidebar.showFewer' : 'sidebar.showMoreCount',
				{ count: hiddenWorkspaces }
			)}
		>
			<span class="ws-show-more-chevron">
				<Icon name="chevron-right" size={11} />
			</span>
			<span class="truncate">
				{$t($workspacesExpandedList ? 'sidebar.showFewer' : 'sidebar.showMoreCount', {
					count: hiddenWorkspaces
				})}
			</span>
		</button>
	{/if}

	{#if $workspaceList.length === 0}
		<div class="flex flex-col items-center justify-center py-12">
			<p class="text-xs text-gray-400 dark:text-gray-600">{$t('sidebar.noWorkspaces')}</p>
		</div>
	{/if}
</div>

{#if wsMenuPath && wsMenuAnchor}
	<DropdownMenu
		anchor={wsMenuAnchor}
		items={[
			{
				label: $t('workspaceTools.title'),
				icon: 'plug',
				onclick: () => {
					toolServersPath = wsMenuPath;
				}
			},
			{
				label: $t('sidebar.remove'),
				icon: 'xmark',
				onclick: () => handleRemoveWorkspace(wsMenuPath!)
			}
		]}
		onclose={closeWsMenu}
	/>
{/if}

{#if toolServersPath}
	<WorkspaceToolServersModal path={toolServersPath} onclose={() => (toolServersPath = null)} />
{/if}

{#if chatMenu}
	<DropdownMenu
		anchor={chatMenu.anchor}
		align="end"
		items={[
			{
				label: $t('files.copyPath'),
				icon: 'copy',
				onclick: copyChatPath
			},
			{
				label: $t('files.rename'),
				icon: 'pencil',
				onclick: handleRenameChat
			},
			{
				label: $t('chat.history.delete'),
				icon: 'trash',
				onclick: handleDeleteChat
			}
		]}
		onclose={closeChatMenu}
	/>
{/if}

<style>
	@reference "../../app.css";

	.ws-item {
		margin-bottom: 0.125rem;
	}

	/* The workspace name heads its group of chats. In the tinted palettes it is
	   a quiet label in the theme's mid ink — the weight the Settings navigation
	   gives an inactive row — that takes the full foreground under the pointer.
	   The open workspace wears the same hairline frame as the open chat (see
	   ChatItem) rather than a fill. Both colours come from --app-*, so the
	   heading follows a custom appearance with everything else. */
	.ws-heading {
		border: 1px solid transparent;
		margin-bottom: 0.125rem;
		color: var(--app-fg-muted);
	}

	.ws-heading:hover {
		color: var(--app-fg);
	}

	.ws-heading-current {
		border-color: var(--app-fg-muted);
		color: var(--app-fg);
	}

	/* Share of tracked time. */
	.ws-heading .ws-dwell-share {
		color: var(--app-fg-subtle);
		font-size: 0.625rem;
		font-variant-numeric: tabular-nums;
		letter-spacing: 0.01em;
	}

	/* ── Monochrome (e-ink) palettes ─────────────────────────────────────────
	   A mid-tone label is what a 1-bit panel turns into noise, so the heading is
	   instead a solid ink plate: paper label, paper icons. The open workspace
	   keeps its plate and gains a paper hairline frame — the only "selected"
	   mark that survives a pure ink/paper palette. There is no hover colour:
	   pointing at a row is not something an e-ink panel reports, so the plate
	   stays put. */
	:global(.mono) .ws-heading {
		background: var(--app-fg);
		color: var(--app-bg);
	}

	:global(.mono) .ws-heading:hover {
		color: var(--app-bg);
	}

	:global(.mono) .ws-heading-current {
		border-color: var(--app-bg);
	}

	/* Everything inside the plate has to be paper: the sidebar's greys (and the
	   mono ramp, where grey collapses to ink) would otherwise paint ink on ink. */
	:global(.mono) .ws-heading .ws-icon-toggle .ws-icon-chevron,
	:global(.mono) .ws-heading .ws-heading-action {
		color: var(--app-bg);
	}

	/* The unread count is a paper chip cut into the ink plate. */
	:global(.mono) .ws-heading .ws-unread {
		background: var(--app-bg);
		color: var(--app-fg);
	}

	/* Printed in paper ink like the rest of the heading — deliberately not a
	   grey or an opacity blend, which in the mono palette would smear ink into
	   paper and break the pure ink-on-paper rule. */
	:global(.mono) .ws-heading .ws-dwell-share {
		color: var(--app-bg);
	}

	.ws-icon-toggle {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 0.875rem;
		height: 0.875rem;
		cursor: pointer;
	}

	.ws-icon-folder {
		display: flex;
		transition: opacity 0.1s;
	}

	.ws-icon-chevron {
		position: absolute;
		inset: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		opacity: 0;
		transition:
			opacity 0.1s,
			transform 0.1s;
		color: var(--app-fg-subtle);
	}

	:global(.dark) .ws-icon-chevron {
		color: var(--app-fg-muted);
	}

	.ws-icon-toggle:hover .ws-icon-folder {
		opacity: 0;
	}

	.ws-icon-toggle:hover .ws-icon-chevron {
		opacity: 1;
	}

	.ws-chats {
		margin-top: 0.125rem;
		padding-bottom: 0.25rem;
	}

	/* Collapsed-workspace tail toggle -- now the only "show more" left in the
	   sidebar. No background of its own: in bw/mono anything translucent here
	   would come out as a grey wash, and the e-ink palettes forbid one. */
	.ws-show-more {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		width: 100%;
		padding: 0.25rem 0.5rem;
		border: none;
		background: none;
		cursor: pointer;
		font-size: 0.6875rem;
		color: var(--app-fg-subtle);
		text-align: left;
		transition: color 0.1s;
	}

	.ws-show-more:hover {
		color: var(--app-fg);
	}

	.ws-show-more[aria-expanded='true'] .ws-show-more-chevron {
		transform: rotate(-90deg);
	}

	.ws-chat-loading {
		display: flex;
		gap: 0.25rem;
		padding: 0.375rem 0.5rem;
	}

	.ws-chat-loading-dot {
		width: 0.25rem;
		height: 0.25rem;
		border-radius: 50%;
		background: var(--app-fg-subtle);
		animation: dotPulse 1s ease-in-out infinite;
	}

	:global(.dark) .ws-chat-loading-dot {
		background: var(--app-fg-muted);
	}

	.ws-chat-loading-dot:nth-child(2) {
		animation-delay: 0.15s;
	}

	.ws-chat-loading-dot:nth-child(3) {
		animation-delay: 0.3s;
	}

	@keyframes dotPulse {
		0%,
		100% {
			opacity: 0.3;
		}
		50% {
			opacity: 1;
		}
	}
</style>
