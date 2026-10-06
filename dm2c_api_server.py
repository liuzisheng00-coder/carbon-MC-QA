"""
MC²QA — API Server (v2, project-based)

Matches DM2CApp_connected.jsx endpoints:
  POST /api/projects              → upload files + init → returns project payload
  GET  /api/projects/:id          → full project payload (summary + results)
  POST /api/projects/:id/ask      → question → CarbonQL compile + execute → payload
  DELETE /api/projects/:id        → cleanup

Carbon-account questions are served by the M3 CarbonQL chain: the language model
compiles the question into one typed program and the deterministic executor
produces every number. The legacy AgenticRAG modules remain only as the design
backbone reader for model geometry and component context; they never answer a
carbon question.

  POST /api/projects/:id/experiments → benchmark, ablation, robustness, or
                                       manual-comparison run over the same chain

Backend modules:
  dm2c_carbonql_service.py        → CarbonQLService, compile + execute + payload
  dm2c_experiment_backend.py      → benchmark scoring and compiler ablations
  dm2c_agentic_rag.py             → BackboneGraphStore, ComponentContext
  dm2c_agentic_rag_v2.py          → WorkbookFactorStore, ProcessEvidenceStore, ...
  dm2c_agentic_rag_v2_agentic.py  → OpenAICompatibleToolClient, backbone indexing

Point the executor at a frozen canonical-v2 release with DM2C_CARBONQL_RELEASE;
synthetic inputs are accepted by default; set DM2C_CARBONQL_ALLOW_SYNTHETIC=0
to opt out.

Run:
  pip install fastapi uvicorn python-multipart
  uvicorn dm2c_api_server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import time
import traceback
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Backend imports — your existing modules
# ---------------------------------------------------------------------------
from dm2c_agentic_rag import BackboneGraphStore, props
from dm2c_agentic_rag_v2 import (
    ProcessEvidenceStore,
    ProcessProfileStore,
    WorkbookFactorStore,
)
from dm2c_agentic_rag_v2_agentic import (
    AgenticExporter,
    DM2CAgenticRAG,
    OpenAICompatibleToolClient,
)
from dm2c_backbone_builder import build_and_export_backbone
from dm2c_experiment_backend import (
    ABLATION_VARIANTS,
    ExperimentCase,
    evaluate_calculation_validation,
    generate_default_experiment_cases,
    make_robustness_cases,
    run_experiment_suite,
)
from dm2c_ifc_geometry import extract_ifc_geometry
from dm2c_canonical_v2_reader import (
    CanonicalSchemaError,
    graph_document_from_context,
    load_canonical_v2_graph_document,
)
from dm2c_carbonql_service import DEFAULT_VARIANT, CarbonQLService, answer_payload
from dm2c_component_subgraph import (
    ComponentNodeNotFound,
    ComponentSelectorAmbiguous,
    ComponentSelectorInvalid,
    component_subgraph_from_context,
    component_subgraph_from_graph_path,
)
from dm2c_m23_canonical import APPLICATION_CLASSES, SCHEMA_VERSION

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="MC²QA API", version="0.5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_ROOT = Path("uploads")
OUTPUT_ROOT = Path("outputs/api")
DEFAULT_ONTOLOGY_PATH = Path(os.environ.get("DM2C_ONTOLOGY_PATH", Path(__file__).with_name("mic-carbon-ontology.ttl")))
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
DEEPSEEK_OPENAI_BASE_URL = "https://api.deepseek.com"
PROJECT_EXPIRY_SECONDS = int(os.environ.get("DM2C_PROJECT_EXPIRY", 86400))  # 24h default
CARBONQL_RELEASE_ENV = "DM2C_CARBONQL_RELEASE"
CARBONQL_ALLOW_SYNTHETIC_ENV = "DM2C_CARBONQL_ALLOW_SYNTHETIC"
DEFAULT_CARBONQL_RELEASE = (
    Path(__file__).resolve().parent
    / "outputs/research_experiments/m2_typea1_full_en_layerdedup_20260731_d_spread"
)
_EXACT_RELEASE_FILE_NAMES = frozenset(
    {
        "case_version_manifest.json",
        "m2_alignment_report.json",
        "multigranular_carbon_kg.cypher",
        "multigranular_carbon_kg.json",
        "multigranular_carbon_kg_stats.json",
        "multigranular_carbon_kg_validation.csv",
    }
)


def _allow_synthetic_carbonql_release() -> bool:
    """Allow complete releases by default; explicit false values opt out."""
    value = os.environ.get(CARBONQL_ALLOW_SYNTHETIC_ENV, "1").strip().lower()
    return value not in {"0", "false"}


def _is_valid_release_dir(path: Path) -> bool:
    try:
        if not path.is_dir():
            return False
        names = {entry.name for entry in path.iterdir() if entry.is_file()}
        return names == _EXACT_RELEASE_FILE_NAMES
    except OSError:
        return False


def _has_release_files(path: Path) -> bool:
    try:
        if not path.is_dir():
            return False
        names = {entry.name for entry in path.iterdir() if entry.is_file()}
        return _EXACT_RELEASE_FILE_NAMES <= names
    except OSError:
        return False


def _stage_release_dir(source: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in sorted(_EXACT_RELEASE_FILE_NAMES):
        shutil.copy2(source / name, dest / name)
    return dest


def _ensure_loadable_release_dir(source: Path, staging_root: Path) -> Path:
    if _is_valid_release_dir(source):
        return source
    if not _has_release_files(source):
        raise CanonicalSchemaError(f"release directory is missing canonical artifacts: {source}")
    return _stage_release_dir(source, staging_root)


def _configured_release_dir() -> Optional[Path]:
    configured = os.environ.get(CARBONQL_RELEASE_ENV, "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if _is_valid_release_dir(candidate) or _has_release_files(candidate):
            return candidate
        return None
    if _is_valid_release_dir(DEFAULT_CARBONQL_RELEASE):
        return DEFAULT_CARBONQL_RELEASE
    if _has_release_files(DEFAULT_CARBONQL_RELEASE):
        return DEFAULT_CARBONQL_RELEASE
    return None


def _find_release_root(root: Path) -> Optional[Path]:
    if _is_valid_release_dir(root) or _has_release_files(root):
        return root
    for child in root.iterdir():
        if child.is_dir() and (_is_valid_release_dir(child) or _has_release_files(child)):
            return child
    return None


def _extract_release_zip(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(dest)
    release_root = _find_release_root(dest)
    if release_root is None:
        raise HTTPException(
            422,
            "The uploaded archive does not contain a complete canonical-v2 release "
            "(six files including case_version_manifest.json and multigranular_carbon_kg.json).",
        )
    return release_root


def _ensure_stub_factor_workbook(upload_dir: Path) -> Path:
    """Minimal workbook for legacy RAG indexing when CarbonQL serves QA."""
    path = upload_dir / "_canonical_only_stub_factors.xlsx"
    if path.exists():
        return path
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:
        raise HTTPException(
            500,
            "pandas/openpyxl are required for canonical-only project setup. "
            "Run: python -m pip install pandas openpyxl",
        ) from exc
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame(columns=["material category", "factor"]).to_excel(
            writer, sheet_name="Embodied Carbon Coefficients", index=False
        )
        pd.DataFrame(columns=["energy_type", "factor_value"]).to_excel(
            writer, sheet_name="Energy Emission Factors", index=False
        )
    return path


CARBONQL_VARIANT = os.environ.get("DM2C_CARBONQL_VARIANT", "V4")
PROCESS_DOC_CHUNK_CHARS = 3500
PROCESS_ACTIVITY_HINTS = {
    "welding": ["welding", "weld", "gmaw", "smaw", "焊", "焊接"],
    "cutting": ["cutting", "saw", "laser", "plasma", "切割", "下料"],
    "assembly": ["assembly", "assemble", "fit-up", "fabrication", "装配", "组装", "制造"],
    "inspection": ["inspection", "itp", "hold point", "ndt", "qc", "qa", "检查", "检验", "验收"],
    "lifting": ["lifting", "crane", "hoist", "吊装", "起重"],
    "casting": ["concrete", "pouring", "curing", "vibrating", "混凝土", "浇筑", "养护", "振捣"],
}
PROCESS_COMPONENT_HINTS = {
    "IfcBeam": ["beam", "girder", "梁"],
    "IfcColumn": ["column", "pillar", "柱"],
    "IfcSlab": ["slab", "floor", "板", "楼板"],
    "IfcWall": ["wall", "墙"],
    "IfcWindow": ["window", "窗"],
    "IfcDoor": ["door", "门"],
    "IfcMember": ["member", "brace", "支撑", "构件"],
}


def normalize_llm_provider(provider: Optional[str]) -> str:
    value = (provider or "none").strip().lower()
    aliases = {
        "google": "gemini",
        "google_gemini": "gemini",
        "google-gemini": "gemini",
        "deep-seek": "deepseek",
        "deep_seek": "deepseek",
    }
    return aliases.get(value, value)


def _resolve_embedding_provider(chat_provider: str) -> str:
    """Pick which provider serves embeddings.

    Chat and embeddings are independent calls, so the embedding provider can
    differ from the chat provider. This matters because DeepSeek has no
    embeddings endpoint: keeping DeepSeek for NL->CarbonQL compilation while
    borrowing an embedding-capable provider is the only way to enable the
    semantic name-matching fallback without switching the whole chat stack.
    Explicit DM2C_EMBEDDING_PROVIDER wins; otherwise reuse the chat provider
    when it can embed, else auto-borrow whichever embedding key is present.
    """
    explicit_raw = os.environ.get("DM2C_EMBEDDING_PROVIDER", "").strip()
    if explicit_raw:
        return normalize_llm_provider(explicit_raw)
    if chat_provider in {"openai", "gemini"}:
        return chat_provider
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return ""


def _embedding_model_for(provider: str) -> str:
    if provider == "gemini":
        return (
            os.environ.get("GEMINI_EMBEDDING_MODEL")
            or os.environ.get("OPENAI_EMBEDDING_MODEL")
            or "gemini-embedding-001"
        )
    if provider == "openai":
        return os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    if provider == "deepseek":
        # DeepSeek exposes no embeddings endpoint; only honour an explicit override.
        return os.environ.get("DEEPSEEK_EMBEDDING_MODEL", "")
    return ""


def default_project_config() -> Dict[str, str]:
    default_provider = "deepseek" if os.environ.get("DEEPSEEK_API_KEY") else "openai"
    provider = normalize_llm_provider(os.environ.get("DM2C_LLM_PROVIDER", default_provider))
    if provider == "gemini":
        model = os.environ.get("GEMINI_MODEL") or os.environ.get("OPENAI_MODEL") or "gemini-2.5-flash"
    elif provider == "deepseek":
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    else:
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    embedding_provider = _resolve_embedding_provider(provider)
    embedding_model = _embedding_model_for(embedding_provider)

    return {
        "factory_grid": os.environ.get("DM2C_FACTORY_GRID", "Guangdong"),
        "llm_provider": provider,
        "model": model,
        "embedding_provider": embedding_provider,
        "embedding_model": embedding_model,
        "embedding_match_floor": os.environ.get("DM2C_EMBED_MATCH_FLOOR", "0.60"),
        "top_process_k": os.environ.get("DM2C_TOP_PROCESS_K", "8"),
    }


def make_llm_client(config: Dict[str, str]) -> OpenAICompatibleToolClient:
    """Create LLM client. An LLM provider is required — no deterministic fallback."""
    provider = normalize_llm_provider(config.get("llm_provider"))
    config["llm_provider"] = provider

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        missing_key_message = "LLM provider is 'openai' but OPENAI_API_KEY env var is not set."
    elif provider == "gemini":
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        base_url = os.environ.get("GEMINI_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or GEMINI_OPENAI_BASE_URL
        config["model"] = config.get("model") or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        config["embedding_model"] = (
            config.get("embedding_model")
            or os.environ.get("GEMINI_EMBEDDING_MODEL")
            or "gemini-embedding-001"
        )
        missing_key_message = (
            "LLM provider is 'gemini' but no Gemini key was found. "
            "Set GEMINI_API_KEY or GOOGLE_API_KEY."
        )
    elif provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", DEEPSEEK_OPENAI_BASE_URL)
        config["model"] = config.get("model") or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        config["embedding_model"] = config.get("embedding_model") or os.environ.get("DEEPSEEK_EMBEDDING_MODEL", "")
        missing_key_message = (
            "LLM provider is 'deepseek' but DEEPSEEK_API_KEY env var is not set."
        )
    else:
        raise HTTPException(
            400,
            f"Unsupported LLM provider '{provider}'. Use 'openai', 'gemini', or 'deepseek'. "
            f"Set DM2C_LLM_PROVIDER environment variable.",
        )

    if not api_key:
        raise HTTPException(400, missing_key_message)

    return OpenAICompatibleToolClient(
        api_key=api_key,
        model=config["model"],
        base_url=base_url,
        temperature=0.0,
        timeout=60,
    )


def make_embedding_client(
    config: Dict[str, str], chat_client: "OpenAICompatibleToolClient | None" = None
) -> "tuple[OpenAICompatibleToolClient | None, str]":
    """Build the client that serves entity-name embeddings, or return no client.

    The embedding provider may differ from the chat provider (see
    ``_resolve_embedding_provider``). When it matches the chat provider the chat
    client is reused; otherwise a dedicated client is created against the
    embedding provider's endpoint. Missing keys disable the fallback silently
    rather than blocking chat, so the deterministic lexical tiers still apply.
    """
    provider = normalize_llm_provider(
        config.get("embedding_provider") or config.get("llm_provider")
    )
    model = config.get("embedding_model") or ""
    if not model or provider == "deepseek":
        return None, ""

    if provider == chat_client_provider(chat_client) and chat_client is not None:
        return chat_client, model

    if provider == "gemini":
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        base_url = (
            os.environ.get("GEMINI_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or GEMINI_OPENAI_BASE_URL
        )
    elif provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    else:
        return None, ""

    if not api_key:
        return None, ""

    return (
        OpenAICompatibleToolClient(
            api_key=api_key,
            model=model,
            base_url=base_url,
            temperature=0.0,
            timeout=60,
        ),
        model,
    )


def _embedding_match_floor(config: Dict[str, str]) -> float:
    """Minimum cosine similarity for the semantic name-matching fallback.

    Calibrated for ``gemini-embedding-001`` (real paraphrases score ~0.73+,
    off-topic text ~0.55), so the default cleanly rejects junk. Other embedding
    models sit on a different scale; override with DM2C_EMBED_MATCH_FLOOR.
    """
    try:
        return float(config.get("embedding_match_floor", "0.60"))
    except (TypeError, ValueError):
        return 0.60


def chat_client_provider(client: "OpenAICompatibleToolClient | None") -> str:
    """Best-effort identify which provider a chat client points at, so a
    matching embedding provider can reuse it instead of opening a second one."""
    if client is None:
        return ""
    base_url = str(getattr(client, "base_url", "") or "").lower()
    if "deepseek" in base_url:
        return "deepseek"
    if "generativelanguage" in base_url or "gemini" in base_url:
        return "gemini"
    if "openai" in base_url:
        return "openai"
    return ""


def compact_process_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text


def chunk_process_text(text: str, limit: int = PROCESS_DOC_CHUNK_CHARS) -> List[str]:
    text = compact_process_text(text)
    if not text:
        return []
    chunks: List[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + limit])
        start += limit
    return chunks


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:
        raise HTTPException(
            500,
            "Reading uploaded PDF manufacturing evidence requires pypdf. Run: python -m pip install pypdf",
        ) from exc

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def extract_spreadsheet_text(path: Path) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    rows: List[str] = []
    for sheet in wb.worksheets:
        rows.append(f"Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(cell).strip() for cell in row if cell not in (None, "")]
            if cells:
                rows.append(" | ".join(cells))
    return "\n".join(rows)


def infer_process_activities(text: str) -> List[str]:
    haystack = normalize_for_hinting(text)
    activities = []
    for activity, hints in PROCESS_ACTIVITY_HINTS.items():
        if any(hint in haystack for hint in hints):
            activities.append(activity)
    return activities


def infer_component_types(text: str) -> List[str]:
    haystack = normalize_for_hinting(text)
    component_types = []
    for ifc_type, hints in PROCESS_COMPONENT_HINTS.items():
        if any(hint in haystack for hint in hints):
            component_types.append(ifc_type)
    return component_types


def normalize_for_hinting(text: str) -> str:
    return (text or "").casefold()


def make_process_keywords(path: Path, text: str) -> List[str]:
    stem_tokens = re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", path.stem)
    keywords = [token for token in stem_tokens if token]
    keywords.extend(infer_process_activities(text))
    keywords.extend(infer_component_types(text))
    if "itp" in normalize_for_hinting(path.name + " " + text):
        keywords.append("ITP")
    return sorted(set(keywords), key=str.casefold)


def build_process_jsonl_from_uploads(project: Project, manufacturing_paths: List[Path]) -> Optional[Path]:
    rows: List[Dict[str, Any]] = []
    for source_path in manufacturing_paths:
        suffix = source_path.suffix.lower()
        if suffix == ".pdf":
            text = extract_pdf_text(source_path)
            source_type = "pdf"
        elif suffix in {".txt", ".md"}:
            text = source_path.read_text(encoding="utf-8", errors="ignore")
            source_type = suffix.lstrip(".")
        elif suffix == ".xlsx":
            text = extract_spreadsheet_text(source_path)
            source_type = "xlsx"
        else:
            continue

        chunks = chunk_process_text(text)
        if not chunks:
            continue
        component_types = infer_component_types(text)
        activities = infer_process_activities(text)
        keywords = make_process_keywords(source_path, text)
        for index, chunk in enumerate(chunks, start=1):
            rows.append({
                "doc_id": f"{source_path.stem}:{index}",
                "title": f"{source_path.stem} chunk {index}",
                "component_types": component_types,
                "keywords": keywords,
                "activities": activities,
                "text": chunk,
                "source_file": str(source_path),
                "source_type": source_type,
                "sequence": index,
            })

    if not rows:
        return None

    out_path = project.upload_dir / "manufacturing" / "_uploaded_process_docs.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return out_path

# ---------------------------------------------------------------------------
# Project store (in-memory; swap for DB in production)
# ---------------------------------------------------------------------------


@dataclass
class Project:
    project_id: str
    created_at: float
    upload_dir: Path
    output_dir: Path
    file_manifest: Dict[str, List[str]] = field(default_factory=dict)
    # resolved paths
    graph_path: Optional[Path] = None
    canonical_graph_path: Optional[Path] = None
    canonical_graph_info: Dict[str, Any] = field(default_factory=dict)
    canonical_query_available: bool = False
    canonical_query_status: str = "not_connected_to_executor"
    factor_path: Optional[Path] = None
    process_docs_path: Optional[Path] = None
    profile_path: Optional[Path] = None
    ifc_path: Optional[Path] = None
    backbone_build: Optional[Dict[str, Any]] = None
    uploaded_release_dir: Optional[Path] = None
    canonical_release_dir: Optional[Path] = None
    carbon_accounts: Dict[str, Any] = field(default_factory=dict)
    # runtime
    runner: Optional[DM2CAgenticRAG] = None
    carbonql: Optional[CarbonQLService] = None
    llm_client: Optional[OpenAICompatibleToolClient] = None
    payload: Optional[Dict[str, Any]] = None
    experiment_runs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    config: Dict[str, str] = field(default_factory=default_project_config)
    status: str = "created"  # created → initializing → analyzed → error
    error_message: str = ""


# project_id → Project
_projects: Dict[str, Project] = {}


def get_project(project_id: str) -> Project:
    proj = _projects.get(project_id)
    if not proj:
        raise HTTPException(404, f"Project {project_id} not found")
    return proj


def cleanup_expired_projects() -> int:
    """Remove projects older than PROJECT_EXPIRY_SECONDS. Returns count removed."""
    now = time.time()
    expired = [pid for pid, p in _projects.items() if now - p.created_at > PROJECT_EXPIRY_SECONDS]
    for pid in expired:
        project = _projects.pop(pid, None)
        if project:
            if project.upload_dir.exists():
                shutil.rmtree(project.upload_dir, ignore_errors=True)
            if project.output_dir.exists():
                shutil.rmtree(project.output_dir, ignore_errors=True)
    return len(expired)


# ---------------------------------------------------------------------------
# File classification + role resolution
# ---------------------------------------------------------------------------

_UNSAFE_UPLOAD_FILENAME_CHARS = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED_FILENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
    | {f"COM{index}" for index in "¹²³"}
    | {f"LPT{index}" for index in "¹²³"}
)


def _validate_upload_filename(filename: str) -> str:
    """Return one safe basename or reject the multipart filename."""
    name = str(filename)
    windows_path = PureWindowsPath(name)
    posix_path = PurePosixPath(name)
    stem = name.partition(".")[0].rstrip(" ").upper()
    if (
        not name
        or name in {".", ".."}
        or name != name.strip()
        or name.endswith(".")
        or len(name) > 255
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or posix_path.is_absolute()
        or windows_path.name != name
        or posix_path.name != name
        or any(character in _UNSAFE_UPLOAD_FILENAME_CHARS for character in name)
        or any(ord(character) < 32 for character in name)
        or stem in _WINDOWS_RESERVED_FILENAMES
    ):
        raise HTTPException(400, "Uploaded filename must be one safe basename")
    return name

def save_uploaded_files(
    project: Project,
    design: List[UploadFile],
    manufacturing: List[UploadFile],
    carbon_factors: List[UploadFile],
) -> Dict[str, List[str]]:
    """Save all uploaded files to project directory, return manifest."""
    manifest: Dict[str, List[str]] = {"design": [], "manufacturing": [], "carbon_factors": []}
    upload_root = project.upload_dir.expanduser().resolve()
    planned: List[tuple[str, UploadFile, Path]] = []
    for category, file_list in [("design", design), ("manufacturing", manufacturing), ("carbon_factors", carbon_factors)]:
        category_root = (upload_root / category).resolve()
        try:
            category_root.relative_to(upload_root)
        except ValueError as exc:
            raise HTTPException(500, "Upload category resolved outside the project directory") from exc
        seen_names: set[str] = set()
        for f in file_list:
            if not f.filename:
                continue
            safe_name = _validate_upload_filename(f.filename)
            collision_key = safe_name.casefold()
            if collision_key in seen_names:
                raise HTTPException(400, f"Duplicate uploaded filename in {category}: {safe_name}")
            seen_names.add(collision_key)
            dest = (category_root / safe_name).resolve()
            try:
                dest.relative_to(category_root)
            except ValueError as exc:
                raise HTTPException(400, "Uploaded filename resolved outside its category") from exc
            if dest.parent != category_root:
                raise HTTPException(400, "Uploaded filename must resolve to a direct category child")
            if dest.exists():
                raise HTTPException(409, f"Uploaded file already exists: {safe_name}")
            planned.append((category, f, dest))

    for category, upload, dest in planned:
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = upload.file.read()
        dest.write_bytes(content)
        manifest[category].append(str(dest))

    project.file_manifest = manifest
    return manifest


def convert_ifc_to_backbone(project: Project, ifc_path: Path) -> Path:
    """Convert an uploaded IFC file into the graph JSON consumed by the RAG backend."""
    ontology_path = DEFAULT_ONTOLOGY_PATH.expanduser().resolve()
    if not ontology_path.exists():
        raise HTTPException(
            500,
            f"Ontology file not found: {ontology_path}. Set DM2C_ONTOLOGY_PATH or place mic-carbon-ontology.ttl beside dm2c_api_server.py.",
        )

    try:
        result = build_and_export_backbone(
            ifc_path=ifc_path.expanduser().resolve(),
            ontology_path=ontology_path,
            out_dir=project.output_dir / "backbone",
            unit_graph_level="compact",
            quantity_node_mode="preferred",
        )
    except Exception as exc:
        raise HTTPException(500, f"IFC to backbone graph conversion failed: {exc}") from exc

    graph_path = Path(result["graph_json"])
    if not graph_path.exists():
        raise HTTPException(500, f"IFC conversion finished but graph JSON was not created: {graph_path}")

    project.ifc_path = ifc_path
    project.backbone_build = result
    return graph_path


def _canonical_graph_metadata(path: Path, document: Any) -> Dict[str, Any]:
    class_counts = {label: 0 for label in APPLICATION_CLASSES}
    for node in document.nodes:
        for label in node.get("labels", ()):
            if label in class_counts:
                class_counts[label] += 1
    return {
        "schemaVersion": SCHEMA_VERSION,
        "canonicalGraphAvailable": True,
        "canonicalGraphStatus": "graph_document_validated",
        "canonicalValidationLevel": "graph_document",
        "graph": str(path),
        "stats": {
            "nodeCount": len(document.nodes),
            "edgeCount": len(document.edges),
            "applicationClassCounts": class_counts,
        },
    }


class _DuplicateJsonKey(ValueError):
    pass


def _unique_json_object(pairs: List[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate key: {key}")
        result[key] = value
    return result


def _load_design_json(path: Path) -> Dict[str, Any]:
    try:
        text_value = path.read_text(encoding="utf-8")
        payload = json.loads(text_value, object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as exc:
        raise HTTPException(422, f"Invalid design JSON {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            422,
            f"Invalid design JSON {path.name}: top-level value must be an object.",
        )
    return payload


def _project_graph_info(project: Project) -> Dict[str, Any]:
    account_available = (
        project.canonical_graph_path is not None or project.carbonql is not None
    )
    query_available = bool(project.canonical_query_available and project.carbonql)
    query_status = (
        project.canonical_query_status
        if account_available
        else "canonical_graph_unavailable"
    )
    backbone_info = {
        "ifcRole": "design_backbone_geometry" if project.ifc_path else "",
        "backboneGraph": str(project.graph_path) if project.graph_path else "",
        "backboneStats": dict((project.backbone_build or {}).get("stats", {})),
        "canonicalQueryAvailable": query_available,
        "canonicalQueryStatus": query_status,
        "carbonAccount": (
            project.carbonql.graph_summary() if project.carbonql else {}
        ),
    }
    if project.canonical_graph_path is not None:
        return {
            **project.canonical_graph_info,
            **backbone_info,
            "schemaVersion": SCHEMA_VERSION,
            "canonicalGraphAvailable": True,
            "canonicalGraphStatus": "graph_document_validated",
            "canonicalValidationLevel": "graph_document",
            "graph": str(project.canonical_graph_path),
            "stats": dict(project.canonical_graph_info.get("stats", {})),
        }
    if project.carbonql is not None:
        return {
            **project.canonical_graph_info,
            **backbone_info,
            "schemaVersion": SCHEMA_VERSION,
            "canonicalGraphAvailable": True,
            "canonicalGraphStatus": "frozen_release_validated",
            "canonicalValidationLevel": "release_envelope",
            "graph": str(project.canonical_release_dir or ""),
            "stats": dict(project.canonical_graph_info.get("stats", {})),
        }
    return {
        **backbone_info,
        "schemaVersion": "",
        "canonicalGraphAvailable": False,
        "canonicalGraphStatus": "backbone_only",
        "canonicalValidationLevel": "none",
        "graph": "",
        "stats": {},
    }


def _resolve_canonical_release_dir(project: Project) -> Optional[Path]:
    """Locate the frozen six-file release that backs the canonical graph.

    Priority order:
    1. Uploaded release archive unpacked during project creation
    2. Explicit DM2C_CARBONQL_RELEASE or the default case-study release
    3. Parent directory of an uploaded canonical-v2 graph document
    """
    if project.uploaded_release_dir is not None:
        return project.uploaded_release_dir
    configured = _configured_release_dir()
    if configured is not None:
        return configured
    if project.canonical_graph_path is not None:
        parent = project.canonical_graph_path.parent
        if _is_valid_release_dir(parent) or _has_release_files(parent):
            return parent
    return None


def _populate_carbon_accounts(project: Project) -> None:
    if project.carbonql is None:
        return
    project.carbon_accounts = {
        view: [dict(row) for row in rows]
        for view, rows in project.carbonql.perspective_accounts().items()
    }


def connect_carbonql_executor(project: Project) -> None:
    """Attach the CarbonQL chain, or record why carbon queries stay disabled."""
    project.carbonql = None
    project.canonical_query_available = False
    allow_synthetic = _allow_synthetic_carbonql_release()

    if project.llm_client is None:
        project.canonical_query_status = "llm_client_unavailable"
        return

    embedding_client, embedding_model = make_embedding_client(
        project.config, project.llm_client
    )
    embedding_match_floor = _embedding_match_floor(project.config)

    if project.canonical_graph_path is not None:
        try:
            project.carbonql = CarbonQLService.from_graph_document(
                project.canonical_graph_path,
                project.llm_client,
                allow_synthetic=allow_synthetic,
                variant=CARBONQL_VARIANT,
                embedding_client=embedding_client,
                embedding_model=embedding_model,
                embedding_match_floor=embedding_match_floor,
            )
            project.canonical_release_dir = project.canonical_graph_path.parent
            project.canonical_query_available = True
            project.canonical_query_status = "carbonql_graph_document_connected"
            _populate_carbon_accounts(project)
            return
        except CanonicalSchemaError as exc:
            project.canonical_query_status = f"canonical_graph_rejected: {exc}"
            return
        except (OSError, ValueError) as exc:
            project.canonical_query_status = f"carbonql_executor_unavailable: {exc}"
            return

    release_dir = _resolve_canonical_release_dir(project)
    project.canonical_release_dir = release_dir
    if release_dir is None:
        project.canonical_query_status = "canonical_release_unavailable"
        return

    try:
        loadable_release = _ensure_loadable_release_dir(
            release_dir,
            project.upload_dir / "canonical_release_staging",
        )
        project.carbonql = CarbonQLService.from_release(
            loadable_release,
            project.llm_client,
            allow_synthetic=allow_synthetic,
            variant=CARBONQL_VARIANT,
            embedding_client=embedding_client,
            embedding_model=embedding_model,
            embedding_match_floor=embedding_match_floor,
        )
        project.canonical_release_dir = loadable_release
    except CanonicalSchemaError as exc:
        project.canonical_query_status = f"canonical_release_rejected: {exc}"
        return
    except (OSError, ValueError) as exc:
        project.canonical_query_status = f"carbonql_executor_unavailable: {exc}"
        return

    project.canonical_query_available = True
    project.canonical_query_status = "carbonql_executor_connected"
    _adopt_release_graph(project)


def _release_validation_coverage(context: Any) -> Dict[str, Any]:
    """Recount the release's accepted and rejected records for the graph panel.

    An uploaded graph document carries no validation ledger, so the panel says
    coverage is unavailable. A release does carry one, and hiding it would
    understate how much of the source evidence actually entered the account.
    """
    accepted_by_kind: Counter[str] = Counter()
    rejected_by_kind: Counter[str] = Counter()
    rejected_by_reason: Counter[str] = Counter()
    for row in context.validation_rows:
        kind = str(row.get("kind", ""))
        if str(row.get("status", "")) == "accepted":
            accepted_by_kind[kind] += 1
        else:
            rejected_by_kind[kind] += 1
            rejected_by_reason[str(row.get("reasonCode", ""))] += 1
    accepted = sum(accepted_by_kind.values())
    rejected = sum(rejected_by_kind.values())
    candidates = accepted + rejected
    return {
        "acceptedByKind": dict(sorted(accepted_by_kind.items())),
        "acceptedCount": accepted,
        "candidateCount": candidates,
        "coverageFormula": "acceptedCount / candidateCount",
        "coverageValue": accepted / candidates if candidates else 0.0,
        "rejectedByKind": dict(sorted(rejected_by_kind.items())),
        "rejectedByReason": dict(sorted(rejected_by_reason.items())),
        "rejectedCount": rejected,
    }


def _adopt_release_graph(project: Project) -> None:
    """Describe the release's graph for panels that inspect it, not query it.

    A pinned release carries the same validated graph an upload would, so the
    schema statistics and the component closures have to come from it. They are
    read once here because the alternative is re-reading and re-validating an
    11 MB document on every panel request.
    """
    if project.carbonql is None or project.canonical_graph_path is not None:
        return
    try:
        document = graph_document_from_context(project.carbonql.context)
    except CanonicalSchemaError:
        return
    metadata = _canonical_graph_metadata(project.canonical_release_dir or Path(), document)
    project.canonical_graph_info = {
        **metadata,
        "stats": {
            **metadata["stats"],
            "validation": _release_validation_coverage(project.carbonql.context),
        },
        # A release is validated as a whole envelope, which is a stronger claim
        # than the standalone graph document an upload can make. Saying so here
        # keeps the panel from understating what was checked.
        "canonicalGraphStatus": "frozen_release_validated",
        "canonicalValidationLevel": "release_envelope",
    }
    _populate_carbon_accounts(project)


def _require_canonical_query(project: Project) -> None:
    if project.canonical_graph_path is None and project.canonical_release_dir is None:
        raise HTTPException(
            409,
            "Canonical-v2 carbon graph unavailable; carbon-account queries are disabled.",
        )
    if not project.canonical_query_available or project.carbonql is None:
        raise HTTPException(
            409,
            "Canonical-v2 graph is validated, but the CarbonQL executor is not "
            f"connected ({project.canonical_query_status}); legacy backbone QA is "
            "not used as a substitute.",
        )


def resolve_roles(project: Project) -> None:
    """Determine which uploaded file serves which backend role."""
    manifest = project.file_manifest

    # IFC/RAG design backbone and the canonical carbon graph are distinct roles.
    design_paths = [Path(path_str) for path_str in manifest.get("design", [])]
    ifc_paths = [path for path in design_paths if path.suffix.lower() == ".ifc"]
    zip_paths = [path for path in design_paths if path.suffix.lower() == ".zip"]
    json_paths = [
        path for path in design_paths
        if path.suffix.lower() == ".json"
    ]
    canonical_candidates: List[tuple[Path, Any]] = []
    legacy_json_paths: List[Path] = []
    for path in json_paths:
        marker = _load_design_json(path)
        if marker.get("schemaVersion") != SCHEMA_VERSION:
            legacy_json_paths.append(path)
            continue
        try:
            document = load_canonical_v2_graph_document(path)
        except CanonicalSchemaError as exc:
            raise HTTPException(
                422,
                f"Invalid canonical-v2 graph document: {exc}",
            ) from exc
        canonical_candidates.append((path, document))

    factor_paths = [
        Path(path_str)
        for path_str in manifest.get("carbon_factors", [])
        if Path(path_str).suffix.lower() == ".xlsx"
    ]
    manufacturing_paths = [Path(path_str) for path_str in manifest.get("manufacturing", [])]
    process_doc_paths = [path for path in manufacturing_paths if path.suffix.lower() == ".jsonl"]
    profile_paths = [path for path in manufacturing_paths if path.suffix.lower() == ".csv"]

    # Every singleton role must be unambiguous before any role is committed or
    # an IFC conversion is started. Multipart ordering must never choose data.
    if len(ifc_paths) > 1:
        raise HTTPException(400, "Multiple IFC files were uploaded; the design backbone role is ambiguous.")
    if len(zip_paths) > 1:
        raise HTTPException(400, "Multiple carbon release archives were uploaded; upload one zip or rely on the server default release.")
    if len(canonical_candidates) > 1:
        raise HTTPException(
            400,
            "Multiple canonical-v2 graph documents were uploaded; the carbon graph role is ambiguous.",
        )
    if len(legacy_json_paths) > 1:
        raise HTTPException(400, "Multiple legacy design graphs were uploaded; the legacy backbone role is ambiguous.")
    if ifc_paths and legacy_json_paths:
        raise HTTPException(
            400,
            "Both an IFC file and a legacy design graph were uploaded for the design backbone role; choose one backbone source.",
        )
    if len(factor_paths) > 1:
        raise HTTPException(400, "Multiple carbon factor workbooks were uploaded; the factor role is ambiguous.")
    if len(process_doc_paths) > 1:
        raise HTTPException(400, "Multiple process JSONL documents were uploaded; the process evidence role is ambiguous.")
    if len(profile_paths) > 1:
        raise HTTPException(400, "Multiple manufacturing CSV profiles were uploaded; the profile role is ambiguous.")

    project.ifc_path = ifc_paths[0] if ifc_paths else None
    if zip_paths:
        project.uploaded_release_dir = _extract_release_zip(
            zip_paths[0],
            project.upload_dir / "canonical_release_upload",
        )
    if canonical_candidates:
        canonical_path, document = canonical_candidates[0]
        project.canonical_graph_path = canonical_path
        project.canonical_graph_info = _canonical_graph_metadata(
            canonical_path,
            document,
        )
    if legacy_json_paths:
        # Only a legacy design graph may serve the legacy RAG reader.
        project.graph_path = legacy_json_paths[0]

    if project.graph_path is None and project.ifc_path is not None:
        project.graph_path = convert_ifc_to_backbone(project, project.ifc_path)

    # Carbon factors → one unambiguous workbook (.xlsx)
    project.factor_path = factor_paths[0] if factor_paths else None

    # Manufacturing → one process document + one profile; PDFs/text/workbooks
    # may still be aggregated into a generated process document.
    project.process_docs_path = process_doc_paths[0] if process_doc_paths else None
    project.profile_path = profile_paths[0] if profile_paths else None

    if project.process_docs_path is None:
        project.process_docs_path = build_process_jsonl_from_uploads(project, manufacturing_paths)

    # Create empty .jsonl if none found (ProcessEvidenceStore requires a file)
    if project.process_docs_path is None:
        empty = project.upload_dir / "manufacturing" / "_empty_process_docs.jsonl"
        empty.parent.mkdir(parents=True, exist_ok=True)
        empty.write_text("", encoding="utf-8")
        project.process_docs_path = empty


# ---------------------------------------------------------------------------
# Runner initialization
# ---------------------------------------------------------------------------

def init_runner(project: Project) -> None:
    """Index the design backbone and connect the CarbonQL chain.

    The backbone reader supplies model geometry and component context for the
    interface; carbon questions are answered only by the CarbonQL executor.
    """
    if project.graph_path is None:
        raise HTTPException(
            400,
            "No IFC or legacy design/RAG backbone is available. A canonical-v2 carbon graph is not compatible with the legacy BackboneGraphStore.",
        )
    release_ready = (
        _resolve_canonical_release_dir(project) is not None
        or project.canonical_graph_path is not None
    )
    if project.factor_path is None:
        if release_ready or project.canonical_graph_path is not None:
            project.factor_path = _ensure_stub_factor_workbook(project.upload_dir)
        else:
            raise HTTPException(
                400,
                "No carbon factor workbook (.xlsx) found. Upload carbon factors, "
                "or provide a canonical-v2 release zip together with the IFC model.",
            )

    # LLM client
    project.llm_client = make_llm_client(project.config)

    # Stores — same construction as main()
    graph_store = BackboneGraphStore(project.graph_path)
    factor_store = WorkbookFactorStore(project.factor_path)
    process_store = ProcessEvidenceStore(project.process_docs_path)
    profile_store = ProcessProfileStore(project.profile_path)

    project.runner = DM2CAgenticRAG(
        llm_client=project.llm_client,
        graph_store=graph_store,
        factor_store=factor_store,
        process_store=process_store,
        profile_store=profile_store,
        top_process_k=int(project.config["top_process_k"]),
        factory_grid=project.config["factory_grid"],
        embedding_model=project.config["embedding_model"],
    )
    connect_carbonql_executor(project)


def _carbon_accounts_payload(project: Project) -> Dict[str, Any]:
    """Return the three stakeholder views, or nothing when there is no account."""
    return {view: [dict(row) for row in rows] for view, rows in project.carbon_accounts.items()}


def _account_summary(project: Project, component_count: int) -> Dict[str, Any]:
    """Summarize the carbon account the panels are about to render.

    The component count still describes the uploaded design backbone, because
    that is what the reader indexed. Every carbon figure comes from the product
    view so that the header and the table cannot disagree.
    """
    rows = project.carbon_accounts.get("product") or ()
    material = [row["materialKgCO2e"] for row in rows if row.get("materialKgCO2e") is not None]
    process = [row["processKgCO2e"] for row in rows if row.get("processKgCO2e") is not None]
    return {
        "components": component_count,
        "materialCarbonComplete": len(material),
        "processCarbonComplete": len(process),
        "totalMaterialCarbon_kgCO2e": math.fsum(material),
        "knownProcessCarbon_kgCO2e": math.fsum(process),
        "knownTotalCarbon_kgCO2e": math.fsum(material) + math.fsum(process),
        "processInputGaps": sum(1 for row in rows if row.get("processKgCO2e") is None),
    }


def build_initialized_payload(project: Project) -> Dict[str, Any]:
    """Return project metadata after indexing, before any user question runs RAG."""
    if project.runner is None:
        raise HTTPException(500, "Project runner is not initialized.")

    component_count = len(project.runner.graph.component_contexts())
    # Readiness is about the executor, not about how the graph arrived. A pinned
    # release answers questions just as an uploaded document does, and saying
    # otherwise told the interface that working QA was unavailable.
    query_ready = bool(project.canonical_query_available and project.carbonql)
    payload = {
        "question": "",
        "architecture": (
            "project initialized; canonical-aware carbon QA is ready"
            if query_ready
            else "design backbone initialized; canonical carbon-account QA unavailable"
        ),
        "llmEnabled": project.llm_client is not None,
        "plan": {"groups": []},
        "results": [],
        "carbonAccounts": _carbon_accounts_payload(project),
        "summary": _account_summary(project, component_count),
        "validation": {"flags": [], "overall_confidence": None, "llm_validation": {}},
        "reasoningTrace": [
            {
                "agent": "api",
                "thought": (
                    "Project files and the canonical query executor are ready."
                    if query_ready
                    else "Project design files were indexed, but a canonical-aware query executor is not connected."
                ),
                "action": "project_initialized" if query_ready else "project_initialized_graph_only",
                "observation": {"components": component_count},
                "confidence": 1.0,
                "reflection": (
                    "Ready for a canonical-grounded user question."
                    if query_ready
                    else "Canonical carbon-account querying is not ready."
                ),
            }
        ],
        "inputs": {
            "graph": str(project.graph_path),
            "canonicalGraph": str(project.canonical_graph_path) if project.canonical_graph_path else "",
            "ifcSource": str(project.ifc_path) if project.ifc_path else "",
            "factorWorkbook": str(project.factor_path),
            "processDocs": str(project.process_docs_path),
            "processProfile": str(project.profile_path) if project.profile_path else "",
            "factoryGrid": project.config["factory_grid"],
            "llmProvider": project.config["llm_provider"],
            "model": project.config["model"],
            "embeddingModel": project.config["embedding_model"],
            "materialFactorRows": len(getattr(project.runner.factors, "material_factors", [])),
            "energyFactorRows": len(getattr(project.runner.factors, "energy_factors", [])),
            "processEvidenceRows": len(getattr(project.runner.processes, "docs", [])),
            "backboneBuild": {
                "enabled": bool(project.backbone_build),
                "ontology": str(DEFAULT_ONTOLOGY_PATH),
                "stats": (project.backbone_build or {}).get("stats", {}),
            },
        },
        "graphInfo": _project_graph_info(project),
    }
    project.payload = payload
    return payload


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AskRequest(BaseModel):
    question: str
    selected_component_ids: List[str] = Field(default_factory=list)
    scope: str = "project"
    filters: Dict[str, Any] = Field(default_factory=dict)


class ExperimentCaseRequest(BaseModel):
    id: str = ""
    case_id: str = ""
    question: str
    expected: Dict[str, Any] = Field(default_factory=dict)
    selected_component_ids: List[str] = Field(default_factory=list)
    scope: str = "project"
    filters: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)

    def to_case(self, index: int = 0) -> ExperimentCase:
        data = self.model_dump() if hasattr(self, "model_dump") else self.dict()
        return ExperimentCase.from_dict(data, index=index)


class ExperimentRunRequest(BaseModel):
    experiment_type: str = "qa_benchmark"
    cases: List[ExperimentCaseRequest] = Field(default_factory=list)
    calculation_references: List[Dict[str, Any]] = Field(default_factory=list)
    variants: List[str] = Field(default_factory=list)
    include_default_cases: bool = False
    max_cases: int = 100
    tolerance_abs: float = 1e-6
    tolerance_pct: float = 0.01


def _unique_texts(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out


def _component_id_matches(component_id: str, selected_ids: set[str]) -> bool:
    return bool(component_id) and component_id in selected_ids


def _find_payload_component(payload: Optional[Dict[str, Any]], component_id: str) -> Optional[Dict[str, Any]]:
    if not payload:
        return None
    for row in payload.get("results", []) or []:
        ids = [
            row.get("componentGlobalId", ""),
            row.get("globalId", ""),
            row.get("id", ""),
        ]
        if component_id in {str(value) for value in ids if value}:
            return row
    return None


def _summarize_component_result(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not row:
        return {}

    factor = row.get("selectedMaterialFactor") or {}
    material = row.get("materialCarbon") or {}
    process = row.get("processCarbon") or {}
    drivers = process.get("drivers") or []
    evidence = row.get("selectedProcessSteps") or []

    return {
        "selectedMaterialFactor": {
            "rowId": factor.get("rowId", ""),
            "materialName": factor.get("materialName", ""),
            "factorValue": factor.get("factorValue", None),
            "factorUnit": factor.get("factorUnit", ""),
            "sourceFile": factor.get("sourceFile", ""),
        } if factor else None,
        "materialCarbon": {
            "status": material.get("status", row.get("materialStatus", "")),
            "value_kgco2e": material.get("value_kgco2e", None),
            "formula": material.get("formula", ""),
            "quantity_name": material.get("quantity_name", ""),
            "quantity_value": material.get("quantity_value", None),
            "quantity_unit": material.get("quantity_unit", ""),
            "issue": material.get("issue", row.get("materialError", "")),
        },
        "processCarbon": {
            "status": process.get("status", row.get("processStatus", "")),
            "value_kgco2e": process.get("value_kgco2e", None),
            "issue": process.get("issue", row.get("processError", "")),
            "missing_driver_count": process.get("missing_driver_count", None),
            "drivers": [
                {
                    "process_title": driver.get("process_title", ""),
                    "driver_kind": driver.get("driver_kind", ""),
                    "status": driver.get("status", ""),
                    "required_quantity": driver.get("required_quantity", ""),
                    "preferred_factor_row_id": driver.get("preferred_factor_row_id", ""),
                }
                for driver in drivers[:12]
            ],
        },
        "processEvidence": [
            {
                "docId": item.get("docId", ""),
                "title": item.get("title", ""),
                "rationale": item.get("rationale", ""),
                "matchedTerms": item.get("matchedTerms", []),
            }
            for item in evidence[:6]
        ],
        "gaps": (row.get("gaps") or {}).get("gaps", [])[:12],
        "knownTotalCarbon_kgCO2e": row.get("knownTotalCarbon_kgCO2e", None),
        "totalCarbonStatus": row.get("totalCarbonStatus", ""),
        "confidence": row.get("confidence", None),
    }


def _build_selected_component_context(
    project: Project,
    selected_component_ids: List[str],
    payload: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    selected_ids = set(_unique_texts(selected_component_ids))
    if not selected_ids or project.runner is None:
        return []

    rows: List[Dict[str, Any]] = []
    matched_ids = set()
    for context in project.runner.graph.component_contexts():
        component_node_id = str(context.component.get("id", ""))
        global_id = context.global_id
        if not (
            _component_id_matches(global_id, selected_ids)
            or _component_id_matches(component_node_id, selected_ids)
        ):
            continue

        component_props = props(context.component)
        quantity_basis_props = props(context.quantity_basis) if context.quantity_basis else {}
        process_target_props = props(context.process_target) if context.process_target else {}
        result_row = _find_payload_component(payload or project.payload, global_id) or _find_payload_component(
            payload or project.payload,
            component_node_id,
        )

        rows.append(
            {
                "globalId": global_id,
                "componentNodeId": component_node_id,
                "componentName": context.name,
                "ifcType": context.ifc_type,
                "reference": component_props.get("reference", ""),
                "objectType": component_props.get("objectType", ""),
                "typeObjectName": component_props.get("typeObjectName", ""),
                "materialText": context.material_text,
                "quantityBasis": {
                    "quantityReadiness": quantity_basis_props.get("quantityReadiness", ""),
                    "preferredForMassFactor": quantity_basis_props.get("preferredForMassFactor", []),
                    "preferredForVolumeFactor": quantity_basis_props.get("preferredForVolumeFactor", []),
                    "preferredForAreaFactor": quantity_basis_props.get("preferredForAreaFactor", []),
                    "unitInterpretationStatus": quantity_basis_props.get("unitInterpretationStatus", ""),
                },
                "quantities": [
                    {
                        "quantityName": props(quantity).get("quantityName", ""),
                        "quantityValue": props(quantity).get("quantityValue", None),
                        "quantityUnit": props(quantity).get("quantityUnit", ""),
                    }
                    for quantity in context.quantities[:12]
                ],
                "processAssessmentTarget": {
                    "targetType": process_target_props.get("targetType", ""),
                    "genericRetrievalHints": process_target_props.get("genericRetrievalHints", []),
                    "requiredRuntimeInputs": process_target_props.get("requiredRuntimeInputs", []),
                },
                "carbonAssessmentTargets": [
                    {
                        "materialText": props(target).get("materialText", ""),
                        "factorLookupKeys": props(target).get("factorLookupKeys", []),
                        "requiredRuntimeInputs": props(target).get("requiredRuntimeInputs", []),
                    }
                    for target in context.carbon_targets[:6]
                ],
                "latestAssessment": _summarize_component_result(result_row),
            }
        )
        matched_ids.update({global_id, component_node_id})

    for component_id in selected_ids - matched_ids:
        result_row = _find_payload_component(payload or project.payload, component_id)
        if result_row:
            rows.append(
                {
                    "globalId": result_row.get("componentGlobalId", component_id),
                    "componentNodeId": "",
                    "componentName": result_row.get("componentName", ""),
                    "ifcType": result_row.get("ifcType", ""),
                    "materialText": result_row.get("materialText", ""),
                    "latestAssessment": _summarize_component_result(result_row),
                }
            )

    return rows


def _question_with_selected_context(req: AskRequest, selected_context: List[Dict[str, Any]]) -> str:
    if not selected_context:
        return req.question

    context_json = json.dumps(selected_context, ensure_ascii=False, indent=2)
    return (
        "The user is asking from a component-aware UI.\n"
        "When the user says 'this component', 'selected component', '这个构件', or similar, "
        "interpret it as the selected component context below.\n\n"
        f"Scope: {req.scope or 'selected_component'}\n"
        f"Selected component context:\n{context_json}\n\n"
        f"Original user question:\n{req.question}"
    )


def _experiment_request_to_dict(req: ExperimentRunRequest | Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(req, dict):
        return dict(req)
    return req.model_dump() if hasattr(req, "model_dump") else req.dict()


def _build_experiment_cases(project: Project, data: Dict[str, Any]) -> List[ExperimentCase]:
    payload = project.payload or {"results": []}
    cases = [
        ExperimentCase.from_dict(raw_case, index=index)
        for index, raw_case in enumerate(data.get("cases") or [])
        if str(raw_case.get("question", "")).strip()
    ]

    if data.get("include_default_cases") or not cases:
        cases.extend(generate_default_experiment_cases(payload))

    experiment_type = str(data.get("experiment_type") or "qa_benchmark").strip()
    if experiment_type == "robustness":
        cases = make_robustness_cases(cases)

    max_cases = int(data.get("max_cases") or 100)
    return cases[:max(1, max_cases)]


def _default_experiment_variants(data: Dict[str, Any]) -> List[str]:
    variants = [str(value).strip() for value in data.get("variants") or [] if str(value).strip()]
    if variants:
        return variants
    experiment_type = str(data.get("experiment_type") or "qa_benchmark").strip()
    if experiment_type == "ablation":
        return list(ABLATION_VARIANTS)
    return [DEFAULT_VARIANT]


def _require_experiment_service(project: Project) -> CarbonQLService:
    if project.carbonql is None:
        raise HTTPException(400, "CarbonQL executor not initialized.")
    return project.carbonql


def _run_project_experiment_sync(project: Project, req: ExperimentRunRequest | Dict[str, Any]) -> Dict[str, Any]:
    data = _experiment_request_to_dict(req)
    if project.payload is None and project.runner is not None:
        project.payload = build_initialized_payload(project)

    experiment_type = str(data.get("experiment_type") or "qa_benchmark").strip()
    if experiment_type == "carbon_validation":
        references = data.get("calculation_references") or data.get("references") or []
        if not references:
            raise HTTPException(400, "carbon_validation requires calculation_references.")
        report = evaluate_calculation_validation(
            service=_require_experiment_service(project),
            references=references,
            tolerance_abs=float(data.get("tolerance_abs") or 1e-6),
            tolerance_pct=float(data.get("tolerance_pct") or 0.01),
        )
        report["project_id"] = project.project_id
        report["inputs"] = {
            "reference_count": len(references),
            "tolerance_abs": float(data.get("tolerance_abs") or 1e-6),
            "tolerance_pct": float(data.get("tolerance_pct") or 0.01),
        }
        project.experiment_runs[report["run_id"]] = report
        if project.output_dir:
            out_dir = Path(project.output_dir) / "experiments"
            out_dir.mkdir(parents=True, exist_ok=True)
            AgenticExporter.write_json(out_dir / f"{report['run_id']}.json", report)
        return report

    service = _require_experiment_service(project)
    cases = _build_experiment_cases(project, data)
    variants = _default_experiment_variants(data)
    report = run_experiment_suite(
        service=service,
        assessment_payload=project.payload or {"results": []},
        cases=cases,
        variants=variants,
    )
    report["experiment_type"] = str(data.get("experiment_type") or "qa_benchmark")
    report["project_id"] = project.project_id
    report["inputs"] = {
        "case_count": len(cases),
        "variants": variants,
        "include_default_cases": bool(data.get("include_default_cases")),
        "max_cases": int(data.get("max_cases") or 100),
    }

    project.experiment_runs[report["run_id"]] = report
    if project.output_dir:
        out_dir = Path(project.output_dir) / "experiments"
        out_dir.mkdir(parents=True, exist_ok=True)
        AgenticExporter.write_json(out_dir / f"{report['run_id']}.json", report)
    return report


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/api/projects")
async def create_project(
    design: List[UploadFile] = File(default=[]),
    manufacturing: List[UploadFile] = File(default=[]),
    carbon_factors: List[UploadFile] = File(default=[]),
):
    """
    One-step project creation: upload files → init stores → run initial assessment.

    Frontend sends FormData with three field names matching the file categories.
    Each field can contain multiple files.

    Heavy work (IFC conversion, store init, RAG assessment) runs in a thread
    so the event loop stays responsive for other requests.
    """
    # Cleanup expired projects on each creation
    cleanup_expired_projects()

    pid = uuid.uuid4().hex[:12]
    project = Project(
        project_id=pid,
        created_at=time.time(),
        upload_dir=UPLOAD_ROOT / pid,
        output_dir=OUTPUT_ROOT / pid,
    )
    _projects[pid] = project

    try:
        # 1. Save files (fast, IO-bound)
        manifest = save_uploaded_files(project, design, manufacturing, carbon_factors)
        total_files = sum(len(v) for v in manifest.values())
        if total_files == 0 and _configured_release_dir() is None:
            raise HTTPException(
                400,
                "No files uploaded. Provide an IFC model, or upload a canonical-v2 release zip, "
                "or rely on the server default release together with an IFC model.",
            )

        # 2-4. Heavy work in thread to avoid blocking event loop
        project.status = "initializing"
        payload = await asyncio.to_thread(_create_project_sync, project)

        # 5. Return with project_id so frontend can make follow-up /ask calls
        return {
            "project_id": pid,
            "status": project.status,
            "payload": payload,
        }

    except HTTPException:
        project.status = "error"
        raise
    except Exception as e:
        project.status = "error"
        project.error_message = str(e)
        raise HTTPException(500, f"Project creation failed: {e}\n{traceback.format_exc()}")


def _create_project_sync(project: Project) -> Dict[str, Any]:
    """Synchronous heavy work — runs in a worker thread."""
    resolve_roles(project)
    init_runner(project)
    payload = build_initialized_payload(project)
    project.status = "initialized"
    return payload


@app.get("/api/projects/{project_id}")
async def get_project_detail(project_id: str):
    """
    Return the latest payload for a project.

    The frontend calls this after create if the initial response
    didn't contain results (e.g. if the backend only returned a project_id).
    """
    project = get_project(project_id)
    if project.payload is None:
        # Runner exists but no assessment run yet — run now
        if project.runner:
            try:
                payload = build_initialized_payload(project)
                return {"project_id": project_id, "status": project.status, "payload": payload}
            except Exception as e:
                raise HTTPException(500, f"Assessment failed: {e}")
        return {
            "project_id": project_id,
            "status": "initialized_no_results",
            "payload": {"summary": {}, "results": []},
        }

    return {
        "project_id": project_id,
        "status": project.status,
        "payload": project.payload,
    }


@app.get("/api/projects/{project_id}/ifc-geometry")
async def get_project_ifc_geometry(
    project_id: str,
    start_product: int = Query(default=0, ge=0),
    max_products: int = Query(default=250, ge=1, le=1000),
    max_triangles: int = Query(default=180_000, ge=1_000, le=1_000_000),
):
    """Return one cursor-paged IFC mesh batch for the browser Three.js viewer."""
    project = get_project(project_id)
    if project.ifc_path is None:
        raise HTTPException(404, "No IFC source file is associated with this project.")
    try:
        geometry = await asyncio.to_thread(
            extract_ifc_geometry,
            project.ifc_path,
            start_product=start_product,
            max_products=max_products,
            max_triangles=max_triangles,
        )
        return {
            "project_id": project_id,
            "status": "success",
            "geometry": geometry,
        }
    except Exception as e:
        raise HTTPException(500, f"IFC geometry extraction failed: {e}")


@app.get("/api/projects/{project_id}/ifc-file")
async def get_project_ifc_file(project_id: str):
    """Return the raw IFC source stored for a project."""
    project = get_project(project_id)
    if project.ifc_path is None:
        raise HTTPException(404, "No IFC source file is associated with this project.")

    path = Path(project.ifc_path).resolve()
    if not path.is_file():
        raise HTTPException(404, "No IFC source file is associated with this project.")

    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=path.name,
        headers={"Cache-Control": "private, max-age=3600"},
    )


@app.get("/api/projects/{project_id}/components/{global_id}/subgraph")
async def get_project_component_subgraph(
    project_id: str,
    global_id: str,
    max_nodes: int = Query(default=80, ge=10, le=200),
    expand_shared: bool = Query(default=False),
):
    """Return one strictly validated canonical-v2 component product closure."""
    project = get_project(project_id)
    # A pinned release is a validated carbon graph too, and it is the one the
    # answers came from, so the closure must be readable from it rather than
    # only from an uploaded document.
    if project.canonical_graph_path is not None:
        extract = partial(
            component_subgraph_from_graph_path, project.canonical_graph_path
        )
    elif project.carbonql is not None:
        extract = partial(component_subgraph_from_context, project.carbonql.context)
    else:
        raise HTTPException(
            409,
            "Canonical-v2 carbon graph unavailable: no validated carbon graph is associated with this project; an IFC or legacy JSON, when present, is only a design backbone for the legacy RAG reader.",
        )
    try:
        subgraph = await asyncio.to_thread(
            extract,
            global_id,
            max_nodes,
            expand_shared,
        )
    except ComponentNodeNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ComponentSelectorInvalid as exc:
        raise HTTPException(400, str(exc)) from exc
    except ComponentSelectorAmbiguous as exc:
        raise HTTPException(409, str(exc)) from exc
    except CanonicalSchemaError as exc:
        raise HTTPException(422, f"Invalid canonical-v2 graph: {exc}") from exc
    return {
        "project_id": project_id,
        "status": "success",
        "subgraph": subgraph,
    }


@app.post("/api/projects/{project_id}/ask")
async def ask_question(project_id: str, req: AskRequest):
    """
    Answer one carbon question from the canonical-v2 account via CarbonQL.

    The language model only compiles the question, together with any components
    selected in the model, into one typed program; the deterministic executor
    produces every number. A question the account cannot answer returns its
    status and no value rather than a fabricated total.
    """
    project = get_project(project_id)
    _require_canonical_query(project)
    service = project.carbonql
    if service is None:
        raise HTTPException(400, "CarbonQL executor not initialized.")

    try:
        start = time.time()
        selected_ids = _unique_texts(req.selected_component_ids)
        selected_context = _build_selected_component_context(
            project, selected_ids, project.payload
        )
        answer = await asyncio.to_thread(service.answer, req.question, selected_ids)
        elapsed = round((time.time() - start) * 1000, 1)

        previous = project.payload or {}
        payload: Dict[str, Any] = {
            **answer_payload(answer),
            "llmEnabled": True,
            "plan": {"groups": []},
            "summary": dict(previous.get("summary") or {}),
            # The account does not change because a question was asked, so the
            # panels beside the answer must keep showing the same figures.
            "carbonAccounts": _carbon_accounts_payload(project),
            "requestContext": {
                "scope": req.scope or ("selected_component" if selected_ids else "project"),
                "selectedComponentIds": selected_ids,
                "selectedComponents": selected_context,
                "filters": req.filters,
            },
            "inputs": {
                **dict(previous.get("inputs") or {}),
                "elapsedMs": elapsed,
                "graph": str(project.graph_path) if project.graph_path else "",
                "canonicalGraph": str(project.canonical_graph_path or ""),
                "carbonRelease": str(project.canonical_release_dir or ""),
                "ifcSource": str(project.ifc_path) if project.ifc_path else "",
                "llmProvider": project.config["llm_provider"],
                "model": project.config["model"],
                "carbonqlVariant": answer.variant,
                "backboneBuild": {
                    "enabled": bool(project.backbone_build),
                    "ontology": str(DEFAULT_ONTOLOGY_PATH),
                    "stats": (project.backbone_build or {}).get("stats", {}),
                },
            },
        }
        payload["graphInfo"] = _project_graph_info(project)
        project.payload = payload
        project.status = "analyzed"

        # Save updated results
        project.output_dir.mkdir(parents=True, exist_ok=True)
        AgenticExporter.write_json(project.output_dir / "results.json", payload)

        return {
            "project_id": project_id,
            "status": "success",
            "elapsed_ms": elapsed,
            "payload": payload,
        }

    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {e}\n{traceback.format_exc()}")


@app.post("/api/projects/{project_id}/experiments")
async def run_project_experiment(project_id: str, req: ExperimentRunRequest):
    """
    Run M3 QA experiments against an initialized project.

    experiment_type:
      - qa_benchmark: run a supplied or default question set.
      - ablation: default variants are full, graph_only, no_m33_llm.
      - robustness: appends unknown-target and missing-input cases.
      - efficiency: records per-question latency with the selected variants.
      - carbon_validation: compares system carbon results with manual references.
    """
    project = get_project(project_id)
    _require_canonical_query(project)
    try:
        report = await asyncio.to_thread(_run_project_experiment_sync, project, req)
        return {
            "project_id": project_id,
            "status": "success",
            "run_id": report["run_id"],
            "report": report,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Experiment failed: {e}\n{traceback.format_exc()}")


@app.get("/api/projects/{project_id}/experiments")
async def list_project_experiments(project_id: str):
    """List experiment runs stored for a project."""
    project = get_project(project_id)
    return {
        "project_id": project_id,
        "experiments": [
            {
                "run_id": report.get("run_id"),
                "experiment_type": report.get("experiment_type"),
                "case_count": report.get("case_count"),
                "elapsed_ms": report.get("elapsed_ms"),
                "variants": {
                    name: variant.get("metrics", {})
                    for name, variant in (report.get("variants") or {}).items()
                },
            }
            for report in project.experiment_runs.values()
        ],
    }


@app.get("/api/projects/{project_id}/experiments/{run_id}")
async def get_project_experiment(project_id: str, run_id: str):
    """Return a stored experiment report."""
    project = get_project(project_id)
    report = project.experiment_runs.get(run_id)
    if report is None:
        raise HTTPException(404, f"Experiment run {run_id} not found")
    return {
        "project_id": project_id,
        "run_id": run_id,
        "report": report,
    }


@app.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """Remove project data from memory and disk."""
    project = _projects.pop(project_id, None)
    if project:
        if project.upload_dir.exists():
            shutil.rmtree(project.upload_dir)
        if project.output_dir.exists():
            shutil.rmtree(project.output_dir)
    return {"deleted": project_id}


@app.get("/api/projects")
async def list_projects():
    """List all active projects."""
    return {
        "projects": [
            {
                "project_id": p.project_id,
                "created_at": p.created_at,
                "status": p.status,
                "files": {k: len(v) for k, v in p.file_manifest.items()},
                "has_results": p.payload is not None,
                "experiment_runs": len(p.experiment_runs),
                "factory_grid": p.config["factory_grid"],
                "error": p.error_message or None,
            }
            for p in _projects.values()
        ]
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    default_release = _configured_release_dir()
    return {
        "status": "ok",
        "version": "0.5.0",
        "active_projects": len(_projects),
        "project_expiry_hours": PROJECT_EXPIRY_SECONDS / 3600,
        "defaultCarbonqlRelease": str(default_release or ""),
        "defaultCarbonqlReleaseReady": default_release is not None,
    }


@app.on_event("startup")
async def start_cleanup_loop():
    """Periodically clean up expired projects."""
    async def _loop():
        while True:
            await asyncio.sleep(3600)  # check every hour
            removed = cleanup_expired_projects()
            if removed:
                print(f"Cleaned up {removed} expired project(s)")
    asyncio.create_task(_loop())


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    startup_config = default_project_config()

    print("MC²QA API v0.3.0")
    print()
    print("Endpoints (matches DM2CApp_connected.jsx):")
    print("  POST   /api/projects              Upload + init + assess")
    print("  GET    /api/projects/:id           Get project payload")
    print("  POST   /api/projects/:id/ask       Ask a question")
    print("  POST   /api/projects/:id/experiments  Run a benchmark or ablation")
    print("  DELETE /api/projects/:id           Remove project")
    print("  GET    /api/projects               List all projects")
    print()
    print(f"LLM provider: {startup_config['llm_provider']}")
    print(f"LLM model: {startup_config['model']}")
    print(f"Embedding model: {startup_config['embedding_model']}")
    print(f"CarbonQL release: {os.environ.get(CARBONQL_RELEASE_ENV) or '(from upload)'}")
    print(f"CarbonQL variant: {CARBONQL_VARIANT}")
    print("Listening on http://localhost:8000")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000)
