variable "aws_region" {
  type        = string
  description = "AWS region hosting the state bucket."
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI profile used to create and maintain the state bucket."
  default     = "lakehouse-deployer"
}

variable "terraform_operator_role_name" {
  type        = string
  description = "Dedicated role granted access to this project's remote Terraform state."
  default     = "aws-lakehouse-platform-demo-terraform-operator"
}
