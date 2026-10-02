"""
Agentic RAG Pipeline over Three-Domain Knowledge Graph
=======================================================
Architecture:
  User Question → Schema Summary + Question → LLM → SPARQL
  → rdflib in-memory execution → Result Table → LLM → Answer

No external database required. All queries run in-memory via rdflib.
Works with any OpenAI-compatible API (Anthropic, OpenAI, local models).
"""

import json, re, time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from rdflib import Graph, Namespace
from rdflib.plugins.sparql import prepareQuery

# ── Namespaces ──────────────────────────────────────────────
BIM    = Namespace("http://example.org/bim#")
MC     = Namespace("http://example.org/mc#")
CARBON = Namespace("http://example.org/carbon#")
ONTO   = Namespace("http://example.org/onto#")
INST   = Namespace("http://example.org/inst#")

PREFIXES = """
PREFIX rdf:    <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs:   <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:    <http://www.w3.org/2002/07/owl#>
PREFIX xsd:    <http://www.w3.org/2001/XMLSchema#>
PREFIX bim:    <http://example.org/bim#>
PREFIX mc:     <http://example.org/mc#>
PREFIX carbon: <http://example.org/carbon#>
PREFIX onto:   <http://example.org/onto#>
PREFIX inst:   <http://example.org/inst#>
"""


# ═══════════════════════════════════════════════════════════════
# 1. IN-MEMORY RDF STORE
# ═══════════════════════════════════════════════════════════════

class InMemoryKGStore:
    """Load and query the three-domain KG entirely in Python memory."""

    def __init__(self, ttl_paths: List[str]):
        self.g = Graph()
        for p, ns in [("bim",BIM),("mc",MC),("carbon",CARBON),("onto",ONTO),("inst",INST)]:
            self.g.bind(p, ns)
        for path in ttl_paths:
            print(f"  Loading {path}...")
            self.g.parse(str(path), format="turtle")
        print(f"  Total triples: {len(self.g)}")

    def query(self, sparql: str) -> Tuple[bool, object, str]:
        """Execute SPARQL and return (success, results, error_msg)."""
        full_query = PREFIXES + "\n" + sparql
        try:
            start = time.time()
            results = self.g.query(full_query)
            elapsed = (time.time() - start) * 1000
            return True, results, f"OK ({elapsed:.1f}ms, {len(results)} rows)"
        except Exception as e:
            return False, None, str(e)

    def get_schema_summary(self) -> str:
        """Auto-generate a schema summary by querying the loaded graph."""
        lines = ["# Knowledge Graph Schema Summary\n"]

        # Classes
        lines.append("## Classes (with instance counts)")
        class_query = """
        SELECT ?class (COUNT(?inst) AS ?count) WHERE {
            ?inst a ?class .
            FILTER(STRSTARTS(STR(?class), "http://example.org/"))
        } GROUP BY ?class ORDER BY DESC(?count)
        """
        ok, res, _ = self.query(class_query)
        if ok:
            for row in res:
                cls = str(row[0]).split("#")[-1]
                cnt = int(row[1])
                if cnt > 0:
                    lines.append(f"  - {cls}: {cnt} instances")

        # Object Properties (with usage counts)
        lines.append("\n## Object Properties (relationships)")
        prop_query = """
        SELECT ?p (COUNT(*) AS ?count) WHERE {
            ?s ?p ?o .
            FILTER(STRSTARTS(STR(?p), "http://example.org/"))
            FILTER(isIRI(?o))
        } GROUP BY ?p ORDER BY DESC(?count) LIMIT 30
        """
        ok, res, _ = self.query(prop_query)
        if ok:
            for row in res:
                prop = str(row[0]).replace("http://example.org/bim#","bim:").replace(
                    "http://example.org/mc#","mc:").replace(
                    "http://example.org/carbon#","carbon:").replace(
                    "http://example.org/onto#","onto:")
                lines.append(f"  - {prop} ({int(row[1])} uses)")

        # Datatype Properties (sample)
        lines.append("\n## Datatype Properties (attributes)")
        dp_query = """
        SELECT DISTINCT ?p WHERE {
            ?s ?p ?o .
            FILTER(STRSTARTS(STR(?p), "http://example.org/"))
            FILTER(isLiteral(?o))
        } LIMIT 30
        """
        ok, res, _ = self.query(dp_query)
        if ok:
            for row in res:
                prop = str(row[0]).replace("http://example.org/bim#","bim:").replace(
                    "http://example.org/mc#","mc:").replace(
                    "http://example.org/carbon#","carbon:").replace(
                    "http://example.org/onto#","onto:")
                lines.append(f"  - {prop}")

        # Sample instances
        lines.append("\n## Sample Instances (first 3 per major class)")
        for cls_prefix in ["bim:IfcWall", "bim:IfcBeam", "bim:IfcMaterial",
                           "mc:ModuleProduction", "carbon:CarbonEmission"]:
            ns, local = cls_prefix.split(":")
            ns_uri = {"bim":BIM,"mc":MC,"carbon":CARBON}[ns]
            sample_q = f"""
            SELECT ?inst ?name WHERE {{
                ?inst a <{ns_uri[local]}> .
                OPTIONAL {{ ?inst bim:name ?name }}
                OPTIONAL {{ ?inst mc:stageName ?name }}
                OPTIONAL {{ ?inst rdfs:label ?name }}
            }} LIMIT 3
            """
            ok, res, _ = self.query(sample_q)
            if ok and len(res) > 0:
                lines.append(f"\n  {cls_prefix}:")
                for row in res:
                    uri = str(row[0]).split("#")[-1]
                    name = str(row[1]) if row[1] else ""
                    lines.append(f"    - {uri}: {name}")

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 2. TEXT-TO-SPARQL AGENT
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are a SPARQL query generator for a three-domain knowledge graph about modular construction.

