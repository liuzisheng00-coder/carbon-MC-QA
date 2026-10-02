from pathlib import Path

import pytest
from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS
from rdflib.namespace import SH, XSD

from dm2c_m23_canonical import (
    APPLICATION_CLASSES,
    CanonicalLPGGraph,
    OPTIONAL_CONTEXT_PREDICATES,
    PRINCIPAL_EDGE_TRIPLES,
    PRINCIPAL_PREDICATES,
)


BIM = Namespace("https://w3id.org/dm2c/bim#")
MC = Namespace("https://w3id.org/dm2c/manufacturing#")
CARBON = Namespace("https://w3id.org/dm2c/carbon#")
ONTO = Namespace("https://w3id.org/dm2c/integration#")
SHAPES = Namespace("https://w3id.org/dm2c/shapes#")
IFCOWL = Namespace("https://standards.buildingsmart.org/IFC/DEV/IFC4_3/OWL#")


APPLICATION_CLASS_URIS = {
    "ProductionBatch": MC.ProductionBatch,
    "ModularUnit": BIM.ModularUnit,
    "BuildingComponent": BIM.BuildingComponent,
    "ComponentType": BIM.ComponentType,
    "IfcMaterial": BIM.IfcMaterial,
    "DesignQuantity": BIM.DesignQuantity,
    "ManufacturingProcessTemplate": MC.ManufacturingProcessTemplate,
    "ProductionStage": MC.ProductionStage,
    "ManufacturingActivity": MC.ManufacturingActivity,
    "ManufacturingResource": MC.ManufacturingResource,
    "MaterialConsumption": CARBON.MaterialConsumption,
    "EnergyConsumption": CARBON.EnergyConsumption,
    "ConsumptionQuantity": CARBON.ConsumptionQuantity,
    "EmissionFactor": CARBON.EmissionFactor,
    "EnergyCarrier": CARBON.EnergyCarrier,
    "CarbonEmission": CARBON.CarbonEmission,
}

ABSTRACT_PARENT_URIS = {
    "ProductObject": BIM.ProductObject,
    "ProductType": BIM.ProductType,
    "ProcessElement": MC.ProcessElement,
    "ConsumptionDriver": CARBON.ConsumptionDriver,
}

# Namespace rule: structural properties carry the prefix of the sub-ontology
# that defines them; properties that attach a carbon record to a design or
# manufacturing entity carry integr:.
PROPERTY_NAMESPACES = {
    # bim: design structure
    "containsComponent": BIM,
    "hasComponentType": BIM,
    "hasMaterial": BIM,
    "hasDesignQuantity": BIM,
    # mc: manufacturing structure and product-to-process entry
    "produces": MC,
    "hasProcessTemplate": MC,
    "hasStage": MC,
    "hasActivity": MC,
    "usesResource": MC,
    "manufacturedBy": MC,
    "directlyPrecedes": MC,
    # carbon: calculation chain
    "hasQuantity": CARBON,
    "hasFactor": CARBON,
    "ofCarrier": CARBON,
    "hasCarbonDriver": CARBON,
    # integr: carbon record -> design / manufacturing entity
    "recordedForObject": ONTO,
    "ofMaterial": ONTO,
    "derivedFrom": ONTO,
    "associatedWithProcess": ONTO,
    "recordedForResource": ONTO,
}

INTEGRATION_PREDICATES = frozenset(
    {"recordedForObject", "ofMaterial", "derivedFrom", "associatedWithProcess", "recordedForResource"}
)


def property_uri(predicate: str):
    return PROPERTY_NAMESPACES[predicate][predicate]


PRINCIPAL_PROPERTY_URIS = {
    predicate: property_uri(predicate) for predicate in PRINCIPAL_PREDICATES
}
OPTIONAL_PROPERTY_URIS = {
    predicate: property_uri(predicate) for predicate in OPTIONAL_CONTEXT_PREDICATES
}

