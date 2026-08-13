{{/*
Chart name, overridable.
*/}}
{{- define "augur.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Qualified name for every resource.

Normally "<release>-<chart>", but when the release name already contains the
chart name that produces "augur-augur". Collapsing the duplicate is the
convention Helm's own scaffold uses.

  release=augur  chart=augur  ->  augur
  release=test   chart=augur  ->  test-augur
*/}}
{{- define "augur.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
Labels on every object.

`version` is deliberately absent from selectorLabels: selectors are immutable
on a Deployment, so including a version there makes the next chart bump fail
the upgrade.
*/}}
{{- define "augur.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "augur.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "augur.selectorLabels" -}}
app.kubernetes.io/name: {{ include "augur.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Container image reference.
*/}}
{{- define "augur.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{/*
Environment for the app and the migration init container. Config from the
ConfigMap; everything sensitive from an existing Secret, never values.yaml.
*/}}
{{- define "augur.envFrom" -}}
- configMapRef:
    name: {{ include "augur.fullname" . }}-config
- secretRef:
    name: {{ .Values.existingSecret }}
{{- end -}}

{{/*
Postgres service name. Defined once so the StatefulSet and the wait init
container can never drift apart.
*/}}
{{- define "augur.postgresHost" -}}
{{ include "augur.fullname" . }}-postgres
{{- end -}}

{{/*
Selector labels for the bundled Postgres.

Deliberately a DIFFERENT app.kubernetes.io/name, not the app's selectorLabels
plus a component. Sharing `name` makes the app's Service and Deployment select
the database pod too, so traffic load-balances between the API and Postgres and
`kubectl logs deploy/augur` picks the wrong pod.
*/}}
{{- define "augur.postgresSelectorLabels" -}}
app.kubernetes.io/name: {{ include "augur.name" . }}-postgres
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: database
{{- end -}}