The three domains are:
1. BIM Design Domain (prefix bim:) — building elements, materials, quantities
2. MC Production Domain (prefix mc:) — production stages, manufacturing activities, resources
3. Carbon Emission Domain (prefix carbon:) — emissions, consumption drivers, emission factors

Cross-domain relationships use prefix onto: (e.g., onto:requiresStage, onto:producesEmission, onto:consumesMaterial)

{schema_summary}

RULES:
1. Always use the prefixes defined above. Never invent predicates.
2. Use specific class names when the user mentions a specific element type.
3. For aggregation queries, use GROUP BY and aggregation functions.
4. Return only the SPARQL query, wrapped in ```sparql ... ``` code blocks.
5. Prefer SELECT queries. Use OPTIONAL for properties that may be missing.
6. When asked about carbon/emission, link through onto:producesEmission.
7. When asked about production/manufacturing, link through onto:requiresStage or mc:hasProductionStage.

FEW-SHOT EXAMPLES:

Question: "How many walls are there?"
```sparql
SELECT (COUNT(?w) AS ?count) WHERE {{ ?w a bim:IfcWall }}
```

Question: "What materials are used in the beams?"
```sparql
SELECT DISTINCT ?beamName ?matName WHERE {{
    ?beam a bim:IfcBeam .
    ?beam bim:name ?beamName .
    ?beam bim:hasMaterial ?mat .
    ?mat bim:name ?matName .
}}
```

Question: "What is the total carbon emission of all walls?"
```sparql
SELECT (SUM(?ev) AS ?totalEmission) WHERE {{
    ?wall a bim:IfcWall .
    ?wall onto:producesEmission ?ce .
    ?ce carbon:emissionValue ?ev .
}}
```

Question: "Which production stages does beam FK1 require?"
```sparql
SELECT ?beamName ?stageName ?stageType WHERE {{
    ?beam a bim:IfcBeam .
    ?beam bim:name ?beamName .
    FILTER(CONTAINS(?beamName, "FK1"))
    ?beam onto:requiresStage ?stage .
    ?stage mc:stageName ?stageName .
    ?stage a ?stageType .
    FILTER(?stageType != mc:ProductionStage)
}}
```
"""

ANSWER_PROMPT = """Based on the SPARQL query results below, answer the user's question in natural language.

