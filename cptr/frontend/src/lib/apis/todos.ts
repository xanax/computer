import { fetchJSON, jsonBody } from './index';

export type TodoData = {
	id: string;
	workspace: string;
	title: string;
	status: 'open' | 'done';
	source: 'human' | 'chat';
	created_at: number;
	updated_at: number;
};

export type TodoRequestData = {
	id: string;
	workspace: string;
	action: 'add' | 'complete' | 'reopen' | 'remove';
	todo_id: string | null;
	title: string | null;
	status: 'pending' | 'approved' | 'rejected';
	created_at: number;
	resolved_at: number | null;
};

export type TodosResponse = {
	todos: TodoData[];
	pending_requests: TodoRequestData[];
};

export async function getTodos(workspace: string): Promise<TodosResponse> {
	return fetchJSON(`/api/todos?workspace=${encodeURIComponent(workspace)}`);
}

export async function addTodo(workspace: string, title: string): Promise<TodoData> {
	return fetchJSON('/api/todos', jsonBody({ workspace, title }));
}

export async function toggleTodo(id: string): Promise<TodoData> {
	return fetchJSON(`/api/todos/${id}/toggle`, { method: 'POST' });
}

export async function removeTodo(id: string): Promise<{ ok: boolean }> {
	return fetchJSON(`/api/todos/${id}`, { method: 'DELETE' });
}

export async function approveTodoRequest(id: string): Promise<{ ok: boolean }> {
	return fetchJSON(`/api/todos/requests/${id}/approve`, { method: 'POST' });
}

export async function rejectTodoRequest(id: string): Promise<{ ok: boolean }> {
	return fetchJSON(`/api/todos/requests/${id}/reject`, { method: 'POST' });
}
