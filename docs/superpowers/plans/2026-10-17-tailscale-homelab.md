# Tailscale in the Homelab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the Tailscale Kubernetes operator in the homelab so cluster services can be reached from the owner's devices over the tailnet, starting with the golf-etl upload page.

**Architecture:** An OpenSpec change `add-tailscale-operator` in `rumstead/homelab` adds one multi-source Argo CD Application `tailscale-operator` under the app-of-apps, the same shape as `kube-prometheus-stack`: one source is the official Helm chart, the other is `kubernetes/manifests/tailscale/` with the privileged `namespace.yaml` and the operator's OAuth credentials rendered from a SOPS file through KSOPS. Services join the tailnet through a Tailscale `Ingress` with HTTPS on the `ts.net` name. The tailnet policy, HTTPS setting, and OAuth client are one-time clicks in the Tailscale admin console; nothing outside the owner's hands can change the tailnet policy.

**Tech Stack:** Argo CD, Helm chart `tailscale-operator` 1.104.1 from `https://pkgs.tailscale.com/helmcharts`, Kustomize with KSOPS, SOPS (age), OpenSpec CLI 1.14.0.

**Spec:** `docs/superpowers/specs/2026-10-17-github-sessions-design.md` (section "Tailscale in the homelab")

**Repo:** all file paths below are in `/home/rumstead/homelab` unless they start with `golf-etl/`.

## Global Constraints

