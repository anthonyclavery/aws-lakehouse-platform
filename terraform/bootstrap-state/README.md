# Terraform state bootstrap

This isolated configuration creates the persistent state bucket for this project. It is separate from demonstration data, blocked from public access, encrypted with S3-managed encryption, versioned, and protected by `prevent_destroy`.

The first local run creates the bucket using the controlled bootstrap identity. Its state is then migrated to `bootstrap-state/terraform.tfstate`. The main infrastructure state is stored separately at `control-plane/terraform.tfstate`.

`backend.hcl` is deliberately ignored: it contains the account-specific bucket name and must never be committed.