PROPERTY_DOMAIN_RANGE = {
    "produces": (MC.ProductionBatch, BIM.ModularUnit),
    "containsComponent": (BIM.ModularUnit, BIM.BuildingComponent),
    "hasComponentType": (BIM.BuildingComponent, BIM.ComponentType),
    "hasMaterial": (BIM.BuildingComponent, BIM.IfcMaterial),
    "hasDesignQuantity": (BIM.BuildingComponent, BIM.DesignQuantity),
    "hasStage": (MC.ManufacturingProcessTemplate, MC.ProductionStage),
    "hasActivity": (MC.ProductionStage, MC.ManufacturingActivity),
    "usesResource": (MC.ManufacturingActivity, MC.ManufacturingResource),
    "manufacturedBy": (BIM.ProductObject, MC.ManufacturingActivity),
    "recordedForObject": (CARBON.ConsumptionDriver, BIM.ProductObject),
    "ofMaterial": (CARBON.MaterialConsumption, BIM.IfcMaterial),
    "hasQuantity": (CARBON.ConsumptionDriver, CARBON.ConsumptionQuantity),
    "hasFactor": (CARBON.ConsumptionDriver, CARBON.EmissionFactor),
    "ofCarrier": (CARBON.EnergyConsumption, CARBON.EnergyCarrier),
    "hasCarbonDriver": (CARBON.CarbonEmission, CARBON.ConsumptionDriver),
    "derivedFrom": (CARBON.ConsumptionQuantity, BIM.DesignQuantity),
}

PROPERTY_UNION_DOMAIN_RANGE = {
    "hasProcessTemplate": (
        {BIM.ProductObject, BIM.ComponentType},
        MC.ManufacturingProcessTemplate,
    ),
}

PROHIBITED_CLASS_LOCAL_NAMES = {
    "AtomicCarbonEmission",
    "AggregateCarbonEmission",
    "AttributionObject",
    "SourceObject",
    "FactoryTarget",
    "Material",
    "Quantity",
    "Resource",
    "CarbonBoundary",
    "VersionMapping",
    "GranularityLevel",
    "DataSource",
    "CaseSpecificExtension",
    "ProductionLine",
    "Allocation",
}

PROHIBITED_PROPERTY_LOCAL_NAMES = {
    "EMISSION_OF",
    "emissionOf",
    "hasProductType",
    "relatedToQuantity",
    "hasTotalEmission",
}


@pytest.fixture(scope="module")
def ontology_graph() -> Graph:
    graph = Graph()
    graph.parse(Path("mic-carbon-ontology.ttl"), format="turtle")
    return graph


@pytest.fixture(scope="module")
def shapes_graph() -> Graph:
    graph = Graph()
    path = Path("mic-carbon-shapes.ttl")
    if path.exists():
        graph.parse(path, format="turtle")
    return graph


def _truthy_subjects(graph: Graph, predicate) -> set:
    return {
        subject
        for subject, value in graph.subject_objects(predicate)
        if value.toPython() is True
    }


def _local_name(uri) -> str:
    text = str(uri)
    return text.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _property_shape(graph: Graph, node_shape, path):
    matches = []
    for prop_shape in graph.objects(node_shape, SH.property):
        if (prop_shape, SH.path, path) in graph:
            matches.append(prop_shape)
    assert len(matches) == 1, (node_shape, path, matches)
    return matches[0]


def _inverse_property_shape(graph: Graph, node_shape, inverse_path):
    matches = []
    for prop_shape in graph.objects(node_shape, SH.property):
        for path_node in graph.objects(prop_shape, SH.path):
            if (path_node, SH.inversePath, inverse_path) in graph:
                matches.append(prop_shape)
    assert len(matches) == 1, (node_shape, inverse_path, matches)
    return matches[0]


def _assert_cardinality_and_class(
    graph: Graph,
    prop_shape,
    *,
    minimum: int,
    maximum: int | None,
    value_class,
) -> None:
    assert (prop_shape, SH.minCount, Literal(minimum)) in graph
    if maximum is None:
        assert not list(graph.objects(prop_shape, SH.maxCount))
    else:
        assert (prop_shape, SH.maxCount, Literal(maximum)) in graph
    assert (prop_shape, SH["class"], value_class) in graph


