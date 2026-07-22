# Toolchain research protocol

Use this reference only when `inferred_project.tooling_assessment.gaps` is non-empty or when updating an approved check whose command may be stale.

## Rules

1. Treat the deterministic assessment as evidence of what is present, not permission to install anything.
2. Verify each recommended tool against its official documentation. Use package registries only to confirm an exact package version after choosing the tool from official documentation.
3. Record the official URL, verification date, proposed version constraint, exact files changed, install command, configuration files, and argv-based check command.
4. Prefer a repository-native tool already used by the project over the catalog default.
5. Do not reject a candidate solely because it is not installed, requires a development-only dependency, or changes an approved development-tool configuration. Those facts belong in the proposal and approval gate.
6. Keep development tools out of runtime dependencies. Check both the tool's execution-Python requirement and the project's target-Python compatibility before selecting a version.
7. For legacy repositories, propose changed-file, baseline, or warning-only adoption when a full check would fail on unrelated historical code.
8. If browsing is unavailable, label the recommendation `official verification pending`; do not install or modify dependencies.
9. Never edit dependency manifests, project files, lock files, or tool configuration before the user approves the complete tooling proposal.

## Official starting points

| Language | Capability | Default candidate | Official documentation |
|---|---|---|---|
| Python | lint and format | Ruff | https://docs.astral.sh/ruff/ |
| Python | type check | mypy | https://mypy.readthedocs.io/en/stable/getting_started.html |
| Python | test | pytest | https://docs.pytest.org/en/stable/getting-started.html |
| C# | format | dotnet format | https://learn.microsoft.com/dotnet/core/tools/dotnet-format |
| C# | analyze | .NET analyzers | https://learn.microsoft.com/dotnet/fundamentals/code-analysis/overview |
| C# | test | dotnet test | https://learn.microsoft.com/dotnet/core/tools/dotnet-test |
| Vue | lint | eslint-plugin-vue | https://eslint.vuejs.org/user-guide/ |
| Vue | format | Prettier | https://prettier.io/docs/ |
| Vue | CSS lint | Stylelint | https://stylelint.io/user-guide/get-started/ |
| Vue | type check | vue-tsc | https://vuejs.org/guide/typescript/overview.html |
| Vue | test | Vitest | https://vitest.dev/guide/ |

These are candidates, not mandatory architecture. Official research and repository evidence decide the proposal.
