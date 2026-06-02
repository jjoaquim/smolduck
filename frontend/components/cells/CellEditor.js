import { html } from "htm/preact";
import { useEffect, useRef } from "preact/hooks";
import { EditorView, keymap, lineNumbers, highlightActiveLineGutter, drawSelection } from "@codemirror/view";
import { EditorState, Compartment } from "@codemirror/state";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { sql } from "@codemirror/lang-sql";
import { syntaxHighlighting, defaultHighlightStyle, bracketMatching } from "@codemirror/language";
import { autocompletion, completionKeymap, closeBrackets } from "@codemirror/autocomplete";

// Shared editor used by every code-bearing cell. Python keeps a plain editor
// until the kernel lands — no @codemirror/lang-python in the import map
// yet, to avoid re-vendoring the offline image for a not-yet-runnable cell.

const theme = EditorView.theme(
  {
    "&": { backgroundColor: "transparent", color: "#f3ece0", fontSize: "13.5px" },
    ".cm-content": { fontFamily: "'JetBrains Mono', monospace", caretColor: "#f6a623", padding: "10px 0" },
    ".cm-gutters": { backgroundColor: "transparent", color: "#6f6457", border: "none" },
    ".cm-activeLine": { backgroundColor: "rgba(246,166,35,0.05)" },
    ".cm-activeLineGutter": { backgroundColor: "transparent", color: "#a89a85" },
    "&.cm-focused .cm-cursor": { borderLeftColor: "#f6a623" },
    ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { backgroundColor: "rgba(84,199,176,0.25)" },
    ".cm-tooltip": { backgroundColor: "#1d1812", border: "1px solid #3d3225", color: "#f3ece0" },
    ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "#2c241b", color: "#f6a623" },
  },
  { dark: true }
);

function schemaFromCatalog(catalog) {
  const schema = {};
  for (const s of catalog || []) {
    schema[s.view_name] = (s.columns || []).map((c) => c.name);
  }
  return schema;
}

function langExtension(language, catalog) {
  if (language === "sql") {
    return sql({ upperCaseKeywords: true, schema: schemaFromCatalog(catalog) });
  }
  return []; // plain text (python, markdown, chart config)
}

export function CellEditor({ value = "", language = "sql", catalog, onChange, onRun }) {
  const hostRef = useRef(null);
  const viewRef = useRef(null);
  const langRef = useRef(new Compartment());
  const onRunRef = useRef(onRun);
  const onChangeRef = useRef(onChange);
  onRunRef.current = onRun;
  onChangeRef.current = onChange;

  useEffect(() => {
    const runFromEditor = () => {
      onRunRef.current && onRunRef.current();
      return true;
    };
    const updateListener = EditorView.updateListener.of((u) => {
      if (u.docChanged) onChangeRef.current && onChangeRef.current(u.state.doc.toString());
    });
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        history(),
        drawSelection(),
        bracketMatching(),
        closeBrackets(),
        autocompletion(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
        langRef.current.of(langExtension(language, catalog)),
        updateListener,
        keymap.of([
          { key: "Mod-Enter", preventDefault: true, run: runFromEditor },
          { key: "Shift-Enter", preventDefault: true, run: runFromEditor },
          ...completionKeymap,
          ...historyKeymap,
          ...defaultKeymap,
        ]),
        theme,
        EditorView.lineWrapping,
      ],
    });
    viewRef.current = new EditorView({ state, parent: hostRef.current });
    return () => viewRef.current.destroy();
    // Editor is built once per mount; the cell is keyed by id+kind so a kind
    // switch remounts with the right language while preserving source.
    // eslint-disable-next-line
  }, []);

  // Keep SQL autocomplete in sync with the catalog.
  useEffect(() => {
    if (!viewRef.current || language !== "sql") return;
    viewRef.current.dispatch({
      effects: langRef.current.reconfigure(langExtension(language, catalog)),
    });
  }, [catalog, language]);

  return html`<div class="cm-host" ref=${hostRef}></div>`;
}
