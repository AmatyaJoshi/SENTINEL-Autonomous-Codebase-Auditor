{{- define "sentinel.name" -}}{{ .Chart.Name }}{{- end -}}
{{- define "sentinel.fullname" -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- define "sentinel.labels" -}}
app.kubernetes.io/name: {{ include "sentinel.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "sentinel.env" -}}
- name: SENTINEL_WORK_DIR
  value: /data
- name: SENTINEL_API_HOST
  value: 0.0.0.0
- name: SENTINEL_LOG_JSON
  value: {{ .Values.config.logJson | quote }}
- name: SENTINEL_ENVIRONMENT
  value: {{ .Values.config.environment | quote }}
- name: SENTINEL_EXECUTION_MODE
  value: {{ .Values.config.executionMode | quote }}
- name: SENTINEL_API_CORS_ORIGINS
  value: {{ .Values.config.corsOrigins | quote }}
- name: SENTINEL_RETENTION_DAYS
  value: {{ .Values.config.retentionDays | quote }}
{{- if .Values.config.otlpEndpoint }}
- name: SENTINEL_OTLP_ENDPOINT
  value: {{ .Values.config.otlpEndpoint | quote }}
{{- end }}
{{- if .Values.config.primaryModel }}
- name: SENTINEL_PRIMARY_MODEL
  value: {{ .Values.config.primaryModel | quote }}
{{- end }}
{{- if .Values.config.cheapModel }}
- name: SENTINEL_CHEAP_MODEL
  value: {{ .Values.config.cheapModel | quote }}
{{- end }}
{{- if .Values.postgres.url }}
- name: SENTINEL_DATABASE_URL
  value: {{ .Values.postgres.url | quote }}
{{- else if .Values.postgres.urlSecret.name }}
- name: SENTINEL_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.urlSecret.name }}
      key: {{ .Values.postgres.urlSecret.key }}
{{- else }}
- name: SENTINEL_DATABASE_URL
  value: sqlite:////data/sentinel.db
{{- end }}
{{- end -}}
