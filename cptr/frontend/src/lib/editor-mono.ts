import { Decoration, EditorView } from '@codemirror/view';

/** Monochrome has two colours, so a selection has to be a solid inversion:
    ink slab, paper text. CodeMirror paints that slab in a layer of its own
    *behind* the text (`.cm-editor > .cm-scroller > .cm-selectionLayer`), out of
    reach of every selector, so the covered ranges are marked here instead and
    app.css flips their colour. Add to an editor's extensions when
    `document.documentElement` carries `.mono`; on the cursor line the bar is
    already ink, so app.css inverts that one the other way round.
    See `.agent-kb/areas/mono-codemirror-rendering.md`. */
const monoSelectedMark = Decoration.mark({ class: 'cm-mono-selected' });

export const monoSelectionMarks = EditorView.decorations.compute(['selection'], (state) =>
	Decoration.set(
		state.selection.ranges.filter((range) => !range.empty).map((range) => monoSelectedMark.range(range.from, range.to)),
		true
	)
);
