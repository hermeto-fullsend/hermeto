# [pnpm][]

## Prerequisites

Ensure both package.json and pnpm-lock.yaml are present in the project directory.

The `lockfileVersion` field must be within the range of >= 9.0 and < 10.0. Earlier
versions are not supported. Newer versions could introduce breaking changes to the
lockfile format, so Hermeto might not be able to process them correctly.

pnpm 9 through 12 all write `lockfileVersion: '9.0'`, so Hermeto reads all four.
For pnpm 12 that means prefetch and the SBOM only: the offline `pnpm install`
does not work yet for the projects pnpm 12 creates by default, as
[Build the image offline](#build-the-image-offline) explains.
Since pnpm 12 the file may hold *two* YAML documents instead of one: a
leading env document recording the project's [config dependencies][] and its
[pinned package manager][], followed by the project's own dependency graph. Hermeto
reads every document, so the packages the env document declares — the pinned pnpm
release and its platform binaries — are fetched and reported in the SBOM like any
other dependency. Each document must declare a supported `lockfileVersion` of its
own.

Two documents are the most Hermeto reads; it rejects a longer stream rather than
quietly dropping the documents in the middle. When both documents declare the same
package, they must agree on its `resolution.integrity` — Hermeto cannot tell which
of two tarballs pnpm would install, so it rejects the lockfile instead of guessing.
Running `pnpm install` regenerates a lockfile in either case.

## Usage

Using Hermeto locally with pnpm requires no special configuration or additional
software (including pnpm itself). Hermeto downloads dependencies only explicitly
declared in pnpm-lock.yaml without invoking the pnpm binary, which avoids arbitrary
code execution during prefetch and keeps the process predictable.

Make sure the file is up to date by running pnpm install. Otherwise, the hermetic
build will likely fail.

Each dependency is downloaded as a tar archive into the output directory under deps/pnpm/.
Packages from the npm and JSR registries are stored in separate subdirectories.

```text
hermeto-output/deps/pnpm/
├── npm/
│   ├── react-18.3.1.tgz
│   └── ...
├── jsr/
│   ├── ...
```

Hermeto modifies .npmrc and pnpm-lock.yaml to point registries and tarball URLs
to local files in the output directory. Other settings in .npmrc are preserved.
If the file does not exist, it is created.

These updates happen automatically when calling Hermeto's inject-files command.
See the [Example](#example) section below.

A snippet of .npmrc after modification:

```text
registry=file:///tmp/hermeto-output/deps/pnpm/npm/
@jsr:registry=file:///tmp/hermeto-output/deps/pnpm/jsr/
```

A snippet of pnpm-lock.yaml after modification:

```diff
packages:
  react@18.3.1:
    resolution:
      integrity: sha512-...
-     tarball: https://registry.npmjs.org/react/-/react-18.3.1.tgz
+     tarball: react-18.3.1.tgz
```

## SBOM

Hermeto stores the SBOM in the output directory (hermeto-output/bom.json) with
information about all dependencies. See the [SBOM](sbom.md) page for more details.

## Proxy

Configure the proxy via a config file. See the [Configuration](configuration.md)
page for supported config file locations.

```yaml
pnpm:
  proxy_url: https://my-npm-proxy.example.com
  proxy_login: user
  proxy_password: pass
```

Alternatively, configure the proxy via environment variables:

```shell
export HERMETO_PNPM__PROXY_URL=https://my-npm-proxy.example.com
export HERMETO_PNPM__PROXY_LOGIN=user
export HERMETO_PNPM__PROXY_PASSWORD=pass
```

> [!NOTE]
> The proxy is used only for packages from the official npm registry. Other package
> sources do not work with the proxy.

## Example

### Fetch dependencies

Fetch all dependencies from pnpm-lock.yaml and generate an SBOM.

```shell
hermeto fetch-deps pnpm
tree hermeto-output/deps/pnpm
cat hermeto-output/bom.json
```

### Generate environment variables

> [!NOTE]
> This step is only required when using pnpm v11.3 and later.

Starting with pnpm 11.3, the [trustLockfile][] setting controls whether `pnpm install`
re-applies supply-chain verification checks to lockfile entries. Hermeto injects
pnpm-lock.yaml to point tarball URLs to local files, so this verification must be
skipped during the hermetic build.

Hermeto sets `PNPM_CONFIG_TRUST_LOCKFILE=true` for this purpose.

```shell
hermeto generate-env ./hermeto-output --for-output-dir /tmp/hermeto-output --output hermeto.env
cat hermeto.env
```

### Inject project files

Modify pnpm-lock.yaml and .npmrc for the hermetic build.

```shell
hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
git diff
```

### Build the image offline

> [!IMPORTANT]
> **pnpm 12 and the offline build.** A project whose lockfile carries an env
> document — which is most pnpm 12 projects, since `pnpm init` pins
> `packageManager` by default — cannot complete `pnpm install` offline today,
> whatever Hermeto writes. pnpm resolves the env document's
> `packageManagerDependencies` and `configDependencies` before it reads the
> project's `.npmrc`, using a client that refuses a `file://` registry: it
> fails with `ERR_PNPM_BAD_CONFIG_DEP` ("URL scheme is not allowed") when the
> registry is set, reaches for `registry.npmjs.org` when it is not, and ignores
> the `resolution.tarball` Hermeto writes for those packages.
>
> Prefetch and the SBOM are correct — every package both documents declare is
> downloaded and reported. It is the install step that cannot be made hermetic
> from Hermeto's side. See
> [hermetoproject/hermeto#1802](https://github.com/hermetoproject/hermeto/issues/1802).

This example uses [generic](generic.md) backend artifact to install a specific
version of pnpm using `npm` binary. There is an [official pnpm base image][], but
without `node` runtime and no tags for pnpm v10. Unofficial images on Docker Hub
are also an option.

Dockerfile:

```dockerfile
FROM docker.io/library/node:24

# ...

RUN npm install -g /tmp/hermeto-output/deps/generic/pnpm-11.x.y.tgz

WORKDIR /app
COPY . .

RUN . /tmp/hermeto.env && pnpm install

# ...
```

> [!NOTE]
> When using pnpm v10, you can omit sourcing hermeto.env and the volume mount in
> the command below.
>
> Installing pnpm from a [generic](generic.md) artifact, as above, applies to
> pnpm 11 and earlier. A pnpm 12 project pins its package manager in the
> lockfile's env document instead, and Hermeto fetches it from there — but see
> the limitation above before relying on that for an offline build.

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag pnpm-app
```

For more detailed instructions, see the [Usage](usage.md) page or run `hermeto --help`.

[pnpm]: https://pnpm.io
[config dependencies]: https://pnpm.io/settings#configdependencies
[pinned package manager]: https://pnpm.io/settings#packagemanagerdependencies
[trustLockfile]: https://pnpm.io/blog/releases/11.3#trustlockfile
[official pnpm base image]: https://pnpm.io/docker#official-pnpm-base-image
