/**
 * Force PTY output down to ink on paper while a monochrome theme is active.
 *
 * xterm's theme only recolours the 16-colour palette. A shell with
 * COLORTERM=truecolor (and the 256-colour cube) paints with SGR 38/48 and
 * SGR 2 (faint), which the renderer draws as real colour and as 50% grey.
 * This filter removes those sequences on the way in and leaves weight,
 * underline, italic and inverse alone — those stay two-tone.
 *
 * Bytes are handled as a stream: one escape sequence often arrives split
 * across WebSocket frames.
 */

const ESC = 0x1b;
const BEL = 0x07;
const CAN = 0x18;
const SUB = 0x1a;

/** OSC commands that set or query palette, foreground, background or cursor colour. */
const COLOR_OSC = new Set([
	4, 5, 10, 11, 12, 13, 14, 15, 16, 17, 19, 104, 105, 110, 111, 112, 113, 114, 115, 116, 117,
	119
]);

const enum State {
	Ground,
	Esc,
	EscIntermediate,
	Csi,
	/** Reading the OSC command number (digits after ESC ]). */
	OscNumber,
	OscBody,
	/** DCS / SOS / PM / APC, passed through until ST. */
	String,
	/** Saw ESC inside a string or OSC; `\` would complete ST. */
	StringEsc
}

/** Drop colour and faint from one SGR parameter string (the bytes between CSI and `m`). */
export function stripSgrColor(params: string): string | null {
	if (params === '') return '0';
	const parts = params.split(';');
	const kept: string[] = [];
	for (let i = 0; i < parts.length; i++) {
		const part = parts[i];
		const code = sgrCode(part);
		if (code === 38 || code === 48 || code === 58) {
			// Colon form (`38:2::r:g:b`, `38:5:n`) is a single parameter.
			if (!part.includes(':')) {
				const mode = i + 1 < parts.length ? sgrCode(parts[i + 1]) : -1;
				// Land on the last consumed parameter; the loop increment steps past it.
				// Mode 2 is `38;2;r;g;b` (what xterm consumes). Mode 5 is `38;5;n`.
				if (mode === 5) i += 2;
				else if (mode === 2) i += 4;
				else if (mode >= 0) i += 1;
			}
			continue;
		}
		if (isColorOrFaint(code)) continue;
		kept.push(part === '' ? '0' : part);
	}
	if (kept.length === 0) return null;
	return kept.join(';');
}

function sgrCode(param: string): number {
	const head = param.split(':')[0];
	if (head === '') return 0;
	const n = Number(head);
	return Number.isFinite(n) ? n : 0;
}

function isColorOrFaint(code: number): boolean {
	if (code === 2) return true;
	if (code >= 30 && code <= 49) return true;
	if (code >= 90 && code <= 97) return true;
	if (code >= 100 && code <= 107) return true;
	return false;
}

export class MonoAnsiFilter {
	private state = State.Ground;
	private buf: number[] = [];
	private oscNumber = 0;
	private oscSawDigit = false;
	private oscSwallow = false;

	/** Drop a half-parsed sequence. Call when leaving monochrome mode. */
	reset(): void {
		this.state = State.Ground;
		this.buf.length = 0;
		this.oscNumber = 0;
		this.oscSawDigit = false;
		this.oscSwallow = false;
	}

