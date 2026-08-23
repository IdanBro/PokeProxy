{{- define "pokeproxy.name" -}}
{{ .Chart.Name }}
{{- end -}}

{{- define "pokeproxy.chartLabel" -}}
{{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "pokeproxy.labels" -}}
helm.sh/chart: {{ include "pokeproxy.chartLabel" . }}
app.kubernetes.io/part-of: {{ include "pokeproxy.name" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "pokeproxy.component.fullname" -}}
{{- if eq .component (include "pokeproxy.name" .context) -}}
{{ .context.Release.Name }}
{{- else -}}
{{ .context.Release.Name }}-{{ .component }}
{{- end -}}
{{- end -}}

{{- define "pokeproxy.component.selectorLabels" -}}
app.kubernetes.io/name: {{ .component }}
app.kubernetes.io/instance: {{ .context.Release.Name }}
{{- end -}}

{{- define "pokeproxy.component.labels" -}}
{{ include "pokeproxy.component.selectorLabels" . }}
{{ include "pokeproxy.labels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}
