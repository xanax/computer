<script lang="ts">
	import { onMount } from 'svelte';
	import {
		appVersion,
		showChangelog,
		streamingBehavior,
		showUpdateToastPref,
		updateAvailable,
		latestVersion
	} from '$lib/stores';
	import type { StreamingBehavior } from '$lib/stores';
	import { t, locale, changeLocale, supportedLocales } from '$lib/i18n';
	import { session } from '$lib/session';
	import { getProfile, saveProfile } from '$lib/apis/state';
	import ToggleSwitch from '../common/ToggleSwitch.svelte';

	interface Props {
		showPwaSettings?: boolean;
	}

	let { showPwaSettings = false }: Props = $props();

	const REPO_URL = 'https://github.com/open-webui/computer';
	const SHARE_TEXT = 'Check out Computer. Your computer, from anywhere.';

	const shareLinks = [
		{
			label: 'X',
			href: `https://x.com/intent/tweet?text=${encodeURIComponent(SHARE_TEXT)}&url=${encodeURIComponent(REPO_URL)}`
		},
		{
			label: 'Reddit',
			href: `https://reddit.com/submit?url=${encodeURIComponent(REPO_URL)}&title=${encodeURIComponent(SHARE_TEXT)}`
		},
		{
			label: 'LinkedIn',
			href: `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(REPO_URL)}`
		}
	];

	let copied = $state(false);
	let resetting = $state(false);

	// ── Global profile ─────────────────────────────────────────────
	//
	// The one prompt that opens *every* chat: every workspace, and the home page.
	// It is deliberately not part of the preferences blob — that blob is autosaved
	// wholesale, so a stale echo of it could revert an edit made a moment earlier.
	let profile = $state('');
	let profileDraft = $state('');
	let profileEditing = $state(false);
	let profileBusy = $state(false);
	let profileSaved = $state(false);
	let profileError = $state(false);
	let profileMax = $state(8000);

	onMount(() => {
		getProfile()
			.then((res) => {
				profile = res.profile ?? '';
				profileMax = res.max_chars ?? profileMax;
				if (!profileEditing) profileDraft = profile;
			})
			.catch(() => {});
	});

	function startProfileEdit() {
		profileDraft = profile;
		profileSaved = false;
		profileError = false;
		profileEditing = true;
	}

	function cancelProfileEdit() {
		profileDraft = profile;
		profileEditing = false;
		profileSaved = false;
		profileError = false;
	}

	async function handleSaveProfile() {
		if (profileBusy) return;
		profileBusy = true;
		profileError = false;
		try {
			const res = await saveProfile(profileDraft.trim());
			profile = res.profile ?? '';
			profileDraft = profile;
			profileEditing = false;
			profileSaved = true;
			setTimeout(() => (profileSaved = false), 2000);
		} catch {
			// Keep the box open with the user's text so the save can be retried.
			profileError = true;
		} finally {
			profileBusy = false;
		}
	}

	function copyLink() {
		navigator.clipboard.writeText(REPO_URL);
		copied = true;
		setTimeout(() => (copied = false), 2000);
	}

	async function resetPwa() {
		resetting = true;

		try {
			if ('serviceWorker' in navigator) {
				const registrations = await navigator.serviceWorker.getRegistrations();
				await Promise.all(registrations.map((registration) => registration.unregister()));
			}

			if ('caches' in window) {
				const keys = await caches.keys();
				await Promise.all(
					keys.filter((key) => key.startsWith('cptr-')).map((key) => caches.delete(key))
				);
			}
		} finally {
			location.reload();
		}
	}
</script>

