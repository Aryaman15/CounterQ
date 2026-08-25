"use client";

import dynamic from "next/dynamic";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => (
    <div className="monaco-loading" role="status" aria-label="Loading C++ code editor">
      Preparing editor
    </div>
  ),
});

type MonacoInterviewEditorProps = {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
};

export function MonacoInterviewEditor({ value, onChange, readOnly = false }: MonacoInterviewEditorProps) {
  return (
    <div className="editor-shell" data-testid="monaco-editor-surface">
      <MonacoEditor
        height="100%"
        language="cpp"
        theme="vs-dark"
        value={value}
        onChange={(nextValue) => onChange(nextValue ?? "")}
        options={{
          automaticLayout: true,
          bracketPairColorization: { enabled: true },
          cursorBlinking: "smooth",
          fontFamily: "JetBrains Mono, Menlo, Monaco, Consolas, monospace",
          fontLigatures: false,
          fontSize: 14,
          lineHeight: 22,
          lineNumbers: "on",
          minimap: { enabled: false },
          padding: { top: 18, bottom: 18 },
          quickSuggestions: true,
          renderLineHighlight: "line",
          readOnly,
          scrollBeyondLastLine: false,
          smoothScrolling: true,
          tabSize: 4,
          wordWrap: "off",
        }}
      />
    </div>
  );
}
