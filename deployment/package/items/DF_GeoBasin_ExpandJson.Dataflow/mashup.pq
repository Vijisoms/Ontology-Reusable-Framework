section Section1;
shared fxExpandProperties = (Input as table, ExpectedKeys as list) as table =>
let
    Parsed = Table.AddColumn(
        Input,
        "__properties",
        each
            let
                EntityId = [id],
                Value = Json.Document([properties_json]),
                Checked = if not Value.Is(Value, type record) then
                    error Error.Record("InvalidEntityProperties", "Expected one JSON object per entity.", [id = EntityId])
                else if List.AnyTrue(List.Transform(Record.FieldValues(Value), each Value.Is(_, type list) or Value.Is(_, type record))) then
                    error Error.Record("NestedEntityProperties", "Nested objects and arrays require an explicit transformation.", [id = EntityId])
                else Value,
                Unexpected = List.Difference(Record.FieldNames(Checked), ExpectedKeys)
            in
                if not List.IsEmpty(Unexpected) then
                    error Error.Record("PropertiesSchemaChanged", "New JSON keys require updating the authored output schema.", [id = EntityId, keys = Unexpected])
                else Record.SelectFields(Checked, ExpectedKeys, MissingField.UseNull),
        type record
    ),
    Names = List.Transform(ExpectedKeys, each "prop_" & Text.Lower(Text.Combine(
        List.Select(Text.SplitAny(_, " /-().,:;[]{}"), each _ <> ""), "_"
    ))),
    CheckedNames = if List.Count(List.Distinct(List.Transform(Names, Text.Lower))) <> List.Count(Names)
        or not List.IsEmpty(List.Intersect({List.Transform(Names, Text.Lower), List.Transform(Table.ColumnNames(Input), Text.Lower)}))
        then error "Expanded property column names are not unique." else Names,
    Expanded = Table.ExpandRecordColumn(Parsed, "__properties", ExpectedKeys, CheckedNames),
    Typed = Table.TransformColumnTypes(
        Expanded,
        List.Transform(Table.ColumnNames(Expanded), each {_, if List.Contains({"lon", "lat"}, _) then type number else type text}),
        "en-US"
    )
in
    Typed;

shared EntityLakehouse = let
    Root = Lakehouse.Contents(),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data]
in Lakehouse;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "Basin_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared Basin = fxExpandProperties(
    EntityLakehouse{[Name = "raw.Basin", ItemKind = "Table"]}[Data],
    {"Age", "Area", "Basin floor", "Boreholes in the layer", "Mapped in", "Outline", "State"}
);
shared Basin_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "Basin", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "CoreImage_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared CoreImage = fxExpandProperties(
    EntityLakehouse{[Name = "raw.CoreImage", ItemKind = "Table"]}[Data],
    {"Depth", "Depths", "Formation", "Image", "Panel", "Play interval", "Product", "Unit", "Well", "Wells"}
);
shared CoreImage_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "CoreImage", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "Document_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared Document = fxExpandProperties(
    EntityLakehouse{[Name = "raw.Document", ItemKind = "Table"]}[Data],
    {"Licence", "Pages", "Source", "Type"}
);
shared Document_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "Document", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "HydroInterval_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared HydroInterval = fxExpandProperties(
    EntityLakehouse{[Name = "raw.HydroInterval", ItemKind = "Table"]}[Data],
    {"Interval"}
);
shared HydroInterval_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "HydroInterval", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "Hypothesis_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared Hypothesis = fxExpandProperties(
    EntityLakehouse{[Name = "raw.Hypothesis", ItemKind = "Table"]}[Data],
    {"Kind", "Verdict rule"}
);
shared Hypothesis_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "Hypothesis", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "PetroleumRole_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared PetroleumRole = fxExpandProperties(
    EntityLakehouse{[Name = "raw.PetroleumRole", ItemKind = "Table"]}[Data],
    {"Meaning", "Role"}
);
shared PetroleumRole_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "PetroleumRole", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "PlayInterval_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared PlayInterval = fxExpandProperties(
    EntityLakehouse{[Name = "raw.PlayInterval", ItemKind = "Table"]}[Data],
    {"Age", "Depositional environment", "Lithology", "Period", "Petroleum roles", "Province", "Resource flags"}
);
shared PlayInterval_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "PlayInterval", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "ResourceFlag_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared ResourceFlag = fxExpandProperties(
    EntityLakehouse{[Name = "raw.ResourceFlag", ItemKind = "Table"]}[Data],
    {"Flag", "Meaning"}
);
shared ResourceFlag_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "ResourceFlag", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "StratigraphicChart_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared StratigraphicChart = fxExpandProperties(
    EntityLakehouse{[Name = "raw.StratigraphicChart", ItemKind = "Table"]}[Data],
    {"Image", "Product"}
);
shared StratigraphicChart_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "StratigraphicChart", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "StratigraphicUnit_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared StratigraphicUnit = fxExpandProperties(
    EntityLakehouse{[Name = "raw.StratigraphicUnit", ItemKind = "Table"]}[Data],
    {"Age", "Depositional environment", "Hydrostratigraphy", "Lithology", "Mapped in", "Petroleum role", "Province", "Rank", "Resource flag"}
);
shared StratigraphicUnit_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "StratigraphicUnit", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "StructuralElement_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared StructuralElement = fxExpandProperties(
    EntityLakehouse{[Name = "raw.StructuralElement", ItemKind = "Table"]}[Data],
    {"Basin floor nearby", "Position", "Type"}
);
shared StructuralElement_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "StructuralElement", ItemKind = "Table"]}?[Data]?
in Target;
[DataDestinations = {[
    Definition = [Kind = "Reference", QueryName = "Wellbore_DataDestination", IsNewTarget = true],
    Settings = [Kind = "Automatic", TypeSettings = [Kind = "Table"]]
]}]
shared Wellbore = fxExpandProperties(
    EntityLakehouse{[Name = "raw.Wellbore", ItemKind = "Table"]}[Data],
    {"Operator", "Package", "Position", "Result", "Scanned report", "Total depth", "Type", "Year"}
);
shared Wellbore_DataDestination = let
    Root = Lakehouse.Contents([HierarchicalNavigation = null, CreateNavigationProperties = false, EnableFolding = false]),
    Workspace = Root{[workspaceId = "2a5449be-c5ac-5850-8580-f0700a57903d"]}[Data],
    Lakehouse = Workspace{[lakehouseId = "6f6c49b7-d05e-5fc8-a324-2ad38b2aeac0"]}[Data],
    Target = Lakehouse{[Id = "Wellbore", ItemKind = "Table"]}?[Data]?
in Target;
