# https://github.com/hermetoproject/hermeto/issues/1802
Feature: pnpm multi-document lockfiles

  Since pnpm 12 a pnpm-lock.yaml may be a stream of two YAML documents: a
  leading env document recording the project's config dependencies and its
  pinned package manager, followed by the project's own dependency graph. Both
  documents declare the same lockfileVersion, and both name packages that pnpm
  installs. Hermeto must read every document, report and fetch every package
  the lockfile installs, and preserve the stream when it rewrites the lockfile
  for the hermetic build.

  Scenario: A two-document lockfile is accepted
    Given a project with a pnpm lockfile holding an env document and a dependency graph
    When hermeto fetches the pnpm dependencies
    Then the fetch succeeds

  Scenario: The SBOM covers packages from both documents
    Given a project with a pnpm lockfile holding an env document and a dependency graph
    When hermeto fetches the pnpm dependencies
    Then the SBOM lists every package declared in the lockfile
    And the SBOM lists the pinned package manager declared in the env document

  Scenario: Packages from the env document are available for the hermetic build
    Given a project with a pnpm lockfile holding an env document and a dependency graph
    When hermeto fetches the pnpm dependencies
    Then every package declared in the env document resolves to a local tarball

  Scenario: The rewritten lockfile keeps the pinned package manager
    Given a project with a pnpm lockfile holding an env document and a dependency graph
    When hermeto fetches the pnpm dependencies
    And hermeto injects the files for the hermetic build
    Then the rewritten lockfile still declares the pinned package manager

  Scenario: The rewritten lockfile keeps the document start marker
    Given a project with a two-document pnpm lockfile
    When hermeto fetches the pnpm dependencies
    And hermeto injects the files for the hermetic build
    Then the rewritten lockfile begins with a document start marker

  Scenario Outline: The rewritten lockfile keeps the document structure it was given
    Given a project with a <shape> pnpm lockfile
    When hermeto fetches the pnpm dependencies
    And hermeto injects the files for the hermetic build
    Then the rewritten lockfile is a <shape> stream

    Examples:
      | shape           |
      | single-document |
      | two-document    |

  Scenario: A lockfile that opens with a document start marker keeps it
    Given a project with a marked-single-document pnpm lockfile
    When hermeto fetches the pnpm dependencies
    And hermeto injects the files for the hermetic build
    Then the rewritten lockfile begins with a document start marker

  Scenario: A lockfile written without a document start marker gains none
    Given a project with a single-document pnpm lockfile
    When hermeto fetches the pnpm dependencies
    And hermeto injects the files for the hermetic build
    Then the rewritten lockfile begins with no document start marker

  Scenario: A package both documents declare is rewritten in both
    Given a project with a pnpm lockfile declaring one package in both documents
    When hermeto fetches the pnpm dependencies
    And hermeto injects the files for the hermetic build
    Then every package in the rewritten lockfile resolves to a local tarball

  Scenario: A lockfile with more than two documents is rejected
    Given a project with a three-document pnpm lockfile
    When hermeto fetches the pnpm dependencies
    Then the fetch fails
    And the error reports how many documents the lockfile holds
    And the error suggests regenerating the lockfile

  Scenario: Documents that disagree on a package checksum are rejected
    Given a project with a pnpm lockfile whose documents disagree on a checksum
    When hermeto fetches the pnpm dependencies
    Then the fetch fails
    And the error names the package the documents disagree on
    And the error suggests regenerating the lockfile

  Scenario: An env document declaring an unsupported lockfileVersion is rejected
    Given a project with a pnpm lockfile whose env document declares lockfileVersion 10.0
    When hermeto fetches the pnpm dependencies
    Then the fetch fails
    And the error names the unsupported lockfileVersion
    And the error points at the env document
