/**
 * Wall-clock helpers for the "run later" when-picker.
 *
 * The jobs API takes `at` as either a relative offset ("30m") or an RFC 3339
 * timestamp carrying an explicit UTC offset — `cptr/utils/timers.py` rejects a
 * bare local time. A date/time pair read out of `<input>` has no timezone at
 * all, so this module exists to turn a pick into an unambiguous instant, and to
 * render an instant back as the countdown the picker shows under it.
 */

export const MINUTE_MS = 60_000;
export const HOUR_MS = 60 * MINUTE_MS;
export const DAY_MS = 24 * HOUR_MS;

/** The translator (`$t` from `$lib/i18n`), injected so this module stays UI-free. */
export type Translate = (key: string, vars?: { count: number }) => string;

export function nsToMs(ns: number): number {
	return Math.floor(ns / 1_000_000);
}

function pad(value: number): string {
	return String(value).padStart(2, '0');
}

/** "2026-09-21" — the shape `<input type="date">` wants. */
export function toDateInputValue(ms: number): string {
	const d = new Date(ms);
	return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** "09:05" — the shape `<input type="time">` wants. */
export function toTimeInputValue(ms: number): string {
	const d = new Date(ms);
	return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * The instant a date + time pair means in this browser's timezone, or null when
 * a half is missing, malformed, or rolled over (2026-02-31 would otherwise
 * silently become March 3rd and schedule the wrong day).
 *
 * Built from components rather than `Date.parse`: "2026-09-21 09:00" is read
 * inconsistently across engines, and some of them treat it as UTC.
 */
export function fromInputs(dateStr: string, timeStr: string): number | null {
	const date = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr.trim());
	const time = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(timeStr.trim());
	if (!date || !time) return null;

	const [year, month, day] = [Number(date[1]), Number(date[2]), Number(date[3])];
	const [hour, minute, second] = [Number(time[1]), Number(time[2]), Number(time[3] ?? 0)];
	const when = new Date(year, month - 1, day, hour, minute, second, 0);
	if (Number.isNaN(when.getTime())) return null;
	if (when.getFullYear() !== year || when.getMonth() !== month - 1 || when.getDate() !== day) {
		return null;
	}
	return when.getTime();
}

/** RFC 3339 with this browser's offset — "2026-09-21T09:00:00+01:00". */
export function toRfc3339(ms: number): string {
	const d = new Date(ms);
	// getTimezoneOffset is minutes *behind* UTC, so its sign is inverted here.
	const offset = -d.getTimezoneOffset();
	const sign = offset < 0 ? '-' : '+';
	const abs = Math.abs(offset);
	return (
		`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}` +
		`T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` +
		`${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
	);
}

/**
 * The next `minutes` boundary at or after `ms`. A picker default rounds up so
 * opening the form never offers a time that is already spent.
 */
export function ceilTo(ms: number, minutes = 5): number {
	const step = minutes * MINUTE_MS;
	return Math.ceil(ms / step) * step;
}

/** `hour:minute` on the day `dayOffset` days from today, in local time. */
export function atLocalTime(dayOffset: number, hour: number, minute = 0): number {
	const now = new Date();
	return new Date(
		now.getFullYear(),
		now.getMonth(),
		now.getDate() + dayOffset,
		hour,
		minute,
		0,
		0
	).getTime();
}

/**
 * "Sep 20, 19:08 · in 4 hours". The relative half is dropped once the moment
 * has passed — a stale trigger must not read as a countdown.
 */
export function formatWhen(ms: number, t: Translate): string {
	const dateStr = new Date(ms).toLocaleString(undefined, {
		month: 'short',
		day: 'numeric',
		hour: '2-digit',
		minute: '2-digit'
	});

	const diff = ms - Date.now();
	if (diff <= 0) return dateStr;

	const mins = Math.floor(diff / MINUTE_MS);
	if (mins < 1) return `${dateStr} · ${t('dashboard.now')}`;
	if (mins < 60) return `${dateStr} · ${t('dashboard.minutes', { count: mins })}`;
	const hrs = Math.floor(mins / 60);
	if (hrs < 24) return `${dateStr} · ${t('dashboard.hours', { count: hrs })}`;
	const days = Math.floor(hrs / 24);
	return `${dateStr} · ${t('dashboard.days', { count: days })}`;
}
