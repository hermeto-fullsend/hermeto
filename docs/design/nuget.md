# NuGet Design Document

## Overview

[NuGet](https://learn.microsoft.com/en-us/nuget/what-is-nuget) is the package manager for
.NET. It is the primary mechanism for sharing and consuming .NET libraries and tools.
Packages are hosted on [nuget.org](https://www.nuget.org/), which serves as the default
public registry. NuGet is integrated into the .NET SDK and Visual Studio, making it the
standard dependency management tool across the .NET ecosystem.

### Developer Workflow

1. **Prerequisites**: Developers install the [.NET SDK](https://dotnet.microsoft.com/),
   which includes the `dotnet` CLI and NuGet client. Projects are created with
   `dotnet new`.
2. **Adding dependencies**: Dependencies are declared as
   [`<PackageReference>`](https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files)
   elements in project files (`.csproj`, `.fsproj`, `.vbproj`):

   ```xml
   <ItemGroup>
     <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
   </ItemGroup>
   ```

3. **Dependency management**: `dotnet add package`, `dotnet remove package`, and
   `dotnet list package` manage dependencies. `dotnet restore` resolves and downloads
   all dependencies.
4. **Build process**: `dotnet build` compiles the project. NuGet restore runs
   automatically as part of `dotnet build` unless explicitly disabled.

### How the Package Manager Works

- **Registry model**: Packages are hosted on [nuget.org](https://www.nuget.org/) by
  default. The registry exposes a
  [V3 REST API](https://learn.microsoft.com/en-us/nuget/api/overview) with a service
  index at `https://api.nuget.org/v3/index.json`. Private feeds and alternative
  registries are supported via
  [NuGet.Config](https://learn.microsoft.com/en-us/nuget/reference/nuget-config-file).
- **Package identity and versioning**: Packages are identified by a case-insensitive ID
  and a [SemVer 2.0.0](https://semver.org/) version. A package is a ZIP archive with a
  `.nupkg` extension containing compiled assemblies and a
  [`.nuspec`](https://learn.microsoft.com/en-us/nuget/reference/nuspec) XML manifest.
- **Dependency resolution**: The NuGet client resolves dependencies transitively. Resolved
  packages are stored in a
  [global packages folder](https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders)
  (default: `~/.nuget/packages`) shared across projects.
- **Configuration**: Behavior is controlled through
  [`NuGet.Config`](https://learn.microsoft.com/en-us/nuget/reference/nuget-config-file)
  files (hierarchically merged) and
  [environment variables](https://learn.microsoft.com/en-us/nuget/reference/cli-reference/cli-ref-environment-variables)
  such as `NUGET_PACKAGES`.

## Design

### Scope

**In scope:**

- Modern SDK-style projects using `PackageReference` in project files (`.csproj`,
  `.fsproj`, `.vbproj`). These are the standard for .NET Core, .NET 5+, and .NET Standard
  projects.
- The `packages.lock.json`
  [lockfile format](https://devblogs.microsoft.com/dotnet/enable-repeatable-package-restores-using-a-lock-file/).
- Packages from the default nuget.org registry and custom NuGet V3-compatible feeds.

**Out of scope:**

- Legacy `packages.config` format. It has no lockfile support and uses a flat dependency
  model that does not track transitive dependencies or content hashes.
  [Migration to PackageReference](https://learn.microsoft.com/en-us/nuget/consume-packages/migrate-packages-config-to-package-reference)
  is the recommended path.
- NuGet V2 API feeds (deprecated).
- Building or compiling .NET projects — Hermeto only prefetches and verifies packages.

**Edge cases:**

- Multi-targeting projects produce a `packages.lock.json` with multiple target framework
  sections. Hermeto must handle all of them, deduplicating packages that appear under
  multiple frameworks.
- [Central Package Management](https://learn.microsoft.com/en-us/nuget/consume-packages/central-package-management)
  via `Directory.Packages.props` uses `CentralTransitive` dependency types in the lockfile.
  Hermeto should treat these the same as `Transitive` for fetching purposes.

### Dependency List Generation

#### Dependency List Toolchain

The lockfile is generated natively by the NuGet client:

```bash
dotnet restore --use-lock-file
```

Alternatively, the developer enables lockfile generation permanently by adding
a property to the project file or `Directory.Build.props`:

```xml
<PropertyGroup>
  <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>
</PropertyGroup>
```

The `packages.lock.json` file is widely supported and stable (the format has been
available since NuGet 4.9 / .NET Core SDK 2.1.500). In CI, setting
[`RestoreLockedMode`](https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files#locking-dependencies)
to `true` ensures that restore fails with error `NU1004` if the lockfile is out of date
rather than silently updating it.

#### Dependency List Format

The lockfile is a JSON file named `packages.lock.json` located at the project root.

**Structure:**

```json
{
  "version": 1,
  "dependencies": {
    "net8.0": {
      "Newtonsoft.Json": {
        "type": "Direct",
        "requested": "[13.0.3, )",
        "resolved": "13.0.3",
        "contentHash": "HrC5BXdl00IP9zeV+0Z848QWPAoCr9P3bDEZguI+gkLcBKAOxix/tLEAAHC+UvDNPv4a2d18lOReHMOagPa+zQ==",
        "dependencies": {
        }
      },
      "Microsoft.CSharp": {
        "type": "Transitive",
        "resolved": "4.7.0",
        "contentHash": "pTj+D3uJWyN3My70i2Hqo+OXixq3Os2D1nJ2x92FFo6sk8fYS1m1WLNTs8Dc1ITzIBFx+eMQ20T/rVnIzEgw==",
        "dependencies": {
        }
      }
    }
  }
}
```

**Required fields per dependency:**

| Field | Description | Present for |
|-------|-------------|-------------|
| `type` | `Direct`, `Transitive`, `Project`, or `CentralTransitive` | All |
| `resolved` | The exact resolved version (e.g., `13.0.3`) | All |
| `contentHash` | Base64-encoded SHA-512 hash of the package content | All non-project |
| `dependencies` | Map of the package's own dependencies (`"id": "version"`) | All |
| `requested` | Version range from the project file (e.g., `[13.0.3, )`) | `Direct` only |

The top-level key under `dependencies` is the
[Target Framework Moniker](https://learn.microsoft.com/en-us/dotnet/standard/frameworks)
(TFM), such as `net8.0` or `net6.0`. Multi-targeting projects have multiple TFM sections.

#### Checksum Generation

- **Native checksum support**: Yes. The `contentHash` field in `packages.lock.json`
  provides a checksum for every non-project dependency.
- **Checksum algorithm**: SHA-512, base64-encoded.
- **Checksum source**: Computed by the NuGet client during restore. The hash covers the
  package content **excluding the package signature file** (`.signature.p7s`). This
  ensures the hash remains stable when packages are re-signed (e.g., repository
  signatures added by nuget.org after publication).
- **Missing checksum handling**: Every resolved package in the lockfile has a
  `contentHash`. Project-type dependencies (local project references) do not have
  checksums and should be excluded from fetching.

> [!IMPORTANT]
> Because the `contentHash` excludes signature content, verifying it requires
> re-computing the hash with the same exclusion logic — a raw SHA-512 of the downloaded
> `.nupkg` file will not match. Hermeto must unzip the `.nupkg`, exclude
> `.signature.p7s`, and hash the remaining content using the same algorithm NuGet uses
> internally (via sorted entry enumeration). The exact algorithm is documented in the
> [NuGet client source](https://github.com/NuGet/NuGet.Client).

### Fetching Content

#### Native vs. Hermeto Fetch

Hermeto should fetch packages directly rather than delegating to `dotnet restore`. The
`dotnet restore` command can execute MSBuild targets and NuGet plugins, which could run
arbitrary code. This violates Hermeto's no-arbitrary-code-execution principle.

The NuGet V3 API is well-documented and straightforward. Downloading `.nupkg` files
requires only HTTP GET requests — no authentication for public packages on nuget.org, and
standard HTTP authentication for private feeds.

#### Project Structure

A typical .NET project using NuGet with lockfile:

```
project.git/
├── MyProject.csproj          # PackageReference declarations
├── packages.lock.json        # Lockfile (committed to source control)
├── NuGet.Config               # Optional: package source configuration
├── Directory.Build.props      # Optional: shared MSBuild properties
└── src/
    └── Program.cs
```

The NuGet global packages cache (where restored packages are stored):

```
~/.nuget/packages/
├── newtonsoft.json/
│   └── 13.0.3/
│       ├── newtonsoft.json.13.0.3.nupkg
│       ├── newtonsoft.json.nuspec
│       ├── .nupkg.metadata
│       └── lib/
│           ├── net6.0/
│           │   └── Newtonsoft.Json.dll
│           └── netstandard2.0/
│               └── Newtonsoft.Json.dll
└── microsoft.csharp/
    └── 4.7.0/
        └── ...
```

#### File Formats and Metadata

- **Package format**: `.nupkg` files are ZIP archives. They contain compiled assemblies,
  a `.nuspec` XML manifest, and optional content such as analyzers, build scripts, and
  native libraries.
- **Naming conventions**: Packages are stored in the global packages folder under
  `{lowercase-id}/{version}/`. The `.nupkg` file is named
  `{lowercase-id}.{version}.nupkg`.
- **Metadata file**: NuGet creates a `.nupkg.metadata` JSON file alongside each cached
  package containing the `contentHash` and source URL:

  ```json
  {
    "version": 2,
    "contentHash": "HrC5BXdl00IP9zeV+0Z848QWPAoCr9P3bDEZguI+...",
    "source": "https://api.nuget.org/v3/index.json"
  }
  ```

#### Network Requirements

- **Service index**: `https://api.nuget.org/v3/index.json` — the entry point for the V3
  API. Returns a JSON document listing available resources by `@type`.
- **Package downloads**: The
  [PackageBaseAddress](https://learn.microsoft.com/en-us/nuget/api/package-base-address-resource)
  resource (flat container) provides direct download URLs:

  ```
  GET {base-url}/{LOWER_ID}/{LOWER_VERSION}/{LOWER_ID}.{LOWER_VERSION}.nupkg
  ```

  For nuget.org, the base URL is `https://api.nuget.org/v3-flatcontainer/`.
  Both the package ID and version must be lowercased.
- **Authentication**: Not required for public nuget.org packages. Private feeds may
  require API keys or HTTP basic authentication, configurable via `NuGet.Config`
  [`<packageSourceCredentials>`](https://learn.microsoft.com/en-us/nuget/reference/nuget-config-file#packagesourcecredentials).
- **Rate limiting**: nuget.org does not publish formal rate limits but throttles abusive
  clients. Hermeto should implement reasonable concurrency limits and respect HTTP 429
  responses.
- **Mirror support**: Alternative feeds are supported via `NuGet.Config`
  `<packageSources>`. Hermeto should allow users to specify a custom service index URL.

### Build Environment Config

#### Environment Variables

| Variable Name | Purpose | Example Value | Required |
|---------------|---------|---------------|----------|
| `NUGET_PACKAGES` | Points to the pre-populated global packages folder | `/path/to/hermeto-output/deps/nuget` | Yes |
| `DOTNET_NOLOGO` | Suppresses .NET welcome message | `true` | No |
| `MSBUILDDISABLENODEREUSE` | Disables MSBuild node reuse for clean builds | `1` | No |

Setting `NUGET_PACKAGES` to the Hermeto output directory makes `dotnet restore --locked-mode`
find all packages locally without network access.

#### Configuration Files

Hermeto should generate a `NuGet.Config` file that disables all remote package sources and
points to the pre-fetched packages:

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="hermeto-local" value="{output-dir}/deps/nuget" />
  </packageSources>
  <fallbackPackageFolders>
    <clear />
  </fallbackPackageFolders>
</configuration>
```

The `<clear />` elements are essential — they remove all inherited sources (including
nuget.org) so only the local source populated by Hermeto is used.

Hermeto output directory structure:

```
<output>/
├── deps/
│   └── nuget/
│       ├── newtonsoft.json/
│       │   └── 13.0.3/
│       │       ├── newtonsoft.json.13.0.3.nupkg
│       │       ├── newtonsoft.json.nuspec
│       │       ├── .nupkg.metadata
│       │       └── lib/
│       │           └── ...
│       └── microsoft.csharp/
│           └── 4.7.0/
│               └── ...
├── NuGet.Config
└── bom.json
```

The `deps/nuget` path follows the convention used by other Hermeto backends (e.g.,
`deps/npm`, `deps/rpm`).

#### Build Process Integration

At build time, the developer runs:

```bash
export NUGET_PACKAGES=/path/to/hermeto-output/deps/nuget
dotnet restore --locked-mode --configfile /path/to/hermeto-output/NuGet.Config
dotnet build --no-restore
```

The `--locked-mode` flag ensures the lockfile is not modified. The `--no-restore` flag on
`dotnet build` prevents an implicit restore that might bypass the Hermeto configuration.

## Implementation Notes

The backend should use the `x-nuget` experimental prefix initially, following the Hermeto
convention for new backends.

### SBOM Generation

PURLs for NuGet packages follow the
[PURL specification](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#nuget):

```
pkg:nuget/Newtonsoft.Json@13.0.3
```

The PURL type is `nuget`, the name preserves the original casing from the lockfile, and the
version is the resolved version string. Checksums should be included as qualifiers where
available.

### Content Hash Verification

The primary implementation challenge is reproducing NuGet's `contentHash` computation. The
hash is a SHA-512 over the package content excluding `.signature.p7s`. The exact algorithm
involves:

1. Opening the `.nupkg` as a ZIP archive.
2. Enumerating entries in a sorted, normalized order.
3. Excluding `.signature.p7s` and computing SHA-512 over the remaining content.

The implementation must match NuGet's internal
[`PackageArchiveReader.GetContentHash()`](https://github.com/NuGet/NuGet.Client) behavior
exactly. Differences in entry ordering or normalization will produce mismatched hashes.

If exact reproduction proves infeasible, Hermeto can fall back to recording the hash from
the lockfile in the SBOM without verification, marking those dependencies with
`hermeto:missing_hash:in_file` as other backends do for unverified checksums.

### Current Limitations

- **Missing features**: Private feed authentication is not implemented in the initial
  version. Only public nuget.org packages are supported.
- **Edge cases**: Packages with multiple target frameworks share the same `.nupkg` file.
  Hermeto must deduplicate downloads across TFM sections in the lockfile.
- **Ecosystem considerations**: The `contentHash` algorithm is not formally specified
  outside the NuGet client source code. Changes to the algorithm in future NuGet versions
  could require updates to Hermeto's verification logic.
- **Legacy format**: `packages.config` projects are not supported. Users must
  [migrate to PackageReference](https://learn.microsoft.com/en-us/nuget/consume-packages/migrate-packages-config-to-package-reference)
  before using Hermeto.

## References

- [What is NuGet? - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/what-is-nuget)
- [NuGet V3 API Overview - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/api/overview)
- [Package Base Address (Flat Container) - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/api/package-base-address-resource)
- [NuGet Service Index - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/api/service-index)
- [packages.lock.json - .NET Blog](https://devblogs.microsoft.com/dotnet/enable-repeatable-package-restores-using-a-lock-file/)
- [Lock File Wiki - NuGet/Home](https://github.com/NuGet/Home/wiki/Enable-repeatable-package-restore-using-lock-file)
- [PackageReference in Project Files - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/consume-packages/package-references-in-project-files)
- [.nuspec Reference - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/reference/nuspec)
- [nuget.config Reference - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/reference/nuget-config-file)
- [NuGet CLI Environment Variables - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/reference/cli-reference/cli-ref-environment-variables)
- [Managing Global Packages and Cache Folders - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders)
- [Central Package Management - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/consume-packages/central-package-management)
- [Migrate from packages.config to PackageReference - Microsoft Learn](https://learn.microsoft.com/en-us/nuget/consume-packages/migrate-packages-config-to-package-reference)
- [PURL Type: nuget - PURL Spec](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#nuget)
- [NuGet Client Source Code - GitHub](https://github.com/NuGet/NuGet.Client)
