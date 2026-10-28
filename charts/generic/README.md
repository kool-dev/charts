## generic

Kool generic chart, you can deploy pretty much anything using this little guy.

### Deployment lifecycle (1.9.2)

All settings are optional; omission preserves previous rendering and Kubernetes defaults.
`strategy` is passed through unchanged (as in earlier chart versions).

```yaml
terminationGracePeriodSeconds: 90
minReadySeconds: 5
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1
containers:
  default:
    image:
      repository: ghcr.io/example/app
      tag: latest
    ports:
      - containerPort: 80
    readinessProbe:
      httpGet: {path: /up, port: 80}
      periodSeconds: 5
    startupProbe:
      httpGet: {path: /up, port: 80}
      periodSeconds: 2
      failureThreshold: 30
    lifecycle:
      preStop:
        exec:
          command: ["sh", "-c", "sleep 10"]
```

`minReadySeconds` belongs to the Deployment; `terminationGracePeriodSeconds` belongs
to its pod spec. Both preserve explicit `0` (zero grace disables graceful shutdown).
Lifecycle and startup probes apply only to the container that declares them, never
to init containers, hook Jobs, or CronJobs. Existing readiness-to-liveness fallback
is unchanged. Resource names and selectors are unchanged.

The delay lets traffic removal propagate before SIGTERM. The application must then
drain requests itself (for example, Nginx before Octane). A 10-second delay plus about
60 seconds of draining fits inside 90 seconds of total grace; these are application
choices, not chart defaults. Align Supervisor stop timeouts and server shutdown
timeouts with that budget. Kubernetes counts preStop time against the total grace.

Run render tests with Python 3, PyYAML and Helm installed:
`python3 -m unittest discover -s tests -v`.


### Upgrading to 1.8

In 1.8, we've fixed a bug in the Deployment labels and selector labels. As a result, the `matchLabels` of the Deployment have changed. Unfortunately, `matchLabels` are immutable (see https://handbook.giantswarm.io/docs/dev-and-releng/app-developer-processes/matchlabels-are-immutable/), so to complete the upgrade, you need to delete the Deployment before proceeding.

To do this, run the following command:

```bash
kubectl delete deployment --namespace={namespace} --selector=app.kubernetes.io/instance={release_name} --selector='!app.kubernetes.io/deployment'
```

### Upgrading to 1.4

Service signature has changed in order to support multiple ports in the same service:

```diff
-services:
-  default:
-    ...
-    port: 80
-    # targetPort: 80
+services:
+  default:
+    ...
+    ports:
+      - port: 80
+        # targetPort: 80
```
