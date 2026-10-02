# Section 3.3 API-based RAG, Graph RAG, and Agentic RAG Comparison

## Methodological objective

Section 3.3 evaluates how different retrieval and reasoning configurations affect manufacturing-stage carbon reasoning over the IFC-derived semantic backbone. The input consists of three evidence sources: the graph backbone generated in Section 3.2, a carbon emission factor table, and manufacturing process documents. The graph provides project-specific design grounding, the factor table provides material-specific emission factors, and the process documents provide manufacturing-process evidence.

The comparison uses the same API-based language model across three retrieval configurations. This keeps the model constant and isolates the effect of the retrieval architecture.

## Compared configurations

**Standard RAG (`rag`)** retrieves external carbon-factor and process-document evidence using text derived from the component name, IFC type, and material text. The graph is used only to obtain a flattened component description. It does not expose graph topology, quantity-basis preferences, or grounding edges to the model.

**Graph RAG (`graph_rag`)** retrieves the component through the IFC-derived graph backbone and provides structured graph context to the model, including the `BuildingComponent`, `IfcMaterial`, `QuantityBasis`, `ProcessAssessmentTarget`, and `CarbonAssessmentTarget` evidence. It still relies on the model to interpret the retrieved evidence and does not call the deterministic calculator.

**Agentic RAG (`agentic_rag`)** uses the same graph-grounded retrieval as Graph RAG, but adds tool execution. The model selects the most appropriate emission-factor row from retrieved candidates, after which a deterministic calculator consumes the selected factor, `QuantityBasis`, quantity observations, and density when required. The model then synthesizes the final answer from the graph evidence, retrieved documents, selected factor, and calculation record.

## Runtime sequence

1. Retrieve each `BuildingComponent` from the backbone graph.
2. Retrieve associated IFC material and quantity evidence.
3. Retrieve candidate carbon factors from the tabular factor source.
4. Retrieve manufacturing-process evidence from the document corpus.
5. Ask the API model to select the most appropriate factor candidate.
6. For `agentic_rag`, call the deterministic carbon calculator.
7. Ask the API model to produce a provenance-grounded answer.
8. Record retrieval, selection, calculation, and latency metrics.

## Evaluation metrics

The experiment records graph-context usage, factor retrieval rate, process retrieval rate, selected factor ID, factor-selection accuracy against the mock ground-truth label, tool-use rate, calculation completion rate, calculated carbon value, formula, and latency. These metrics are exported per component and summarized by RAG configuration and API model.

## Implementation

The runnable implementation is `dm2c_agentic_rag.py`. It accepts OpenAI-compatible chat completion APIs through `OPENAI_API_KEY`, `OPENAI_MODEL`, and optionally `OPENAI_BASE_URL`.

Example:

```powershell
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="your-model-name"
python dm2c_agentic_rag.py `
  --graph outputs\dm2c_backbone_graph.json `
  --out-dir outputs\rag_experiments `
  --rag-variants rag,graph_rag,agentic_rag
```

When real external files are available, replace the mock sources:

```powershell
python dm2c_agentic_rag.py `
  --graph outputs\dm2c_backbone_graph.json `
  --factor-table path\to\carbon_factors.xlsx `
  --process-docs path\to\manufacturing_process_docs.pdf `
  --out-dir outputs\rag_experiments `
  --models your-model-name
```

## Result recording

The script records experiment outputs in:

- `agentic_rag_results.json`
- `agentic_rag_results.csv`
- `agentic_rag_summary.json`
- `agentic_rag_report.md`

The current workspace has not executed the API benchmark because `OPENAI_API_KEY` and `OPENAI_MODEL` are not set in the shell environment. Therefore, no API-based performance values should be reported yet.
