## generic

Kool generic chart, you can deploy pretty much anything using this little guy.


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
