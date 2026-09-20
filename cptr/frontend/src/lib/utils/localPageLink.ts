/**
 * Markdown lexer options that make loopback references clickable.
 *
 * GFM autolinks URLs that carry a scheme, but a chat writes `localhost:5173` or
 * `127.0.0.1:8000/api`. This inline extension claims those as ordinary `link`
 * tokens, so InlineRenderer routes them to the Browser tab (see localPage.ts)
 * exactly like a written-out `http://localhost:5173`.
 *
 * Kept apart from MarkdownRenderer so it can be exercised on its own: see
 * notes/_scratch/test-local-page-lexer.ts.
 */
import { defaults as markedDefaults, type Tokens, type TokenizerExtension, type TokenizerThis } from 'marked';
import { findBareLocalPage } from './localPage';

/** Inside link text (`[localhost:5173](...)`) the reference is already a label. */
function insideLinkLabel(this: TokenizerThis): boolean {
	return Boolean(this.lexer?.state?.inLink);
}

export const localPageLinkExtension: TokenizerExtension = {
	name: 'localPageLink',
	level: 'inline',
	// Best case: jump straight to the next loopback reference.
	start(src) {
		// A label has nowhere to go: leave it as plain text.
		if (insideLinkLabel.call(this)) return undefined;
		return findBareLocalPage(src)?.index;
	},
	tokenizer(src) {
		if (insideLinkLabel.call(this)) return undefined;
		const found = findBareLocalPage(src);
		if (!found || found.index !== 0) return undefined;
		return {
			type: 'link',
			raw: found.raw,
			href: found.page.url,
			title: null,
			text: found.raw,
			tokens: [{ type: 'text', raw: found.raw, text: found.raw }]
		} as Tokens.Generic;
	}
};

/**
 * `new Lexer(options)` does not merge with the module defaults, so spread them:
 * dropping `gfm` would silently stop rendering tables, autolinks and `~~del~~`.
 */
export const localPageLexerOptions = {
	...markedDefaults,
	extensions: {
		renderers: {},
		childTokens: {},
		inline: [localPageLinkExtension.tokenizer],
		startInline: [localPageLinkExtension.start!]
	}
};