def _assert_union_members(graph: Graph, subject, predicate, expected_members: set) -> None:
    values = list(graph.objects(subject, predicate))
    assert len(values) == 1
    collection_head = graph.value(values[0], OWL.unionOf)
    assert collection_head is not None
    assert set(graph.items(collection_head)) == expected_members


def test_ontology_declares_exactly_the_mapped_v2_application_classes(ontology_graph):
    assert set(APPLICATION_CLASS_URIS) == set(APPLICATION_CLASSES)
    for ontology_class in APPLICATION_CLASS_URIS.values():
        assert (ontology_class, RDF.type, OWL.Class) in ontology_graph
    assert _truthy_subjects(ontology_graph, ONTO.instantiable) == set(
        APPLICATION_CLASS_URIS.values()
    )


def test_only_the_four_permitted_abstract_parents_are_marked_abstract(ontology_graph):
    for ontology_class in ABSTRACT_PARENT_URIS.values():
        assert (ontology_class, RDF.type, OWL.Class) in ontology_graph
    assert _truthy_subjects(ontology_graph, ONTO.abstract) == set(
        ABSTRACT_PARENT_URIS.values()
    )
    assert set(APPLICATION_CLASS_URIS.values()).isdisjoint(
        ABSTRACT_PARENT_URIS.values()
    )


def test_abstract_parents_are_tbox_only_in_the_canonical_runtime():
    graph = CanonicalLPGGraph()
    for label in APPLICATION_CLASSES:
        graph.add_node(f"fixture:{label}", [label])
    runtime_labels = {
        label for node in graph.nodes.values() for label in node["labels"]
    }
    assert runtime_labels.isdisjoint(ABSTRACT_PARENT_URIS)
    for label in ABSTRACT_PARENT_URIS:
        with pytest.raises(ValueError, match="forbidden runtime label"):
            graph.add_node(f"forbidden:{label}", [label])


def test_required_subclass_and_ifcowl_alignments_are_explicit(ontology_graph):
    required = {
        (BIM.ProductObject, IFCOWL.IfcProduct),
        (BIM.ModularUnit, BIM.ProductObject),
        (BIM.BuildingComponent, BIM.ProductObject),
        (BIM.ComponentType, BIM.ProductType),
        (BIM.IfcMaterial, IFCOWL.IfcMaterial),
        (BIM.DesignQuantity, IFCOWL.IfcPhysicalQuantity),
        (MC.ManufacturingProcessTemplate, MC.ProcessElement),
        (MC.ProductionStage, MC.ProcessElement),
        (MC.ManufacturingActivity, MC.ProcessElement),
        (CARBON.MaterialConsumption, CARBON.ConsumptionDriver),
        (CARBON.EnergyConsumption, CARBON.ConsumptionDriver),
    }
    for child, parent in required:
        assert (child, RDFS.subClassOf, parent) in ontology_graph
        assert (child, OWL.equivalentClass, parent) not in ontology_graph


def test_ontology_declares_exact_principal_and_optional_object_properties(
    ontology_graph,
):
    assert set(PRINCIPAL_PROPERTY_URIS) == set(PRINCIPAL_PREDICATES)
    assert set(OPTIONAL_PROPERTY_URIS) == set(OPTIONAL_CONTEXT_PREDICATES)
    expected = set(PRINCIPAL_PROPERTY_URIS.values()) | set(
        OPTIONAL_PROPERTY_URIS.values()
    )
    assert set(ontology_graph.subjects(RDF.type, OWL.ObjectProperty)) == expected


