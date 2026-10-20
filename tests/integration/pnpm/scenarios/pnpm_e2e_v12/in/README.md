# pnpm v12 scenario

A test scenario for pnpm v12, whose `pnpm-lock.yaml` is a stream of two YAML documents rather
than one: a leading env document recording the project's config dependencies and its pinned
package manager, followed by the project's own dependency graph.

The env document appears whenever the project has `configDependencies` or
`packageManagerDependencies`. `pnpm init` pins `packageManager` by default, so most pnpm v12
projects have one. Both documents declare `lockfileVersion: '9.0'`, and both name packages that
pnpm installs.

## Prerequisites

Check the pnpm version in the `package.json` file. If you don't have this version, you can
install it via corepack. It is possible to have multiple pnpm versions installed on the system.

## Dependencies

```bash
pnpm install
```

This project is designed to validate that pnpm v12 correctly handles:

- [x] A multi-document lockfile is accepted rather than rejected as invalid YAML
- [x] Packages declared in the env document are reported in the SBOM
- [x] Packages declared in the env document are prefetched to local tarballs
- [x] The rewritten lockfile keeps both documents, including the leading `---`

## Why this scenario does not run `pnpm install`

Unlike the v10 and v11 scenarios, the `Containerfile` here checks the prefetch result
instead of performing a hermetic install.

pnpm 12 resolves the env document's `packageManagerDependencies` and
`configDependencies` before it reads the project's `.npmrc` — a config dependency may
itself supply that `.npmrc` — using a client that refuses a `file://` registry. It
fails with `ERR_PNPM_BAD_CONFIG_DEP` ("URL scheme is not allowed") when the registry is
set, reaches for `registry.npmjs.org` when it is not, and ignores the
`resolution.tarball` Hermeto writes for those packages. No offline install of a project
with an env document succeeds today, whatever Hermeto emits.

What Hermeto controls — and what regressed — is the prefetch half, which this scenario
covers. Dropping the env document on rewrite, or dropping its leading `---`, makes pnpm
re-resolve the pinned package manager and fail with
`ERR_PNPM_FROZEN_LOCKFILE_WITH_OUTDATED_LOCKFILE`.

## Useful resources

- <https://pnpm.io/lockfile>
- <https://pnpm.io/settings#packagemanagerdependencies>
- <https://pnpm.io/settings#configdependencies>
- <https://github.com/hermetoproject/hermeto/issues/1802>
