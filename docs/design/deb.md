# Hermeto support for prefetching Debian/Ubuntu .deb packages - design

## Overview

Many container base images use Debian-based distributions (Debian, Ubuntu, etc.).
Containers install `.deb` packages via APT at build time, but hermetic builds
cannot access the network during the build step. This feature solves the
problem by pre-fetching `.deb` packages before the build, following the same
pattern established by the [RPM backend](rpm.md).

The general idea: the developer specifies which `.deb` packages they need in a
`debs.lock.yaml` lockfile. In a step before the actual build, hermeto downloads
all packages into `deps/deb/` and prepares a local APT repository that is
available during the build.

APT package management documentation:
- [Debian package management](https://www.debian.org/doc/manuals/debian-reference/ch02.en.html)
- [APT documentation](https://manpages.debian.org/bookworm/apt/apt.8.en.html)
- [Ubuntu packages](https://packages.ubuntu.com/)

### Developer Workflow

1. **Prerequisites**: A Debian-based system (or container) with `apt` installed.
   No additional tooling is strictly required to use hermeto's deb backend,
   though generating the lockfile requires access to `apt-cache` or similar
   tools (see [Dependency List Toolchain](#dependency-list-toolchain-optional)).
2. **Adding dependencies**: Packages are declared in a `debs.lock.yaml` file
   with pinned URLs, checksums, and architecture information.
3. **Build process**: `hermeto fetch-deps` downloads declared packages.
   `hermeto inject-files` generates the APT metadata so the build can install
   packages offline.

### How the Package Manager Works

- **Registry/repository model**: Packages are hosted in APT repositories
  (e.g., `deb.debian.org`, `archive.ubuntu.com`). Each repository provides
  a `Packages` index listing available `.deb` files with their metadata.
- **Package identity and versioning**: Packages are identified by name,
  version, and architecture (e.g., `libssl3_3.0.9-1_amd64.deb`).
- **Dependency resolution**: APT resolves dependencies; hermeto does not.
  Users must include all transitive dependencies in the lockfile, same as RPM.

## Design

### Scope

**In scope**:
- Fetching `.deb` binary packages from URLs listed in a lockfile
- Fetching `.dsc`/`.orig.tar.*`/`.debian.tar.*` source entries (for source
  container generation, analogous to RPM source entries)
- Checksum and size verification of downloaded files
- SBOM generation with `pkg:deb` PURLs per the
  [PURL specification](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#deb)
- Generating a local APT repository via `dpkg-scanpackages` during
  `inject-files` (the minimal viable approach)
- Landing as `x-deb` (experimental), following the same path as the Maven
  backend (`x-maven`)

**Out of scope** (for the initial implementation):
- Dependency resolution -- users must list all transitive dependencies
- Parsing existing APT repository metadata or `Packages` indices
- `apt-ftparchive` / `Release` / `InRelease` generation -- the minimal
  `dpkg-scanpackages` approach is sufficient for APT offline installs when
  the repository is configured with `[trusted=yes]` (see
  [Build Environment Config](#build-environment-config))
- Automatic lockfile generation (see
  [Dependency List Toolchain](#dependency-list-toolchain-optional) for
  discussion)
- GPG signature verification of repository metadata (hermeto verifies
  individual file checksums instead, consistent with the RPM backend)

### Dependency List Generation

#### Dependency List Toolchain [optional]

There is currently no dedicated lockfile generator for `.deb` packages
analogous to `rpm-lockfile-prototype` for RPMs. Users must construct the
`debs.lock.yaml` manually, using output from tools such as:

- `apt-cache show <package>` -- provides version, architecture, SHA256,
  and download URL
- `apt-cache depends <package>` -- lists transitive dependencies
- `apt download --print-uris <package>` -- prints the download URL and
  checksum for a package

A lockfile generator tool is desirable future work but is outside the
scope of this backend implementation.

#### Dependency List Format [optional]

The lockfile format mirrors the RPM lockfile, adapted for the deb
ecosystem:

```yaml
lockfileVersion: 1
lockfileVendor: <distro>
arches:
  - arch: <arch>
    packages:
      - repoid: <repoid>
        url: <url>
        checksum: <method>:<digest>
        size: <size>
    source:
      - repoid: <repoid-source>
        url: <url>
        checksum: <method>:<digest>
        size: <size>
```

- **lockfileVersion**: Integer, must be `1`.
- **lockfileVendor**: String identifying the distribution. Per the PURL
  specification, `pkg:deb/debian/` and `pkg:deb/ubuntu/` are distinct
  namespaces, so the vendor should match the distribution providing the
  packages -- e.g., `"debian"` or `"ubuntu"`. This keeps SBOM PURLs
  accurate and aligned with vulnerability databases that index by
  PURL namespace.
- **arches**: List of architecture entries. The `arch` value corresponds
  to Debian architecture names (e.g., `amd64`, `arm64`, `i386`, `all`).
- **packages**: List of `.deb` binary packages to fetch.
- **source**: List of source package entries (`.dsc` files, orig tarballs,
  etc.) for source container generation.

Fields per package:
- **repoid** (optional): Repository identifier, used for organizing the
  output directory structure and the generated `sources.list`. When not
  provided, hermeto generates a random identifier (same behavior as RPM).
- **url** (required): Direct download URL for the `.deb` file.
- **checksum** (optional): Format `<algorithm>:<hex-digest>`, e.g.,
  `sha256:abc123...`. When provided, hermeto verifies the downloaded file
  against this checksum. When absent, the component is marked with
  `hermeto:missing_hash:in_file` in the SBOM.
- **size** (optional): Expected file size in bytes.

Example:

```yaml
lockfileVersion: 1
lockfileVendor: debian
arches:
  - arch: amd64
    packages:
      - repoid: bookworm-main
        url: https://deb.debian.org/debian/pool/main/libs/libsodium/libsodium23_1.0.18-1_amd64.deb
        checksum: sha256:7eb8e859c483cd4aa85e8e2e1005b1e205b314e16ad032f2fbd2cd6d35e6f35c
        size: 163084
      - repoid: bookworm-main
        url: https://deb.debian.org/debian/pool/main/o/openssl/libssl3_3.0.9-1_amd64.deb
        checksum: sha256:a49b38d8c8a0a8f3bb8e5afe97f4c35ce5d5b8cb2ca58faae86a63a49c32ad72
        size: 2012456
    source:
      - repoid: bookworm-main-src
        url: https://deb.debian.org/debian/pool/main/o/openssl/openssl_3.0.9-1.dsc
        checksum: sha256:dfc4d1c5b3da1d6b15c243f0e47e8ed14e75ca4e4ddc36cd5f6badc2abba08c1
        size: 2726
```

#### Checksum Generation [optional]

- **Native checksum support**: APT repositories include SHA256 checksums
  in their `Packages` metadata. Users can extract these via `apt-cache
  show` or from the `Packages` index directly.
- **Checksum algorithms**: SHA-256 is the standard. hermeto supports any
  algorithm available through Python's `hashlib` (same as RPM).
- **Missing checksum handling**: When a package entry in the lockfile
  omits the `checksum` field, hermeto still downloads the file but marks
  the corresponding SBOM component with the
  `hermeto:missing_hash:in_file` property.

### Fetching Content

#### Native vs. Hermeto Fetch [optional]

Hermeto downloads packages directly from the URLs in the lockfile.
APT is not invoked during fetching -- APT's plugin and hook system can
execute arbitrary code, violating hermeto's no-arbitrary-code-execution
principle. This is the same approach used by the RPM backend.

#### Project Structure [optional]

Output directory layout:

```
<output>/deps/deb/<arch>/<repoid>/*.deb
<output>/deps/deb/<arch>/<repoid-source>/*.dsc
<output>/deps/deb/<arch>/<repoid-source>/*.orig.tar.*
<output>/deps/deb/<arch>/<repoid-source>/*.debian.tar.*
<output>/bom.json
```

After `inject-files`:

```
<output>/deps/deb/<arch>/sources.list.d/hermeto.list
<output>/deps/deb/<arch>/<repoid>/Packages
<output>/deps/deb/<arch>/<repoid>/Packages.gz
```

#### Network Requirements [optional]

- **Registry endpoints**: Any HTTP/HTTPS URL hosting `.deb` files.
  Standard mirrors include `deb.debian.org`, `archive.ubuntu.com`, and
  corporate internal mirrors.
- **Authentication**: Supported via hermeto's existing SSL/TLS client
  certificate options (same as RPM).
- **Rate limiting**: Standard HTTP retry logic via hermeto's async
  download infrastructure.

### Build Environment Config

#### Environment Variables

No environment variables are needed. APT is configured via `sources.list`
files rather than environment variables.

#### Configuration Files

The `inject-files` step generates APT metadata so the build container can
install pre-fetched packages offline:

1. **`Packages` index**: Generated by running `dpkg-scanpackages` on each
   repoid directory. This is the minimal metadata APT needs to discover
   available packages.
2. **`sources.list` file**: A repository configuration file pointing APT
   at the local package directories.

Content of the generated `hermeto.list` file:

```
deb [trusted=yes] file://<for-output-dir>/deps/deb/<arch>/<repoid> ./
```

The `[trusted=yes]` option tells APT to skip GPG signature verification
for this repository. This is acceptable because hermeto has already
verified each package's checksum individually. Generating signed
`Release`/`InRelease` files (via `apt-ftparchive`) would add complexity
and require managing GPG keys without meaningful security benefit on top
of per-file checksum verification.

The `sources.list.d/` directory for the corresponding architecture can
be mounted into the build container as `/etc/apt/sources.list.d/`.

#### Build Process Integration [optional]

No Dockerfile changes are needed beyond mounting the pre-fetched
dependencies and the generated `sources.list`. Example usage in a
multi-stage build:

```dockerfile
# Install pre-fetched packages
COPY hermeto-output/deps/deb/amd64/ /tmp/deb/
COPY hermeto-output/deps/deb/amd64/sources.list.d/ /etc/apt/sources.list.d/
RUN apt-get update && apt-get install -y libssl3
```

### PURL Generation

PURLs follow the [PURL specification for deb packages](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#deb):

```
pkg:deb/<distro>/<name>@<version>?arch=<arch>&repository_id=<repoid>
```

The `<distro>` namespace comes from the `lockfileVendor` field, ensuring
accuracy per the PURL spec (e.g., `pkg:deb/debian/libssl3@3.0.9-1` vs.
`pkg:deb/ubuntu/libssl3@3.0.9-1ubuntu1`).

Unlike RPM, `.deb` files do not embed a vendor tag. The lockfileVendor
field is the authoritative source for the PURL namespace.

Example SBOM component:

```json
{
  "bom-ref": "pkg:deb/debian/libssl3@3.0.9-1?arch=amd64&repository_id=bookworm-main",
  "name": "libssl3",
  "purl": "pkg:deb/debian/libssl3@3.0.9-1?arch=amd64&repository_id=bookworm-main",
  "version": "3.0.9-1",
  "properties": [{"name": "hermeto:found_by", "value": "hermeto"}],
  "type": "library"
}
```

## Implementation Notes

The backend lands as `x-deb` (experimental prefix) following the established
pattern for new backends. Components produced by the backend are identified
through a document-level annotation `hermeto:backend:experimental:x-deb` in
generated SBOMs.

### Implementation Plan

The implementation closely follows the RPM backend structure:

1. **Lockfile model** (`hermeto/core/package_managers/deb/models.py`):
   Pydantic models for `debs.lock.yaml` parsing, similar to
   `hermeto/core/package_managers/rpm/redhat.py`. The `lockfileVendor`
   validator accepts any string (unlike RPM's `"redhat"` restriction),
   since the deb ecosystem spans multiple distributions.

2. **Main module** (`hermeto/core/package_managers/deb/main.py`):
   - `fetch_deb_source()`: Entry point, iterates over deb packages in the
     request, calls the project resolver.
   - `_resolve_deb_project()`: Reads and validates the lockfile, downloads
     packages, verifies checksums, generates SBOM components. Reuses
     `async_download_files` from `hermeto.core.package_managers.general`.
   - `_verify_downloaded()`: Size and checksum verification (same logic
     as RPM).
   - `_generate_sbom_components()`: Extracts package name, version, and
     architecture from `.deb` filenames or metadata, generates `pkg:deb`
     PURLs.
   - `inject_files_post()`: Runs `dpkg-scanpackages` on each repoid
     directory to generate `Packages` indices, then writes a
     `hermeto.list` file to `sources.list.d/`.

3. **Input model** (`hermeto/core/models/input.py`):
   - Add `"x-deb"` to `PackageManagerType`
   - Add `DebPackageInput` class
   - Add `deb_packages` property to `Request`
   - Add to `PackageInput` union

4. **Resolver registration** (`hermeto/core/resolver.py`):
   - Import deb backend
   - Register `"x-deb": deb.fetch_deb_source` in `_package_managers`
   - Wire `inject_files_post` callback

5. **Package metadata extraction**: Unlike RPM (which uses the `rpm`
   command to query tags from `.rpm` files), `.deb` metadata extraction
   uses `dpkg-deb --showformat` or the `ar`/`tar` approach to read
   the `control` file. The `dpkg-deb` tool is standard on Debian-based
   systems.

### Current Limitations

- **No lockfile generator**: Users must manually construct `debs.lock.yaml`.
  A generator tool (analogous to `rpm-lockfile-prototype`) is desirable
  future work.
- **`dpkg-scanpackages` required**: The `inject-files` step requires
  `dpkg-scanpackages` to be available. It is provided by the `dpkg-dev`
  package on Debian-based systems. On non-Debian build hosts, this tool
  may not be available.
- **`dpkg-deb` required**: Package metadata extraction requires `dpkg-deb`.
  This is standard on Debian-based systems and available in most
  container images used for building.
- **No `Release`/`InRelease` generation**: The minimal `Packages`-only
  approach requires `[trusted=yes]` in the sources.list entry. This is
  functionally sufficient for hermetic builds but means APT will not
  verify repository-level signatures (per-file checksums are verified
  by hermeto).
- **Mixed-distro lockfiles**: A single lockfile can only declare one
  `lockfileVendor`. Users building images that combine packages from
  both Debian and Ubuntu must use separate lockfiles (one per distro),
  each passed as a separate package input to hermeto. This ensures
  PURL namespaces remain accurate.

## References [optional]

- [PURL specification - deb type](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#deb)
- [Debian repository format](https://wiki.debian.org/DebianRepository/Format)
- [dpkg-scanpackages manpage](https://manpages.debian.org/bookworm/dpkg-dev/dpkg-scanpackages.1.en.html)
- [dpkg-deb manpage](https://manpages.debian.org/bookworm/dpkg/dpkg-deb.1.en.html)
- [APT sources.list format](https://manpages.debian.org/bookworm/apt/sources.list.5.en.html)
- [RPM backend design](rpm.md) -- reference implementation
- [Package manager design template](package-manager-template.md)
- [hermetoproject/hermeto#1393](https://github.com/hermetoproject/hermeto/issues/1393) -- original upstream issue