def test_principal_properties_have_the_canonical_domains_and_ranges(ontology_graph):
    assert set(PROPERTY_DOMAIN_RANGE) | set(PROPERTY_UNION_DOMAIN_RANGE) == set(
        PRINCIPAL_PREDICATES
    )
    for predicate, (domain, range_) in PROPERTY_DOMAIN_RANGE.items():
        uri = property_uri(predicate)
        assert set(ontology_graph.objects(uri, RDFS.domain)) == {domain}
        assert set(ontology_graph.objects(uri, RDFS.range)) == {range_}
    for predicate, (domain_members, range_) in PROPERTY_UNION_DOMAIN_RANGE.items():
        uri = property_uri(predicate)
        _assert_union_members(ontology_graph, uri, RDFS.domain, domain_members)
        assert set(ontology_graph.objects(uri, RDFS.range)) == {range_}


def test_object_properties_follow_the_namespace_rule(ontology_graph):
    declared = set(ontology_graph.subjects(RDF.type, OWL.ObjectProperty))
    assert declared == {property_uri(p) for p in PROPERTY_NAMESPACES}
    assert set(PROPERTY_NAMESPACES) == set(PRINCIPAL_PREDICATES) | set(
        OPTIONAL_CONTEXT_PREDICATES
    )
    # integr: holds exactly the carbon-record-to-entity links
    integration = {_local_name(uri) for uri in declared if str(uri).startswith(str(ONTO))}
    assert integration == set(INTEGRATION_PREDICATES)
    for predicate in INTEGRATION_PREDICATES:
        domains = set(ontology_graph.objects(property_uri(predicate), RDFS.domain))
        assert domains <= {
            CARBON.ConsumptionDriver,
            CARBON.MaterialConsumption,
            CARBON.EnergyConsumption,
            CARBON.ConsumptionQuantity,
        }
    # local names are unique across the four namespaces (LPG stores local names only)
    local_names = [_local_name(uri) for uri in declared]
    assert len(local_names) == len(set(local_names)) == 20


def test_optional_context_properties_are_separate_and_correctly_typed(ontology_graph):
    assert set(OPTIONAL_CONTEXT_PREDICATES).isdisjoint(PRINCIPAL_PREDICATES)
    assert set(OPTIONAL_CONTEXT_PREDICATES) == {
        "associatedWithProcess",
        "recordedForResource",
        "directlyPrecedes",
    }
    assert set(ontology_graph.objects(ONTO.recordedForResource, RDFS.domain)) == {
        CARBON.EnergyConsumption
    }
    assert set(ontology_graph.objects(ONTO.recordedForResource, RDFS.range)) == {
        MC.ManufacturingResource
    }
    assert set(ontology_graph.objects(ONTO.associatedWithProcess, RDFS.domain)) == {
        CARBON.EnergyConsumption
    }
    _assert_union_members(
        ontology_graph,
        ONTO.associatedWithProcess,
        RDFS.range,
        {
            MC.ProductionStage,
            MC.ManufacturingActivity,
        },
    )
    assert set(ontology_graph.objects(MC.directlyPrecedes, RDFS.domain)) == {
        MC.ManufacturingActivity
    }
    assert set(ontology_graph.objects(MC.directlyPrecedes, RDFS.range)) == {
        MC.ManufacturingActivity
    }


def test_manufacturing_resource_kind_is_a_datatype_property(ontology_graph):
    assert (MC.resourceKind, RDF.type, OWL.DatatypeProperty) in ontology_graph
    assert set(ontology_graph.objects(MC.resourceKind, RDFS.domain)) == {
        MC.ManufacturingResource
    }
    assert set(ontology_graph.objects(MC.resourceKind, RDFS.range)) == {XSD.string}
    assert (MC.powerKw, RDF.type, OWL.DatatypeProperty) in ontology_graph
    assert set(ontology_graph.objects(MC.powerKw, RDFS.domain)) == {
        MC.ManufacturingResource
    }
    assert set(ontology_graph.objects(MC.powerKw, RDFS.range)) == {XSD.decimal}


def test_deprecated_operational_vocabulary_and_existential_restrictions_are_absent(
    ontology_graph,
):
    declared_classes = set(ontology_graph.subjects(RDF.type, OWL.Class))
    declared_properties = set(ontology_graph.subjects(RDF.type, OWL.ObjectProperty))
    assert {
        uri
        for uri in declared_classes
        if _local_name(uri) in PROHIBITED_CLASS_LOCAL_NAMES
    } == set()
    assert {
        uri
        for uri in declared_properties
        if _local_name(uri) in PROHIBITED_PROPERTY_LOCAL_NAMES
    } == set()
    assert {
        uri for uri in declared_properties if "_" in _local_name(uri)
    } == set()
    assert set(ontology_graph.subjects(RDF.type, OWL.Restriction)) == set()


