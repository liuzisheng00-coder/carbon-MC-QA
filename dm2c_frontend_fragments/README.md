# DM2C Fragments BIM Viewer

This is a separate BIM-viewer frontend. It uses ThatOpen Fragments to convert and render IFC in the browser. It does not replace or modify `./dm2c_frontend`, CarbonQL, RAG, factor handling, or KG construction.

## Start it

Start the current DM2C API first, from `the repository root`:

```powershell
python -m uvicorn dm2c_api_server:app --host 127.0.0.1 --port 8000
```

In a second PowerShell window, start this standalone frontend:

```powershell
cd ./dm2c_frontend_fragments
npm run dev
```

Open [http://127.0.0.1:5174](http://127.0.0.1:5174).

Do not start the API with `--reload` when using a project you have already created: the API keeps project records in memory, and a reload discards those records.

## Choose a project

This viewer deliberately has no upload or project-creation flow. Create the project first through the existing DM2C application/API, while the same API process is running. Copy the value shown in the existing application's header as `Project <id>` and paste only that ID into this viewer's **Project ID** box, then select **Open**.

The API must be the current checkout and serve this exact project endpoint:

```
GET http://127.0.0.1:8000/api/projects/<project-id>/ifc-file
```

If the project ID is invalid, the API has been restarted, or the IFC file is unavailable, the viewer shows **Fragment error**. It does not fall back to the old Web IFC viewer or the backend geometry stream.

## What the viewer does

- First visit: downloads the project IFC, converts it in one browser Worker, renders the resulting Fragment, and saves it in the browser's IndexedDB cache.
- Later visit with unchanged IFC: downloads the IFC to verify its SHA-256, then loads the matching cached Fragment; the status briefly shows **Fragment cache**.
- Cache identity includes the raw IFC SHA-256 and the Fragment converter version. A changed IFC or converter version creates a new cache entry.
- **X-Ray On** makes non-selected fragments transparent but still pickable. Clicking a fragment keeps that item opaque/high-contrast and shows its original IFC `GlobalId` in the inspector. Use **Clear selection** to return to only the X-Ray policy.

The conversion and cache are browser-local. Clearing the browser's site data/IndexedDB removes cached Fragments and makes the next visit convert again. Very large IFCs can still take time and memory on the first browser conversion.

## Verify isolation and build

From `the repository root`:

```powershell
cd ./dm2c_frontend
npm test
npm run build

cd ./dm2c_frontend_fragments
npm test
npm run build
rg -n "dm2c_frontend[\\/]src[\\/]web-ifc|/ifc-geometry" .\src -g "!*.test.*"
```

The final `rg` command should return no matches. The Fragment test suite also enforces this source-level isolation rule.
