# DM2C full Fragments frontend

This is the normal DM2C application. It keeps the upload, QA, Product,
Material, Process, and Graph workspaces; its **Model** workspace uses ThatOpen
Fragments with X-Ray and IFC `GlobalId` inspection.

## Normal use: two terminals, one browser address

Start the API first and keep it running. **Do not use `--reload`**: project
state is held in memory, so restarting the API makes existing project IDs
unavailable.

```powershell
# API terminal
# From the cloned repository root
python -m uvicorn dm2c_api_server:app --host 127.0.0.1 --port 8000
```

```powershell
# Full DM2C Fragments terminal
cd ./dm2c_frontend_fragments_full
npm run dev
```

Open <http://127.0.0.1:5173>. In this same address, upload files and create a project, then remain here for Model, QA, Product, Material,
Process, and Graph. No project-ID copying or second frontend address is needed
for ordinary use.

The optional reopening handoff remains available: when a project still exists
in the running API, open
`http://127.0.0.1:5173/?projectId=<id>`. This is useful for restoring an
already-created workspace, not a required normal-use step.

## Model interaction

- Select **X-Ray** to make background IFC components transparent while keeping
  them clickable.
- Click a component to highlight it in amber. The lower-right inspector reports
  its original IFC `GlobalId` and available label/type metadata.
- The first Model opening shows **Converting IFC**. Converted data is cached in
  the browser's IndexedDB by IFC contents and converter version. Later opens in
  the same browser can show **Fragment cache** and avoid conversion.
- The Model tab has no progressive-geometry or legacy Web IFC fallback. A
  missing IFC/project is shown as a concrete Fragment error instead of silently
  switching renderers.

## Repository scope

`dm2c_frontend_fragments_full` is the single application frontend retained in
this final research release. Earlier backup and diagnostic frontends have been
removed from the current repository tree.

## QA and carbon-account capabilities

All non-Model controls retain the behavior of the connected DM2C application.
QA and CarbonQL/account-projection availability depend on the same API process,
release configuration, canonical KG, and LLM configuration. This frontend does
not alter RAG, CarbonQL, carbon factors, the KG, or server-side project setup.

## Validation status

Automated interface, Fragment loader/state/runtime, and production-build tests
are run during development. Live acceptance still requires a project created in
the currently running API: upload/create it at 5173, confirm Model
conversion/cache, X-Ray, clickable `GlobalId`, and the retained
QA/Product/Material/Process/Graph controls.
