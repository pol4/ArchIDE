{{/* chart name, overridable */}}
{{- define "archide.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* fully qualified app name */}}
{{- define "archide.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s" (include "archide.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "archide.labels" -}}
app: {{ include "archide.fullname" . }}
app.kubernetes.io/name: {{ include "archide.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: archide
{{- end -}}

{{- define "archide.selectorLabels" -}}
app.kubernetes.io/name: {{ include "archide.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* the image reference: a digest wins over a tag */}}
{{- define "archide.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{/* name of the secret holding the LLM key */}}
{{- define "archide.secretName" -}}
{{- default (printf "%s-llm" (include "archide.fullname" .)) .Values.config.llm.existingSecret -}}
{{- end -}}

{{/* name of the claim backing /data */}}
{{- define "archide.pvcName" -}}
{{- default (printf "%s-data" (include "archide.fullname" .)) .Values.persistence.existingClaim -}}
{{- end -}}
