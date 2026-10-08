# Query views and record projection

For a grouped query, `GroupBy` determines the answer's view label. A product
filter restricts the population without changing a material or process grouping
into a product grouping. For example, filtering to structural components and
grouping by material gives the material view while retaining those components'
attributed carbon contributions.

`derive_projection_perspective` retains its public name and returns this answer
label. `derive_view_signature` and the service's `perspective` field use it.
`derive_record_projection_perspective` selects the record policy from the union
of grouping and filter fields. The executor retains its existing selector
overrides: resolved product selectors use attributed contributions, and resolved
material or process selectors use matching source records. Record values,
allocation shares, filters and evidence paths follow this separate policy.

The execution summary keeps `projection_perspective` as the answer label for
compatibility and adds `record_projection_perspective` for the record policy.
The latter describes the policy **before selector overrides**. It does not on
its own identify the actual projection used by an entity-scoped query. Coverage
checks and reference calculations use the record policy so a label correction
does not suppress a relevant rejected input or change a numerical reference.

The answer adapter uses source-specific total names only when the requested
source matches the material or process label. For other source scopes, it keeps
the executor's neutral `total_kgCO2e` and declares `source_scope`. A mixed total
grouped by material is therefore not reported as material-only carbon.

## Label rule

The following precedence applies to `GroupBy` fields when present. With no
`GroupBy`, it applies to `Filter` fields, preserving the ungrouped-query policy.

| Fields | Label |
| --- | --- |
| Any product field | `product` |
| Material and process fields, without a product field | `source_union` |
| Material fields without product or process fields | `material_source` |
| Process fields without product or material fields | `energy_source` |
| Only factor fields or `source_kind` | The requested source: material, process, or union |
| No grouping or filter fields | `energy_source` for process-only requests that do not select product objects; otherwise `product` |

Product selectors are `SelectClicked` and `ResolveEntities` for components or
modules. `source_union` combines the material and process view families; it does
not guarantee that records from both sources survive the selector, source and
filter restrictions, or that its total is a product-attributed total.

An ungrouped `Aggregate` yields `CarbonScalar`; aggregation after `GroupBy`
yields `CarbonTable`. `Trace` yields `TraceResult`, including for queries with
neither grouping nor filters. Output type follows the validated operator chain.

## Compiler responsibilities

The language model generates the selector, source arguments and analytical
operators in `steps`, with `holes` for unresolved requirements. Its instructions
prohibit a separately generated perspective label. Deterministic rules derive
the label from the validated program, and the executor retrieves records and
calculates values.

## Verification scope

Regression tests cover component, component-type and IFC-class filters,
allocated process records, mixed grouping, unchanged evidence, reference
evaluation, API answer labels and rejected inputs. The checked-in frozen study
results are historical observations. Changing a derived label or compiler prompt
does not retrospectively change those observations or establish a new accuracy
rate.
