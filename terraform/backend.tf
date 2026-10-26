terraform {
  # Backend values stay outside version control in backend.hcl because they
  # contain the account-specific bucket name created by this configuration.
  backend "s3" {}
}
