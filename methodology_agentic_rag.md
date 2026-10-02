# 3.X Agentic RAG over the Three-Domain Knowledge Graph

## 3.X.1 Architectural overview

After the three-domain knowledge graph is constructed as an RDF dataset (Section 3.X-1), the next challenge is to enable natural language querying over this graph to support early design decision-making. This section presents an agentic retrieval-augmented generation (RAG) pipeline that translates user questions into structured SPARQL queries, retrieves relevant subgraphs from the knowledge graph, and synthesises natural language answers grounded in the retrieved evidence.

Unlike conventional RAG systems that rely on unstructured text chunks and vector similarity search, the proposed approach operates directly on the formal graph structure: the retrieval step is a SPARQL query that traverses typed nodes and edges across the three ontology domains, ensuring that the retrieved context is semantically precise and traceable to specific IFC entities, production stages, or carbon emission records. This design is referred to as *Graph RAG* or *structured RAG* in the emerging literature on knowledge-grounded language models.

The pipeline consists of four components:

1. **In-memory RDF store**: The knowledge graph (TBox + ABox) is loaded into a Python rdflib Graph object at initialisation. No external database service (GraphDB, Fuseki, Neo4j) is required. For the case study model (9,316 triples), the in-memory store loads in under 1 second and supports sub-second SPARQL execution.

2. **Schema summariser**: A compact textual summary of the ontology schema (class hierarchy, object properties, datatype properties, and sample instance patterns) is pre-generated and included in every LLM prompt. This gives the language model the structural knowledge needed to formulate valid SPARQL queries without exceeding context window limits.

3. **Text-to-SPARQL agent**: Given a user question and the schema summary, a large language model generates a SPARQL SELECT query targeting the relevant domain(s). The agent operates in a ReAct-style loop: if the initial query returns no results or produces a SPARQL syntax error, the agent receives the error message and reformulates the query (up to a configurable maximum of three retry attempts).

4. **Answer synthesiser**: The SPARQL results (a set of variable bindings) are serialised as a Markdown table and fed back to the language model alongside the original question. The model generates a natural language answer with explicit references to the retrieved entities (e.g., element GlobalIds, material names, emission values).

## 3.X.2 Justification for in-memory RDF over external databases

The selection of rdflib as the runtime query engine, rather than a dedicated triplestore (GraphDB, Blazegraph) or a property graph database (Neo4j), is motivated by three considerations:

First, **deployment simplicity**. The entire pipeline runs within a single Python process with no external service dependencies. This is essential for reproducibility: any researcher can clone the repository, install dependencies via pip, and execute the pipeline without configuring database servers, connection strings, or authentication.

Second, **scale appropriateness**. The knowledge graph for a single modular construction project contains approximately 10,000 triples. At this scale, rdflib's in-memory SPARQL engine provides query response times comparable to dedicated triplestores (both in the low-millisecond range), while eliminating the operational overhead of database administration.

Third, **ontology-native querying**. Because the TBox (ontology_v3.ttl) and ABox (kg_v3.ttl) are loaded into the same rdflib Graph, SPARQL queries can leverage RDFS entailment natively. For example, a query for all instances of `bim:IfcBuildingElement` automatically returns instances of its subclasses (IfcBeam, IfcWall, IfcColumn, IfcSlab) without requiring explicit UNION clauses. This reduces the complexity of generated SPARQL and improves LLM generation accuracy.

## 3.X.3 Text-to-SPARQL agent design

The Text-to-SPARQL agent follows the ReAct (Reason + Act) paradigm. At each turn, the agent:

1. **Reasons** about the user question by identifying which ontology domain(s) are involved (e.g., "what materials are used in the beams?" involves the BIM domain; "which production stage has the highest carbon?" involves all three domains).

2. **Acts** by generating a SPARQL query and executing it against the in-memory store.

3. **Observes** the result: if the query succeeds, the bindings are passed to the answer synthesiser. If it fails (syntax error, empty result, timeout), the error is fed back to the agent for self-correction.

The system prompt provided to the language model includes:
- The complete schema summary (class names, property names, sample URIs)
- The list of namespace prefixes used in the knowledge graph
- Three few-shot examples of (question, SPARQL, expected result shape) triples
- Explicit instructions to use only predicates defined in the schema and to prefer specific class names (e.g., `bim:IfcBeam`) over generic parent classes when the question mentions a specific element type

This design ensures that the retrieval step is a formal, auditable SPARQL query — not an opaque vector similarity search — which is critical for engineering applications where traceability and precision are more important than recall.

## 3.X.4 Answer synthesis and provenance

The final answer is generated by the language model based on:
- The original user question
- The SPARQL query that was executed (for transparency)
- The result bindings as a structured table

The model is instructed to cite specific entity identifiers (e.g., GlobalId values, material names, emission values) from the results and to explicitly state when the query returned no results. This provenance chain — from natural language question to SPARQL query to graph traversal to structured result to cited answer — constitutes the "reasoning trace" of the agentic system and can be presented in the case study to demonstrate the system's transparency and reliability.
