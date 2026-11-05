# Ontology Reusable Framework 
This package deploys from local files. You do not need access to the publisher's
Fabric workspace, connections, tenant or Azure subscription.

## What is included

- `lh_terra` lakehouse creation settings.
- `Pl_End_to_End_Terra_workflow` and all exported called pipelines, including
  `Pl_Copy_Files_to_lh`.
- The referenced `DF_GeoBasin_ExpandJson` Dataflow Gen2.
- The self-contained `create_ontology_new_experience` notebook.
- Connection requirements and a recipient-only configuration file. Credentials
  and source connection IDs are not included.
- Sample CSVs and source attribution under `data` when the publisher includes data.

Readable source files are under `deployment\package`:

```text
deployment\package\
  manifest.json
  items\
    Pl_End_to_End_Terra_workflow.DataPipeline\pipeline-content.json
    Pl_Copy_Files_to_lh.DataPipeline\pipeline-content.json
    DF_GeoBasin_ExpandJson.Dataflow\
      mashup.pq
      queryMetadata.json
      .platform
  notebooks\create_ontology_new_experience.ipynb
```

GUIDs in these definitions are package-local placeholders, not IDs that need
access to another tenant. Deployment substitutes IDs from your environment.
The manifest includes integrity hashes for the definitions and notebook.

## 1. Prepare your environment

1. Clone the repository and open its root folder in VS Code, or download and
   extract the complete ZIP:

   ```powershell
   git clone https://github.com/vijisoms/Ontology-Reusable-Framework.git
   Set-Location .\Ontology-Reusable-Framework
   ```

2. Install Python 3.10+ and Azure CLI 2.55+ on PATH. No pip packages are needed.
3. Use or create a Fabric workspace with active capacity and the necessary
   Data Factory, Dataflow Gen2 and notebook features enabled. You need
   Contributor/Member/Admin access and permission to list its assigned capacity.
4. Sign in to **your** tenant:

   ```powershell
   az login --allow-no-subscriptions --tenant <your-tenant-id>
   ```

The deployment task does not create workspaces, resume capacity or assign licences.
Ontology creation/graph materialization additionally require tenant feature
enablement and supported capacity; they are separate from artifact deployment.

## 2. Authenticate your own connections once

In Fabric, use **Settings > Manage connections and gateways** to create or
select shared cloud connections of these connector types:

| Config alias | Connector type | Authentication |
|---|---|---|
| `fabric_pipelines` | `FabricDataPipelines` | Sign in with your organizational account |
| `lakehouse` | `Lakehouse` | Sign in with your organizational account |
| `notebook` | `Notebook` | Sign in with your organizational account |

Create your local configuration from the included template before configuring
connections:

```powershell
Copy-Item .\deployment\deploy-config.example.json .\deployment\deploy-config.json
```

If you already have a local configuration, edit it instead of overwriting it.
The local `deployment\deploy-config.json` is ignored by Git; do not commit your
environment-specific configuration.

Only configure aliases actually listed in the included config. The `notebook`
connection is required when the exported workflow invokes a notebook through
that connector. A package can also have multiple connections of one type.
Copy each required connection's ID into
`deployment\deploy-config.json`, for example:

```json
{
  "connection_bindings": {
    "fabric_pipelines": "<your-FabricDataPipelines-connection-guid>",
    "lakehouse": "<your-Lakehouse-connection-guid>",
    "notebook": "<your-Notebook-connection-guid>"
  },
  "reference_map": {}
}
```

Replace the example placeholders with real GUIDs. Do not enter passwords,
client secrets, access tokens or source/publisher connection IDs.

The task reads only your connections, verifies connector types, and resolves
your dataflow connection's `ClusterId` using the Power BI datasource listing.
You need permission to read/use those connections and access that listing.
Connection credential validity and the eventual job identity's access must
still be verified when running the workflow.

OAuth2 credentials cannot be exported or created by the public Connections
Create API, so this one-time sign-in cannot be bundled. Connections are not
cloned or silently substituted with the publisher's credentials.

## 3. Deploy the artifacts

Select **Terminal > Run Task > Deploy to Fabric**, then paste your target
workspace GUID from its Fabric URL:
`https://app.fabric.microsoft.com/groups/<workspace-guid>/...`.

Alternatively, run from the repository or extracted root:

```powershell
python .\deployment\deploy_to_fabric.py --workspace-id <your-workspace-guid> --package .\deployment\package\manifest.json
```

The task validates local definitions and your target connections, creates the
lakehouse/notebook/pipeline/dataflow items, remaps their references, reads back
dataflow bindings and prints the resulting IDs and workspace URL.

It does **not** contact the publisher's workspace or connections, execute jobs,
refresh dataflows, create an ontology, materialize a graph, or upload data.
The notebook is deployed with `apply_changes = False` and
`allow_update_existing = False`.

## 4. Upload the bundled sample CSVs

If a `data\fabric-iq-kit` folder is included, upload that **whole folder**
to the new `lh_terra` lakehouse's **Files** area, preserving this layout:

```text
Files\fabric-iq-kit\
  schema.csv
  cooper_boreholes.csv
  entities\*.csv
  relationships\*.csv
  optional\split-relationship-tables\*.csv
```

The included source manifest and data credit retain publisher and licence
attribution. These are sample CSVs, not an export of the publisher's Delta
tables. If no `data` folder is present, supply your own compatible inputs.

## 5. Review and run separately

- Review pipeline activity states, connections, source paths and destination
  schemas. The snapshot preserves the source's behavior, including inactive
  activities; deployment does not enable them or repair source logic.
- The original sample workflow has inactive child calls and a dataflow refresh.
  Enable the steps you intend to run. Review any table overwrite behavior first.
- The dataflow expects the entity CSVs loaded into `raw` Delta tables before
  it expands them into the ontology's tables. Its first refresh after deployment
  must apply the draft definition (`executeOption: ApplyChangesIfNeeded`);
  review/publish it in Fabric before running the workflow.
- The package sends the complete `mashup.pq`, `queryMetadata.json` and `.platform`.
  M preview is deliberately skipped at deployment because the raw tables do not
  exist in an empty lakehouse yet.
- Run the ontology notebook in plan mode after the expected Delta tables are
  populated. Review the report before explicitly enabling ontology creation.
  Follow its UI source-finalization and **Manage graph > Materialize** steps;
  graph readiness is not guaranteed by artifact deployment.

## Existing items and failures

Existing lakehouse data is reused, not overwritten by deployment. Existing
managed notebook, pipeline or dataflow definitions require explicit replacement:

```powershell
python .\deployment\deploy_to_fabric.py --workspace-id <your-workspace-guid> --package .\deployment\package\manifest.json --update-existing
```

This replaces the complete managed definitions, including called pipelines and
dataflows, and resets the notebook to plan mode. Review customizations first.
No automatic rollback, deletion or POST retry is performed. Inspect any partial
deployment before retrying. Integrity errors require a fresh package or a
deliberately regenerated manifest; they are not silently ignored.

## Validate locally without deploying

Run the deployment unit tests, including package integrity validation, from the
repository root. These tests do not contact Fabric or execute jobs:

```powershell
python -m unittest discover -s .\deployment -p "test_*.py" -v
```