User Question: {question}

SPARQL Query Executed:
```sparql
{sparql}
```

Results:
{results_table}

RULES:
1. Cite specific values from the results (element names, numbers, material names).
2. If results are empty, say the information was not found in the knowledge graph.
3. Be concise and factual. Do not speculate beyond the data.
4. If the results include numeric values, present them with appropriate units.
"""


class Text2SPARQLAgent:
    """ReAct-style agent: Question → SPARQL → Execute → Answer."""

    def __init__(self, kg_store: InMemoryKGStore, llm_fn, max_retries=3):
        """
        Args:
            kg_store: InMemoryKGStore instance
            llm_fn: function(system_prompt, user_message) -> str
                     Any LLM API wrapper that returns text.
            max_retries: max SPARQL generation attempts
        """
        self.store = kg_store
        self.llm = llm_fn
        self.max_retries = max_retries
        self.schema_summary = kg_store.get_schema_summary()
        self.system_prompt = SYSTEM_PROMPT.format(schema_summary=self.schema_summary)

    def _extract_sparql(self, llm_output: str) -> str:
        """Extract SPARQL from LLM response (inside ```sparql ... ```)."""
        match = re.search(r"```sparql\s*(.*?)\s*```", llm_output, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Fallback: try to find SELECT/ASK/CONSTRUCT
        match = re.search(r"(SELECT|ASK|CONSTRUCT|DESCRIBE).*", llm_output, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(0).strip()
        return llm_output.strip()

    def _results_to_table(self, results) -> str:
        """Convert SPARQL results to markdown table."""
        if results is None:
            return "(no results)"
        rows = list(results)
        if not rows:
            return "(empty result set)"
        vars_ = [str(v) for v in results.vars]
        lines = ["| " + " | ".join(vars_) + " |"]
        lines.append("| " + " | ".join(["---"] * len(vars_)) + " |")
        for row in rows[:50]:  # cap at 50 rows
            cells = []
            for v in row:
                s = str(v) if v is not None else ""
                # Shorten URIs
                s = s.replace("http://example.org/inst#", "inst:")
                s = s.replace("http://example.org/bim#", "bim:")
                s = s.replace("http://example.org/mc#", "mc:")
                s = s.replace("http://example.org/carbon#", "carbon:")
                cells.append(s)
            lines.append("| " + " | ".join(cells) + " |")
        if len(rows) > 50:
            lines.append(f"... ({len(rows)} total rows, showing first 50)")
        return "\n".join(lines)

    def ask(self, question: str) -> Dict:
        """
        Full agentic RAG turn.
        Returns dict with keys: question, sparql, results_table, answer, attempts, success
        """
        trace = {"question": question, "attempts": [], "success": False}

        # Step 1: Generate SPARQL (with retries)
        sparql = None
        results = None
        results_table = ""
        user_msg = question

        for attempt in range(1, self.max_retries + 1):
            llm_output = self.llm(self.system_prompt, user_msg)
            sparql = self._extract_sparql(llm_output)
            ok, results, msg = self.store.query(sparql)

            trace["attempts"].append({
                "attempt": attempt,
                "sparql": sparql,
                "success": ok,
                "message": msg,
            })

            if ok:
                results_table = self._results_to_table(results)
                if "(empty result set)" not in results_table:
                    trace["success"] = True
                    break
                else:
                    # Empty results: ask LLM to retry with broader query
                    user_msg = (
                        f"The previous query returned no results:\n```sparql\n{sparql}\n```\n"
                        f"Original question: {question}\n"
                        f"Try a broader or corrected query."
                    )
            else:
                # Syntax/execution error: ask LLM to fix
                user_msg = (
                    f"The previous query failed with error:\n{msg}\n"
                    f"Query was:\n```sparql\n{sparql}\n```\n"
                    f"Original question: {question}\n"
                    f"Fix the SPARQL query."
                )

        trace["sparql"] = sparql
        trace["results_table"] = results_table

        # Step 2: Generate natural language answer
        answer_input = ANSWER_PROMPT.format(
            question=question,
            sparql=sparql or "(no query generated)",
            results_table=results_table or "(no results)",
        )
        answer = self.llm(
            "You are a helpful construction engineering assistant. Answer based on the query results.",
            answer_input,
        )
        trace["answer"] = answer

        return trace


# ═══════════════════════════════════════════════════════════════
# 3. LLM WRAPPER (Anthropic Claude via API)
# ═══════════════════════════════════════════════════════════════

def make_anthropic_llm(api_key: str = None, model: str = "claude-sonnet-4-20250514"):
    """Create LLM function using Anthropic API."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

        def llm_fn(system_prompt: str, user_message: str) -> str:
            response = client.messages.create(
                model=model,
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text
        return llm_fn
    except ImportError:
        print("Warning: anthropic package not installed. Using mock LLM.")
        return None


def make_openai_llm(api_key: str = None, model: str = "gpt-4o"):
    """Create LLM function using OpenAI-compatible API."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key) if api_key else OpenAI()

        def llm_fn(system_prompt: str, user_message: str) -> str:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                max_tokens=2000,
            )
            return response.choices[0].message.content
        return llm_fn
    except ImportError:
        return None


# ═══════════════════════════════════════════════════════════════
# 4. DEMO MODE (no LLM needed — shows pipeline with preset queries)
# ═══════════════════════════════════════════════════════════════

DEMO_QUERIES = [
    {
        "question": "How many building elements are there in each category?",
        "sparql": """SELECT ?type (COUNT(?e) AS ?count) WHERE {
    ?e a ?type .
    ?type rdfs:subClassOf bim:IfcBuildingElement .
} GROUP BY ?type ORDER BY DESC(?count)"""
    },
    {
        "question": "What materials are used in the walls and what is their density?",
        "sparql": """SELECT DISTINCT ?wallName ?matName ?density WHERE {
    ?wall a bim:IfcWall .
    ?wall bim:name ?wallName .
    ?wall bim:hasMaterial ?mat .
    ?mat bim:name ?matName .
    ?mat bim:density ?density .
} ORDER BY ?wallName"""
    },
    {
        "question": "What is the total embodied carbon (A1-A3) for each element type?",
        "sparql": """SELECT ?elemType (SUM(?ev) AS ?totalCO2) (COUNT(?ce) AS ?nRecords) WHERE {
    ?elem a ?elemType .
    ?elemType rdfs:subClassOf bim:IfcBuildingElement .
    ?elem onto:producesEmission ?ce .
    ?ce carbon:emissionValue ?ev .
    ?ce carbon:lifeCycleStage "A1-A3" .
} GROUP BY ?elemType ORDER BY DESC(?totalCO2)"""
    },
    {
        "question": "Which production stages are required for columns, and in what order?",
        "sparql": """SELECT ?colName ?stageName ?stageType WHERE {
    ?col a bim:IfcColumn .
    ?col bim:name ?colName .
    ?col onto:requiresStage ?stage .
    ?stage mc:stageName ?stageName .
    ?stage a ?stageType .
    FILTER(?stageType != mc:ProductionStage)
} ORDER BY ?colName ?stageName LIMIT 20"""
    },
    {
        "question": "What is the emission factor for each material?",
        "sparql": """SELECT ?matName ?factor ?unit ?source WHERE {
    ?mat a bim:IfcMaterial .
    ?mat bim:name ?matName .
    ?mat carbon:hasEmissionFactor ?ef .
    ?ef carbon:factorValue ?factor .
    ?ef carbon:factorUnit ?unit .
    ?ef carbon:factorSource ?source .
}"""
    },
    {
        "question": "Which manufacturing activities consume steel material?",
        "sparql": """SELECT DISTINCT ?actName ?matName WHERE {
    ?act a mc:ManufacturingActivity .
    ?act mc:activityName ?actName .
    ?act onto:consumesMaterial ?mat .
    ?mat bim:name ?matName .
    FILTER(CONTAINS(?matName, "钢"))
} LIMIT 20"""
    },
    {
        "question": "Cross-domain query: for each wall, show its material, production template, and carbon emission",
        "sparql": """SELECT ?wallName ?matName ?template ?emission WHERE {
    ?wall a bim:IfcWall .
    ?wall bim:name ?wallName .
    ?wall bim:hasMaterial ?mat .
    ?mat bim:name ?matName .
    OPTIONAL {
        ?mp onto:hasInput ?wall .
        ?mp a mc:ModuleProduction .
        BIND("WallPanelProduction" AS ?template)
    }
    OPTIONAL {
        ?wall onto:producesEmission ?ce .
        ?ce carbon:emissionValue ?emission .
    }
} ORDER BY ?wallName LIMIT 15"""
    },
]


def run_demo(store: InMemoryKGStore):
    """Run preset SPARQL queries to demonstrate the pipeline without LLM."""
    print("\n" + "=" * 70)
    print("DEMO MODE — Executing preset SPARQL queries")
    print("(No LLM required. Shows retrieval step of the agentic pipeline.)")
    print("=" * 70)

    for i, dq in enumerate(DEMO_QUERIES, 1):
        print(f"\n{'─' * 60}")
        print(f"Q{i}: {dq['question']}")
        print(f"{'─' * 60}")
        print(f"SPARQL:\n{dq['sparql']}\n")

        ok, results, msg = store.query(dq["sparql"])
        if ok:
            rows = list(results)
            if rows:
                vars_ = [str(v) for v in results.vars]
                # Print as table
                header = " | ".join(f"{v:30s}" for v in vars_)
                print(header)
                print("-" * len(header))
                for row in rows[:15]:
                    cells = []
                    for v in row:
                        s = str(v).replace("http://example.org/bim#","bim:").replace(
                            "http://example.org/mc#","mc:").replace(
                            "http://example.org/inst#","inst:")[:30] if v else ""
                        cells.append(f"{s:30s}")
                    print(" | ".join(cells))
                if len(rows) > 15:
                    print(f"  ... ({len(rows)} rows total)")
            else:
                print("  (no results)")
            print(f"  [{msg}]")
        else:
            print(f"  ERROR: {msg}")


# ═══════════════════════════════════════════════════════════════
# 5. CLI
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Agentic RAG over Three-Domain KG")
    ap.add_argument("--ontology", default="ontology_v3.ttl", help="TBox TTL")
    ap.add_argument("--kg", default="v3_output/kg_v3.ttl", help="ABox TTL")
    ap.add_argument("--mode", choices=["demo", "interactive", "schema"], default="demo",
                    help="demo=preset queries, interactive=LLM-powered Q&A, schema=print schema summary")
    ap.add_argument("--llm", choices=["anthropic", "openai", "none"], default="none")
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()

    print("Loading Knowledge Graph...")
    ttl_files = [args.ontology, args.kg]
    ttl_files = [f for f in ttl_files if Path(f).exists()]
    store = InMemoryKGStore(ttl_files)

    if args.mode == "schema":
        print("\n" + store.get_schema_summary())
        return

    if args.mode == "demo":
        run_demo(store)
        return

    # Interactive mode with LLM
    llm_fn = None
    if args.llm == "anthropic":
        llm_fn = make_anthropic_llm(args.api_key)
    elif args.llm == "openai":
        llm_fn = make_openai_llm(args.api_key)

    if llm_fn is None:
        print("No LLM configured. Falling back to demo mode.")
        run_demo(store)
        return

    agent = Text2SPARQLAgent(store, llm_fn)
    print("\nAgentic RAG ready. Type your question (or 'quit' to exit).\n")

    while True:
        question = input("You: ").strip()
        if question.lower() in ("quit", "exit", "q"):
            break
        if not question:
            continue

        print("\nThinking...")
        result = agent.ask(question)

        print(f"\nSPARQL Generated:\n{result['sparql']}\n")
        print(f"Results:\n{result['results_table']}\n")
        print(f"Answer: {result['answer']}\n")

        # Show retry trace if any
        if len(result["attempts"]) > 1:
            print(f"(Took {len(result['attempts'])} attempts)")


if __name__ == "__main__":
    main()
