/**
 * Chat state: tracks whether chat is available (at least one connection configured).
 */
import { writable, get } from 'svelte/store';
import { toast } from 'svelte-sonner';
import { fetchJSON } from '$lib/apis';
import { socketStore } from '$lib/stores/socket.svelte';
import { activeHomeTab, activeTab, currentWorkspace, sleepClosedChatTabs } from '$lib/stores';
import { getPathDisplayName, isSupportedWorkspacePath } from '$lib/utils/paths';
import { i18next } from '$lib/i18n';
import { registerPerfGauge } from '$lib/utils/perf';

export const chatEnabled = writable<boolean>(false);

export interface ChatStatus {
	workspace: string;
	updatedAt: number | null;
	lastReadAt: number | null;
	active: boolean;
}

type ChatStatusSource = {
	id: string;
	workspace?: string;
	updated_at: number;
	last_read_at: number | null;
	is_active?: boolean;
};

/** Shared chat state for sidebar rows, workspace summaries, and chat tabs. */
export const chatStatuses = writable<Map<string, ChatStatus>>(new Map());

function statusFrom(
	source: ChatStatusSource,
	current?: ChatStatus,
	workspace?: string
): ChatStatus {
	return {
		workspace: workspace ?? source.workspace ?? current?.workspace ?? '',
		updatedAt: source.updated_at,
		lastReadAt: source.last_read_at,
		active: source.is_active ?? current?.active ?? false
	};
}

export function updateChatStatuses(sources: ChatStatusSource[], workspace?: string) {
	chatStatuses.update((statuses) => {
		const next = new Map(statuses);
		for (const source of sources)
			next.set(source.id, statusFrom(source, next.get(source.id), workspace));
		return next;
	});
}

export function setChatActive(chatId: string, active: boolean, workspace = '') {
	chatStatuses.update((statuses) => {
		const current = statuses.get(chatId);
		const next = new Map(statuses);
		next.set(chatId, {
			workspace: workspace || current?.workspace || '',
			updatedAt: current?.updatedAt ?? null,
			lastReadAt: current?.lastReadAt ?? null,
			active
		});
		return next;
	});
}

export function setChatReadAt(chatId: string, lastReadAt = Date.now()) {
	chatStatuses.update((statuses) => {
		const current = statuses.get(chatId);
		if (!current) return statuses;
		const next = new Map(statuses);
		next.set(chatId, { ...current, lastReadAt });
		return next;
	});
}

/**
 * Close (conclude) a chat, or reopen it. A closed chat drops out of the
 * workspace sidebar until new activity makes it unread again, and its live
 * tab/panel is put to sleep (unmounted) because concluded chats are rarely
 * reopened. The server echoes the change to every session.
 */
export function setChatClosed(chatId: string, closed: boolean) {
	socketStore.getSocket()?.emit('chat:closed', { chat_id: chatId, closed });
	// Drop the live panel immediately; the socket echo does the same for other sessions.
	if (closed) sleepClosedChatTabs(chatId);
}

export function isChatUnread(status: ChatStatus | undefined): boolean {
	return (
		!!status &&
		!status.active &&
		status.updatedAt !== null &&
		(status.lastReadAt === null || status.updatedAt > status.lastReadAt)
	);
}

/** Set of tab IDs whose chat is currently streaming (assistant message not done). */
export const streamingChatTabs = writable<Set<string>>(new Set());

// Stamp every perf sample with how many chats are working, so the long tasks
// that pile up during a multi-chat session can finally be split from the idle
// ones. `active` comes from the server's own is_active flag (the truth), while
// `streamingChatTabs` only counts tabs this browser knows are mid-stream.
registerPerfGauge(() => {
	let working = 0;
	for (const status of get(chatStatuses).values()) if (status.active) working++;
	return { working_chats: working, streaming_tabs: get(streamingChatTabs).size };
});

/**
 * Maps chatId -> tab IDs so we can clear streamingChatTabs from a global
 * socket listener even when the ChatPanel component is unmounted.
 */
const chatToTabs = new Map<string, Set<string>>();

export function registerStreamingChat(chatId: string, tabId: string) {
	const tabIds = chatToTabs.get(chatId) ?? new Set<string>();
	tabIds.add(tabId);
	chatToTabs.set(chatId, tabIds);
}

export function unregisterStreamingChat(chatId: string, tabId: string) {
	const tabIds = chatToTabs.get(chatId);
	if (!tabIds) return;
	tabIds.delete(tabId);
	if (tabIds.size === 0) chatToTabs.delete(chatId);
}

type ToolApprovalShortcutHandler = (approved: boolean) => boolean;

let toolApprovalShortcutHandler: ToolApprovalShortcutHandler | null = null;

export function registerToolApprovalShortcutHandler(
	handler: ToolApprovalShortcutHandler
): () => void {
	toolApprovalShortcutHandler = handler;
	return () => {
		if (toolApprovalShortcutHandler === handler) toolApprovalShortcutHandler = null;
	};
}

