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
