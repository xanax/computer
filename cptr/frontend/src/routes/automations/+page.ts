/**
 * `/automations` is `/scheduled` now.
 *
 * The two pages were one thing all along — a schedule is a task with an rrule —
 * so the old URL redirects rather than keeping a second, older list alive.
 */
import { redirect } from '@sveltejs/kit';

export function load() {
	redirect(308, '/scheduled');
}
