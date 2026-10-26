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

variable "cleanup_schedule_enabled" {
  type        = bool
  description = "Whether to create the one-time EventBridge Scheduler cleanup invocation."
  default     = false
}

variable "cleanup_demo_id" {
  type        = string
  description = "Demo identifier that the cleanup build must verify before destruction."
  default     = null
  nullable    = true
}

variable "cleanup_expires_at" {
  type        = string
  description = "Expiry timestamp expected by the cleanup build before destruction."
  default     = null
  nullable    = true
}

variable "cleanup_schedule_at" {
  type        = string
  description = "One-time local date and time in YYYY-MM-DDTHH:MM:SS format, evaluated in Europe/Paris."
  default     = null
  nullable    = true
}