export function approveActiveToolCallShortcut(): boolean {
	return toolApprovalShortcutHandler?.(true) ?? false;
}

export function rejectActiveToolCallShortcut(): boolean {
	return toolApprovalShortcutHandler?.(false) ?? false;
}

/**
 * Bind a global socket listener that clears streamingChatTabs when a
 * chat's "done" event arrives -- even if the ChatPanel is not mounted.
 * Also fires browser notifications and in-app toasts.
 * Called once from the app layout.
 */
let globalListenerBound = false;

/** Whether browser notifications are enabled (persisted to localStorage). */
export const notificationsEnabled = writable<boolean>(
	typeof localStorage !== 'undefined'
		? localStorage.getItem('notificationsEnabled') === 'true'
		: false
);
notificationsEnabled.subscribe((v) => {
	if (typeof localStorage !== 'undefined') localStorage.setItem('notificationsEnabled', String(v));
});

/** Whether notification sound is enabled (persisted to localStorage). */
export const notificationSound = writable<boolean>(
	typeof localStorage !== 'undefined' ? localStorage.getItem('notificationSound') !== 'false' : true
);
notificationSound.subscribe((v) => {
	if (typeof localStorage !== 'undefined') localStorage.setItem('notificationSound', String(v));
});

/**
 * The `open_browser` tool asks us to put a page in front of the user -- normally
 * a web app the model just built, or a dev server it just started.
 *
 * Prefer cptr's own Browser tab: it is a normal UI tab (no popup blocker to
 * fight, since this runs off a socket event rather than a click) and its proxy
 * fetches from the server, so WSL-localhost URLs resolve. With no workspace open
 * there is no tab strip to add to, so fall back to a plain browser tab and let
 * the browser decide whether to allow it.
 */
async function openBrowserFromChat(
	url: string,
	label?: string,
	workspace?: string,
	sessionId?: string,
	tabId?: string
) {
	const stores = await import('$lib/stores');
	// `workspace` is the chat's own workspace, and it is where the tab belongs.
	// An empty/absent value means "open where I am" -- a home chat has no
	// workspace and no tab strip of its own.
	const target = workspace || undefined;

	// A home chat, or no workspace anywhere on screen: nothing to add a tab to,
	// so hand the page to the user's own browser. This path is only reached from
	// a click or a live window, so it will not be popup-blocked.
	if (!target && !get(stores.currentWorkspace)) {
		window.open(url, '_blank', 'noopener');
		return;
	}

	// The server already put the tab in the owning workspace's layout. If that is
	// the workspace on screen, the tab strip is live state the client owns, so
	// the server's copy is not loaded here -- adopt the session rather than
	// opening a second tab for the same page.
	if (target && get(stores.currentWorkspace)?.path === target) {
		if (sessionId && tabId) {
			stores.adoptBrowserTab(sessionId, tabId, url, label);
			return;
		}
	}

	// Otherwise the page is in another workspace's tab strip, which is where it
	// should stay: it must not pull the user out of the one they are in. Tell
	// them where it went instead. `openLocalPage` with the target returns without
	// opening anything precisely for this case.
	if (target && get(stores.currentWorkspace)?.path !== target) {
		notifyBackgroundOpen(target, url, label);
		return;
	}

	await stores.openLocalPage(url, label);
}

/**
 * Tell the user a chat opened a page in a workspace they are not looking at.
 *
 * The page is already in that workspace's tab strip; this is the only thing the
 * user gets, so it has to answer "what opened, and where do I go". Clicking it
 * switches -- deliberately, on a gesture, which is the whole difference from
 * the tool deciding to switch underneath them.
 */
function notifyBackgroundOpen(workspace: string, url: string, label?: string) {
	const name = getPathDisplayName(workspace);
	const title = i18next.t('chat.notification.browserTitle', { workspace: name });
	const body = `${label || url} · ${i18next.t('chat.notification.browserBody', { workspace: name })}`;

	const goThere = async () => {
		const { goto } = await import('$app/navigation');
		await goto(`/?workspace=${encodeURIComponent(workspace)}`);
	};

	import('$lib/components/NotificationToast.svelte').then((mod) => {
		const toastId = toast.custom(mod.default, {
			componentProps: {
				title,
				content: body,
				onClick: async () => {
					await goThere();
					toast.dismiss(toastId);
				},
				onclose: () => {
					toast.dismiss(toastId);
				}
			},
			duration: 10000,
			unstyled: true
		});
	});

	// The user may have this window in the background entirely -- which is the
	// likeliest case, since the chat that asked for it is in another workspace.
	if (document.hidden && get(notificationsEnabled)) {
		try {
			const notification = new Notification(`${title} • Computer`, {
				body: body.slice(0, 200),
				icon: '/favicon.png'
			});
			notification.onclick = () => {
				window.focus();
				void goThere();
				notification.close();
			};
		} catch {}
	}
}

