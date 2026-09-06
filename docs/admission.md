# Admission control

| | Where | Enforces | If it breaks |
| --- | --- | --- | --- |
| PSA | apiserver, in-tree | `baseline` | cannot break — no webhook, no network |
| Kyverno | webhook, in-cluster | `restricted` + mutation | admission stops for governed namespaces |

PSA is the floor; Kyverno is the policy.

## PSA

`/etc/rancher/k3s/psa.yaml`, wired in with `admission-control-config-file`
([k3s](k3s.md#config)). `enforce: baseline`, `audit`/`warn: restricted`,
`kube-system` exempt.

At the apiserver rather than as namespace labels because a default has to apply
to namespaces nobody has labelled. A label is opt-in; this is the floor.

`technitium` is the only namespace that lifts it, labelled `privileged`:
baseline disallows any non-zero `hostPort` and PSA is all-or-nothing per
namespace. Kyverno does the enforcing there instead — the reason for having
both layers.

## Kyverno

Chart 3.9.0 in its own namespace, deployed as its own Flux tier so it is live
before anything it governs ([gitops](flux.md#tiers)). One replica per
controller; `reportsController` disabled.

`add-default-securitycontext` mutates pods outside the exempt namespaces,
adding only what is missing — the `+(field)` anchor never overwrites. Pod gets
`seccompProfile: RuntimeDefault`; every container and initContainer gets
`allowPrivilegeEscalation: false` and `capabilities.drop: [ALL]`.

`validate-pod-security-restricted` then enforces `restricted` at `latest`.
Mutation runs first, so a workload that merely omits the boilerplate is fixed
rather than rejected.

`technitium` is the one exception, and it takes two rules rather than one
relaxation: the broad rule excludes the namespace, and `restricted-technitium`
re-applies `restricted` there minus `Host Ports`. Excluding the namespace is
required — both rules would otherwise evaluate and the strict one would still
block the pod.

`Host Ports` is a container-level control, so Kyverno rejects the policy at
admission unless an image pattern accompanies it. Both mistakes surface the
same way, as a `dry-run failed` on the Kustomization rather than at pod
creation.

Both set `background: false` — they gate admission, they do not retroactively
mutate or report.

### failurePolicy: Fail

An unreachable webhook rejects regardless of whether the pod would have
passed. What makes that safe is that the namespaces which must come up during
an outage never consult it:

| Namespace | Exempted by |
| --- | --- |
| `kube-system`, `flux-system` | `config.webhooks.namespaceSelector` |
| `kyverno` | the chart's `excludeKyvernoNamespace` default |

`flux-system` had to be added there. The policies' own `exclude` blocks list
it, but a policy-level exclusion still requires Kyverno to be reachable to be
consulted — which would put the recovery mechanism behind the failure.

Cost: while Kyverno is down, governed namespaces cannot schedule pods,
external access included. Recovery holds throughout — kubectl over the admin
AP, Flux reconciling a fix, or deleting the webhook configurations by hand.
Boot is noisier, since each pod creation waits for the webhook timeout and
retries with backoff until Kyverno is up.

`Ignore` still leaves PSA's `baseline` underneath, which is what makes the
choice reversible.

### Known gap

No `PolicyReport` objects — decisions are events with the default 1h TTL.
Enough to debug a rejection, not enough to answer what has been violating a
policy all week.

### Checking it

```bash
kubectl get clusterpolicy
kubectl get mutatingwebhookconfigurations,validatingwebhookconfigurations \
  -o custom-columns='NAME:.metadata.name,FAILURE:.webhooks[*].failurePolicy' | grep -i kyverno
```

`runAsNonRoot` and `seccompProfile` are valid at either level, so a pod-level
setting reads as `<none>` in a container-level column:

```bash
kubectl get pods -A -o custom-columns=\
'NS:.metadata.namespace,POD:.metadata.name,'\
'POD_SECCOMP:.spec.securityContext.seccompProfile.type,'\
'CTR_SECCOMP:.spec.containers[*].securityContext.seccompProfile.type,'\
'ESCALATE:.spec.containers[*].securityContext.allowPrivilegeEscalation,'\
'DROP:.spec.containers[*].securityContext.capabilities.drop'
```

Pods admitted before a policy landed keep what they had until they restart.
