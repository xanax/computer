/**
 * State API: user preferences, workspace state, welcome/system info.
 *
 * State is split into three layers:
 *   - preferences: global user prefs (theme, locale, etc.)
 *   - workspaces: per-workspace state keyed by filesystem path
 *   - active workspace: determined by URL query param, not stored server-side
 */
import { fetchHandler, fetchJSON, jsonBody } from '$lib/apis';

// ── Preferences ─────────────────────────────────────────────────

export const getPreferences = () => fetchJSON<Record<string, unknown>>('/api/state/preferences');

export const savePreferences = (data: Record<string, unknown>) =>
	fetchHandler('/api/state/preferences', { ...jsonBody(data), method: 'PUT' });

// ── Workspace list (sidebar) ────────────────────────────────────

export interface WorkspaceListItem {
	path: string;
	name: string;
	unread_count: number;
}

export const getWorkspaceList = () => fetchJSON<WorkspaceListItem[]>('/api/state/workspaces');

// ── Single workspace CRUD ───────────────────────────────────────

export const getWorkspaceState = (path: string) =>
	fetchJSON<Record<string, unknown>>(`/api/state/workspace?path=${encodeURIComponent(path)}`);

export const saveWorkspaceState = (path: string, data: Record<string, unknown>) =>
	fetchHandler(`/api/state/workspace?path=${encodeURIComponent(path)}`, {
		...jsonBody(data),
		method: 'PUT'
	});

export const deleteWorkspace = (path: string) =>
	fetchHandler(`/api/state/workspace?path=${encodeURIComponent(path)}`, { method: 'DELETE' });

export interface WorkspaceToolServer {
	id: string;
	name: string;
	description: string;
	type: string;
	scope: 'global' | 'workspace' | string;
	enabled: boolean;
}

export const listWorkspaceToolServers = () =>
	fetchJSON<{ servers: WorkspaceToolServer[] }>('/api/state/tool-servers');

export const saveWorkspaceToolServers = (path: string, toolServers: string[]) =>
	fetchJSON<{ status: string; path: string; toolServers: string[] }>(
		`/api/state/workspace/tool-servers?path=${encodeURIComponent(path)}`,
		{ ...jsonBody({ toolServers }), method: 'PUT' }
	);

// ── Workspace prompt ────────────────────────────────────────────

/**
 * The short description of what a workspace is for. It is shown on the
 * workspace's dashboard and injected at the start of every chat started in it.
 */
export interface WorkspacePrompt {
	path: string;
	prompt: string;
}

export const getWorkspacePrompt = (path: string) =>
	fetchJSON<WorkspacePrompt>(`/api/state/workspace/prompt?path=${encodeURIComponent(path)}`);

export const saveWorkspacePrompt = (path: string, prompt: string) =>
	fetchJSON<WorkspacePrompt & { status: string }>(
		`/api/state/workspace/prompt?path=${encodeURIComponent(path)}`,
		{ ...jsonBody({ prompt }), method: 'PUT' }
	);

// ── Workspace services ──────────────────────────────────────────

export interface WorkspaceService {
	id: string;
	name: string;
	command: string;
	cwd: string;
	port: number | null;
	health_url: string;
	status: 'running' | 'stopped' | 'unhealthy';
	pid: number | null;
	url: string;
	healthy: boolean | null;
}

export interface UnmanagedService {
	command_session_id: string;
	command: string;
	cwd: string;
	pid: number;
	created_at: number;
}

export interface WorkspaceServices {
	path: string;
	services: WorkspaceService[];
	unmanaged: UnmanagedService[];
}

export interface WorkspaceServiceInput {
	name: string;
	command: string;
	cwd: string;
	port: number | null;
	health_url: string;
}

const servicesPath = (path: string) =>
	`/api/state/workspace/services?path=${encodeURIComponent(path)}`;

export const getWorkspaceServices = (path: string) =>
	fetchJSON<WorkspaceServices>(servicesPath(path));

export const createWorkspaceService = (path: string, body: WorkspaceServiceInput) =>
	fetchJSON<WorkspaceService>(servicesPath(path), { ...jsonBody(body), method: 'POST' });

export const updateWorkspaceService = (path: string, id: string, body: WorkspaceServiceInput) =>
	fetchJSON<WorkspaceService>(
		`/api/state/workspace/services/${encodeURIComponent(id)}?path=${encodeURIComponent(path)}`,
		{ ...jsonBody(body), method: 'PUT' }
	);

export const deleteWorkspaceService = (path: string, id: string) =>
	fetchJSON<{ status: string }>(
		`/api/state/workspace/services/${encodeURIComponent(id)}?path=${encodeURIComponent(path)}`,
		{ method: 'DELETE' }
	);

export const startWorkspaceService = (path: string, id: string) =>
	fetchJSON<WorkspaceService>(
		`/api/state/workspace/services/${encodeURIComponent(id)}/start?path=${encodeURIComponent(path)}`,
		{ method: 'POST' }
	);

export const stopWorkspaceService = (path: string, id: string) =>
	fetchJSON<WorkspaceService>(
		`/api/state/workspace/services/${encodeURIComponent(id)}/stop?path=${encodeURIComponent(path)}`,
		{ method: 'POST' }
	);

export const restartWorkspaceService = (path: string, id: string) =>
	fetchJSON<WorkspaceService>(
		`/api/state/workspace/services/${encodeURIComponent(id)}/restart?path=${encodeURIComponent(path)}`,
		{ method: 'POST' }
	);

export const stopUnmanagedService = (path: string, sessionId: string) =>
	fetchJSON<{ status: string }>(
		`/api/state/workspace/services/unmanaged/${encodeURIComponent(sessionId)}/stop?path=${encodeURIComponent(path)}`,
		{ method: 'POST' }
	);

export const adoptUnmanagedService = (
	path: string,
	sessionId: string,
	body: { name: string; port: number | null; health_url: string }
) =>
	fetchJSON<WorkspaceService>(
		`/api/state/workspace/services/unmanaged/${encodeURIComponent(sessionId)}/adopt?path=${encodeURIComponent(path)}`,
		{ ...jsonBody(body), method: 'POST' }
	);

// ── Welcome page ────────────────────────────────────────────────

export const getWelcome = () => fetchJSON<Record<string, unknown>>('/api/state/welcome');

// ── Processes and the server itself ─────────────────────────────

export interface SystemProcess {
	pid: number;
	cpu: number;
	mem: number;
	name: string;
	/** Full command line (absent on platforms where it cannot be read). */
	cmd?: string;
}

export interface KillProcessResult {
	status: string;
	pid: number;
	signal: string;
	force: boolean;
}

export interface RestartServerResult {
	status: string;
	/** pid of the server being replaced. */
	pid: number;
	delay_seconds: number;
	port: number | null;
	log: string;
}

export interface HealthResult {
	status: string;
	uptime_seconds: number;
	/** pid of the server answering — changes when a restart has happened. */
	pid: number;
}

/** Signal one process. `force` skips SIGTERM and sends SIGKILL. */
export const killProcess = (pid: number, force = false) =>
	fetchJSON<KillProcessResult>('/api/state/processes/kill', jsonBody({ pid, force }));

/** Stop this server and start it again with the same command line. */
export const restartServer = (delaySeconds?: number) =>
	fetchJSON<RestartServerResult>(
		'/api/state/server/restart',
		jsonBody({ delay_seconds: delaySeconds ?? null })
	);

/** Health probe, used to notice when a restarted server is back. */
export const getHealth = () => fetchJSON<HealthResult>('/api/health');
