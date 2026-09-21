{{- define "onix.name" -}}{{ .Chart.Name }}{{- end -}}
{{- define "onix.fullname" -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- define "onix.labels" -}}
app.kubernetes.io/name: {{ include "onix.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{- define "onix.env" -}}
- name: ONIX_WORK_DIR
  value: /data
- name: ONIX_API_HOST
  value: 0.0.0.0
- name: ONIX_LOG_JSON
  value: {{ .Values.config.logJson | quote }}
- name: ONIX_ENVIRONMENT
  value: {{ .Values.config.environment | quote }}
- name: ONIX_EXECUTION_MODE
  value: {{ .Values.config.executionMode | quote }}
- name: ONIX_API_CORS_ORIGINS
  value: {{ .Values.config.corsOrigins | quote }}
- name: ONIX_RETENTION_DAYS
  value: {{ .Values.config.retentionDays | quote }}
{{- if .Values.config.otlpEndpoint }}
- name: ONIX_OTLP_ENDPOINT
  value: {{ .Values.config.otlpEndpoint | quote }}
{{- end }}
{{- if .Values.config.primaryModel }}
- name: ONIX_PRIMARY_MODEL
  value: {{ .Values.config.primaryModel | quote }}
{{- end }}
{{- if .Values.config.cheapModel }}
- name: ONIX_CHEAP_MODEL
  value: {{ .Values.config.cheapModel | quote }}
{{- end }}
{{- if .Values.postgres.url }}
- name: ONIX_DATABASE_URL
  value: {{ .Values.postgres.url | quote }}
{{- else if .Values.postgres.urlSecret.name }}
- name: ONIX_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.postgres.urlSecret.name }}
      key: {{ .Values.postgres.urlSecret.key }}
{{- else }}
- name: ONIX_DATABASE_URL
  value: sqlite:////data/onix.db
{{- end }}
{{- end -}}
