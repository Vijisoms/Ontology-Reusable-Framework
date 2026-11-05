"""Deploy Terra artifacts, without executing jobs or copying lakehouse data."""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

API = "https://api.fabric.microsoft.com/v1"
ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("terra.deploy")
GUID = re.compile(
    r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])", re.IGNORECASE
)


def guid(value: str) -> str:
    return str(uuid.UUID(value))


class FabricClient:
    def __init__(self) -> None:
        self.az = shutil.which("az")
        if not self.az:
            raise RuntimeError("Azure CLI is missing. Install it and run az login --allow-no-subscriptions.")
        self.token = ""
        self.expires = 0.0

    def authenticate(self) -> None:
        result = subprocess.run(
            [self.az, "account", "get-access-token", "--resource",
             "https://api.fabric.microsoft.com", "--output", "json"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise RuntimeError(
                "Azure CLI token acquisition failed. Run az login --allow-no-subscriptions "
                "in the correct tenant, then retry.\n" + result.stderr.strip()
            )
        token = json.loads(result.stdout)
        self.token = token["accessToken"]
        self.expires = float(token["expires_on"])

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict, dict]:
        url = path if urlparse(path).scheme or path.startswith("//") else API + path
        parsed = urlparse(url)
        regional_operation = (
            method == "GET"
            and re.fullmatch(r"wabi-[a-z0-9-]+\.analysis\.windows\.net", parsed.netloc) is not None
            and re.fullmatch(
                r"/v1/operations/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}(?:/result)?",
                parsed.path,
            ) is not None
            and not parsed.query
            and not parsed.fragment
        )
        if parsed.scheme != "https" or (
            parsed.netloc != "api.fabric.microsoft.com" and not regional_operation
        ):
            raise ValueError(f"Refusing untrusted API URL: {url}")
        if time.time() >= self.expires - 120:
            self.authenticate()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })
        try:
            with urlopen(request, timeout=120) as response:
                raw = response.read()
                return response.status, dict(response.headers), json.loads(raw) if raw else {}
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            request_id = error.headers.get("requestId", error.headers.get("x-ms-request-id", "unknown"))
            raise RuntimeError(
                f"Fabric {method} {parsed.path}: HTTP {error.code}, request ID {request_id}: {details}. "
                "No POST was retried. Inspect the workspace before rerunning an uncertain create."
            ) from error

    def call(self, method: str, path: str, body: dict | None = None, *, result: bool = False) -> dict:
        status, headers, value = self.request(method, path, body)
        if status != 202:
            return value
        headers = {key.lower(): val for key, val in headers.items()}
        location = headers.get("location")
        if not location:
            raise RuntimeError("Fabric returned 202 without Location; inspect the workspace before retrying.")
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            time.sleep(max(1, min(int(headers.get("retry-after", "10")), 60)))
            _, poll_headers, value = self.request("GET", location)
            headers = {key.lower(): val for key, val in poll_headers.items()}
            if value.get("status") == "Succeeded":
                return self.request("GET", location.rstrip("/") + "/result")[2] if result else value
            if value.get("status") in {"Failed", "Cancelled"}:
                raise RuntimeError(f"Fabric operation failed at {location}: {json.dumps(value)}")
        raise TimeoutError(f"Fabric operation timed out: {location}. Inspect it before retrying.")

    def listing(self, path: str) -> list[dict]:
        values: list[dict] = []
        while path:
            page = self.call("GET", path)
            values.extend(page["value"])
            path = page.get("continuationUri", "")
        return values

    def datasource_clusters(self) -> dict[str, str]:
        result = subprocess.run(
            [self.az, "rest", "--method", "get",
             "--resource", "https://analysis.windows.net/powerbi/api",
             "--url", "https://api.powerbi.com/v2.0/myorg/me/gatewayClusterDatasources",
             "--output", "json"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise RuntimeError("Cannot resolve dataflow connection ClusterIds:\n" + result.stderr.strip())
        page = json.loads(result.stdout)
        if page.get("continuationToken") or page.get("continuationUri") or page.get("@odata.nextLink"):
            raise RuntimeError("Connection cluster listing is paginated; resolve replacement bindings in Fabric first.")
        return {guid(value["id"]): guid(value["clusterId"]) for value in page["value"]}


def find_item(items: list[dict], name: str, kind: str) -> dict | None:
    matches = [item for item in items if item["displayName"] == name and item["type"] == kind]
    if len(matches) > 1:
        raise ValueError(f"Ambiguous {kind} named {name}; rename duplicates before deployment.")
    return matches[0] if matches else None


def active_workspace(client: FabricClient, workspace_id: str, capacities: list[dict]) -> dict:
    workspace = client.call("GET", f"/workspaces/{workspace_id}")
    capacity = next((c for c in capacities if c["id"] == workspace.get("capacityId")), None)
    if not capacity or capacity.get("state") != "Active":
        raise RuntimeError(
            f"Workspace {workspace['displayName']} must have active capacity. "
            "Resume/assign capacity and ensure your account can list that capacity."
        )
    return workspace


def notebook_definition(path: Path, workspace: dict, lakehouse_id: str, lakehouse_name: str) -> dict:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    replacements = {
        "workspace_name": workspace["displayName"],
        "lakehouse_name": lakehouse_name,
        "apply_changes": False,
        "allow_update_existing": False,
    }
    seen: set[str] = set()
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            cell["execution_count"] = None
            cell["outputs"] = []
        if "parameters" not in cell.get("metadata", {}).get("tags", []):
            continue
        source = "".join(cell["source"])
        lines = source.splitlines(keepends=True)
        edits = []
        for node in ast.parse(source).body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in replacements:
                edits.append((node.lineno - 1, node.end_lineno, target.id))
                seen.add(target.id)
        for start, end, name in reversed(edits):
            lines[start:end] = [f"{name} = {replacements[name]!r}\n"]
        cell["source"] = lines
    if seen != set(replacements):
        raise ValueError(f"Notebook parameter cell is missing: {sorted(set(replacements) - seen)}")
    dependencies = notebook.setdefault("metadata", {}).setdefault("dependencies", {})
    dependencies["lakehouse"] = {
        "default_lakehouse": lakehouse_id,
        "default_lakehouse_name": lakehouse_name,
        "default_lakehouse_workspace_id": workspace["id"],
        "known_lakehouses": [{"id": lakehouse_id}],
    }
    return {
        "format": "ipynb",
        "parts": [{
            "path": "notebook-content.ipynb",
            "payload": base64.b64encode(json.dumps(notebook).encode("utf-8")).decode("ascii"),
            "payloadType": "InlineBase64",
        }],
    }


def pipeline_content(definition: dict) -> dict:
    parts = [p for p in definition["parts"] if p["path"] == "pipeline-content.json"]
    if len(parts) != 1 or parts[0].get("payloadType") != "InlineBase64":
        raise ValueError("Expected one InlineBase64 pipeline-content.json in the source export.")
    return json.loads(base64.b64decode(parts[0]["payload"], validate=True))


def dataflow_content(definition: dict) -> dict:
    parts = {}
    for part in definition["parts"]:
        if part["path"] not in {"mashup.pq", "queryMetadata.json", ".platform"}:
            raise ValueError(f"Unsupported dataflow definition part: {part['path']}")
        if part.get("payloadType") != "InlineBase64" or part["path"] in parts:
            raise ValueError("Dataflow parts must be unique InlineBase64 payloads.")
        text = base64.b64decode(part["payload"], validate=True).decode("utf-8-sig")
        parts[part["path"]] = text if part["path"] == "mashup.pq" else json.loads(text)
    if set(parts) != {"mashup.pq", "queryMetadata.json", ".platform"}:
        raise ValueError("Dataflow export needs mashup.pq, queryMetadata.json and .platform.")
    if parts["queryMetadata.json"].get("formatVersion") != "202502":
        raise ValueError("Unsupported dataflow queryMetadata.json formatVersion; expected 202502.")
    return parts


def connection_binding(value: str) -> tuple[str, str | None]:
    value = value.strip()
    if value.startswith("{"):
        binding = json.loads(value)
        return guid(binding["DatasourceId"]), guid(binding["ClusterId"])
    try:
        return guid(value), None
    except ValueError as error:
        raise ValueError(f"Dynamic or invalid connection reference cannot be deployed: {value}") from error


def connection_references(value: object) -> set[str]:
    result: set[str] = set()
    if isinstance(value, list):
        for child in value:
            result.update(connection_references(child))
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in {"connection", "connectionId"} and isinstance(child, str):
                result.add(connection_binding(child)[0])
            else:
                result.update(connection_references(child))
    return result


def dependency_content(exported: dict) -> dict:
    if exported["item"]["type"] == "DataPipeline":
        return exported["content"]
    metadata = json.loads(json.dumps(exported["content"]["queryMetadata.json"]))
    for query in metadata.get("queriesMetadata", {}).values():
        query.pop("queryId", None)
    for connection in metadata.get("connections", []):
        connection["connectionId"] = connection_binding(connection["connectionId"])[0]
    return {"mashup": exported["content"]["mashup.pq"], "metadata": metadata}


def named_pipeline_references(value: object, source_items: list[dict]) -> set[str]:
    result: set[str] = set()
    if isinstance(value, list):
        for child in value:
            result.update(named_pipeline_references(child, source_items))
    elif isinstance(value, dict):
        if value.get("type") == "InvokePipeline":
            reference = value.get("typeProperties", {}).get("pipelineId")
            if not isinstance(reference, str) or not GUID.fullmatch(reference):
                raise ValueError("InvokePipeline needs a literal pipelineId; expression-based calls require manual configuration.")
        if value.get("type") == "PipelineReference":
            name = value["referenceName"]
            if GUID.fullmatch(name):
                result.add(guid(name))
            else:
                pipeline = find_item(source_items, name, "DataPipeline")
                if not pipeline:
                    raise ValueError(f"Named source pipeline '{name}' was not found.")
                result.add(guid(pipeline["id"]))
        for child in value.values():
            result.update(named_pipeline_references(child, source_items))
    return result


def export_dependencies(client: FabricClient, source_id: str, source_items: list[dict],
                        root: dict, config: dict) -> dict[str, dict]:
    source_index = {guid(item["id"]): item for item in source_items}
    explicit = {guid(key) for key in config["reference_map"]}
    exports: dict[str, dict] = {}
    pending = [root]
    while pending:
        item = pending.pop()
        identity = guid(item["id"])
        if identity in exports:
            continue
        export = client.call(
            "POST", f"/workspaces/{source_id}/items/{identity}/getDefinition", {}, result=True,
        )
        content = (pipeline_content(export["definition"]) if item["type"] == "DataPipeline"
                   else dataflow_content(export["definition"]))
        exports[identity] = {"item": item, "content": content}
        dependencies = dependency_content(exports[identity])
        refs = references(dependencies) | named_pipeline_references(dependencies, source_items)
        for reference in sorted(refs):
            child = source_index.get(reference)
            if child and child["type"] in {"DataPipeline", "Dataflow"} and reference not in explicit:
                pending.append(child)
    LOG.info("Exported %d pipeline/dataflow definitions, including nested dependencies", len(exports))
    return exports


def package_file(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative or ":" in relative:
        raise ValueError(f"Invalid package-relative path: {relative}")
    resolved = root.joinpath(*path.parts).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Package file escapes its root: {relative}")
    return resolved


def load_package(manifest_path: Path) -> dict:
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format_version") != 1:
        raise ValueError("Unsupported standalone package format.")
    root = manifest_path.parent
    files = manifest["files"]
    for relative, expected_hash in files.items():
        if hashlib.sha256(package_file(root, relative).read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"Package integrity check failed: {relative}")
    if manifest["notebook"] not in files:
        raise ValueError("Package notebook is missing its integrity record.")
    exports = {}
    for entry in manifest["definitions"]:
        item = entry["item"]
        identity = guid(item["id"])
        if identity in exports or item["type"] not in {"DataPipeline", "Dataflow"}:
            raise ValueError("Duplicate or unsupported packaged item.")
        parts = []
        for name, relative in entry["parts"].items():
            if relative not in files:
                raise ValueError(f"Definition has no integrity record: {relative}")
            parts.append({
                "path": name,
                "payload": base64.b64encode(package_file(root, relative).read_bytes()).decode("ascii"),
                "payloadType": "InlineBase64",
            })
        definition = {"parts": parts}
        content = pipeline_content(definition) if item["type"] == "DataPipeline" else dataflow_content(definition)
        exports[identity] = {"item": item, "content": content}
    pipeline = find_item([entry["item"] for entry in exports.values()],
                         manifest["config"]["pipeline_name"], "DataPipeline")
    if not pipeline:
        raise ValueError("Standalone package does not include its root pipeline.")
    catalog = {guid(connection["id"]): connection for connection in manifest["connections"]}
    aliases = [connection["alias"] for connection in catalog.values()]
    if len(catalog) != len(manifest["connections"]) or len(set(aliases)) != len(aliases):
        raise ValueError("Duplicate package connection IDs or aliases.")
    source_items = manifest["items"]
    if len({guid(item["id"]) for item in source_items}) != len(source_items):
        raise ValueError("Duplicate package item IDs.")
    for identity, export in exports.items():
        if export["item"] not in source_items:
            raise ValueError(f"Packaged definition is missing from its item catalog: {identity}")
    return {
        "config": manifest["config"], "exports": exports, "source_items": source_items,
        "source_pipeline": pipeline, "connections": catalog,
        "notebook_path": package_file(root, manifest["notebook"]),
    }


def package_config(bundle: dict, recipient: dict) -> dict:
    config = {**bundle["config"], "reference_map": dict(recipient.get("reference_map", {})),
              "reuse_source_connections": False}
    supplied = recipient.get("connection_bindings", {})
    aliases = {connection["alias"] for connection in bundle["connections"].values()}
    if set(supplied) - aliases:
        raise ValueError(f"Unknown connection aliases: {sorted(set(supplied) - aliases)}")
    for source_id, connection in bundle["connections"].items():
        value = supplied.get(connection["alias"])
        if not value:
            continue
        target_id = guid(value)
        existing = config["reference_map"].get(source_id)
        if existing and guid(existing) != target_id:
            raise ValueError(f"Conflicting mapping for connection alias {connection['alias']}.")
        config["reference_map"][source_id] = target_id
    return config


def plan_connections(client: FabricClient, content: dict, config: dict,
                     catalog: dict | None = None) -> tuple[dict[str, str], list[dict]]:
    explicit = {guid(key): guid(value) for key, value in config["reference_map"].items()}
    mapping: dict[str, str] = {}
    connections = []
    for source_id in sorted(connection_references(content)):
        target_id = explicit.get(source_id)
        if not target_id and not config.get("reuse_source_connections", False):
            label = catalog.get(source_id, {}).get("alias", source_id) if catalog is not None else source_id
            raise ValueError(f"Connection {label} requires a recipient connection ID in connection_bindings or reference_map before deployment.")
        target_id = target_id or source_id
        source = catalog[source_id] if catalog is not None else client.call("GET", f"/connections/{source_id}")
        target = (source if target_id == source_id and catalog is None
                  else client.call("GET", f"/connections/{target_id}"))
        if guid(target["id"]) != target_id or guid(source["id"]) != source_id:
            raise ValueError("Connection lookup returned an unexpected ID.")
        if source["connectionDetails"]["type"] != target["connectionDetails"]["type"]:
            raise ValueError(f"Replacement connection {target_id} has an incompatible connector type.")
        if target_id == source_id and target.get("connectivityType") != "ShareableCloud":
            raise ValueError(f"Connection {source_id} is not ShareableCloud; configure an explicit replacement.")
        mapping[source_id] = target_id
        connections.append({
            "source_id": source_id, "target_id": target_id,
            "display_name": target["displayName"], "type": target["connectionDetails"]["type"],
        })
        LOG.info("Validated connection '%s' (%s)", target["displayName"], target_id)
    return mapping, connections


def dataflow_definition(content: dict, mapping: dict[str, str], clusters: dict[str, str],
                        platform: dict) -> dict:
    metadata = json.loads(json.dumps(content["queryMetadata.json"]))
    # Query IDs are definition-local identities, not workspace dependencies.
    internal = {guid(query["queryId"]): guid(query["queryId"])
                for query in metadata.get("queriesMetadata", {}).values() if "queryId" in query}
    bindings = metadata.pop("connections", [])
    metadata = remap(metadata, {**internal, **mapping})
    new_bindings = []
    for binding in bindings:
        source_id, _ = connection_binding(binding["connectionId"])
        target_id = mapping[source_id]
        if target_id not in clusters:
            raise ValueError(f"ClusterId not found for dataflow connection {target_id}.")
        mapped_binding = remap({key: value for key, value in binding.items() if key != "connectionId"}, mapping)
        mapped_binding["connectionId"] = json.dumps({
            "ClusterId": clusters[target_id], "DatasourceId": target_id,
        }, separators=(",", ":"))
        new_bindings.append(mapped_binding)
    metadata["connections"] = new_bindings
    documents = {
        "mashup.pq": remap(content["mashup.pq"], mapping),
        "queryMetadata.json": json.dumps(metadata),
        ".platform": json.dumps(platform),
    }
    return {"parts": [{
        "path": path, "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "payloadType": "InlineBase64",
    } for path, text in documents.items()]}


def references(value: object) -> set[str]:
    if isinstance(value, str):
        return {match.group().lower() for match in GUID.finditer(value)}
    if isinstance(value, list):
        return set().union(*(references(item) for item in value))
    if isinstance(value, dict):
        return set().union(*(references(item) for item in value.values()))
    return set()


def remap(value: object, mapping: dict[str, str]) -> object:
    if isinstance(value, str):
        return GUID.sub(lambda match: mapping[match.group().lower()], value)
    if isinstance(value, list):
        return [remap(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: remap(item, mapping) for key, item in value.items()}
    return value


def remap_pipeline(value: object, mapping: dict[str, str], names: dict[str, str]) -> object:
    if isinstance(value, list):
        return [remap_pipeline(child, mapping, names) for child in value]
    if isinstance(value, dict):
        result = {key: remap_pipeline(child, mapping, names) for key, child in value.items()}
        if value.get("type") == "PipelineReference" and not GUID.fullmatch(value["referenceName"]):
            result["referenceName"] = names[value["referenceName"]]
        return result
    return remap(value, mapping)


def reference_plan(content: dict, source_items: list[dict], target_items: list[dict],
                   config: dict, target_id: str, deployment_items: dict | None = None,
                   connection_mapping: dict[str, str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    mapping = {guid(config["source_workspace_id"]): target_id}
    mapping.update(connection_mapping or {})
    explicit = {guid(key): guid(value) for key, value in config["reference_map"].items()}
    source_index = {item["id"].lower(): item for item in source_items}
    managed: dict[str, str] = {}
    missing = []
    for reference in sorted(references(content)):
        if reference in mapping:
            continue
        if reference in explicit:
            mapping[reference] = explicit[reference]
            continue
        if reference in (deployment_items or {}):
            managed[reference] = reference
            continue
        source = source_index.get(reference)
        if source:
            name, kind = source["displayName"], source["type"]
            if name == config["lakehouse_name"] and kind in {"Lakehouse", "SQLEndpoint"}:
                managed[reference] = kind
                continue
            if kind == "Notebook" and name in config["source_notebook_names"]:
                managed[reference] = "Notebook"
                continue
            if name == config["pipeline_name"] and kind == "DataPipeline":
                managed[reference] = "DataPipeline"
                continue
            target = find_item(target_items, name, kind)
            if target:
                mapping[reference] = target["id"]
                continue
            missing.append(f"{reference}: target needs {kind} '{name}'")
        else:
            missing.append(f"{reference}: external reference needs an explicit reference_map entry")
    if missing:
        raise ValueError(
            "Unresolved pipeline dependencies (no target writes made):\n  "
            + "\n  ".join(missing)
            + "\nProvision these dependencies or configure source GUID -> target GUID in deploy-config.json. "
            "Connections require existing target connections and permission to use them."
        )
    return mapping, managed


def ensure_item(client: FabricClient, workspace_id: str, items: list[dict],
                name: str, kind: str, definition: dict | None = None) -> dict:
    existing = find_item(items, name, kind)
    if existing:
        LOG.info("Reusing %s '%s' (%s); not replacing its content", kind, name, existing["id"])
        return existing
    body: dict = {"displayName": name, "type": kind}
    if kind == "Lakehouse":
        body["creationPayload"] = {"enableSchemas": True}
    if definition:
        body["definition"] = definition
    endpoint = f"/workspaces/{workspace_id}/items"
    if kind == "Dataflow":
        endpoint = f"/workspaces/{workspace_id}/dataflows"
        body.pop("type")
    created = client.call("POST", endpoint, body, result=True)
    if not created.get("id"):
        raise RuntimeError(f"Create {name} returned no item ID. Inspect the workspace before retrying.")
    items.append(created)
    LOG.info("Created %s '%s' (%s)", kind, name, created["id"])
    return created


def deploy(client: FabricClient, workspace_id: str, config: dict, *, update_existing: bool = False,
           package: Path | None = None) -> dict:
    bundle = load_package(package) if package else None
    if bundle:
        config = package_config(bundle, config)
    workspace_id = guid(workspace_id)
    source_id = guid(config["source_workspace_id"])
    if source_id == workspace_id:
        raise ValueError("Choose a different target workspace; the source is read-only for this deploy task.")
    capacities = client.listing("/capacities")
    if not bundle:
        active_workspace(client, source_id, capacities)
    target = active_workspace(client, workspace_id, capacities)
    source_items = bundle["source_items"] if bundle else client.listing(f"/workspaces/{source_id}/items")
    target_items = client.listing(f"/workspaces/{workspace_id}/items")
    source_pipeline = find_item(source_items, config["pipeline_name"], "DataPipeline")
    if not source_pipeline:
        raise ValueError(f"Source pipeline '{config['pipeline_name']}' was not found.")
    exports = bundle["exports"] if bundle else export_dependencies(client, source_id, source_items, source_pipeline, config)
    all_content = {identity: dependency_content(export) for identity, export in exports.items()}
    named_refs = set().union(*(named_pipeline_references(content, source_items) for content in all_content.values()))
    all_content["_named_pipeline_refs"] = sorted(named_refs)
    connection_mapping, connections = plan_connections(
        client, all_content, config, bundle["connections"] if bundle else None,
    )
    mapping, managed = reference_plan(
        all_content, source_items, target_items, config, workspace_id, exports, connection_mapping,
    )
    pipeline_names = {}
    for reference in named_refs:
        source = next(item for item in source_items if guid(item["id"]) == reference)
        if reference in exports:
            pipeline_names[source["displayName"]] = source["displayName"]
        else:
            replacement = next((item for item in target_items if guid(item["id"]) == mapping[reference]
                                and item["type"] == "DataPipeline"), None)
            if not replacement:
                raise ValueError(f"Named pipeline override {reference} must identify a target DataPipeline.")
            pipeline_names[source["displayName"]] = replacement["displayName"]
    clusters = client.datasource_clusters() if any(
        export["item"]["type"] == "Dataflow" and export["content"]["queryMetadata.json"].get("connections")
        for export in exports.values()
    ) else {}
    for export in exports.values():
        if export["item"]["type"] == "Dataflow":
            for binding in export["content"]["queryMetadata.json"].get("connections", []):
                target_connection = mapping[connection_binding(binding["connectionId"])[0]]
                if target_connection not in clusters:
                    raise ValueError(f"ClusterId not found for dataflow connection {target_connection}; no target writes made.")
    notebook_path = bundle["notebook_path"] if bundle else ROOT / "notebooks" / "create_ontology_new_experience.ipynb"
    notebook_definition(notebook_path, target, str(uuid.UUID(int=0)), config["lakehouse_name"])
    for name, kind in [(config["lakehouse_name"], "Lakehouse"), (config["notebook_name"], "Notebook")]:
        find_item(target_items, name, kind)
    existing_notebook = find_item(target_items, config["notebook_name"], "Notebook")
    existing_dependencies = {
        identity: find_item(target_items, export["item"]["displayName"], export["item"]["type"])
        for identity, export in exports.items()
    }
    names = [(export["item"]["displayName"], export["item"]["type"]) for export in exports.values()]
    if len(names) != len(set(names)):
        raise ValueError("Dependency exports contain duplicate name/type pairs; deployment would be ambiguous.")
    if not update_existing and (existing_notebook or any(existing_dependencies.values())):
        raise ValueError(
            "A managed notebook, pipeline or dataflow already exists. Deployment stopped before writes. "
            "Review it, then use --update-existing to explicitly replace its definition."
        )
    platforms = {}
    for identity, export in exports.items():
        if export["item"]["type"] != "Dataflow":
            continue
        existing = existing_dependencies[identity]
        if existing:
            saved = client.call(
                "POST", f"/workspaces/{workspace_id}/items/{existing['id']}/getDefinition", {}, result=True,
            )
            platform = dataflow_content(saved["definition"])[".platform"]
        else:
            platform = json.loads(json.dumps(export["content"][".platform"]))
            platform.setdefault("config", {})["logicalId"] = str(uuid.uuid4())
        platforms[identity] = platform
    preview_mapping = {**mapping, **{reference: str(uuid.UUID(int=0)) for reference in managed}}
    for identity, export in exports.items():
        if export["item"]["type"] == "Dataflow":
            dataflow_definition(export["content"], preview_mapping, clusters, platforms[identity])
    LOG.info("Preflight passed. Deploying to %s", target["displayName"])
    lakehouse = ensure_item(client, workspace_id, target_items, config["lakehouse_name"], "Lakehouse")
    definition = notebook_definition(notebook_path, target, lakehouse["id"], config["lakehouse_name"])
    notebook = ensure_item(client, workspace_id, target_items, config["notebook_name"], "Notebook", definition)
    if existing_notebook:
        client.call("POST", f"/workspaces/{workspace_id}/items/{notebook['id']}/updateDefinition",
                    {"definition": definition})
    ids = {"Lakehouse": lakehouse["id"], "Notebook": notebook["id"]}
    if "SQLEndpoint" in managed.values():
        deadline = time.monotonic() + 300
        while True:
            details = client.call("GET", f"/workspaces/{workspace_id}/lakehouses/{lakehouse['id']}")
            endpoint = details.get("properties", {}).get("sqlEndpointProperties", {}).get("id")
            if endpoint:
                ids["SQLEndpoint"] = endpoint
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Lakehouse SQL endpoint was not provisioned. Rerun with --update-existing later.")
            time.sleep(10)
    deployed = {}
    # Allocate every ID before uploading definitions, so cycles and repeated calls resolve.
    for identity, export in exports.items():
        item = export["item"]
        deployed[identity] = ensure_item(client, workspace_id, target_items, item["displayName"], item["type"])
        ids[identity] = deployed[identity]["id"]
    mapping.update({source: ids[kind] for source, kind in managed.items()})
    mapping.update({identity: deployed[identity]["id"] for identity in exports})
    for identity, export in exports.items():
        if export["item"]["type"] != "Dataflow":
            continue
        definition = dataflow_definition(export["content"], mapping, clusters, platforms[identity])
        item_id = deployed[identity]["id"]
        LOG.info("Skipping M preview: target raw tables may not exist until the workflow is run separately.")
        client.call("POST", f"/workspaces/{workspace_id}/dataflows/{item_id}/updateDefinition?updateMetadata=true",
                    {"definition": definition})
        saved = client.call("POST", f"/workspaces/{workspace_id}/dataflows/{item_id}/getDefinition", {}, result=True)
        expected = dataflow_content(definition)
        actual = dataflow_content(saved["definition"])
        expected_connections = expected["queryMetadata.json"].get("connections", [])
        actual_connections = actual["queryMetadata.json"].get("connections", [])
        normalize = lambda bindings: sorted(
            (binding["kind"], binding["path"], *connection_binding(binding["connectionId"]))
            for binding in bindings
        )
        queries = actual["queryMetadata.json"].get("queriesMetadata", {})
        expected_queries = expected["queryMetadata.json"].get("queriesMetadata", {})
        disabled_queries_preserved = all(
            query.get("loadEnabled") is not False or queries.get(name, {}).get("loadEnabled") is False
            for name, query in expected_queries.items()
        )
        if (normalize(actual_connections) != normalize(expected_connections)
                or actual["mashup.pq"].replace("\r\n", "\n") != expected["mashup.pq"].replace("\r\n", "\n")
                or set(queries) != set(expected_queries) or not disabled_queries_preserved):
            raise RuntimeError(f"Saved dataflow bindings or M differ from the deployed definition: {item_id}")
    for identity, export in exports.items():
        if export["item"]["type"] != "DataPipeline":
            continue
        deployed_content = remap_pipeline(export["content"], mapping, pipeline_names)
        definition = {"parts": [{
            "path": "pipeline-content.json",
            "payload": base64.b64encode(json.dumps(deployed_content).encode("utf-8")).decode("ascii"),
            "payloadType": "InlineBase64",
        }]}
        client.call("POST", f"/workspaces/{workspace_id}/items/{deployed[identity]['id']}/updateDefinition",
                    {"definition": definition})
    pipeline = deployed[guid(source_pipeline["id"])]
    saved_items = client.listing(f"/workspaces/{workspace_id}/items")
    for item in [lakehouse, notebook, *deployed.values()]:
        saved = find_item(saved_items, item["displayName"], item["type"])
        if not saved or saved["id"] != item["id"]:
            raise RuntimeError(f"Deployment inventory verification failed for {item['displayName']}.")
    return {
        "workspace_url": f"https://app.fabric.microsoft.com/groups/{workspace_id}/list",
        "lakehouse_id": lakehouse["id"],
        "notebook_id": notebook["id"],
        "pipeline_id": pipeline["id"],
        "deployed_dependencies": [
            {"source_id": identity, "target_id": item["id"], "name": item["displayName"], "type": item["type"]}
            for identity, item in deployed.items()
        ],
        "connections": connections,
        "jobs_started": False,
        "lakehouse_data_copied": False,
        "definition_source": "standalone_package" if bundle else "live_workspace",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True, help="Target workspace GUID")
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("deploy-config.json"))
    packaged_manifest = Path(__file__).with_name("package") / "manifest.json"
    parser.add_argument("--package", type=Path, default=packaged_manifest if packaged_manifest.is_file() else None,
                        help="Standalone manifest; no source workspace/connection access is required")
    parser.add_argument("--update-existing", action="store_true",
                        help="Explicitly replace existing managed notebook/pipeline/dataflow definitions")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        result = deploy(FabricClient(), args.workspace_id, config,
                        update_existing=args.update_existing, package=args.package)
    except (RuntimeError, ValueError, KeyError, OSError, TimeoutError) as error:
        LOG.error("Deployment failed: %s", error)
        LOG.error("No rollback or automatic POST retry was attempted; partial created items may remain.")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