<div class="flex flex-col h-full">
	<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5 -mr-1.5">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('general.title')}</h2>

		<div class="mb-5">
			<div class="flex items-baseline gap-2">
				<a
					href={REPO_URL}
					target="_blank"
					rel="noopener noreferrer"
					class="text-xs font-semibold text-gray-900 dark:text-white hover:underline">Computer</a
				>
				{#if $appVersion}
					<button
						onclick={() => showChangelog.set(true)}
						class="text-[0.6875rem] text-gray-400 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 font-mono hover:underline cursor-pointer"
						>v{$appVersion}</button
					>
				{/if}
				{#if $updateAvailable}
					<span class="text-[0.6875rem] text-gray-300 dark:text-gray-600">·</span>
					<a
						href="https://github.com/open-webui/computer/releases"
						target="_blank"
						rel="noopener noreferrer"
						class="text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
						>{$t('about.updateAvailable', { version: $latestVersion })}</a
					>
				{/if}
			</div>

			<p class="text-[0.8125rem] text-gray-500 mt-0.5 mb-2">{$t('app.tagline')}</p>

			<div class="flex items-center gap-1.5">
				<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mr-1"
					>{$t('about.share')}</span
				>
				{#each shareLinks as link, i}
					{#if i > 0}
						<span class="text-[0.6875rem] text-gray-200 dark:text-gray-700">·</span>
					{/if}
					<a
						href={link.href}
						target="_blank"
						rel="noopener noreferrer"
						class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-500 transition-colors"
						>{link.label}</a
					>
				{/each}
				<span class="text-[0.6875rem] text-gray-200 dark:text-gray-700">·</span>
				<button
					onclick={copyLink}
					class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-500 transition-colors cursor-pointer"
				>
					{copied ? $t('about.copied') : $t('about.copyLink')}
				</button>
			</div>
		</div>

		<div class="mb-5">
			<div class="flex items-baseline justify-between gap-2 mb-2">
				<h3 class="text-xs text-gray-400 dark:text-gray-600">{$t('general.profile')}</h3>
				<div class="flex items-baseline gap-2">
					{#if profileSaved}
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
							>{$t('settings.saved')}</span
						>
					{/if}
					{#if !profileEditing}
						<button
							class="text-[0.6875rem] text-gray-400 dark:text-gray-600 hover:text-gray-700 dark:hover:text-gray-300 transition-colors cursor-pointer"
							onclick={startProfileEdit}
						>
							{profile ? $t('common.edit') : $t('common.add')}
						</button>
					{/if}
				</div>
			</div>

			{#if profileEditing}
				<textarea
					class="block w-full rounded-lg border border-gray-200 dark:border-white/10 bg-transparent px-2.5 py-2 text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none resize-y"
					rows="5"
					maxlength={profileMax}
					bind:value={profileDraft}
					placeholder={$t('general.profilePlaceholder')}
				></textarea>
				<div class="flex items-center gap-2 mt-2">
					<button
						class="h-7 px-2.5 rounded-lg text-xs font-medium bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white transition-colors disabled:opacity-40 disabled:pointer-events-none"
						onclick={handleSaveProfile}
						disabled={profileBusy}
					>
						{$t('common.save')}
					</button>
					<button
						class="h-7 px-2.5 rounded-lg text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors disabled:opacity-40 disabled:pointer-events-none"
						onclick={cancelProfileEdit}
						disabled={profileBusy}
					>
						{$t('common.cancel')}
					</button>
					<span class="ml-auto font-mono text-[0.6875rem] text-gray-400 dark:text-gray-600"
						>{profileDraft.trim().length}/{profileMax}</span
					>
				</div>
			{:else}
				<p
					class="text-[0.8125rem] leading-relaxed whitespace-pre-line {profile
						? 'text-gray-700 dark:text-gray-300'
						: 'text-gray-400 dark:text-gray-600'}"
				>
					{profile || $t('general.profileEmpty')}
				</p>
			{/if}

			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
				{profileError ? $t('general.profileSaveFailed') : $t('general.profileDesc')}
			</p>
		</div>

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">{$t('general.language')}</h3>
		<select
			class="w-full max-w-[12.5rem] bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-1 cursor-pointer"
			value={$locale}
			onchange={(e) => changeLocale((e.currentTarget as HTMLSelectElement).value)}
		>
			{#each supportedLocales as loc}
				<option value={loc.code}>{loc.label}</option>
			{/each}
		</select>

		{#if $session?.role === 'admin'}
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('general.updates')}</h3>
			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400"
					>{$t('general.updateNotifications')}</span
				>
				<ToggleSwitch value={$showUpdateToastPref} onchange={(v) => showUpdateToastPref.set(v)} />
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
				{$t('general.updateNotificationsDesc')}
			</p>
		{/if}

		<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('general.messageQueue')}</h3>
		<div class="flex gap-1">
			{#each [{ value: 'queue' as StreamingBehavior, label: $t('general.queue') }, { value: 'interrupt' as StreamingBehavior, label: $t('general.interrupt') }] as opt}
				<button
					class="flex items-center gap-1.5 h-7 px-2.5 rounded-lg text-xs transition-colors duration-100
					{$streamingBehavior === opt.value
						? 'bg-gray-200/50 dark:bg-white/8 text-gray-900 dark:text-white font-medium'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					onclick={() => streamingBehavior.set(opt.value)}
				>
					{opt.label}
				</button>
			{/each}
		</div>
		<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
			{$streamingBehavior === 'queue' ? $t('general.queueDesc') : $t('general.interruptDesc')}
		</p>

		<div class="pt-5">
			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-1">
				{$t('about.license')}
			</h3>
			<p class="text-[0.6875rem] text-gray-500 leading-relaxed font-mono whitespace-pre-line">
				<a
					href="https://github.com/open-webui/computer/blob/main/LICENSE"
					target="_blank"
					rel="noopener noreferrer"
					class="underline hover:text-gray-700 dark:hover:text-gray-300"
					>{$t('about.licenseName')}</a
				>

				<br />
				{$t('about.copyright')}
			</p>
			<p class="text-[0.6875rem] text-gray-300 dark:text-gray-700 pt-4">
				{$t('about.createdBy')}
			</p>

			{#if showPwaSettings}
				<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">{$t('pwa.resetTitle')}</h3>
				<button
					class="text-[0.8125rem] text-gray-500 dark:text-gray-500 hover:text-gray-900 dark:hover:text-white transition-colors disabled:opacity-40 disabled:pointer-events-none"
					onclick={resetPwa}
					disabled={resetting}
				>
					{resetting ? $t('pwa.resetting') : $t('pwa.reset')}
				</button>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
					{$t('pwa.resetDesc')}
				</p>
			{/if}
		</div>
	</div>
</div>
