import React from "react";
import { ArrowUp, GitCompareArrows, X, XCircle } from "lucide-react";

export default function SelectionComposer({
  input,
  selectionTags = [],
  compareSelection,
  loading,
  disabled = false,
  onInputChange,
  onSubmit,
  onRemoveSelection,
  onClearSelection,
  onToggleCompareSelection,
}) {
  const canSubmit = Boolean(input?.trim()) && !loading && !disabled;

  const submit = (event) => {
    event.preventDefault();
    if (canSubmit) onSubmit?.();
  };

  return (
    <form className="dm2c-composer" onSubmit={submit}>
      <div className="dm2c-composer-field">
        <div className="dm2c-context-tags" aria-label="Selected BIM context">
          {selectionTags.map((tag) => (
            <span className="dm2c-context-tag" key={tag.id} title={tag.id}>
              <span className="dm2c-context-dot" aria-hidden="true" />
              <span className="dm2c-context-label">{tag.label}</span>
              <button
                type="button"
                className="dm2c-tag-remove"
                aria-label={`Remove ${tag.label} from question context`}
                title={`Remove ${tag.label}`}
                onClick={() => onRemoveSelection?.(tag.id)}
              >
                <X size={13} strokeWidth={2} aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
        <textarea
          rows={1}
          value={input}
          onChange={(event) => onInputChange?.(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) submit(event);
          }}
          disabled={disabled}
          placeholder={disabled
            ? "Canonical-v2 carbon graph and query executor required"
            : selectionTags.length
              ? "Ask about the selected component..."
              : "Ask about carbon assessment..."}
          aria-label="Carbon assessment question"
        />
      </div>

      {selectionTags.length > 1 && (
        <button
          type="button"
          className="dm2c-icon-button"
          aria-label="Clear BIM selection"
          title="Clear selection"
          onClick={onClearSelection}
        >
          <XCircle size={17} aria-hidden="true" />
        </button>
      )}

      <button
        type="button"
        className={`dm2c-icon-button${compareSelection ? " is-active" : ""}`}
        aria-pressed={compareSelection}
        aria-label="Compare selection"
        title="Compare selection"
        onClick={onToggleCompareSelection}
      >
        <GitCompareArrows size={17} aria-hidden="true" />
      </button>

      <button
        type="submit"
        className="dm2c-send-button"
        aria-label="Send question"
        title="Send question"
        disabled={!canSubmit || disabled}
      >
        <ArrowUp size={18} strokeWidth={2.4} aria-hidden="true" />
      </button>
    </form>
  );
}