- Free Tailscale Personal plan features only. No Funnel: nothing is reachable from the public internet.
- No credential that quietly expires. The operator authenticates with a Tailscale OAuth client, never a personal auth key.
- Chart pinned to `1.104.1`.
- Secrets are committed only as `*.sops.yaml`, encrypted with `sops encrypt -i` (the repo's `.sops.yaml` rule encrypts `data` and `stringData`).
- Commits: `<type>(<scope>): <subject>`, subject line only, `git commit -s`, no AI attribution. Scope is `tailscale`.
- PR bodies follow the owner's `writing-as-rumstead` skill: short, lowercase, conversational.
- No em dashes or en dashes anywhere.
- Work on a branch, never on `main`. Merge only when the owner says to.

## Review Focus

1. The operator pod starting before the `operator-oauth` Secret exists: Argo CD applies the Namespace and Secret before the Deployment within one sync, so the pod should start with no manual restart. Task 4 Step 2 checks this.
2. Proxy pods rejected by the cluster's Pod Security default (`enforce: baseline` in `talos/controlplane.yaml`): the proxy's init container needs privileges, so the `tailscale` namespace is labeled `privileged`. Task 4 Step 4 checks the proxy pod is Running.
3. The OAuth client not allowed to use `tag:k8s-operator`: the operator logs an auth error and never joins. Task 4 Step 3 greps the logs.
4. Plaintext credentials committed: Task 3 Step 4 checks the file before commit.
5. The smoke test leaving a device in the tailnet: Task 4 Step 6 checks it is gone.

---

### Task 1: OpenSpec change proposal

**Files:**
- Create: `openspec/changes/add-tailscale-operator/proposal.md`
- Create: `openspec/changes/add-tailscale-operator/tasks.md`
- Create: `openspec/changes/add-tailscale-operator/specs/tailscale/spec.md`

**Interfaces:**
- Produces: change id `add-tailscale-operator`, new capability `tailscale`. Task 5 archives it.

- [ ] **Step 1: Branch**

```bash
cd /home/rumstead/homelab && git checkout main && git pull && git checkout -b feat/tailscale-operator
```

- [ ] **Step 2: Write `proposal.md`**

Follow `openspec/changes/archive/2026-10-05-add-golf-etl/proposal.md`: `# Change: Run the Tailscale operator`, then `## Why` (owner's devices need to reach cluster services from off the LAN without a public endpoint; golf-etl's upload page is the first user), `## What Changes` (the Application, the `tailscale` namespace labeled privileged, the tailnet policy and settings kept as one-time admin console steps, OAuth credentials as `operator-oauth.sops.yaml`, services exposed with a Tailscale Ingress), `## Impact` (Affected specs: `tailscale` (new); affected code: `kubernetes/argocd-apps/tailscale/`, `kubernetes/manifests/tailscale/`, `AGENTS.md`; breaking changes: none; out of scope: exposing any real service, a subnet router, Funnel).

- [ ] **Step 3: Write `specs/tailscale/spec.md`**

`## ADDED Requirements` with these requirements, each with the scenarios listed, in the `#### Scenario:` / `- **WHEN**` / `- **THEN**` format of the archived golf-etl spec:

- `### Requirement: Operator Install`: Argo CD Application `tailscale-operator` installs chart `tailscale-operator` version `1.104.1` from `https://pkgs.tailscale.com/helmcharts` into namespace `tailscale`. Scenarios: "Operator joins the tailnet" (WHEN the Application is synced THEN a device named `tailscale-operator` tagged `tag:k8s-operator` SHALL appear in the tailnet).
- `### Requirement: OAuth Credentials`: the OAuth client ID and secret SHALL be stored in git only as `kubernetes/manifests/tailscale/operator-oauth.sops.yaml`, rendered by KSOPS into Secret `operator-oauth` (keys `client_id`, `client_secret`) by Application `tailscale-operator`. Scenarios: "Credentials at rest" (only encrypted), "Credentials do not expire" (WHEN 180 days pass THEN the operator SHALL keep authenticating without a manual step).
- `### Requirement: Tailnet Only Exposure`: a Service SHALL be reachable only from tailnet devices, through an Ingress with `ingressClassName: tailscale` and HTTPS on its `ts.net` name. Funnel SHALL NOT be enabled. Scenarios: "Device on the tailnet" (WHEN a tailnet device opens `https://<name>.<tailnet>.ts.net` THEN it SHALL get the service over a valid certificate), "Device off the tailnet" (WHEN a device not on the tailnet tries THEN the name SHALL NOT resolve to anything reachable).
- `### Requirement: Proxy Pod Security`: the `tailscale` namespace SHALL carry `pod-security.kubernetes.io/enforce`, `audit`, and `warn` set to `privileged`. Scenario: "Proxy created" (WHEN an Ingress is created THEN its proxy pod SHALL be admitted and Running).

- [ ] **Step 4: Write `tasks.md`**

Same layout as the archived golf-etl `tasks.md`: `## 1. Prerequisites` (tailnet policy tags, MagicDNS and HTTPS, OAuth client), `## 2. Manifests` (Task 3's files, local render, `AGENTS.md`), `## 3. Rollout and verification` (Task 4's checks). All unchecked.

- [ ] **Step 5: Validate**

```bash
cd /home/rumstead/homelab && podman run --rm -v "$PWD":/w:Z -w /w docker.io/library/node:22-slim npx -y @fission-ai/openspec@1.14.0 validate add-tailscale-operator --strict
```
Expected: `Change 'add-tailscale-operator' is valid`

- [ ] **Step 6: Commit**

```bash
git add openspec/changes/add-tailscale-operator
git commit -s -m "docs(tailscale): propose running the tailscale operator"
```

### Task 2: Owner's Tailscale setup (manual, blocking)

These are done by the owner in the Tailscale admin console. Hand them over as a short checklist and wait for "done" before Task 3 Step 3.

- [ ] **Step 1: Owner adds tags to the tailnet policy file** (Access controls, under `tagOwners`):

```json
"tag:k8s-operator": [],
"tag:k8s": ["tag:k8s-operator"]
```

- [ ] **Step 2: Owner turns on MagicDNS and HTTPS certificates** (DNS page).

- [ ] **Step 3: Owner creates an OAuth client** (Settings, OAuth clients) with write access for `General/Services`, `Devices/Core`, and `Keys/Auth Keys`, each with tag `tag:k8s-operator`. The owner keeps the client ID and secret for Task 3 Step 3. They never go into chat.

- [ ] **Step 4: Owner installs the Tailscale app on the iPhone and signs in** to the same tailnet.

### Task 3: Manifests

**Files:**
- Create: `kubernetes/argocd-apps/tailscale/tailscale-operator-app.yaml`
- Create: `kubernetes/manifests/tailscale/namespace.yaml`
- Create: `kubernetes/manifests/tailscale/kustomization.yaml`
- Create: `kubernetes/manifests/tailscale/ksops-generator.yaml`
- Create: `kubernetes/manifests/tailscale/operator-oauth.sops.yaml`
- Modify: `AGENTS.md` (Managed Argo CD applications list)

**Interfaces:**
- Produces: Secret `tailscale/operator-oauth`; IngressClass `tailscale` (installed by the chart). The upload service plan uses `ingressClassName: tailscale`.

- [ ] **Step 1: Write `tailscale-operator-app.yaml`**

Copy the multi-source shape of `kubernetes/argocd-apps/monitoring/kube-prometheus-stack-app.yaml` (`sources:` list, automated prune and selfHeal). Values: `metadata.name: tailscale-operator`; first source `repoURL: https://pkgs.tailscale.com/helmcharts`, `chart: tailscale-operator`, `targetRevision: 1.104.1`, no `oauth` values (the chart then mounts the pre-created Secret `operator-oauth`); second source `repoURL: https://github.com/rumstead/homelab.git`, `targetRevision: main`, `path: kubernetes/manifests/tailscale`; `destination.namespace: tailscale`; no `CreateNamespace` (the namespace comes from `namespace.yaml`).

- [ ] **Step 2: Write `namespace.yaml`, `kustomization.yaml`, `ksops-generator.yaml`**

`namespace.yaml` copies `kubernetes/manifests/monitoring/namespace.yaml` with `name: tailscale` (the three `pod-security.kubernetes.io/*: privileged` labels), with a one-line comment that Tailscale proxy pods need privileges the cluster's `baseline` default rejects. `kustomization.yaml` has `resources: [namespace.yaml]` and `generators: [ksops-generator.yaml]`. `ksops-generator.yaml` copies `kubernetes/manifests/golf-etl/ksops-generator.yaml` with `name: tailscale-secrets` and `files: [./operator-oauth.sops.yaml]`.

- [ ] **Step 3: Owner writes the encrypted Secret**

Give the owner this to run with `!` so the credentials never reach chat:

```bash
cd /home/rumstead/homelab && read -rp "client id: " ID && read -rsp "client secret: " SECRET && echo && cat > kubernetes/manifests/tailscale/operator-oauth.sops.yaml <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: operator-oauth
  namespace: tailscale
stringData:
  client_id: "$ID"
  client_secret: "$SECRET"
EOF
sops encrypt -i kubernetes/manifests/tailscale/operator-oauth.sops.yaml && unset ID SECRET
```

- [ ] **Step 4: Check nothing is in plaintext**

```bash
grep -E 'client_(id|secret): ' kubernetes/manifests/tailscale/operator-oauth.sops.yaml | grep -vc 'ENC\['
```
Expected: `0`

- [ ] **Step 5: Render both Applications locally**

```bash
kustomize build --enable-alpha-plugins --enable-exec kubernetes/manifests/tailscale | kubectl apply --dry-run=client -f -
helm template tailscale-operator tailscale-operator --repo https://pkgs.tailscale.com/helmcharts --version 1.104.1 -n tailscale | kubectl apply --dry-run=client -f - >/dev/null && echo chart-ok
```
Expected: `namespace/tailscale created (dry run)`, `secret/operator-oauth created (dry run)`, and `chart-ok`. Do not print the rendered Secret.

- [ ] **Step 6: Add `tailscale-operator` to `AGENTS.md`** under "Managed Argo CD applications", after `golf-etl`.

- [ ] **Step 7: Commit, push, open the PR**

```bash
git add kubernetes/argocd-apps/tailscale kubernetes/manifests/tailscale AGENTS.md
git commit -s -m "feat(tailscale): run the tailscale operator"
git push -u origin feat/tailscale-operator
```
Open a PR titled `feat(tailscale): run the tailscale operator (openspec add-tailscale-operator)` with a short body in the owner's voice. Ask the owner before merging.

### Task 4: Rollout and verification

**Interfaces:**
- Consumes: Task 3 merged to `main`.

- [ ] **Step 1: Sync**

```bash
kubectl -n argocd annotate application app-of-apps argocd.argoproj.io/refresh=normal --overwrite
kubectl -n argocd get application tailscale-operator -o custom-columns=NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status
```
Expected: `Synced` and `Healthy` within a few minutes.

- [ ] **Step 2: Operator is Running without a manual restart**

```bash
kubectl -n tailscale get pods -l app=operator
```
Expected: one pod `Running`, `RESTARTS` 0 or a small number from waiting on the Secret. No `kubectl delete pod` was needed.

- [ ] **Step 3: Operator authenticated**

```bash
kubectl -n tailscale logs deploy/operator | grep -iE 'not permitted|invalid|unauthorized|403' || echo clean
```
Expected: `clean`. The owner confirms a `tailscale-operator` device tagged `tag:k8s-operator` on the Machines page.

- [ ] **Step 4: Smoke test with a throwaway service**

Apply, not committed: namespace `tailscale-smoke`, a Deployment and Service for `traefik/whoami:v1.11.0` on port 80, and an Ingress with `ingressClassName: tailscale`, `defaultBackend` to that Service, and `tls: [{hosts: [whoami]}]`.

```bash
kubectl -n tailscale get pods
kubectl -n tailscale-smoke get ingress whoami -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```
Expected: a proxy pod `Running` in `tailscale` and a hostname like `whoami.<tailnet>.ts.net`.

- [ ] **Step 5: Owner checks from the iPhone**

With Tailscale on and wifi off (cell data), the owner opens `https://whoami.<tailnet>.ts.net` in Safari. Expected: the whoami text, no certificate warning. With Tailscale off, the page does not load.

- [ ] **Step 6: Remove the smoke test**

```bash
kubectl delete namespace tailscale-smoke
```
Expected: the `whoami` device disappears from the Machines page within a few minutes (the owner confirms).

- [ ] **Step 7: Tick `openspec/changes/add-tailscale-operator/tasks.md`** on a branch, and keep it for Task 5.

### Task 5: Archive the change

- [ ] **Step 1: Archive**

```bash
cd /home/rumstead/homelab && git checkout main && git pull && git checkout -b docs/archive-add-tailscale-operator
podman run --rm -v "$PWD":/w:Z -w /w docker.io/library/node:22-slim npx -y @fission-ai/openspec@1.14.0 archive add-tailscale-operator --yes
podman run --rm -v "$PWD":/w:Z -w /w docker.io/library/node:22-slim npx -y @fission-ai/openspec@1.14.0 validate --strict
```
Expected: the change moves to `openspec/changes/archive/2026-10-<dd>-add-tailscale-operator/`, `openspec/specs/tailscale/spec.md` exists, validation passes. Include the ticked `tasks.md` from Task 4 Step 7.

- [ ] **Step 2: Commit and PR**

```bash
git add openspec
git commit -s -m "docs(openspec): archive add-tailscale-operator"
git push -u origin docs/archive-add-tailscale-operator
```
Open the PR and ask the owner before merging.