export function bindGlobalChatListener() {
	if (globalListenerBound) return;

	socketStore.on(
		'events:chat',
		(data: {
			type?: string;
			chat_id: string;
			status?: string;
			done?: boolean;
			title?: string;
			content?: string;
			tool_name?: string;
			url?: string;
			label?: string;
			workspace?: string;
			workspace_name?: string;
			browser_session_id?: string;
			tab_id?: string;
			active?: boolean;
			updated_at?: number;
			last_read_at?: number;
			closed_at?: number | null;
		}) => {
			if (data.type === 'open_browser' && typeof data.url === 'string') {
				void openBrowserFromChat(
					data.url,
					data.label,
					data.workspace,
					data.browser_session_id,
					data.tab_id
				);
			}
			if (data.type === 'chat:active' && typeof data.active === 'boolean') {
				setChatActive(data.chat_id, data.active, data.workspace);
			}
			if (typeof data.updated_at === 'number') {
				const updatedAt = data.updated_at;
				chatStatuses.update((statuses) => {
					const current = statuses.get(data.chat_id);
					if (!current) return statuses;
					const next = new Map(statuses);
					next.set(data.chat_id, { ...current, updatedAt });
					return next;
				});
			}
			if (typeof data.last_read_at === 'number') {
				setChatReadAt(data.chat_id, data.last_read_at);
			}
			if (data.closed_at) sleepClosedChatTabs(data.chat_id);
			if (!data.done) return;

			// Clear streaming indicator for the tab
			const tabIds = chatToTabs.get(data.chat_id);
			if (tabIds) {
				streamingChatTabs.update((s) => {
					const next = new Set(s);
					for (const tabId of tabIds) next.delete(tabId);
					return next;
				});
			}

			// ── Notifications ──────────────────────────────────
			// Skip if user is actively viewing this chat
			const currentTab = get(activeTab);
			const currentHomeTab = get(activeHomeTab);
			const isHome = get(currentWorkspace) === null;
			const isViewingThisChat =
				!document.hidden &&
				((!isHome && currentTab?.type === 'chat' && currentTab.path === data.chat_id) ||
					(isHome && currentHomeTab?.type === 'chat' && currentHomeTab.path === data.chat_id));

			if (isViewingThisChat) return;

			const workspacePath = isSupportedWorkspacePath(data.workspace) ? data.workspace : '';
			const workspaceDisplayName = data.workspace_name || getPathDisplayName(workspacePath);
			const workspaceTitlePrefix = workspaceDisplayName ? `[${workspaceDisplayName}] ` : '';
			const chatTitle = data.title || i18next.t('chat.fallbackTitle');
			const approvalRequired = data.status === 'approval_required';
			const inputRequired = data.status === 'input_required';
			const toolName = data.tool_name?.replace(/_/g, ' ');
			const title = approvalRequired
				? `${workspaceTitlePrefix}${i18next.t('chat.notification.approvalTitle', { title: chatTitle })}`
				: inputRequired
					? `${workspaceTitlePrefix}${i18next.t('chat.notification.inputTitle', { title: chatTitle })}`
					: `${workspaceTitlePrefix}${chatTitle}`;
			const body =
				data.content ||
				(approvalRequired
					? i18next.t('chat.notification.approvalBody', {
							tool: toolName || i18next.t('chat.notification.thisTool')
						})
					: inputRequired
						? i18next.t('chat.notification.inputBody')
						: i18next.t('chat.notification.finishedBody'));
			const openChat = async () => {
				const { goto } = await import('$app/navigation');
				const workspaceQuery = workspacePath
					? `workspace=${encodeURIComponent(workspacePath)}&`
					: '';
				await goto(`/?${workspaceQuery}chatId=${encodeURIComponent(data.chat_id)}`);
			};

			// In-app toast
			import('$lib/components/NotificationToast.svelte').then((mod) => {
				const toastId = toast.custom(mod.default, {
					componentProps: {
						title,
						content: body,
						onClick: async () => {
							await openChat();
							toast.dismiss(toastId);
						},
						onclose: () => {
							toast.dismiss(toastId);
						}
					},
					duration: 10000,
					unstyled: true
				});
			});

			// Browser notification (only when tab is hidden)
			if (document.hidden && get(notificationsEnabled)) {
				try {
					const notification = new Notification(`${title} • Computer`, {
						body: body.slice(0, 200),
						icon: '/favicon.png'
					});
					notification.onclick = () => {
						window.focus();
						void openChat();
						notification.close();
					};
				} catch {}
			}
		}
	);

	globalListenerBound = true;
}

export interface ChatModel {
	id: string;
	name: string;
	provider: string;
	connection_id: string;
}

export const chatModels = writable<ChatModel[]>([]);
export const defaultModel = writable<string | null>(null);

export async function refreshChatState() {
	try {
		const data = await fetchJSON<{ models: ChatModel[]; default: string | null }>(
			'/api/chats/models'
		);
		chatModels.set(data.models);
		defaultModel.set(data.default);
		chatEnabled.set(data.models.length > 0);
	} catch {
		chatEnabled.set(false);
		chatModels.set([]);
	}
}
