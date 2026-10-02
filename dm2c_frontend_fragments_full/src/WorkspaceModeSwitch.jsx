import React from "react";
import { Box, Factory, GitBranch, Layers3, PackageSearch } from "lucide-react";

const WORKSPACE_MODES = [
  { key: "model", label: "Model", icon: Box },
  { key: "product", label: "Product", icon: PackageSearch },
  { key: "material", label: "Material", icon: Layers3 },
  { key: "process", label: "Process", icon: Factory },
  { key: "graph", label: "Graph", icon: GitBranch },
];

export default function WorkspaceModeSwitch({ activeMode, onChange, canonicalQueryAvailable = false }) {
  return (
    <div className="dm2c-mode-switch" role="toolbar" aria-label="Carbon account view">
      {WORKSPACE_MODES.map((mode) => {
        const Icon = mode.icon;
        const requiresCanonicalGraph = ["product", "material", "process"].includes(mode.key);
        const disabled = requiresCanonicalGraph && !canonicalQueryAvailable;
        return (
          <button
            key={mode.key}
            type="button"
            className={activeMode === mode.key ? "is-active" : ""}
            aria-label={`${mode.label} view`}
            aria-pressed={activeMode === mode.key}
            title={disabled ? `${mode.label} view requires a canonical-v2 carbon graph` : `${mode.label} view`}
            disabled={disabled}
            onClick={() => onChange?.(mode.key)}
          >
            <Icon size={15} strokeWidth={2} aria-hidden="true" />
            <span>{mode.label}</span>
          </button>
        );
      })}
    </div>
  );
}