def test_ontology_predicate_contract_expands_to_the_runtime_triples():
    assert len(PRINCIPAL_EDGE_TRIPLES) == 25
    assert len(PRINCIPAL_PREDICATES) == 17
    assert {
        predicate for _, predicate, _ in PRINCIPAL_EDGE_TRIPLES
    } == set(PRINCIPAL_PROPERTY_URIS)


def test_carbon_emission_shape_requires_one_generated_source(shapes_graph):
    shape = SHAPES.CarbonEmissionShape
    assert (shape, RDF.type, SH.NodeShape) in shapes_graph
    assert (shape, SH.targetClass, CARBON.CarbonEmission) in shapes_graph
    prop = _property_shape(shapes_graph, shape, CARBON.hasCarbonDriver)
    _assert_cardinality_and_class(
        shapes_graph,
        prop,
        minimum=1,
        maximum=1,
        value_class=CARBON.ConsumptionDriver,
    )


def test_material_consumption_shape_has_exact_cardinalities(shapes_graph):
    shape = SHAPES.MaterialConsumptionShape
    assert (shape, SH.targetClass, CARBON.MaterialConsumption) in shapes_graph
    expected = {
        ONTO.recordedForObject: BIM.ProductObject,
        ONTO.ofMaterial: BIM.IfcMaterial,
        CARBON.hasQuantity: CARBON.ConsumptionQuantity,
        CARBON.hasFactor: CARBON.EmissionFactor,
    }
    for path, value_class in expected.items():
        _assert_cardinality_and_class(
            shapes_graph,
            _property_shape(shapes_graph, shape, path),
            minimum=1,
            maximum=1,
            value_class=value_class,
        )
    _assert_cardinality_and_class(
        shapes_graph,
        _inverse_property_shape(shapes_graph, shape, CARBON.hasCarbonDriver),
        minimum=1,
        maximum=1,
        value_class=CARBON.CarbonEmission,
    )


def test_energy_consumption_shape_allows_process_only_records(shapes_graph):
    shape = SHAPES.EnergyConsumptionShape
    assert (shape, SH.targetClass, CARBON.EnergyConsumption) in shapes_graph
    required = {
        CARBON.hasQuantity: CARBON.ConsumptionQuantity,
        CARBON.hasFactor: CARBON.EmissionFactor,
        CARBON.ofCarrier: CARBON.EnergyCarrier,
    }
    for path, value_class in required.items():
        _assert_cardinality_and_class(
            shapes_graph,
            _property_shape(shapes_graph, shape, path),
            minimum=1,
            maximum=1,
            value_class=value_class,
        )
    _assert_cardinality_and_class(
        shapes_graph,
        _property_shape(shapes_graph, shape, ONTO.recordedForObject),
        minimum=0,
        maximum=None,
        value_class=BIM.ProductObject,
    )
    _assert_cardinality_and_class(
        shapes_graph,
        _inverse_property_shape(shapes_graph, shape, CARBON.hasCarbonDriver),
        minimum=1,
        maximum=1,
        value_class=CARBON.CarbonEmission,
    )


def test_shapes_do_not_guess_derived_from_or_optional_population_cardinality(
    shapes_graph,
):
    shaped_paths = {
        path
        for prop_shape in shapes_graph.subjects(RDF.type, SH.PropertyShape)
        for path in shapes_graph.objects(prop_shape, SH.path)
        if path != RDF.nil
    }
    assert ONTO.derivedFrom not in shaped_paths
    assert ONTO.associatedWithProcess not in shaped_paths
    assert ONTO.recordedForResource not in shaped_paths
    assert MC.directlyPrecedes not in shaped_paths
