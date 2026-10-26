variable "aws_region" {
  description = "AWS Region used only for an explicit demonstration deployment."
  type        = string
}

variable "aws_profile" {
  description = "AWS CLI profile used by Terraform. The dedicated profile is used after bootstrap."
  type        = string
  default     = "lakehouse-deployer"
}

variable "operator_iam_user_name" {
  description = "Dedicated IAM user whose local access keys may assume the project Terraform role."
  type        = string
  default     = "lakehouse_user"
}

variable "demo_id" {
  description = "Unique, reviewable identifier for one isolated demonstration."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9-]{3,32}$", var.demo_id))
    error_message = "demo_id must contain 3 to 32 lowercase letters, digits, or hyphens."
  }
}

variable "expires_at" {
  description = "UTC expiry timestamp recorded on every taggable resource."
  type        = string
}
