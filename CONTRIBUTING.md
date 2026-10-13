# Contribution workflow

All changes target a short-lived branch and reach `main` through a Pull Request. Direct pushes to `main` are not part of the normal workflow.

The Pull Request validation workflow checks Git formatting, blocked file paths, common credential patterns, internal Markdown links, and Mermaid fences. Future implementation work will extend this workflow with Python tests, dependency checks, Terraform formatting, Terraform validation, and infrastructure security checks.

After the checks pass, the Pull Request remains available for review. Merging is manual; automated merge is intentionally disabled for this repository.

The branch-protection rule for `main` must require the `repository-hygiene` check before a Pull Request can be merged.