	push(input: Uint8Array): Uint8Array {
		let out = new Uint8Array(Math.max(input.length, 1));
		let o = 0;
		const ensure = (n: number) => {
			if (o + n <= out.length) return;
			let size = Math.max(out.length * 2, 16);
			while (size < o + n) size *= 2;
			const next = new Uint8Array(size);
			next.set(out.subarray(0, o));
			out = next;
		};
		const emit = (b: number) => {
			ensure(1);
			out[o++] = b;
		};
		const emitBuf = () => {
			ensure(this.buf.length);
			for (let i = 0; i < this.buf.length; i++) out[o++] = this.buf[i];
		};
		const clearSeq = () => {
			this.buf.length = 0;
			this.oscSwallow = false;
			this.state = State.Ground;
		};

		for (let i = 0; i < input.length; i++) {
			const b = input[i];
			switch (this.state) {
				case State.Ground:
					if (b === ESC) {
						this.buf.push(b);
						this.state = State.Esc;
					} else if (b === 0x9b) {
						this.buf.push(ESC, 0x5b);
						this.state = State.Csi;
					} else {
						emit(b);
					}
					break;
				case State.Esc:
					this.buf.push(b);
					if (b === 0x5b) {
						this.state = State.Csi;
					} else if (b === 0x5d) {
						this.oscNumber = 0;
						this.oscSawDigit = false;
						this.oscSwallow = false;
						this.state = State.OscNumber;
					} else if (b === 0x50 || b === 0x58 || b === 0x5e || b === 0x5f) {
						this.state = State.String;
					} else if (b >= 0x20 && b <= 0x2f) {
						this.state = State.EscIntermediate;
					} else if (b >= 0x30 && b <= 0x7e) {
						emitBuf();
						clearSeq();
					} else if (b === ESC) {
						this.buf.length = 0;
						this.buf.push(ESC);
					} else {
						emitBuf();
						clearSeq();
					}
					break;
				case State.EscIntermediate:
					this.buf.push(b);
					if (b >= 0x30 && b <= 0x7e) {
						emitBuf();
						clearSeq();
					} else if (b === ESC) {
						emitBuf();
						this.buf.length = 0;
						this.buf.push(ESC);
						this.state = State.Esc;
					} else if (b < 0x20 || b > 0x2f) {
						emitBuf();
						clearSeq();
					}
					break;
				case State.Csi:
					if (b === CAN || b === SUB) {
						clearSeq();
						break;
					}
					if (b === ESC) {
						this.buf.length = 0;
						this.buf.push(ESC);
						this.state = State.Esc;
						break;
					}
					this.buf.push(b);
					if (this.buf.length > 4096) {
						emitBuf();
						clearSeq();
						break;
					}
					if (b >= 0x40 && b <= 0x7e) {
						this.finishCsi(emit);
						clearSeq();
					}
					break;
				case State.OscNumber:
					if (b >= 0x30 && b <= 0x39) {
						this.buf.push(b);
						this.oscSawDigit = true;
						this.oscNumber = this.oscNumber * 10 + (b - 0x30);
						break;
					}
					this.oscSwallow = this.oscSawDigit && COLOR_OSC.has(this.oscNumber);
					if (b === BEL) {
						if (!this.oscSwallow) {
							this.buf.push(b);
							emitBuf();
						}
						clearSeq();
						break;
					}
					if (b === ESC) {
						this.buf.push(b);
						this.state = State.StringEsc;
						break;
					}
					this.buf.push(b);
					this.state = State.OscBody;
					break;
				case State.OscBody:
					this.buf.push(b);
					if (b === BEL) {
						if (!this.oscSwallow) emitBuf();
						clearSeq();
					} else if (b === ESC) {
						this.state = State.StringEsc;
					} else if (this.buf.length > 1_000_000) {
						if (!this.oscSwallow) emitBuf();
						clearSeq();
					}
					break;
				case State.String:
					this.buf.push(b);
					if (b === ESC) this.state = State.StringEsc;
					else if (this.buf.length > 1_000_000) {
						emitBuf();
						clearSeq();
					}
					break;
				case State.StringEsc: {
					const swallowing = this.oscSwallow && this.buf[1] === 0x5d;
					if (b === 0x5c) {
						this.buf.push(b);
						if (!swallowing) emitBuf();
						clearSeq();
					} else {
						// ESC did not introduce ST. Finish the string before it
						// and reprocess this byte as a new escape.
						this.buf.pop();
						if (!swallowing) emitBuf();
						this.buf.length = 0;
						this.oscSwallow = false;
						this.buf.push(ESC);
						this.state = State.Esc;
						i--;
					}
					break;
				}
			}
		}
		return out.subarray(0, o);
	}

	private finishCsi(emit: (b: number) => void): void {
		const final = this.buf[this.buf.length - 1];
		// SGR is CSI Pm m with no private prefix or intermediate.
		let params = '';
		let sgr = final === 0x6d;
		for (let i = 2; i < this.buf.length - 1; i++) {
			const b = this.buf[i];
			if ((b >= 0x3c && b <= 0x3f) || (b >= 0x20 && b <= 0x2f)) sgr = false;
			params += String.fromCharCode(b);
		}
		if (!sgr) {
			for (let i = 0; i < this.buf.length; i++) emit(this.buf[i]);
			return;
		}
		const kept = stripSgrColor(params);
		if (kept === null) return;
		emit(ESC);
		emit(0x5b);
		for (let i = 0; i < kept.length; i++) emit(kept.charCodeAt(i));
		emit(0x6d);
	}
}
