# Document AI processor for the extractor spike (docs/design/extractor-spike.md, roadmap item
# 0). Document AI only offers processors in the multi-regions "eu" and "us" — never a specific
# region such as "europe-west4" (var.region) — so this is pinned to "eu" directly.
#
# Checked against the hashicorp/google provider pinned in infra/.terraform.lock.hcl (8.3.0,
# matching the "~> 8.0" constraint in infra/versions.tf): the provider's resource reference
# documents google_document_ai_processor as generally available (not google-beta) at this
# version, with location, display_name, and type as its required arguments and processor type
# values passed through to the Document AI API rather than validated by the provider schema.
# LAYOUT_PARSER_PROCESSOR is a current Document AI processor type (the Layout Parser).
resource "google_document_ai_processor" "layout" {
  location     = "eu"
  display_name = "${local.name_prefix}-layout-parser"
  type         = "LAYOUT_PARSER_PROCESSOR"

  depends_on = [google_project_service.required]
}
