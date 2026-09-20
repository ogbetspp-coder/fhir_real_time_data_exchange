resource "google_logging_metric" "completed_runs" {
  name        = "ema_flow/completed_runs"
  description = "Count of completed ePI interoperability runs"
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.worker.name}"
    jsonPayload.message="ePI interoperability run completed"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "outcome"
      value_type  = "STRING"
      description = "validated or persisted"
    }
  }

  label_extractors = {
    outcome = "EXTRACT(jsonPayload.outcome)"
  }
}

resource "google_logging_metric" "failed_runs" {
  name        = "ema_flow/failed_runs"
  description = "Count of failed ePI interoperability requests"
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.worker.name}"
    severity>=ERROR
  EOT
}

resource "google_logging_metric" "validation_rejections" {
  name        = "ema_flow/validation_rejections"
  description = "Count of fail-closed source or EMA validation rejections"
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.worker.name}"
    (jsonPayload.message="Canonical Type 2 preflight rejected" OR jsonPayload.message="EMA preflight rejected")
  EOT
}

resource "google_logging_metric" "run_duration" {
  name        = "ema_flow/run_duration_ms"
  description = "End-to-end deterministic pipeline duration"
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.worker.name}"
    jsonPayload.message="ePI interoperability run completed"
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }

  value_extractor = "EXTRACT(jsonPayload.durationMs)"

  bucket_options {
    exponential_buckets {
      num_finite_buckets = 12
      growth_factor      = 2
      scale              = 100
    }
  }
}

resource "google_monitoring_alert_policy" "pipeline_failures" {
  display_name          = "EMA Flow pipeline failures (${var.environment})"
  combiner              = "OR"
  notification_channels = var.alert_notification_channels

  conditions {
    display_name = "At least one failed run in five minutes"
    condition_threshold {
      filter = join(" AND ", [
        "metric.type=\"logging.googleapis.com/user/${google_logging_metric.failed_runs.name}\"",
        "resource.type=\"cloud_run_revision\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }

      trigger {
        count = 1
      }
    }
  }
}

# Entitlement denials on the query service. Two log lines can say "not-entitled": the audit
# record of a tool call for a document outside the caller's entitlement
# (jsonPayload.stage="query-tool", jsonPayload.outcome="not-entitled") and the pre-transport
# 403 (jsonPayload.stage="query-http", jsonPayload.event="not-entitled"). Both carry
# jsonPayload.service="ema-flow-query"; the resource labels scope the filter to this service's
# revisions regardless.
resource "google_logging_metric" "query_entitlement_denials" {
  name        = "ema_flow/query_entitlement_denials"
  description = "Count of query service requests refused for want of entitlement"
  filter      = <<-EOT
    resource.type="cloud_run_revision"
    resource.labels.service_name="${google_cloud_run_v2_service.query.name}"
    jsonPayload.service="ema-flow-query"
    (jsonPayload.outcome="not-entitled" OR jsonPayload.event="not-entitled")
  EOT

  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "INT64"
    unit        = "1"
    labels {
      key         = "stage"
      value_type  = "STRING"
      description = "query-tool (audit record) or query-http (pre-transport refusal)"
    }
  }

  label_extractors = {
    stage = "EXTRACT(jsonPayload.stage)"
  }
}

resource "google_monitoring_notification_channel" "query_entitlement_denials_email" {
  count = var.alert_notification_email != "" ? 1 : 0

  display_name = "EMA Flow query entitlement denials (${var.environment})"
  type         = "email"
  labels = {
    email_address = var.alert_notification_email
  }
}

# Threshold: more than 5 denials in any rolling hour. A phase-1 tenant is a handful of
# principals with a hand-written entitlement map, so a denial is either a caller naming a
# document it was never given (one or two an hour from a mistyped id is plausible) or a
# misconfigured map after a deploy; a sustained rate above that is a caller probing for
# documents it is not entitled to, which the design note names as the existence oracle to
# watch, or a broken entitlement map that is refusing everyone. Either deserves a person.
resource "google_monitoring_alert_policy" "query_entitlement_denials" {
  count = var.alert_notification_email != "" ? 1 : 0

  display_name          = "EMA Flow query entitlement denials (${var.environment})"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.query_entitlement_denials_email[0].id]

  conditions {
    display_name = "More than five entitlement denials in one hour"
    condition_threshold {
      filter = join(" AND ", [
        "metric.type=\"logging.googleapis.com/user/${google_logging_metric.query_entitlement_denials.name}\"",
        "resource.type=\"cloud_run_revision\"",
      ])
      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "0s"

      aggregations {
        alignment_period     = "3600s"
        per_series_aligner   = "ALIGN_SUM"
        cross_series_reducer = "REDUCE_SUM"
      }

      trigger {
        count = 1
      }
    }
  }
}

resource "google_monitoring_dashboard" "operations" {
  dashboard_json = jsonencode({
    displayName = "EMA Flow — Interoperability and GxP Evidence (${var.environment})"
    mosaicLayout = {
      columns = 12
      tiles = [
        {
          xPos   = 0
          yPos   = 0
          width  = 6
          height = 4
          widget = {
            title = "Completed runs"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.completed_runs.name}\" AND resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod  = "60s"
                      perSeriesAligner = "ALIGN_RATE"
                    }
                  }
                }
                plotType = "LINE"
              }]
              yAxis = {
                label = "runs/s"
                scale = "LINEAR"
              }
            }
          }
        },
        {
          xPos   = 6
          yPos   = 0
          width  = 6
          height = 4
          widget = {
            title = "Failed runs"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.failed_runs.name}\" AND resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod  = "300s"
                      perSeriesAligner = "ALIGN_SUM"
                    }
                  }
                }
                plotType = "STACKED_BAR"
              }]
            }
          }
        },
        {
          xPos   = 0
          yPos   = 4
          width  = 6
          height = 4
          widget = {
            title = "Cloud Run request latency"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"run.googleapis.com/request_latencies\" AND resource.type=\"cloud_run_revision\" AND resource.label.\"service_name\"=\"${google_cloud_run_v2_service.worker.name}\""
                    aggregation = {
                      alignmentPeriod    = "60s"
                      perSeriesAligner   = "ALIGN_DELTA"
                      crossSeriesReducer = "REDUCE_PERCENTILE_95"
                      groupByFields      = ["resource.label.\"service_name\""]
                    }
                  }
                }
                plotType = "LINE"
              }]
            }
          }
        },
        {
          xPos   = 6
          yPos   = 4
          width  = 6
          height = 4
          widget = {
            title = "Workflow executions"
            xyChart = {
              dataSets = [{
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"workflows.googleapis.com/finished_execution_count\" AND resource.type=\"workflows.googleapis.com/Workflow\""
                    aggregation = {
                      alignmentPeriod  = "300s"
                      perSeriesAligner = "ALIGN_SUM"
                    }
                  }
                }
                plotType = "STACKED_BAR"
              }]
            }
          }
        },
        {
          xPos   = 0
          yPos   = 8
          width  = 12
          height = 3
          widget = {
            title = "Architecture evidence"
            text = {
              format = "MARKDOWN"
              content = join("\n", [
                "Use `runId` to correlate Workflows, Cloud Run structured logs, Cloud Audit Logs, immutable GCS artifacts, KMS signatures, the BigQuery ledger, and Healthcare API writes.",
                "",
                "The Healthcare API stream normally reaches BigQuery in dozens of seconds. Workflow completion proves the Bundle row became queryable."
              ])
            }
          }
        }
      ]
    }
  })
}
