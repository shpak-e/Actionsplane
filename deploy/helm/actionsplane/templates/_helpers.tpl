{{- define "actionsplane.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "actionsplane.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "actionsplane.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "actionsplane.labels" -}}
app.kubernetes.io/name: {{ include "actionsplane.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{/* Service DNS names derived from the release */}}
{{- define "actionsplane.postgresHost" -}}{{ include "actionsplane.fullname" . }}-postgres{{- end -}}
{{- define "actionsplane.redisHost" -}}{{ include "actionsplane.fullname" . }}-redis{{- end -}}
{{- define "actionsplane.apiHost" -}}{{ include "actionsplane.fullname" . }}-api{{- end -}}

{{/* image ref for a component: .ctx (root), .name (imageName) */}}
{{- define "actionsplane.image" -}}
{{- printf "%s/%s:%s" .ctx.Values.image.registry .name .ctx.Values.image.tag -}}
{{- end -}}

{{/* shared envFrom for app pods */}}
{{- define "actionsplane.envFrom" -}}
- configMapRef:
    name: {{ include "actionsplane.fullname" . }}-config
- secretRef:
    name: {{ .Values.secrets.existingSecret }}
{{- end -}}

{{/* shared securityContext for app containers */}}
{{- define "actionsplane.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: ["ALL"]
{{- end -}}
